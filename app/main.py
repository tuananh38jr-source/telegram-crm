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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


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
