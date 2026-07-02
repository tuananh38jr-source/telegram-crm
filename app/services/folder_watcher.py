"""
Folder watcher để auto-detect và import CSV files từ Telegram Ads.
"""
import os
import glob
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from app.models import TelegramAdsAccount, Campaign, AdStat
from app.services.telegram_ads_parser import TelegramAdsCSVParser


class FolderWatcher:
    """
    Theo dõi các folder chứa CSV files từ Telegram Ads.
    Tự động detect file mới và import vào CRM.
    """
    
    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.join(os.getcwd(), 'telegram_ads_accounts')
        self.base_dir = base_dir
        self.parser = TelegramAdsCSVParser()
    
    def ensure_base_dir(self):
        """Tạo base directory nếu chưa tồn tại."""
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
    
    def scan_account_folders(self) -> list:
        """
        Scan tất cả account folders trong base_dir.
        Returns list of (account_name, folder_path, csv_files)
        """
        self.ensure_base_dir()
        accounts = []
        
        for account_dir in Path(self.base_dir).iterdir():
            if account_dir.is_dir():
                csv_files = list(account_dir.glob('*.csv'))
                if csv_files:
                    accounts.append({
                        'account_name': account_dir.name,
                        'folder_path': str(account_dir),
                        'csv_files': [str(f) for f in csv_files],
                    })
        
        return accounts
    
    def process_csv_file(self, db: Session, file_path: str, account_name: str) -> dict:
        """
        Xử lý 1 file CSV: parse và import vào database.
        """
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            
            # Parse CSV
            result = self.parser.parse(content, filename=os.path.basename(file_path))
            
            if not result['success']:
                return {
                    'success': False,
                    'error': result['error'],
                    'file': file_path,
                }
            
            # Import data vào database
            imported_count = 0
            for item in result['data']:
                # Tìm hoặc tạo campaign
                campaign_name = item.get('campaign_name')
                campaign_id = item.get('campaign_id')
                
                campaign = None
                if campaign_id:
                    campaign = db.query(Campaign).filter(
                        Campaign.telegram_ad_id == str(campaign_id)
                    ).first()
                
                if not campaign and campaign_name:
                    campaign = db.query(Campaign).filter(
                        Campaign.name == campaign_name
                    ).first()
                
                if not campaign:
                    # Tạo campaign mới
                    campaign = Campaign(
                        name=campaign_name or f"Telegram Ads {campaign_id}",
                        telegram_ad_id=str(campaign_id) if campaign_id else None,
                        status='active',
                    )
                    db.add(campaign)
                    db.flush()
                
                # Tạo hoặc update AdStat
                stat_date = item.get('stat_date')
                existing_stat = db.query(AdStat).filter(
                    AdStat.campaign_id == campaign.id,
                    AdStat.stat_date == stat_date,
                ).first()
                
                if existing_stat:
                    # Update existing
                    existing_stat.impressions += item.get('impressions', 0)
                    existing_stat.clicks += item.get('clicks', 0)
                    existing_stat.spend += item.get('spend', 0)
                    existing_stat.conversions += item.get('conversions', 0)
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
                        ctr=item.get('ctr', 0),
                        cpm=item.get('cpm', 0),
                    )
                    db.add(stat)
                
                imported_count += 1
            
            db.commit()
            
            return {
                'success': True,
                'file': file_path,
                'format': result['format'],
                'imported': imported_count,
            }
        except Exception as e:
            db.rollback()
            return {
                'success': False,
                'error': str(e),
                'file': file_path,
            }
    
    def detect_dual_file_pairs(self, account_dir: str) -> list:
        """
        Detect các cặp file Views + Budget trong cùng 1 folder.
        Logic: file có 'budget' trong tên = budget file, file còn lại = views file.
        Returns list of {'views_file': path, 'budget_file': path}
        """
        csv_files = list(Path(account_dir).glob('*.csv'))
        if len(csv_files) < 2:
            return []
        
        budget_files = [f for f in csv_files if 'budget' in f.stem.lower()]
        views_files = [f for f in csv_files if 'budget' not in f.stem.lower()]
        
        pairs = []
        for bf in budget_files:
            # Tìm views file tương ứng (cùng date pattern trong tên)
            for vf in views_files:
                pairs.append({
                    'views_file': str(vf),
                    'budget_file': str(bf),
                })
                break  # match first pair
        
        return pairs
    
    def process_dual_files(self, db: Session, views_path: str, budget_path: str, account_name: str) -> dict:
        """
        Xử lý cặp 2 file CSV (Views + Budget) và import vào database.
        """
        try:
            with open(views_path, 'rb') as f:
                views_content = f.read()
            with open(budget_path, 'rb') as f:
                budget_content = f.read()
            
            # Parse dual files
            result = self.parser.parse_telegram_ads_dual_files(views_content, budget_content)
            
            if not result['success']:
                return {
                    'success': False,
                    'error': result['error'],
                    'files': [views_path, budget_path],
                }
            
            # Import data vào database
            imported_count = 0
            for item in result['data']:
                stat_date = item.get('stat_date')
                
                # Tìm hoặc tạo campaign
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
                
                existing_stat = db.query(AdStat).filter(
                    AdStat.campaign_id == campaign.id,
                    AdStat.stat_date == stat_date,
                ).first()
                
                if existing_stat:
                    existing_stat.impressions = item.get('impressions', 0)
                    existing_stat.clicks = item.get('clicks', 0)
                    existing_stat.spend = item.get('spend', 0)
                    existing_stat.conversions = item.get('conversions', 0)
                    existing_stat.updated_at = datetime.now()
                else:
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
            
            db.commit()
            
            return {
                'success': True,
                'files': [views_path, budget_path],
                'format': 'telegram_ads_dual_files',
                'imported': imported_count,
            }
        except Exception as e:
            db.rollback()
            return {
                'success': False,
                'error': str(e),
                'files': [views_path, budget_path],
            }
    
    def scan_and_import(self, db: Session) -> dict:
        """
        Scan tất cả account folders và import CSV files mới.
        Ưu tiên detect cặp dual-file (Views + Budget), sau đó xử lý file đơn.
        """
        accounts = self.scan_account_folders()
        results = []
        total_imported = 0
        
        for account in accounts:
            account_name = account['account_name']
            
            # Cập nhật last_sync trong database
            ads_account = db.query(TelegramAdsAccount).filter(
                TelegramAdsAccount.name == account_name
            ).first()
            
            if not ads_account:
                ads_account = TelegramAdsAccount(
                    name=account_name,
                    folder_path=account['folder_path'],
                    is_active=True,
                )
                db.add(ads_account)
                db.flush()
            
            # Thử detect dual-file pairs trước
            dual_pairs = self.detect_dual_file_pairs(account['folder_path'])
            processed_files = set()
            
            for pair in dual_pairs:
                result = self.process_dual_files(
                    db, pair['views_file'], pair['budget_file'], account_name
                )
                results.append(result)
                
                if result['success']:
                    total_imported += result['imported']
                
                processed_files.add(pair['views_file'])
                processed_files.add(pair['budget_file'])
            
            # Xử lý các file đơn còn lại (chưa được pair)
            for csv_file in account['csv_files']:
                if csv_file not in processed_files:
                    result = self.process_csv_file(db, csv_file, account_name)
                    results.append(result)
                    
                    if result['success']:
                        total_imported += result['imported']
            
            # Cập nhật last_sync
            ads_account.last_sync_at = datetime.now()
            ads_account.last_sync_status = 'success'
        
        db.commit()
        
        return {
            'accounts_scanned': len(accounts),
            'total_imported': total_imported,
            'results': results,
        }


def create_account_folder(base_dir: str, account_name: str) -> str:
    """
    Tạo folder cho 1 account Telegram Ads.
    """
    account_dir = os.path.join(base_dir, account_name)
    os.makedirs(account_dir, exist_ok=True)
    return account_dir


def get_account_folders(base_dir: str = None) -> list:
    """
    List tất cả account folders.
    """
    if base_dir is None:
        base_dir = os.path.join(os.getcwd(), 'telegram_ads_accounts')
    
    if not os.path.exists(base_dir):
        return []
    
    return [
        {
            'name': d.name,
            'path': str(d),
            'csv_count': len(list(d.glob('*.csv'))),
        }
        for d in Path(base_dir).iterdir()
        if d.is_dir()
    ]
