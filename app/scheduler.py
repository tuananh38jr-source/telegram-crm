"""
Scheduler cho các tác vụ tự động:
- Sync channel members mỗi 6 tiếng
- Scan folder Telegram Ads CSV mỗi 30 phút
- Auto import Google Sheets (nếu có)
"""
import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.telegram_service import sync_all_channels
from app.services.folder_watcher import FolderWatcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Khởi tạo scheduler
scheduler = AsyncIOScheduler()

# Folder watcher instance
folder_watcher = FolderWatcher()


async def job_sync_channel_members():
    """
    Job: Đồng bộ số thành viên tất cả channels.
    Chạy mỗi 6 tiếng.
    """
    logger.info(f"[{datetime.now()}] Bắt đầu sync channel members...")
    try:
        db = SessionLocal()
        try:
            results = await sync_all_channels(db)
            success_count = sum(1 for r in results if r.get('ok'))
            logger.info(f"[{datetime.now()}] Sync xong: {success_count}/{len(results)} channels thành công")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[{datetime.now()}] Lỗi sync channel members: {e}")


async def job_scan_telegram_ads_csv():
    """
    Job: Scan folder và import CSV files từ Telegram Ads.
    Chạy mỗi 30 phút.
    """
    logger.info(f"[{datetime.now()}] Bắt đầu scan Telegram Ads CSV...")
    try:
        db = SessionLocal()
        try:
            result = folder_watcher.scan_and_import(db)
            logger.info(
                f"[{datetime.now()}] Scan xong: "
                f"{result['accounts_scanned']} accounts, "
                f"{result['total_imported']} rows imported"
            )
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[{datetime.now()}] Lỗi scan CSV: {e}")


async def job_auto_export_telegram_ads():
    """
    Job: Tự động export CSV từ ads.telegram.org bằng Playwright.
    Chạy mỗi 12 tiếng. Cần đã login cookie trước.
    """
    import subprocess
    import os

    logger.info(f"[{datetime.now()}] Bat dau auto-export Telegram Ads...")
    try:
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'telegram_ads_auto_export.py'
        )
        if not os.path.exists(script_path):
            logger.warning(f"[{datetime.now()}] Script auto-export khong ton tai: {script_path}")
            return

        result = subprocess.run(
            ['python', script_path, '--all'],
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode == 0:
            logger.info(f"[{datetime.now()}] Auto-export thanh cong:\n{result.stdout}")
        else:
            logger.error(f"[{datetime.now()}] Auto-export that bai:\n{result.stderr}")
    except subprocess.TimeoutExpired:
        logger.error(f"[{datetime.now()}] Auto-export timeout (>600s)")
    except Exception as e:
        logger.error(f"[{datetime.now()}] Loi auto-export: {e}")


def start_scheduler():
    """
    Khởi động scheduler với tất cả jobs.
    """
    # Job 1: Sync channel members mỗi 6 tiếng
    scheduler.add_job(
        job_sync_channel_members,
        trigger=IntervalTrigger(hours=6),
        id='sync_channels',
        name='Sync Telegram Channel Members',
        replace_existing=True,
    )
    
    # Job 2: Scan Telegram Ads CSV mỗi 30 phút
    scheduler.add_job(
        job_scan_telegram_ads_csv,
        trigger=IntervalTrigger(minutes=30),
        id='scan_ads_csv',
        name='Scan Telegram Ads CSV',
        replace_existing=True,
    )

    # Job 3: Auto-export Telegram Ads CSV mỗi 12 tiếng (Playwright)
    scheduler.add_job(
        job_auto_export_telegram_ads,
        trigger=IntervalTrigger(hours=12),
        id='auto_export_ads',
        name='Auto Export Telegram Ads (Playwright)',
        replace_existing=True,
    )
    
    # Chạy ngay lần đầu khi khởi động
    scheduler.add_job(
        job_sync_channel_members,
        id='sync_channels_init',
        name='Initial Channel Sync',
        replace_existing=True,
    )
    
    scheduler.add_job(
        job_scan_telegram_ads_csv,
        id='scan_ads_csv_init',
        name='Initial CSV Scan',
        replace_existing=True,
    )
    
    scheduler.start()
    logger.info(f"[{datetime.now()}] Scheduler đã khởi động với {len(scheduler.get_jobs())} jobs")
    
    return scheduler


def stop_scheduler():
    """
    Dừng scheduler.
    """
    if scheduler.running:
        scheduler.shutdown()
        logger.info(f"[{datetime.now()}] Scheduler đã dừng")
