"""
Google Docs Scanner Service
============================
Đọc và phân tích nội dung Google Docs (báo cáo vận hành) bằng Google Docs API.
Sử dụng cùng service account với Google Sheets.

Cách dùng:
    from app.services.google_docs_service import GoogleDocsScanner
    scanner = GoogleDocsScanner()
    doc = scanner.read_document("DOC_ID_OR_URL")
    report = scanner.parse_operations_report(doc)
"""
import json
import os
import re
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(_PROJECT_ROOT, "google_docs_reports")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_doc_id(url_or_id: str) -> str:
    """
    Extract Google Docs document ID from URL or return as-is.

    Handles:
      - https://docs.google.com/document/d/DOC_ID/edit
      - https://docs.google.com/document/d/DOC_ID/edit?usp=sharing
      - Plain document ID string
    """
    url = url_or_id.strip()
    if "/document/d/" in url:
        parts = url.split("/document/d/")[1]
        doc_id = parts.split("/")[0].split("?")[0]
        return doc_id
    return url


def _read_element_text(element: dict) -> str:
    """Read text content from a StructuralElement of the Google Docs API."""
    text = ""
    if "paragraph" in element:
        for elem in element["paragraph"].get("elements", []):
            text_run = elem.get("textRun")
            if text_run:
                text += text_run.get("content", "")
    elif "table" in element:
        for row in element["table"].get("tableRows", []):
            for cell in row.get("tableCells", []):
                for nested in cell.get("content", []):
                    text += _read_element_text(nested)
                text += "\t"
            text += "\n"
    return text


def _get_paragraph_style(element: dict) -> str:
    """Return the paragraph style name (e.g. 'Heading 1', 'Normal Text')."""
    if "paragraph" in element:
        style = element["paragraph"].get("paragraphStyle", {})
        named = style.get("namedStyleType", "")
        return named
    return ""


# ---------------------------------------------------------------------------
# Core scanner class
# ---------------------------------------------------------------------------

