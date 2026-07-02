from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import Campaign, AdStat, Channel, ChannelGrowth, Employee, Order, Customer, LeaderboardSnapshot
from datetime import date, datetime, timedelta


def get_dashboard_summary(db: Session) -> dict:
    total_spend = db.query(func.coalesce(func.sum(AdStat.spend), 0)).scalar() or 0
    total_revenue = db.query(func.coalesce(func.sum(Order.total_amount), 0)).filter(
        Order.status == "paid"
    ).scalar() or 0
    total_orders = db.query(func.count(Order.id)).filter(Order.status == "paid").scalar() or 0
    total_leads = db.query(func.count(Customer.id)).scalar() or 0
    total_members = db.query(func.coalesce(func.sum(Channel.current_members), 0)).scalar() or 0
    roas = total_revenue / total_spend if total_spend > 0 else 0
    conversion_rate = (total_orders / total_leads * 100) if total_leads > 0 else 0

    return {
        "total_spend": round(total_spend, 2),
        "total_revenue": round(total_revenue, 2),
        "roas": round(roas, 2),
        "total_orders": total_orders,
        "total_leads": total_leads,
        "conversion_rate": round(conversion_rate, 2),
        "total_members": total_members,
    }


def get_campaigns_performance(db: Session):
    campaigns = db.query(Campaign).all()
    result = []
    for c in campaigns:
        stats = db.query(AdStat).filter(AdStat.campaign_id == c.id).all()
        spend = sum(s.spend for s in stats)
        revenue = sum(s.revenue for s in stats)
        joins = sum(s.joins for s in stats)
        conversions = sum(s.conversions for s in stats)
        roas = revenue / spend if spend > 0 else 0
        result.append({
            "id": c.id,
            "name": c.name,
            "status": c.status,
            "spend": round(spend, 2),
            "revenue": round(revenue, 2),
            "roas": round(roas, 2),
            "joins": joins,
            "conversions": conversions,
        })
    return sorted(result, key=lambda x: x["roas"], reverse=True)


def get_revenue_trend(db: Session, days: int = 30):
    start = date.today() - timedelta(days=days)
    stats = (
        db.query(AdStat.stat_date, func.sum(AdStat.spend), func.sum(AdStat.revenue))
        .filter(AdStat.stat_date >= start)
        .group_by(AdStat.stat_date)
        .order_by(AdStat.stat_date)
        .all()
    )
    return [
        {"date": str(s[0]), "spend": round(s[1] or 0, 2), "revenue": round(s[2] or 0, 2)}
        for s in stats
    ]


def get_employee_leaderboard(db: Session, period_type: str = "monthly"):
    today = date.today()
    if period_type == "monthly":
        start = today.replace(day=1)
        end = today
    elif period_type == "weekly":
        start = today - timedelta(days=today.weekday())
        end = today
    else:
        start = today
        end = today

    employees = db.query(Employee).filter(Employee.is_active == True).all()
    rows = []

    for emp in employees:
        orders = (
            db.query(Order)
            .filter(
                Order.employee_id == emp.id,
                Order.status == "paid",
                Order.order_date >= start,
                Order.order_date <= end,
            )
            .all()
        )
        revenue = sum(o.total_amount for o in orders)
        orders_count = len(orders)

        # Leads assigned trong kỳ
        leads_count = (
            db.query(func.count(Customer.id))
            .filter(
                Customer.assigned_employee_id == emp.id,
                func.date(Customer.created_at) >= start,
                func.date(Customer.created_at) <= end,
            )
            .scalar()
            or 0
        )

        conversion_rate = (orders_count / leads_count * 100) if leads_count > 0 else 0
        score = revenue * 0.5 + orders_count * 20 + leads_count * 2 + conversion_rate

        rows.append({
            "employee_id": emp.id,
            "full_name": emp.full_name,
            "role": emp.role,
            "revenue": round(revenue, 2),
            "orders_count": orders_count,
            "leads_count": leads_count,
            "conversion_rate": round(conversion_rate, 2),
            "score": round(score, 2),
        })

    rows.sort(key=lambda x: x["score"], reverse=True)
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx

    return rows


def recalculate_leaderboard(db: Session, period_type: str = "monthly"):
    today = date.today()
    if period_type == "monthly":
        start = today.replace(day=1)
        end = today
    elif period_type == "weekly":
        start = today - timedelta(days=today.weekday())
        end = today
    else:
        start = today
        end = today

    rows = get_employee_leaderboard(db, period_type)

    # Clear old snapshot
    db.query(LeaderboardSnapshot).filter(
        LeaderboardSnapshot.period_type == period_type,
        LeaderboardSnapshot.period_start == start,
    ).delete()

    for row in rows:
        snapshot = LeaderboardSnapshot(
            employee_id=row["employee_id"],
            period_type=period_type,
            period_start=start,
            period_end=end,
            score=row["score"],
            rank=row["rank"],
            revenue=row["revenue"],
            orders_count=row["orders_count"],
            leads_count=row["leads_count"],
            conversion_rate=row["conversion_rate"],
        )
        db.add(snapshot)

    db.commit()
    return rows
