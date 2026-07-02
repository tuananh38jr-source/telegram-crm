import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db, init_db
from app.models import Campaign, Channel, AdStat, Employee, Order, Product, Customer
from app.schemas import (
    CampaignCreate,
    ChannelCreate,
    EmployeeCreate,
    ProductCreate,
    OrderCreate,
)
from app.services.telegram_service import sync_channel_members, sync_all_channels
from app.services.import_service import import_ad_stats, import_sales, import_employees
from app.services.analytics_service import (
    get_dashboard_summary,
    get_campaigns_performance,
    get_revenue_trend,
    get_employee_leaderboard,
    recalculate_leaderboard,
)
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Telegram Ads CRM", lifespan=lifespan)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "app", "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "app", "templates"))


# ============================================================
# Helpers
# ============================================================
def get_or_404(db, model, obj_id: int):
    obj = db.query(model).filter(model.id == obj_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail=f"{model.__name__} không tồn tại")
    return obj


# ============================================================
# Dashboard
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    summary = get_dashboard_summary(db)
    campaigns = get_campaigns_performance(db)[:5]
    revenue_trend = get_revenue_trend(db, days=30)
    leaderboard = get_employee_leaderboard(db, period_type="monthly")[:5]
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "campaigns": campaigns,
        "revenue_trend": revenue_trend,
        "leaderboard": leaderboard,
    })


# ============================================================
# Campaigns
# ============================================================
@app.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(request: Request, db: Session = Depends(get_db)):
    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    channels = db.query(Channel).filter(Channel.is_active == True).all()
    return templates.TemplateResponse("campaigns.html", {
        "request": request,
        "campaigns": campaigns,
        "channels": channels,
    })


@app.post("/campaigns")
async def create_campaign(
    name: str = Form(...),
    channel_id: int = Form(None),
    budget: float = Form(0),
    currency: str = Form("TON"),
    status: str = Form("active"),
    db: Session = Depends(get_db),
):
    campaign = Campaign(
        name=name,
        channel_id=channel_id,
        budget=budget,
        currency=currency,
        status=status,
    )
    db.add(campaign)
    db.commit()
    return RedirectResponse(url="/campaigns", status_code=303)


@app.post("/campaigns/{campaign_id}/delete")
async def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = get_or_404(db, Campaign, campaign_id)
    db.delete(campaign)
    db.commit()
    return RedirectResponse(url="/campaigns", status_code=303)


# ============================================================
# Channels
# ============================================================
@app.get("/channels", response_class=HTMLResponse)
async def channels_page(request: Request, db: Session = Depends(get_db)):
    channels = db.query(Channel).order_by(Channel.created_at.desc()).all()
    return templates.TemplateResponse("channels.html", {
        "request": request,
        "channels": channels,
    })


@app.post("/channels")
async def create_channel(
    name: str = Form(...),
    username: str = Form(None),
    telegram_id: int = Form(None),
    invite_link: str = Form(None),
    db: Session = Depends(get_db),
):
    channel = Channel(
        name=name,
        username=username,
        telegram_id=telegram_id,
        invite_link=invite_link,
    )
    db.add(channel)
    db.commit()
    return RedirectResponse(url="/channels", status_code=303)


