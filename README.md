# Telegram Ads CRM

Web CRM tùy chỉnh để quản trị quảng cáo Telegram và bán nhóm tín hiệu XAUUSD.

## Tính năng chính

- **Quản lý chiến dịch quảng cáo**: tạo, theo dõi ngân sách và trạng thái từng chiến dịch.
- **Quản lý Telegram Channel**: tự động đếm số thành viên qua Telegram Bot API.
- **Import số liệu quảng cáo** từ file CSV/Excel.
- **Import doanh thu bán nhóm tín hiệu** từ file sheet.
- **Import danh sách nhân viên** từ file sheet.
- **Bảng xếp hạng nhân viên** tự động tính điểm theo doanh thu, số đơn, lead và tỷ lệ chuyển đổi.
- **Dashboard tổng quan** với KPI, biểu đồ chi phí/doanh thu, top chiến dịch, top nhân viên.

## Công nghệ

- Python 3.10+
- FastAPI + Uvicorn
- SQLAlchemy + SQLite (MVP)
- Jinja2 Templates
- Tailwind CSS CDN
- Chart.js
- pandas / openpyxl (import CSV/Excel)
- aiohttp (Telegram Bot API)

## Cài đặt

```bash
# 1. Clone hoặc copy thư mục telegram-crm
cd telegram-crm

# 2. Tạo virtual environment
python -m venv venv

# 3. Kích hoạt
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 4. Cài dependencies
pip install -r requirements.txt
```

## Chạy ứng dụng

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Mở trình duyệt: http://localhost:8000

## Cấu hình tự động đếm thành viên channel

1. Tạo bot qua [@BotFather](https://t.me/BotFather), lấy token.
2. Thêm bot vào channel Telegram với quyền **Administrator**.
3. Set biến môi trường:

```bash
# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN="your-bot-token"

# Windows CMD
set TELEGRAM_BOT_TOKEN=your-bot-token

# macOS/Linux
export TELEGRAM_BOT_TOKEN=your-bot-token
```

4. Vào trang **Channel Telegram** → nhấn **Cập nhật** hoặc **Đồng bộ tất cả**.

## Cấu trúc thư mục

```
telegram-crm/
├── app/
│   ├── main.py                 # FastAPI routes
│   ├── database.py             # SQLite engine + session
│   ├── models.py               # SQLAlchemy ORM models
│   ├── schemas.py              # Pydantic schemas
│   ├── services/
│   │   ├── analytics_service.py
│   │   ├── import_service.py
│   │   └── telegram_service.py
│   ├── static/
│   │   ├── style.css
│   │   └── samples/            # File CSV mẫu
│   └── templates/              # Giao diện HTML Jinja2
├── sample-data/                # Dữ liệu mẫu tham khảo
├── requirements.txt
└── README.md
```

## Hướng dẫn import dữ liệu

### 1. Import số liệu quảng cáo

Vào **Import dữ liệu** → chọn tab **Số liệu quảng cáo**.

Cột bắt buộc: `campaign_id` hoặc `campaign_name`, `stat_date`.

Các cột khác: `impressions`, `clicks`, `ctr`, `cpm`, `spend`, `joins`, `conversions`, `revenue`.

Xem mẫu: `app/static/samples/ads_data.csv`

### 2. Import doanh thu bán nhóm

Cột: `order_code`, `employee_name`, `product_name`, `quantity`, `unit_price`, `total_amount`, `currency`, `order_date`, `status`, `customer_telegram_username`.

Xem mẫu: `app/static/samples/sales_data.csv`

### 3. Import nhân viên

Cột: `full_name`, `telegram_username`, `phone`, `role`, `commission_rate`.

Xem mẫu: `app/static/samples/employees_data.csv`

## Lộ trình mở rộng

- Kết nối trực tiếp Telegram Ads API (hiện tại chưa có public API, dùng Chrome Extension hoặc file export).
- Chuyển từ SQLite sang PostgreSQL/DuckDB khi dữ liệu lớn.
- Thêm authentication và phân quyền.
- Tích hợp webhook tự động cập nhật đơn hàng từ Telegram.
- Thêm báo cáo P&L, cohort, churn.

## Lưu ý

- Ứng dụng đang ở giai đoạn MVP, dữ liệu lưu trong file `app/crm.db`.
- Không commit file `crm.db` lên git.
