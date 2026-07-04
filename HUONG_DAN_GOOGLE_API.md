# Hướng dẫn kết nối Google API (Sheets + Docs) với CRM

## Bạn đã có gì?

- **Service Account** tên `halkeyes-361@dnasuccess-bot.iam.gserviceaccount.com`
- **File JSON key** tên `dnasuccess-bot-07d3b6056139.json` (đã tải từ Google Cloud Console)
- File JSON này đã được copy vào CRM với tên `service_account.json`

## Bước 1: Chia sẻ Google Sheet cho Service Account

Mỗi Google Sheet bạn muốn CRM đọc, hãy chia sẻ cho email service account:

1. Mở Google Sheet cần kết nối (VD: sheet doanh thu)
2. Nhấn nút **Share** (Chia sẻ) ở góc phải trên
3. Trong ô "Add people and groups", dán email:
   ```
   halkeyes-361@dnasuccess-bot.iam.gserviceaccount.com
   ```
4. Chọn quyền **Viewer** (Người xem) — chỉ cần đọc, không cần sửa
5. Bỏ tick "Notify people" (không cần gửi email thông báo)
6. Nhấn **Share** / **Done**

**Lặp lại cho TẤT CẢ** các sheet bạn muốn kết nối:
- Sheet doanh thu / đơn hàng
- Sheet nhân viên / hiệu suất
- Sheet bất kỳ khác

## Bước 2: Chia sẻ Google Docs cho Service Account

Tương tự như Sheets, mỗi Google Docs (báo cáo vận hành) cũng cần share:

1. Mở Google Docs báo cáo
2. Share → dán email `halkeyes-361@dnasuccess-bot.iam.gserviceaccount.com`
3. Quyền Viewer → Done

## Bước 3: Cấu hình trên CRM Web

### 3a. Thêm Google Sheets (Doanh thu + Nhân viên)

Hiện tại, file `google_sheets_config.json` đã có sẵn 2 mục:
- **Sales Data (Doanh thu)** — cần điền URL sheet
- **Employee Performance** — cần điền URL sheet

Để lấy URL sheet:
1. Mở Google Sheet trên trình duyệt
2. Copy URL từ thanh địa chỉ, VD:
   ```
   https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit#gid=0
   ```
3. Dán vào file `google_sheets_config.json` ở phần `"sheet_url"` tương ứng

Hoặc sửa trực tiếp file `google_sheets_config.json` trên server, hoặc mở terminal:
```bash
# Trên server Railway, dùng Railway CLI hoặc SSH để sửa config
```

### 3b. Thêm Google Docs (Báo cáo vận hành)

Trên CRM web:
1. Vào trang **Google Docs** (sidebar bên trái)
2. Dán URL Google Docs vào ô "URL Google Docs"
3. Nhấn **"Quét & Phân tích"** để đọc thử
4. Nếu thành công, điền tên và nhấn **"Lưu vào danh sách"** để tự động quét

## Bước 4: Kiểm tra hoạt động

### Kiểm tra Google Sheets:
1. Trên CRM, vào trang **Doanh thu** → xem có đơn hàng mới không
2. Hoặc vào trang **Import** → chọn "Google Sheets sync"

### Kiểm tra Google Docs:
1. Vào trang **Google Docs** trên CRM
2. Dán URL báo cáo → nhấn "Quét & Phân tích"
3. Xem kết quả: channels, metrics, bảng dữ liệu

## Lịch tự động

CRM tự động chạy các job sau:
- **Mỗi 4 tiếng**: Đọc Google Sheets (doanh thu, nhân viên)
- **Mỗi 6 tiếng**: Quét Google Docs (báo cáo vận hành)
- **Mỗi 30 phút**: Scan folder Telegram Ads CSV
- **Mỗi 12 tiếng**: Auto-export CSV từ ads.telegram.org (nếu có cookie)

## Xử lý lỗi thường gặp

### "Service account key not found"
- Kiểm tra file `service_account.json` có tồn tại trong thư mục project không
- File này chính là file `dnasuccess-bot-07d3b6056139.json` đã copy

### "403 Forbidden" hoặc "The caller does not have permission"
- Chưa share Google Sheet/Docs cho service account email
- Quay lại Bước 1 hoặc Bước 2

### "Spreadsheet not found"
- URL sheet sai hoặc sheet ID không đúng
- Copy lại URL từ trình duyệt

### gspread / google-api-python-client chưa cài
- Chạy: `pip install gspread google-api-python-client google-auth`
- Hoặc deploy lại Railway (requirements.txt đã có sẵn)

## File google_sheets_config.json — Giải thích chi tiết

File này **do CRM tự tạo**, KHÔNG phải file service account JSON.

```json
{
    "service_account_key": "service_account.json",   // ← Đường dẫn tới file key
    "sync_interval_minutes": 60,
    "google_docs": [                                  // ← Danh sách Google Docs
        {
            "name": "Báo cáo vận hành",
            "doc_url": "https://docs.google.com/document/d/...",
            "enabled": true,
            "data_type": "operations_report"
        }
    ],
    "sheets": [                                       // ← Danh sách Google Sheets
        {
            "name": "Sales Data (Doanh thu)",
            "sheet_url": "https://docs.google.com/spreadsheets/d/...",
            "data_type": "sales",
            "worksheet": "Sheet1",
            "enabled": true,
            "column_mapping": { ... }
        }
    ]
}
```

**Phân biệt 2 file JSON:**
- `service_account.json` = File key từ Google Cloud (chứa private_key, client_email...)
- `google_sheets_config.json` = File cấu hình CRM (chứa URL sheets, column mapping...)

## Service Account Email

```
halkeyes-361@dnasuccess-bot.iam.gserviceaccount.com
```

Dùng email này để share tất cả Google Sheets và Google Docs cần kết nối.
