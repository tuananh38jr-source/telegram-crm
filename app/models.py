from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Text, ForeignKey, Boolean, BigInteger
from sqlalchemy.sql import func
from app.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    telegram_ad_id = Column(String(100), nullable=True)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=True)
    budget = Column(Float, default=0)
    currency = Column(String(10), default="TON")
    status = Column(String(50), default="active")
    target_audience = Column(Text, nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=True)
    username = Column(String(100), nullable=True)
    name = Column(String(255), nullable=False)
    invite_link = Column(String(255), nullable=True)
    current_members = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class AdStat(Base):
    __tablename__ = "ad_stats"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    stat_date = Column(Date, nullable=False)
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    ctr = Column(Float, default=0)
    cpm = Column(Float, default=0)
    spend = Column(Float, default=0)
    joins = Column(Integer, default=0)
    conversions = Column(Integer, default=0)
    revenue = Column(Float, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ChannelGrowth(Base):
    __tablename__ = "channel_growth"

    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    recorded_at = Column(DateTime, server_default=func.now())
    total_members = Column(Integer, default=0)
    new_members = Column(Integer, default=0)
    left_members = Column(Integer, default=0)
    paid_joins = Column(Integer, default=0)


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False)
    telegram_username = Column(String(100), nullable=True)
    phone = Column(String(50), nullable=True)
    role = Column(String(50), default="sale")
    commission_rate = Column(Float, default=0.10)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, nullable=True)
    telegram_username = Column(String(100), nullable=True)
    full_name = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    source = Column(String(100), default="organic")
    assigned_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    duration_days = Column(Integer, nullable=True)
    price = Column(Float, default=0)
    currency = Column(String(10), default="USD")
    product_type = Column(String(50), default="monthly")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_code = Column(String(50), unique=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    quantity = Column(Integer, default=1)
    unit_price = Column(Float, default=0)
    total_amount = Column(Float, default=0)
    currency = Column(String(10), default="USD")
    status = Column(String(50), default="paid")
    order_date = Column(Date, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    period_type = Column(String(50), default="monthly")
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    score = Column(Float, default=0)
    rank = Column(Integer, default=0)
    revenue = Column(Float, default=0)
    orders_count = Column(Integer, default=0)
    leads_count = Column(Integer, default=0)
    conversion_rate = Column(Float, default=0)
    created_at = Column(DateTime, server_default=func.now())
