from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import date, datetime


class CampaignBase(BaseModel):
    name: str
    telegram_ad_id: Optional[str] = None
    channel_id: Optional[int] = None
    budget: float = 0
    currency: str = "TON"
    status: str = "active"
    target_audience: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class CampaignCreate(CampaignBase):
    pass


class CampaignOut(CampaignBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class ChannelBase(BaseModel):
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    name: str
    invite_link: Optional[str] = None
    current_members: int = 0
    is_active: bool = True


class ChannelCreate(ChannelBase):
    pass


class ChannelOut(ChannelBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class AdStatBase(BaseModel):
    campaign_id: int
    stat_date: date
    impressions: int = 0
    clicks: int = 0
    ctr: float = 0
    cpm: float = 0
    spend: float = 0
    joins: int = 0
    conversions: int = 0
    revenue: float = 0


class AdStatCreate(AdStatBase):
    pass


class AdStatOut(AdStatBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class EmployeeBase(BaseModel):
    full_name: str
    telegram_username: Optional[str] = None
    phone: Optional[str] = None
    role: str = "sale"
    commission_rate: float = 0.10
    is_active: bool = True


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeOut(EmployeeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class ProductBase(BaseModel):
    name: str
    duration_days: Optional[int] = None
    price: float = 0
    currency: str = "USD"
    product_type: str = "monthly"
    is_active: bool = True


class ProductCreate(ProductBase):
    pass


class ProductOut(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class OrderBase(BaseModel):
    order_code: str
    customer_id: Optional[int] = None
    employee_id: Optional[int] = None
    product_id: Optional[int] = None
    quantity: int = 1
    unit_price: float = 0
    total_amount: float = 0
    currency: str = "USD"
    status: str = "paid"
    order_date: Optional[date] = None


class OrderCreate(OrderBase):
    pass


class OrderOut(OrderBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