class GoogleDocsScanner:
    """
    Reads Google Docs documents and parses structured reports.
    Uses the same service account JSON key as Google Sheets.
    """

    def __init__(self, service_account_key=None):
        self._service_account_key = service_account_key
        self._service = None

    def _get_credentials(self):
        """Build Google credentials from service account key."""
        from google.oauth2 import service_account
        import os

        key = self._service_account_key
        if key is None:
            # Check environment variable first (for Railway)
            env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
            if env_json:
                try:
                    key = json.loads(env_json)
                except json.JSONDecodeError:
                    pass

            if key is None:
                # Load from config file
                config_path = os.path.join(_PROJECT_ROOT, "google_sheets_config.json")
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = json.load(f)
                    key_path = config.get("service_account_key", "")
                    if key_path and not os.path.isabs(key_path):
                        key_path = os.path.join(_PROJECT_ROOT, key_path)
                    key = key_path

        if isinstance(key, dict):
            creds = service_account.Credentials.from_service_account_info(
                key,
                scopes=["https://www.googleapis.com/auth/documents.readonly"],
            )
        elif isinstance(key, str) and os.path.exists(key):
            creds = service_account.Credentials.from_service_account_file(
                key,
                scopes=["https://www.googleapis.com/auth/documents.readonly"],
            )
        else:
            raise FileNotFoundError(
                "Service account key not found. Check google_sheets_config.json"
            )
        return creds

    def connect(self):
        """Initialize the Google Docs API client."""
        from googleapiclient.discovery import build

        creds = self._get_credentials()
        self._service = build("docs", "v1", credentials=creds)
        logger.info("Google Docs API client initialized")
        return self._service

    def _ensure_connected(self):
        if self._service is None:
            self.connect()
        return self._service

    # ------------------------------------------------------------------
    # Reading documents
    # ------------------------------------------------------------------

    def read_document(self, doc_url_or_id: str) -> dict:
        """
        Read a Google Docs document and return structured content.

        Returns:
            {
                "doc_id": "...",
                "title": "Document Title",
                "full_text": "all text content...",
                "sections": [
                    {"heading": "Section Name", "level": 1, "content": "..."},
                    ...
                ],
                "tables": [
                    {"rows": [["col1", "col2"], ["val1", "val2"]], ...}
                ],
                "raw_elements": [...]  # raw API elements
            }
        """
        service = self._ensure_connected()
        doc_id = extract_doc_id(doc_url_or_id)

        logger.info("Reading Google Doc: %s", doc_id)
        doc = service.documents().get(documentId=doc_id).execute()

        title = doc.get("title", "")
        body = doc.get("body", {})
        content = body.get("content", [])

        full_text = ""
        sections = []
        tables = []
        current_section = {"heading": "", "level": 0, "content": ""}

        for element in content:
            # Read text
            text = _read_element_text(element)
            full_text += text

            # Detect headings
            style = _get_paragraph_style(element)
            if style.startswith("HEADING"):
                # Save previous section
                if current_section["heading"] or current_section["content"].strip():
                    sections.append(current_section)

                level = 1
                try:
                    level = int(style.replace("HEADING_", ""))
                except ValueError:
                    level = 1

                heading_text = text.strip().rstrip("\n")
                current_section = {
                    "heading": heading_text,
                    "level": level,
                    "content": "",
                }
            else:
                current_section["content"] += text

            # Detect tables
            if "table" in element:
                table_data = self._parse_table_element(element)
                if table_data:
                    tables.append(table_data)

        # Save last section
        if current_section["heading"] or current_section["content"].strip():
            sections.append(current_section)

        return {
            "doc_id": doc_id,
            "title": title,
            "full_text": full_text,
            "sections": sections,
            "tables": tables,
            "read_at": datetime.now().isoformat(),
        }

    def _parse_table_element(self, element: dict) -> Optional[dict]:
        """Parse a table element into rows/columns."""
        if "table" not in element:
            return None

        rows = []
        for row in element["table"].get("tableRows", []):
            cells = []
            for cell in row.get("tableCells", []):
                cell_text = ""
                for content in cell.get("content", []):
                    cell_text += _read_element_text(content)
                cells.append(cell_text.strip())
            rows.append(cells)

        return {"rows": rows} if rows else None

    # ------------------------------------------------------------------
    # Parsing specific report formats
    # ------------------------------------------------------------------

    def parse_operations_report(self, doc_data: dict) -> dict:
        """
        Parse a "Báo cáo vận hành" (Operations Report) document.

        Extracts:
        - Channel performance metrics (members, growth)
        - Customer/sales data
        - Revenue data
        - Employee performance
        - Key metrics mentioned in the text

        Returns structured data dict.
        """
        full_text = doc_data.get("full_text", "")
        sections = doc_data.get("sections", [])
        tables = doc_data.get("tables", [])

        report = {
            "title": doc_data.get("title", ""),
            "doc_id": doc_data.get("doc_id", ""),
            "parsed_at": datetime.now().isoformat(),
            "channels": [],
            "metrics": {},
            "tables_found": len(tables),
            "sections_found": len(sections),
            "raw_sections": [],
        }

        # Parse each section for relevant data
        for section in sections:
            heading = section["heading"].lower()
            content = section["content"]

            section_data = {
                "heading": section["heading"],
                "level": section["level"],
            }

            # Channel performance section
            if any(kw in heading for kw in ["channel", "kênh", "telegram"]):
                channels = self._parse_channel_metrics(content)
                report["channels"].extend(channels)
                section_data["type"] = "channels"
                section_data["channels"] = channels

            # Revenue/sales section
            elif any(kw in heading for kw in ["doanh thu", "revenue", "sales", "bán hàng"]):
                metrics = self._parse_revenue_metrics(content)
                report["metrics"].update(metrics)
                section_data["type"] = "revenue"
                section_data["metrics"] = metrics

            # Customer section
            elif any(kw in heading for kw in ["khách hàng", "customer", "member", "thành viên"]):
                metrics = self._parse_customer_metrics(content)
                report["metrics"].update(metrics)
                section_data["type"] = "customers"
                section_data["metrics"] = metrics

            # Employee section
            elif any(kw in heading for kw in ["nhân viên", "employee", "staff", "đội ngũ"]):
                metrics = self._parse_employee_metrics(content)
                report["metrics"].update(metrics)
                section_data["type"] = "employees"
                section_data["metrics"] = metrics

            else:
                section_data["type"] = "other"

            report["raw_sections"].append(section_data)

        # Parse tables for structured data
        for i, table in enumerate(tables):
            parsed_table = self._parse_report_table(table)
            if parsed_table:
                if parsed_table.get("type") == "channel":
                    report["channels"].extend(parsed_table.get("data", []))
                elif parsed_table.get("type") == "metrics":
                    report["metrics"].update(parsed_table.get("data", {}))

        # Global metric extraction from full text
        global_metrics = self._extract_global_metrics(full_text)
        report["metrics"].update(global_metrics)

        return report

    def _parse_channel_metrics(self, text: str) -> list:
        """Extract channel performance data from text."""
        channels = []

        # Pattern: channel name with @username and member count
        # e.g., "@apexgoldtrading - 1,234 members" or "Apex Gold: 1234 thành viên"
        channel_patterns = [
            r'@(\w+)[\s\-:]+(\d[\d,]*)\s*(?:members?|thành viên|subs?)',
            r'(\w[\w\s]+?)[\s\-:]+(\d[\d,]*)\s*(?:members?|thành viên|subs?)',
        ]

        for pattern in channel_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                name = match.group(1).strip()
                members_str = match.group(2).replace(",", "")
                try:
                    members = int(members_str)
                    channels.append({
                        "name": name,
                        "members": members,
                    })
                except ValueError:
                    pass

        return channels

    def _parse_revenue_metrics(self, text: str) -> dict:
        """Extract revenue/sales metrics from text."""
        metrics = {}

        # Pattern: "Tổng doanh thu: $12,345" or "Revenue: 12345 USD"
        revenue_patterns = [
            (r'(?:tổng\s+)?doanh\s+thu[:\s]+\$?([\d,]+(?:\.\d+)?)', 'total_revenue'),
            (r'revenue[:\s]+\$?([\d,]+(?:\.\d+)?)', 'total_revenue'),
            (r'(?:tổng\s+)?đơn\s+hàng[:\s]+(\d+)', 'total_orders'),
            (r'(?:total\s+)?orders?[:\s]+(\d+)', 'total_orders'),
        ]

        for pattern, key in revenue_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value_str = match.group(1).replace(",", "")
                try:
                    metrics[key] = float(value_str)
                except ValueError:
                    pass

        return metrics

    def _parse_customer_metrics(self, text: str) -> dict:
        """Extract customer/member metrics from text."""
        metrics = {}

        patterns = [
            (r'(?:tổng\s+)?(?:khách\s+hàng|customers?)[:\s]+(\d[\d,]*)', 'total_customers'),
            (r'(?:thành\s+viên\s+mới|new\s+members?)[:\s]+(\d[\d,]*)', 'new_members'),
            (r'(?:leads?|tiềm\s+năng)[:\s]+(\d[\d,]*)', 'total_leads'),
        ]

        for pattern, key in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value_str = match.group(1).replace(",", "")
                try:
                    metrics[key] = int(value_str)
                except ValueError:
                    pass

        return metrics

    def _parse_employee_metrics(self, text: str) -> dict:
        """Extract employee performance metrics from text."""
        metrics = {}

        patterns = [
            (r'(?:tổng\s+)?nhân\s+viên[:\s]+(\d+)', 'total_employees'),
            (r'(?:total\s+)?employees?[:\s]+(\d+)', 'total_employees'),
        ]

        for pattern, key in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    metrics[key] = int(match.group(1))
                except ValueError:
                    pass

        return metrics

    def _parse_report_table(self, table: dict) -> Optional[dict]:
        """
        Parse a table from the report and determine its type.
        Returns {"type": "channel"|"metrics"|"other", "data": ...}
        """
        rows = table.get("rows", [])
        if not rows or len(rows) < 2:
            return None

        header = [cell.lower().strip() for cell in rows[0]]

        # Check if it's a channel performance table
        channel_keywords = {"channel", "kênh", "members", "thành viên", "subs", "joins"}
        if any(kw in " ".join(header) for kw in channel_keywords):
            data = []
            for row in rows[1:]:
                if len(row) >= 2:
                    entry = {"name": row[0].strip()}
                    for i, col in enumerate(header[1:], 1):
                        if i < len(row):
                            try:
                                val = row[i].replace(",", "").strip()
                                entry[col] = float(val) if "." in val else int(val)
                            except (ValueError, IndexError):
                                entry[col] = row[i].strip() if i < len(row) else ""
                    data.append(entry)
            return {"type": "channel", "data": data}

        # Check if it's a metrics/summary table
        metrics_keywords = {"metric", "chỉ số", "value", "giá trị", "total", "tổng"}
        if any(kw in " ".join(header) for kw in metrics_keywords):
            data = {}
            for row in rows[1:]:
                if len(row) >= 2:
                    key = row[0].strip().lower().replace(" ", "_")
                    try:
                        val = row[1].replace(",", "").strip()
                        data[key] = float(val) if "." in val else int(val)
                    except ValueError:
                        data[key] = row[1].strip()
            return {"type": "metrics", "data": data}

        return {"type": "other", "data": rows}

    def _extract_global_metrics(self, text: str) -> dict:
        """Extract key metrics mentioned anywhere in the document."""
        metrics = {}

        # Total members across all channels
        total_match = re.search(
            r'(?:tổng\s+(?:số\s+)?thành\s+viên|total\s+members?)[:\s]+(\d[\d,]*)',
            text, re.IGNORECASE
        )
        if total_match:
            try:
                metrics["total_members_all_channels"] = int(
                    total_match.group(1).replace(",", "")
                )
            except ValueError:
                pass

        # Conversion rate
        conv_match = re.search(
            r'(?:tỷ\s+lệ\s+chuyển\s+đổi|conversion\s+rate)[:\s]+([\d.]+)\s*%',
            text, re.IGNORECASE
        )
        if conv_match:
            try:
                metrics["conversion_rate"] = float(conv_match.group(1))
            except ValueError:
                pass

        return metrics


