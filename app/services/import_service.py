import pandas as pd
from sqlalchemy.orm import Session
from fastapi import UploadFile
from io import BytesIO
from datetime import datetime, date
from app.models import Campaign, Channel, AdStat, Employee, Order, Product, Customer


async def read_file_to_dataframe(file: UploadFile) -> pd.DataFrame:
    content = await file.read()
    filename = file.filename.lower()
    if filename.endswith(".csv"):
        return pd.read_csv(BytesIO(content))
    elif filename.endswith((".xlsx", ".xls")):
        return pd.read_excel(BytesIO(content))
    else:
        raise ValueError("Chỉ hỗ trợ file CSV hoặc Excel")


def parse_date(value):
    if pd.isna(value):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    return pd.to_datetime(value).date()


def parse_float(value):
    if pd.isna(value):
        return 0.0
    return float(value)


def parse_int(value):
    if pd.isna(value):
        return 0
    return int(value)


async def import_ad_stats(db: Session, file: UploadFile):
    """
    Import số liệu quảng cáo từ file sheet.
    Cột bắt buộc: campaign_id hoặc campaign_name, stat_date.
    Cột tùy chọn: impressions, clicks, ctr, cpm, spend, joins, conversions, revenue.
    """
    df = await read_file_to_dataframe(file)
    df.columns = [c.strip().lower() for c in df.columns]
    imported = 0
    skipped = 0
    errors = []

    for _, row in df.iterrows():
        try:
            campaign_id = None
            if "campaign_id" in df.columns:
                campaign_id = parse_int(row["campaign_id"]) or None
            if not campaign_id and "campaign_name" in df.columns:
                campaign = db.query(Campaign).filter(Campaign.name == str(row["campaign_name"])).first()
                if campaign:
                    campaign_id = campaign.id

            if not campaign_id:
                skipped += 1
                continue

            stat_date = parse_date(row.get("stat_date", datetime.now()))
            if not stat_date:
                skipped += 1
                continue

            existing = db.query(AdStat).filter(
                AdStat.campaign_id == campaign_id,
                AdStat.stat_date == stat_date,
            ).first()

            data = {
                "campaign_id": campaign_id,
                "stat_date": stat_date,
                "impressions": parse_int(row.get("impressions", 0)),
                "clicks": parse_int(row.get("clicks", 0)),
                "ctr": parse_float(row.get("ctr", 0)),
                "cpm": parse_float(row.get("cpm", 0)),
                "spend": parse_float(row.get("spend", 0)),
                "joins": parse_int(row.get("joins", 0)),
                "conversions": parse_int(row.get("conversions", 0)),
                "revenue": parse_float(row.get("revenue", 0)),
            }

            if existing:
                for key, value in data.items():
                    setattr(existing, key, value)
            else:
                db.add(AdStat(**data))

            imported += 1
        except Exception as e:
            errors.append(str(e))
            skipped += 1

    db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors[:10]}


async def import_sales(db: Session, file: UploadFile):
    """
    Import doanh thu bán nhóm tín hiệu từ file sheet.
    Cột: order_code, customer_telegram_username, employee_name, product_name,
         quantity, unit_price, total_amount, currency, order_date, status.
    """
    df = await read_file_to_dataframe(file)
    df.columns = [c.strip().lower() for c in df.columns]
    imported = 0
    skipped = 0
    errors = []

    for _, row in df.iterrows():
        try:
            order_code = str(row.get("order_code", "")).strip()
            if not order_code:
                skipped += 1
                continue

            existing = db.query(Order).filter(Order.order_code == order_code).first()

            # Resolve employee by name
            employee_id = None
            if "employee_name" in df.columns and not pd.isna(row.get("employee_name")):
                emp = db.query(Employee).filter(Employee.full_name == str(row["employee_name"]).strip()).first()
                if emp:
                    employee_id = emp.id

            # Resolve product by name
            product_id = None
            if "product_name" in df.columns and not pd.isna(row.get("product_name")):
                prod = db.query(Product).filter(Product.name == str(row["product_name"]).strip()).first()
                if prod:
                    product_id = prod.id

            # Resolve or create customer
            customer_id = None
            if "customer_telegram_username" in df.columns and not pd.isna(row.get("customer_telegram_username")):
                username = str(row["customer_telegram_username"]).strip()
                customer = db.query(Customer).filter(Customer.telegram_username == username).first()
                if not customer:
                    customer = Customer(telegram_username=username, source="sheet_import")
                    db.add(customer)
                    db.flush()
                customer_id = customer.id

            data = {
                "order_code": order_code,
                "customer_id": customer_id,
                "employee_id": employee_id,
                "product_id": product_id,
                "quantity": parse_int(row.get("quantity", 1)),
                "unit_price": parse_float(row.get("unit_price", 0)),
                "total_amount": parse_float(row.get("total_amount", 0)),
                "currency": str(row.get("currency", "USD")).strip() or "USD",
                "order_date": parse_date(row.get("order_date")),
                "status": str(row.get("status", "paid")).strip() or "paid",
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


async def import_employees(db: Session, file: UploadFile):
    """
    Import danh sách nhân viên từ file sheet.
    Cột: full_name, telegram_username, phone, role, commission_rate.
    """
    df = await read_file_to_dataframe(file)
    df.columns = [c.strip().lower() for c in df.columns]
    imported = 0
    skipped = 0
    errors = []

    for _, row in df.iterrows():
        try:
            full_name = str(row.get("full_name", "")).strip()
            if not full_name:
                skipped += 1
                continue

            telegram_username = str(row.get("telegram_username", "")).strip() or None
            phone = str(row.get("phone", "")).strip() or None

            existing = db.query(Employee).filter(Employee.full_name == full_name)
            if telegram_username:
                existing = existing.filter(Employee.telegram_username == telegram_username)
            existing = existing.first()

            data = {
                "full_name": full_name,
                "telegram_username": telegram_username,
                "phone": phone,
                "role": str(row.get("role", "sale")).strip() or "sale",
                "commission_rate": parse_float(row.get("commission_rate", 0.10)),
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