@app.post("/channels/{channel_id}/sync")
async def sync_channel(channel_id: int, db: Session = Depends(get_db)):
    result = await sync_channel_members(db, channel_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return RedirectResponse(url="/channels", status_code=303)


@app.post("/channels/sync-all")
async def sync_all_channels_route(db: Session = Depends(get_db)):
    await sync_all_channels(db)
    return RedirectResponse(url="/channels", status_code=303)


# ============================================================
# Sales / Orders
# ============================================================
@app.get("/sales", response_class=HTMLResponse)
async def sales_page(request: Request, db: Session = Depends(get_db)):
    orders = (
        db.query(Order)
        .order_by(Order.created_at.desc())
        .limit(200)
        .all()
    )
    products = db.query(Product).filter(Product.is_active == True).all()
    employees = db.query(Employee).filter(Employee.is_active == True).all()
    return templates.TemplateResponse("sales.html", {
        "request": request,
        "orders": orders,
        "products": products,
        "employees": employees,
    })


@app.post("/sales")
async def create_order(
    order_code: str = Form(...),
    employee_id: int = Form(None),
    product_id: int = Form(None),
    quantity: int = Form(1),
    unit_price: float = Form(0),
    total_amount: float = Form(0),
    currency: str = Form("USD"),
    order_date: str = Form(None),
    db: Session = Depends(get_db),
):
    from datetime import datetime as dt
    order = Order(
        order_code=order_code,
        employee_id=employee_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=unit_price,
        total_amount=total_amount or (quantity * unit_price),
        currency=currency,
        order_date=dt.strptime(order_date, "%Y-%m-%d").date() if order_date else None,
    )
    db.add(order)
    db.commit()
    return RedirectResponse(url="/sales", status_code=303)


# ============================================================
# Employees
# ============================================================
@app.get("/employees", response_class=HTMLResponse)
async def employees_page(request: Request, db: Session = Depends(get_db)):
    employees = db.query(Employee).order_by(Employee.created_at.desc()).all()
    return templates.TemplateResponse("employees.html", {
        "request": request,
        "employees": employees,
    })


@app.post("/employees")
async def create_employee(
    full_name: str = Form(...),
    telegram_username: str = Form(None),
    phone: str = Form(None),
    role: str = Form("sale"),
    commission_rate: float = Form(0.10),
    db: Session = Depends(get_db),
):
    employee = Employee(
        full_name=full_name,
        telegram_username=telegram_username,
        phone=phone,
        role=role,
        commission_rate=commission_rate,
    )
    db.add(employee)
    db.commit()
    return RedirectResponse(url="/employees", status_code=303)


# ============================================================
# Leaderboard
# ============================================================
@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request, db: Session = Depends(get_db)):
    leaderboard = get_employee_leaderboard(db, period_type="monthly")
    return templates.TemplateResponse("leaderboard.html", {
        "request": request,
        "leaderboard": leaderboard,
    })


@app.post("/leaderboard/recalculate")
async def recalculate_leaderboard_route(db: Session = Depends(get_db)):
    recalculate_leaderboard(db, period_type="monthly")
    return RedirectResponse(url="/leaderboard", status_code=303)


# ============================================================
# Import from Sheets
# ============================================================
@app.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request})


@app.post("/import/ads")
async def import_ads_route(file: UploadFile = File(...), db: Session = Depends(get_db)):
    result = await import_ad_stats(db, file)
    return JSONResponse(content=result)


@app.post("/import/sales")
async def import_sales_route(file: UploadFile = File(...), db: Session = Depends(get_db)):
    result = await import_sales(db, file)
    return JSONResponse(content=result)


@app.post("/import/employees")
async def import_employees_route(file: UploadFile = File(...), db: Session = Depends(get_db)):
    result = await import_employees(db, file)
    return JSONResponse(content=result)


# ============================================================
# API Endpoints
# ============================================================
@app.get("/api/summary")
async def api_summary(db: Session = Depends(get_db)):
    return get_dashboard_summary(db)


@app.get("/api/campaigns")
async def api_campaigns(db: Session = Depends(get_db)):
    return get_campaigns_performance(db)


@app.get("/api/leaderboard")
async def api_leaderboard(period: str = "monthly", db: Session = Depends(get_db)):
    return get_employee_leaderboard(db, period_type=period)


@app.get("/api/revenue-trend")
async def api_revenue_trend(days: int = 30, db: Session = Depends(get_db)):
    return get_revenue_trend(db, days)


# ============================================================
# Telegram Ads Accounts Management
# ============================================================
@app.get("/telegram-ads", response_class=HTMLResponse)
async def telegram_ads_page(request: Request, db: Session = Depends(get_db)):
    from app.models import TelegramAdsAccount
    from app.services.folder_watcher import get_account_folders
    
    accounts = db.query(TelegramAdsAccount).order_by(TelegramAdsAccount.created_at.desc()).all()
    folders = get_account_folders()
    
    return templates.TemplateResponse("telegram_ads.html", {
        "request": request,
        "accounts": accounts,
        "folders": folders,
    })


@app.post("/telegram-ads/accounts")
async def create_ads_account(
    name: str = Form(...),
    account_id: str = Form(None),
    email: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.models import TelegramAdsAccount
    from app.services.folder_watcher import create_account_folder
    
    # Tạo account trong database
    account = TelegramAdsAccount(
        name=name,
        account_id=account_id,
        email=email,
        is_active=True,
    )
    db.add(account)
    db.flush()
    
    # Tạo folder
    folder_path = create_account_folder(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_accounts'),
        name
    )
    account.folder_path = folder_path
    db.commit()
    
    return RedirectResponse(url="/telegram-ads", status_code=303)


@app.post("/telegram-ads/upload/{account_name}")
async def upload_ads_csv(
    account_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    from app.services.folder_watcher import FolderWatcher
    
    # Lưu file vào account folder
    base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_accounts')
    account_dir = os.path.join(base_dir, account_name)
    os.makedirs(account_dir, exist_ok=True)
    
    file_path = os.path.join(account_dir, file.filename)
    with open(file_path, 'wb') as f:
        content = await file.read()
        f.write(content)
    
    # Import ngay
    watcher = FolderWatcher(base_dir)
    result = watcher.process_csv_file(db, file_path, account_name)
    
    return JSONResponse(content=result)


@app.post("/telegram-ads/upload-dual/{account_name}")
async def upload_ads_dual_files(
    account_name: str,
    views_file: UploadFile = File(...),
    budget_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload 2 files CSV cùng lúc (Views + Budget) từ Telegram Ads.
    Format thực tế: tab-separated, European decimal, date "DD Mon YYYY".
    """
    from app.services.telegram_ads_parser import TelegramAdsCSVParser
    
    # Đọc nội dung 2 files
    views_content = await views_file.read()
    budget_content = await budget_file.read()
    
    # Parse dual files
    parser = TelegramAdsCSVParser()
    result = parser.parse_telegram_ads_dual_files(views_content, budget_content)
    
    if not result['success']:
        return JSONResponse(content=result)
    
    # Lưu files vào account folder
    base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_accounts')
    account_dir = os.path.join(base_dir, account_name)
    os.makedirs(account_dir, exist_ok=True)
    
    with open(os.path.join(account_dir, views_file.filename), 'wb') as f:
        f.write(views_content)
    with open(os.path.join(account_dir, budget_file.filename), 'wb') as f:
        f.write(budget_content)
    
    # Import data vào database
    imported_count = 0
    for item in result['data']:
        stat_date = item.get('stat_date')
        
        # Tìm hoặc tạo campaign cho account này
        campaign = db.query(Campaign).filter(
            Campaign.name == f"Telegram Ads - {account_name}"
        ).first()
        
        if not campaign:
            campaign = Campaign(
                name=f"Telegram Ads - {account_name}",
                status='active',
            )
            db.add(campaign)
            db.flush()
        
        # Check existing stat for this date
        existing_stat = db.query(AdStat).filter(
            AdStat.campaign_id == campaign.id,
            AdStat.stat_date == stat_date,
        ).first()
        
        if existing_stat:
            # Update existing
            existing_stat.impressions = item.get('impressions', 0)
            existing_stat.clicks = item.get('clicks', 0)
            existing_stat.spend = item.get('spend', 0)
            existing_stat.conversions = item.get('conversions', 0)
            existing_stat.updated_at = datetime.now()
        else:
            # Tạo mới
            stat = AdStat(
                campaign_id=campaign.id,
                stat_date=stat_date,
                impressions=item.get('impressions', 0),
                clicks=item.get('clicks', 0),
                spend=item.get('spend', 0),
                conversions=item.get('conversions', 0),
            )
            db.add(stat)
        
        imported_count += 1
    
    # Update last_sync cho account
    from app.models import TelegramAdsAccount
    ads_account = db.query(TelegramAdsAccount).filter(
        TelegramAdsAccount.name == account_name
    ).first()
    if ads_account:
        ads_account.last_sync_at = datetime.now()
        ads_account.last_sync_status = 'success'
    
    db.commit()
    
    # Tính summary
    total_impressions = sum(i.get('impressions', 0) for i in result['data'])
    total_clicks = sum(i.get('clicks', 0) for i in result['data'])
    total_conversions = sum(i.get('conversions', 0) for i in result['data'])
    total_spend = sum(i.get('spend', 0) for i in result['data'])
    
    return JSONResponse(content={
        'success': True,
        'imported': imported_count,
        'format': 'telegram_ads_dual_files',
        'files': {
            'views': views_file.filename,
            'budget': budget_file.filename,
        },
        'summary': {
            'total_impressions': total_impressions,
            'total_clicks': total_clicks,
            'total_conversions': total_conversions,
            'total_spend': round(total_spend, 2),
        },
    })


@app.post("/telegram-ads/scan")
async def manual_scan_ads_csv(db: Session = Depends(get_db)):
    from app.services.folder_watcher import FolderWatcher
    
    base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_accounts')
    watcher = FolderWatcher(base_dir)
    result = watcher.scan_and_import(db)
    
    return JSONResponse(content=result)

