import os
import json
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
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

# CORS - cho phep bookmarklet tu ads.telegram.org gui cookie ve CRM
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.post("/telegram-ads/upload-cookie")
async def upload_cookie_file(
    account_name: str = Form(...),
    cookie_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload a .json cookie file generated by the local Playwright login script.
    File is saved to telegram_ads_cookies/ so the auto-export can use it.
    """
    from app.models import TelegramAdsAccount

    # Validate file extension
    if not cookie_file.filename or not cookie_file.filename.endswith('.json'):
        return JSONResponse(content={
            'success': False,
            'error': 'Chỉ chấp nhận file .json (cookie file từ Playwright login)',
        })

    # Save to telegram_ads_cookies/ directory
    cookies_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_cookies')
    os.makedirs(cookies_dir, exist_ok=True)

    file_path = os.path.join(cookies_dir, cookie_file.filename)
    content = await cookie_file.read()

    with open(file_path, 'wb') as f:
        f.write(content)

    # Update account login status in DB
    import re
    safe_name = re.sub(r'[^\w\s-]', '', account_name).strip().replace(' ', '_')
    account = db.query(TelegramAdsAccount).filter(
        TelegramAdsAccount.name == account_name
    ).first()
    if account:
        account.login_status = 'cookie_present'
        account.cookie_path = file_path
        # Try to detect orgs count from the orgs file
        orgs_file = os.path.join(cookies_dir, f"{safe_name}_orgs.json")
        if os.path.exists(orgs_file):
            try:
                with open(orgs_file, 'r', encoding='utf-8') as f:
                    orgs_data = json.load(f)
                account.orgs_count = len(orgs_data.get("organizations", []))
            except Exception:
                pass
        db.commit()

    return JSONResponse(content={
        'success': True,
        'message': f'Đã lưu cookie cho account "{account_name}"',
        'filename': cookie_file.filename,
        'account_name': account_name,
        'size_bytes': len(content),
    })


@app.post("/telegram-ads/scan")
async def manual_scan_ads_csv(db: Session = Depends(get_db)):
    from app.services.folder_watcher import FolderWatcher

    base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_accounts')
    watcher = FolderWatcher(base_dir)
    result = watcher.scan_and_import(db)

    return JSONResponse(content=result)


@app.post("/telegram-ads/auto-export/{account_name}")
async def auto_export_ads(account_name: str, db: Session = Depends(get_db)):
    """
    Trigger auto-export script cho 1 account.
    Chay Playwright de tu dong export CSV tu ads.telegram.org.
    """
    import subprocess

    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_auto_export.py')

    if not os.path.exists(script_path):
        return JSONResponse(content={
            'success': False,
            'error': 'Script auto-export chua duoc cai dat',
        })

    try:
        result = subprocess.run(
            ['python', script_path, '--account', account_name],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )

        if result.returncode == 0:
            from app.services.folder_watcher import FolderWatcher
            base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_accounts')
            watcher = FolderWatcher(base_dir)
            scan_result = watcher.scan_and_import(db)

            return JSONResponse(content={
                'success': True,
                'message': f'Da export va import thanh cong cho {account_name}',
                'output': result.stdout,
                'scan': scan_result,
            })
        else:
            return JSONResponse(content={
                'success': False,
                'error': result.stderr or result.stdout,
            })

    except subprocess.TimeoutExpired:
        return JSONResponse(content={
            'success': False,
            'error': 'Script chay qua lau (>120s). Co the cookie het han, can login lai.',
        })
    except Exception as e:
        return JSONResponse(content={
            'success': False,
            'error': str(e),
        })


@app.post("/telegram-ads/auto-export-all")
async def auto_export_all_ads(db: Session = Depends(get_db)):
    """Trigger auto-export cho TAT CA accounts."""
    import subprocess

    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_auto_export.py')

    if not os.path.exists(script_path):
        return JSONResponse(content={
            'success': False,
            'error': 'Script auto-export chua duoc cai dat',
        })

    try:
        result = subprocess.run(
            ['python', script_path, '--all'],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )

        if result.returncode == 0:
            from app.services.folder_watcher import FolderWatcher
            base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'telegram_ads_accounts')
            watcher = FolderWatcher(base_dir)
            scan_result = watcher.scan_and_import(db)

            return JSONResponse(content={
                'success': True,
                'message': 'Da export va import thanh cong cho tat ca accounts',
                'output': result.stdout,
                'scan': scan_result,
            })
        else:
            return JSONResponse(content={
                'success': False,
                'error': result.stderr or result.stdout,
            })

    except subprocess.TimeoutExpired:
        return JSONResponse(content={
            'success': False,
            'error': 'Script chay qua lau (>300s).',
        })
    except Exception as e:
        return JSONResponse(content={
            'success': False,
            'error': str(e),
        })


# ============================================================
# Telegram Ads Login Flow (Web-based)
# ============================================================

# Sequential import lock (in-memory, single-server)
_import_lock = {"active": False, "account": None, "started_at": None}


@app.post("/telegram-ads/save-cookie")
async def save_cookie_from_bookmarklet(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Receives cookies from the CRM bookmarklet after user logs in at ads.telegram.org.
    JSON body: { "account_name": "...", "cookies": "...cookie string...", "url": "..." }
    """
    from app.models import TelegramAdsAccount
    import re

    body = await request.json()
    account_name = body.get("account_name", "").strip()
    cookie_string = body.get("cookies", "")
    page_url = body.get("url", "")

    if not account_name or not cookie_string:
        return JSONResponse(content={
            "success": False,
            "error": "Thiếu account_name hoặc cookies",
        })

    # Save cookies to file
    cookies_dir = os.path.join(BASE_DIR, 'telegram_ads_cookies')
    os.makedirs(cookies_dir, exist_ok=True)

    safe_name = re.sub(r'[^\w\s-]', '', account_name).strip().replace(' ', '_')
    cookie_data = {
        "account_name": account_name,
        "cookies": cookie_string,
        "url": page_url,
        "saved_at": datetime.now().isoformat(),
        "source": "bookmarklet",
    }

    file_path = os.path.join(cookies_dir, f"{safe_name}.json")
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(cookie_data, f, indent=2, ensure_ascii=False)

    # Update account in DB
    account = db.query(TelegramAdsAccount).filter(
        TelegramAdsAccount.name == account_name
    ).first()
    if account:
        account.login_status = 'cookie_present'
        account.cookie_path = file_path
        db.commit()

    return JSONResponse(content={
        "success": True,
        "message": f"Đã lưu cookies cho '{account_name}'. Quay lại CRM để auto-export.",
        "account_name": account_name,
    })


@app.get("/telegram-ads/login-status")
async def get_login_status(db: Session = Depends(get_db)):
    """Check cookie/login status for all Telegram Ads accounts."""
    from app.models import TelegramAdsAccount
    import re

    accounts = db.query(TelegramAdsAccount).all()
    result = []

    cookies_dir = os.path.join(BASE_DIR, 'telegram_ads_cookies')

    for acc in accounts:
        safe_name = re.sub(r'[^\w\s-]', '', acc.name).strip().replace(' ', '_')
        cookie_file = os.path.join(cookies_dir, f"{safe_name}.json")
        orgs_file = os.path.join(cookies_dir, f"{safe_name}_orgs.json")

        has_cookie = os.path.exists(cookie_file)
        cookie_age = None
        orgs = []

        if has_cookie:
            mtime = os.path.getmtime(cookie_file)
            cookie_age = int((datetime.now().timestamp() - mtime) / 3600)  # hours

            if acc.login_status == 'none':
                acc.login_status = 'cookie_present'

            if cookie_age > 24:
                acc.login_status = 'expired'

        if os.path.exists(orgs_file):
            try:
                with open(orgs_file, 'r', encoding='utf-8') as f:
                    orgs_data = json.load(f)
                orgs = orgs_data.get("organizations", [])
                acc.orgs_count = len(orgs)
            except Exception:
                pass

        result.append({
            "id": acc.id,
            "name": acc.name,
            "login_status": acc.login_status or 'none',
            "has_cookie": has_cookie,
            "cookie_age_hours": cookie_age,
            "orgs_count": acc.orgs_count or 0,
            "orgs": orgs,
            "last_sync": acc.last_sync_at.isoformat() if acc.last_sync_at else None,
        })

    db.commit()
    return JSONResponse(content={"accounts": result})


@app.get("/telegram-ads/import-lock")
async def get_import_lock():
    """Check if an import is currently in progress (sequential lock)."""
    return JSONResponse(content=_import_lock)


@app.post("/telegram-ads/import-lock")
async def set_import_lock(request: Request):
    """Acquire or release the sequential import lock."""
    body = await request.json()
    action = body.get("action", "acquire")
    account_name = body.get("account_name", "")

    if action == "acquire":
        if _import_lock["active"] and _import_lock["account"] != account_name:
            return JSONResponse(content={
                "success": False,
                "error": f"Đang import account '{_import_lock['account']}'. Hãy chờ xong trước khi import account khác.",
                "current_account": _import_lock["account"],
            })
        _import_lock["active"] = True
        _import_lock["account"] = account_name
        _import_lock["started_at"] = datetime.now().isoformat()
        return JSONResponse(content={"success": True, "message": f"Đã khóa import cho '{account_name}'"})

    elif action == "release":
        _import_lock["active"] = False
        _import_lock["account"] = None
        _import_lock["started_at"] = None
        return JSONResponse(content={"success": True, "message": "Đã mở khóa import"})

    return JSONResponse(content={"success": False, "error": "Action không hợp lệ"})


# ============================================================
# Telegram Ads Web Login (QR Code - Playwright Headless)
# ============================================================

@app.post("/telegram-ads/web-login/start")
async def web_login_start(request: Request):
    """
    Start a new web-based Telegram Ads login session.
    Server opens Playwright headless, captures QR code.
    User scans QR with phone's Telegram app.
    """
    from app.services import telegram_login_service as login_svc

    body = await request.json()
    account_name = body.get("account_name", "").strip()

    if not account_name:
        return JSONResponse(content={
            "success": False,
            "error": "Vui long chon account truoc khi login",
        })

    # Cleanup expired sessions first
    await login_svc.cleanup_expired_sessions()

    result = await login_svc.start_login_session(account_name)

    if result.get("status") == "error":
        return JSONResponse(content={
            "success": False,
            "error": result.get("error", "Khong the mo ads.telegram.org"),
        })

    return JSONResponse(content={
        "success": True,
        "session_id": result["session_id"],
        "status": result["status"],
        "account_name": account_name,
    })


@app.get("/telegram-ads/web-login/status/{session_id}")
async def web_login_status(session_id: str):
    """Check if user has completed Telegram authentication."""
    from app.services import telegram_login_service as login_svc

    result = await login_svc.check_auth_status(session_id)
    return JSONResponse(content=result)


@app.get("/telegram-ads/web-login/qr/{session_id}")
async def web_login_qr_image(session_id: str):
    """Serve the QR code screenshot for the user to scan."""
    from app.services import telegram_login_service as login_svc

    qr_path = login_svc.get_qr_path(session_id)
    if not qr_path:
        raise HTTPException(status_code=404, detail="QR code chua san sang hoac session het han")

    return FileResponse(
        qr_path,
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store"},
    )


@app.post("/telegram-ads/web-login/cancel/{session_id}")
async def web_login_cancel(session_id: str):
    """Cancel an active login session and free browser resources."""
    from app.services import telegram_login_service as login_svc

    result = await login_svc.cancel_session(session_id)
    return JSONResponse(content=result)


# ============================================================
# Google Docs Management
# ============================================================
@app.get("/google-docs", response_class=HTMLResponse)
async def google_docs_page(request: Request, db: Session = Depends(get_db)):
    """Google Docs scanner page."""
    from app.models import GoogleDocsReport

    reports = db.query(GoogleDocsReport).order_by(
        GoogleDocsReport.scanned_at.desc()
    ).limit(50).all()

    # Load config to show configured docs
    config_path = os.path.join(BASE_DIR, "google_sheets_config.json")
    docs_config = []
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        docs_config = config.get("google_docs", [])

    return templates.TemplateResponse("google_docs.html", {
        "request": request,
        "reports": reports,
        "docs_config": docs_config,
    })


@app.post("/google-docs/scan")
async def scan_google_doc(request: Request, db: Session = Depends(get_db)):
    """Scan a Google Docs document and parse its content."""
    from app.models import GoogleDocsReport
    from app.services.google_docs_service import GoogleDocsScanner, save_report

    body = await request.json()
    doc_url = body.get("doc_url", "").strip()

    if not doc_url:
        return JSONResponse(content={
            "success": False,
            "error": "Vui lòng nhập URL Google Docs",
        })

    try:
        scanner = GoogleDocsScanner()
        doc_data = scanner.read_document(doc_url)
        report = scanner.parse_operations_report(doc_data)

        filepath = save_report(report)

        db_report = GoogleDocsReport(
            doc_id=doc_data.get("doc_id", ""),
            title=report.get("title", doc_data.get("title", "")),
            doc_url=doc_url,
            report_type="operations_report",
            full_text=doc_data.get("full_text", "")[:10000],
            parsed_data=json.dumps(report, default=str, ensure_ascii=False),
            channels_found=len(report.get("channels", [])),
            tables_found=report.get("tables_found", 0),
            metrics_summary=json.dumps(report.get("metrics", {}), default=str),
        )
        db.add(db_report)
        db.commit()

        return JSONResponse(content={
            "success": True,
            "title": report.get("title", ""),
            "channels_found": len(report.get("channels", [])),
            "tables_found": report.get("tables_found", 0),
            "sections_found": report.get("sections_found", 0),
            "metrics": report.get("metrics", {}),
            "channels": report.get("channels", [])[:10],
            "saved_to": filepath,
        })

    except Exception as e:
        return JSONResponse(content={
            "success": False,
            "error": str(e),
        })


@app.post("/google-docs/scan-all")
async def scan_all_google_docs(db: Session = Depends(get_db)):
    """Scan all configured Google Docs."""
    from app.services.google_docs_service import sync_google_docs

    result = sync_google_docs()

    if result.get("ok"):
        from app.models import GoogleDocsReport
        for doc_result in result.get("results", []):
            if doc_result.get("ok"):
                db_report = GoogleDocsReport(
                    doc_id="",
                    title=doc_result.get("title", ""),
                    doc_url=doc_result.get("doc_url", ""),
                    report_type="operations_report",
                    channels_found=doc_result.get("channels_found", 0),
                    metrics_summary=json.dumps(doc_result.get("metrics", {}), default=str),
                )
                db.add(db_report)
        db.commit()

    return JSONResponse(content=result)


@app.get("/google-docs/config")
async def get_google_docs_config():
    """Get current Google Docs configuration."""
    config_path = os.path.join(BASE_DIR, "google_sheets_config.json")
    if not os.path.exists(config_path):
        return JSONResponse(content={"error": "Config file not found"})

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    return JSONResponse(content={
        "google_docs": config.get("google_docs", []),
        "service_account_configured": bool(config.get("service_account_key")),
    })


@app.post("/google-docs/config")
async def update_google_docs_config(request: Request):
    """Add or update a Google Docs configuration entry."""
    body = await request.json()
    doc_name = body.get("name", "").strip()
    doc_url = body.get("doc_url", "").strip()

    if not doc_name or not doc_url:
        return JSONResponse(content={
            "success": False,
            "error": "Cần nhập tên và URL Google Docs",
        })

    config_path = os.path.join(BASE_DIR, "google_sheets_config.json")
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    if "google_docs" not in config:
        config["google_docs"] = []

    existing = None
    for doc in config["google_docs"]:
        if doc.get("doc_url") == doc_url:
            existing = doc
            break

    if existing:
        existing["name"] = doc_name
        existing["doc_url"] = doc_url
        existing["enabled"] = True
    else:
        config["google_docs"].append({
            "name": doc_name,
            "doc_url": doc_url,
            "enabled": True,
            "data_type": "operations_report",
        })

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

    return JSONResponse(content={
        "success": True,
        "message": f"Đã thêm/cập nhật Google Docs '{doc_name}'",
    })


@app.get("/google-docs/reports/{report_id}")
async def get_report_detail(report_id: int, db: Session = Depends(get_db)):
    """Get detailed data for a specific scanned report."""
    from app.models import GoogleDocsReport

    report = db.query(GoogleDocsReport).filter(GoogleDocsReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report không tồn tại")

    parsed = {}
    try:
        parsed = json.loads(report.parsed_data) if report.parsed_data else {}
    except Exception:
        pass

    return JSONResponse(content={
        "id": report.id,
        "title": report.title,
        "doc_url": report.doc_url,
        "channels_found": report.channels_found,
        "tables_found": report.tables_found,
        "scanned_at": report.scanned_at.isoformat() if report.scanned_at else None,
        "parsed_data": parsed,
    })

