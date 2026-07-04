"""
Google Sheets integration service for the Telegram CRM.

Connects to Google Sheets via a service account, reads configured spreadsheets,
and imports sales (doanh thu) and employee performance data into the CRM database.

Usage from scheduler:
    from app.services.google_sheets_service import sync_from_sheet
    await sync_from_sheet()

Usage with explicit parameters:
    from app.services.google_sheets_service import GoogleSheetsService
    service = GoogleSheetsService(service_account_key="path/to/key.json")
    df = service.read_sheet(sheet_url="...", worksheet="Sheet1")
"""
import logging
from datetime import datetime, date
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Employee, Order, Product, Customer, LeaderboardSnapshot
from app.services.google_sheets_config import (
    load_config,
    get_service_account_key,
    get_enabled_sheets,
    extract_sheet_id,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers -- reused from import_service but defined here to keep this module
# self-contained (import_service helpers are sync, these are identical).
# ---------------------------------------------------------------------------

def _parse_date(value):
    """Parse a value into a date object, or return None."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _parse_float(value, default: float = 0.0) -> float:
    """Parse a value into a float, returning *default* on failure."""
    if value is None:
        return default
    try:
        if isinstance(value, float) and pd.isna(value):
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _parse_int(value, default: int = 0) -> int:
    """Parse a value into an int, returning *default* on failure."""
    if value is None:
        return default
    try:
        if isinstance(value, float) and pd.isna(value):
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _safe_str(value, default: str = "") -> str:
    """Return a stripped string representation, or *default* if empty/NaN."""
    if value is None:
        return default
    try:
        if isinstance(value, float) and pd.isna(value):
            return default
    except TypeError:
        pass
    s = str(value).strip()
    return s if s else default


# ---------------------------------------------------------------------------
# Core service class
# ---------------------------------------------------------------------------

class GoogleSheetsService:
    """
    Reads data from Google Sheets and returns pandas DataFrames.

    Authentication is handled via a Google service account JSON key file,
    using the ``gspread`` library.
    """

    def __init__(self, service_account_key=None):
        """
        Initialize the service.

        Args:
            service_account_key: Path to the service account JSON key file,
                                 or a dict with the key contents.  If *None*,
                                 the value is read from the config file.
        """
        self._service_account_key = service_account_key
        self._gc = None  # lazily initialised gspread client

    # -- connection ---------------------------------------------------------

    def connect(self):
        """
        Authenticate with Google Sheets API and cache the gspread client.

        Raises:
            ImportError: if gspread is not installed.
            FileNotFoundError: if the service account key file is missing.
            Exception: on authentication failure.
        """
        try:
            import gspread
        except ImportError:
            raise ImportError(
                "gspread is required for Google Sheets integration. "
                "Install it with:  pip install gspread"
            )

        key = self._service_account_key
        if key is None:
            # Fall back to config file
            config = load_config()
            key = get_service_account_key(config)

        if isinstance(key, dict):
            self._gc = gspread.service_account_from_dict(key)
            logger.info("Authenticated with Google Sheets (from dict key)")
        else:
            # key is a file path
            if not __import__("os").path.exists(key):
                raise FileNotFoundError(
                    "Service account key file not found: {}".format(key)
                )
            self._gc = gspread.service_account(filename=key)
            logger.info("Authenticated with Google Sheets (from file: %s)", key)

        return self._gc

    def _ensure_connected(self):
        """Lazily connect if not already connected."""
        if self._gc is None:
            self.connect()
        return self._gc

    # -- reading sheets -----------------------------------------------------

    def read_sheet(
        self,
        sheet_url: str,
        worksheet: str = "Sheet1",
    ) -> pd.DataFrame:
        """
        Read all values from a Google Sheets worksheet into a DataFrame.

        The first row of the worksheet is treated as the header row.

        Args:
            sheet_url: Full Google Sheets URL or spreadsheet ID.
            worksheet: Name (or 0-based index as string) of the worksheet tab.

        Returns:
            A pandas DataFrame with column names lowercased and stripped.
        """
        gc = self._ensure_connected()
        sheet_id = extract_sheet_id(sheet_url)

        logger.info("Opening spreadsheet ID: %s", sheet_id)
        spreadsheet = gc.open_by_key(sheet_id)

        # Resolve worksheet
        try:
            ws_index = int(worksheet)
            ws = spreadsheet.get_worksheet(ws_index)
        except (ValueError, TypeError):
            ws = spreadsheet.worksheet(worksheet)

        records = ws.get_all_records()
        if not records:
            logger.warning("Sheet '%s' worksheet '%s' is empty", sheet_id, worksheet)
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df.columns = [c.strip().lower() for c in df.columns]
        logger.info(
            "Read %d rows x %d columns from sheet '%s' / '%s'",
            len(df), len(df.columns), sheet_id, worksheet,
        )
        return df

    # -- convenience: read + remap columns ----------------------------------

    def read_sheet_mapped(
        self,
        sheet_url: str,
        worksheet: str,
        column_mapping: dict,
    ) -> pd.DataFrame:
        """
        Read a sheet and rename columns according to *column_mapping*.

        Args:
            sheet_url: Spreadsheet URL or ID.
            worksheet: Worksheet tab name.
            column_mapping: ``{sheet_column: db_field}`` mapping.
                Only columns present in both the sheet and the mapping are kept;
                they are renamed to the mapping value.

        Returns:
            DataFrame with renamed columns.
        """
        df = self.read_sheet(sheet_url, worksheet)
        if df.empty:
            return df

        rename = {}
        for sheet_col, db_field in column_mapping.items():
            col_lower = sheet_col.strip().lower()
            if col_lower in df.columns:
                rename[col_lower] = db_field.strip().lower()

        df = df.rename(columns=rename)
        return df


# ---------------------------------------------------------------------------
# Import functions -- write DataFrame rows into the CRM database
# ---------------------------------------------------------------------------

def _import_sales_df(db: Session, df: pd.DataFrame) -> dict:
    """
    Import sales / order data from a DataFrame into the CRM database.

    Expected columns (all optional except *order_code*):
        order_code, employee_name, product_name, total_amount (or amount),
        quantity, order_date (or date), unit_price, currency, status,
        customer_telegram_username

    Returns:
        Dict with keys: imported, skipped, errors.
    """
    imported = 0
    skipped = 0
    errors = []

    # Normalise possible alternate column names
    if "amount" in df.columns and "total_amount" not in df.columns:
        df = df.rename(columns={"amount": "total_amount"})
    if "date" in df.columns and "order_date" not in df.columns:
        df = df.rename(columns={"date": "order_date"})

    for _, row in df.iterrows():
        try:
            order_code = _safe_str(row.get("order_code"))
            if not order_code:
                skipped += 1
                continue

            existing = db.query(Order).filter(Order.order_code == order_code).first()

            # Resolve employee
            employee_id = None
            emp_name = _safe_str(row.get("employee_name"))
            if emp_name:
                emp = db.query(Employee).filter(Employee.full_name == emp_name).first()
                if emp:
                    employee_id = emp.id

            # Resolve product
            product_id = None
            prod_name = _safe_str(row.get("product_name"))
            if prod_name:
                prod = db.query(Product).filter(Product.name == prod_name).first()
                if prod:
                    product_id = prod.id

            # Resolve or create customer
            customer_id = None
            cust_username = _safe_str(row.get("customer_telegram_username"))
            if cust_username:
                customer = db.query(Customer).filter(
                    Customer.telegram_username == cust_username
                ).first()
                if not customer:
                    customer = Customer(
                        telegram_username=cust_username,
                        source="google_sheets",
                    )
                    db.add(customer)
                    db.flush()
                customer_id = customer.id

            data = {
                "order_code": order_code,
                "customer_id": customer_id,
                "employee_id": employee_id,
                "product_id": product_id,
                "quantity": _parse_int(row.get("quantity"), 1),
                "unit_price": _parse_float(row.get("unit_price"), 0),
                "total_amount": _parse_float(row.get("total_amount"), 0),
                "currency": _safe_str(row.get("currency"), "USD") or "USD",
                "order_date": _parse_date(row.get("order_date")),
                "status": _safe_str(row.get("status"), "paid") or "paid",
            }

            if existing:
                for key, value in data.items():
                    setattr(existing, key, value)
            else:
                db.add(Order(**data))

            imported += 1

        except Exception as e:
            errors.append(str(e))
            skipped += 1

    db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors[:10]}


def _import_employees_df(db: Session, df: pd.DataFrame) -> dict:
    """
    Import / update employee records from a DataFrame.

    Expected columns (only *full_name* / *employee_name* is required):
        full_name (or employee_name), telegram_username, phone, role,
        commission_rate

    Returns:
        Dict with keys: imported, skipped, errors.
    """
    imported = 0
    skipped = 0
    errors = []

    # Normalise alternate column name
    if "employee_name" in df.columns and "full_name" not in df.columns:
        df = df.rename(columns={"employee_name": "full_name"})

    for _, row in df.iterrows():
        try:
            full_name = _safe_str(row.get("full_name"))
            if not full_name:
                skipped += 1
                continue

            telegram_username = _safe_str(row.get("telegram_username")) or None
            phone = _safe_str(row.get("phone")) or None

            existing = db.query(Employee).filter(Employee.full_name == full_name)
            if telegram_username:
                existing = existing.filter(
                    Employee.telegram_username == telegram_username
                )
            existing = existing.first()

            data = {
                "full_name": full_name,
                "telegram_username": telegram_username,
                "phone": phone,
                "role": _safe_str(row.get("role"), "sale") or "sale",
                "commission_rate": _parse_float(row.get("commission_rate"), 0.10),
                "is_active": True,
            }

            if existing:
                for key, value in data.items():
                    setattr(existing, key, value)
            else:
                db.add(Employee(**data))

            imported += 1

        except Exception as e:
            errors.append(str(e))
            skipped += 1

    db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors[:10]}


def _import_employee_performance_df(db: Session, df: pd.DataFrame) -> dict:
    """
    Import employee performance / leaderboard data from a DataFrame.

    Expected columns:
        employee_name (or full_name), revenue, orders_count, leads_count,
        conversion_rate

    This updates existing Employee records with the performance fields
    (stored in LeaderboardSnapshot for the current month).

    Returns:
        Dict with keys: imported, skipped, errors.
    """
    imported = 0
    skipped = 0
    errors = []

    if "employee_name" in df.columns and "full_name" not in df.columns:
        df = df.rename(columns={"employee_name": "full_name"})

    today = date.today()
    period_start = today.replace(day=1)
    period_end = today

    for _, row in df.iterrows():
        try:
            full_name = _safe_str(row.get("full_name"))
            if not full_name:
                skipped += 1
                continue

            emp = db.query(Employee).filter(Employee.full_name == full_name).first()
            if not emp:
                # Auto-create the employee so we can attach performance data
                emp = Employee(full_name=full_name, role="sale", is_active=True)
                db.add(emp)
                db.flush()

            revenue = _parse_float(row.get("revenue"), 0)
            orders_count = _parse_int(row.get("orders_count"), 0)
            leads_count = _parse_int(row.get("leads_count"), 0)
            conversion_rate = _parse_float(row.get("conversion_rate"), 0)
            score = revenue * 0.5 + orders_count * 20 + leads_count * 2 + conversion_rate

            # Upsert leaderboard snapshot for current month
            snapshot = db.query(LeaderboardSnapshot).filter(
                LeaderboardSnapshot.employee_id == emp.id,
                LeaderboardSnapshot.period_type == "monthly",
                LeaderboardSnapshot.period_start == period_start,
            ).first()

            snap_data = {
                "employee_id": emp.id,
                "period_type": "monthly",
                "period_start": period_start,
                "period_end": period_end,
                "score": round(score, 2),
                "revenue": round(revenue, 2),
                "orders_count": orders_count,
                "leads_count": leads_count,
                "conversion_rate": round(conversion_rate, 2),
            }

            if snapshot:
                for key, value in snap_data.items():
                    setattr(snapshot, key, value)
            else:
                db.add(LeaderboardSnapshot(**snap_data))

            imported += 1

        except Exception as e:
            errors.append(str(e))
            skipped += 1

    db.commit()

    # Re-rank snapshots after import
    _rerank_leaderboard(db, period_start)

    return {"imported": imported, "skipped": skipped, "errors": errors[:10]}


def _rerank_leaderboard(db: Session, period_start: date) -> None:
    """Recompute rank ordering for the current monthly leaderboard."""
    snapshots = (
        db.query(LeaderboardSnapshot)
        .filter(
            LeaderboardSnapshot.period_type == "monthly",
            LeaderboardSnapshot.period_start == period_start,
        )
        .order_by(LeaderboardSnapshot.score.desc())
        .all()
    )
    for idx, snap in enumerate(snapshots, start=1):
        snap.rank = idx
    db.commit()


# ---------------------------------------------------------------------------
# Top-level sync entry point
# ---------------------------------------------------------------------------

def sync_from_sheet(
    config_path: str = None,
    service_account_key=None,
    data_type: str = None,
) -> dict:
    """
    Read all enabled sheets from the config and import their data into the CRM.

    This is the main entry point intended to be called from the scheduler.

    Args:
        config_path: Path to the google_sheets_config.json file.
            Defaults to the standard location in the project root.
        service_account_key: Override the service account key path/dict.
            If *None*, the value from the config file is used.
        data_type: If provided, only sync sheets of this type
            ('sales' or 'employees').  Otherwise sync all enabled sheets.

    Returns:
        A summary dict::

            {
                "timestamp": "...",
                "sheets_synced": 2,
                "results": [
                    {
                        "name": "Sales Data",
                        "data_type": "sales",
                        "rows_read": 150,
                        "imported": 148,
                        "skipped": 2,
                        "errors": []
                    },
                    ...
                ],
                "total_imported": 148,
                "total_skipped": 2,
                "ok": True
            }
    """
    results = []
    total_imported = 0
    total_skipped = 0
    ok = True

    try:
        config = load_config(config_path)
    except Exception as exc:
        logger.error("Failed to load Google Sheets config: %s", exc)
        return {
            "timestamp": str(datetime.now()),
            "sheets_synced": 0,
            "results": [],
            "total_imported": 0,
            "total_skipped": 0,
            "ok": False,
            "error": str(exc),
        }

    sheets = get_enabled_sheets(config, data_type=data_type)
    if not sheets:
        logger.info("No enabled sheets to sync (data_type filter: %s)", data_type)
        return {
            "timestamp": str(datetime.now()),
            "sheets_synced": 0,
            "results": [],
            "total_imported": 0,
            "total_skipped": 0,
            "ok": True,
        }

    # Initialise the service
    svc = GoogleSheetsService(service_account_key=service_account_key)

    for sheet_cfg in sheets:
        sheet_name = sheet_cfg.get("name", "unnamed")
        sheet_url = sheet_cfg.get("sheet_url", "")
        sheet_type = sheet_cfg.get("data_type", "sales")
        worksheet = sheet_cfg.get("worksheet", "Sheet1")
        column_mapping = sheet_cfg.get("column_mapping", {})

        logger.info("Syncing sheet '%s' (type=%s) ...", sheet_name, sheet_type)

        sheet_result = {
            "name": sheet_name,
            "data_type": sheet_type,
            "rows_read": 0,
            "imported": 0,
            "skipped": 0,
            "errors": [],
        }

        try:
            df = svc.read_sheet_mapped(sheet_url, worksheet, column_mapping)
            sheet_result["rows_read"] = len(df)

            if df.empty:
                logger.info("Sheet '%s' returned no data, skipping.", sheet_name)
                results.append(sheet_result)
                continue

            # Route to the correct importer based on data_type
            db = SessionLocal()
            try:
                if sheet_type == "sales":
                    import_result = _import_sales_df(db, df)
                elif sheet_type == "employees":
                    # Decide between employee records vs. performance data.
                    # If the sheet has performance columns (revenue, orders_count)
                    # treat it as performance data; otherwise as employee roster.
                    perf_cols = {"revenue", "orders_count", "leads_count"}
                    if perf_cols.issubset(set(df.columns)):
                        import_result = _import_employee_performance_df(db, df)
                    else:
                        import_result = _import_employees_df(db, df)
                else:
                    msg = "Unknown data_type '{}' for sheet '{}'".format(
                        sheet_type, sheet_name
                    )
                    logger.warning(msg)
                    sheet_result["errors"].append(msg)
                    results.append(sheet_result)
                    continue

                sheet_result["imported"] = import_result["imported"]
                sheet_result["skipped"] = import_result["skipped"]
                sheet_result["errors"] = import_result.get("errors", [])

            finally:
                db.close()

        except Exception as exc:
            logger.error("Error syncing sheet '%s': %s", sheet_name, exc)
            sheet_result["errors"].append(str(exc))
            ok = False

        total_imported += sheet_result["imported"]
        total_skipped += sheet_result["skipped"]
        results.append(sheet_result)

    summary = {
        "timestamp": str(datetime.now()),
        "sheets_synced": len(results),
        "results": results,
        "total_imported": total_imported,
        "total_skipped": total_skipped,
        "ok": ok,
    }

    logger.info(
        "Google Sheets sync complete: %d sheets, %d imported, %d skipped, ok=%s",
        summary["sheets_synced"],
        summary["total_imported"],
        summary["total_skipped"],
        summary["ok"],
    )
    return summary


# ---------------------------------------------------------------------------
# Convenience: read a single sheet without the full config machinery
# ---------------------------------------------------------------------------

def read_single_sheet(
    sheet_url: str,
    service_account_key=None,
    worksheet: str = "Sheet1",
) -> pd.DataFrame:
    """
    Read a single Google Sheet and return the raw DataFrame.

    Useful for ad-hoc inspection or one-off imports.
    """
    svc = GoogleSheetsService(service_account_key=service_account_key)
    return svc.read_sheet(sheet_url, worksheet)