# ---------------------------------------------------------------------------
# Persistence: save/load parsed reports
# ---------------------------------------------------------------------------

def save_report(report: dict, filename: str = None) -> str:
    """Save a parsed report to JSON file."""
    os.makedirs(REPORTS_DIR, exist_ok=True)

    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        title = report.get("title", "report").replace(" ", "_")[:50]
        filename = f"{title}_{timestamp}.json"

    filepath = os.path.join(REPORTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    logger.info("Report saved: %s", filepath)
    return filepath


def load_reports() -> list:
    """Load all saved reports."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    reports = []

    for filename in sorted(os.listdir(REPORTS_DIR), reverse=True):
        if filename.endswith(".json"):
            filepath = os.path.join(REPORTS_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    reports.append(json.load(f))
            except Exception as e:
                logger.warning("Failed to load report %s: %s", filename, e)

    return reports


def get_latest_report() -> Optional[dict]:
    """Get the most recent parsed report."""
    reports = load_reports()
    return reports[0] if reports else None


# ---------------------------------------------------------------------------
# Top-level sync function (for scheduler)
# ---------------------------------------------------------------------------

def sync_google_docs(config_path: str = None) -> dict:
    """
    Read all enabled Google Docs from config and parse them.
    Entry point for the scheduler.

    Returns:
        Summary dict with docs_scanned, results, etc.
    """
    results = []
    ok = True

    try:
        cfg_path = config_path or os.path.join(_PROJECT_ROOT, "google_sheets_config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        return {"ok": False, "error": str(e), "docs_scanned": 0}

    docs_config = config.get("google_docs", [])
    enabled_docs = [d for d in docs_config if d.get("enabled", True) and d.get("doc_url", "").strip()]

    if not enabled_docs:
        return {"ok": True, "docs_scanned": 0, "results": [], "message": "No Google Docs configured"}

    scanner = GoogleDocsScanner()

    for doc_cfg in enabled_docs:
        doc_name = doc_cfg.get("name", "unnamed")
        doc_url = doc_cfg.get("doc_url", "")

        doc_result = {
            "name": doc_name,
            "doc_url": doc_url,
            "ok": False,
            "error": None,
        }

        try:
            doc_data = scanner.read_document(doc_url)
            report = scanner.parse_operations_report(doc_data)
            filepath = save_report(report)

            doc_result["ok"] = True
            doc_result["title"] = report.get("title", "")
            doc_result["channels_found"] = len(report.get("channels", []))
            doc_result["metrics"] = report.get("metrics", {})
            doc_result["saved_to"] = filepath

        except Exception as e:
            logger.error("Error scanning doc '%s': %s", doc_name, e)
            doc_result["error"] = str(e)
            ok = False

        results.append(doc_result)

    return {
        "ok": ok,
        "docs_scanned": len(results),
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }
