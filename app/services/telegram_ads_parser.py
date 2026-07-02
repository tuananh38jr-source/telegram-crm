"""
Smart parser cho Telegram Ads CSV files.
Tự động detect format và convert sang CRM format.
"""
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional
from io import BytesIO


class TelegramAdsCSVParser:
    """
    Parser thông minh cho CSV từ Telegram Ads platform.
    Hỗ trợ nhiều format khác nhau và tự động map columns.
    """
    
    # Common column mappings từ Telegram Ads sang CRM
    COLUMN_MAPPINGS = {
        # Telegram Ads column names -> CRM column names
        'ad_id': 'campaign_id',
        'ad title': 'campaign_name',
        'campaign': 'campaign_name',
        'views': 'impressions',
        'clicks': 'clicks',
        'actions': 'conversions',
        'spent budget': 'spend',
        'amount': 'spend',
        'cost': 'spend',
        'date': 'stat_date',
        'day': 'stat_date',
        'period': 'stat_date',
        'ctr': 'ctr',
        'cpm': 'cpm',
    }
    
    def __init__(self):
        self.detected_format = None
        
    def detect_format(self, df: pd.DataFrame) -> str:
        """
        Detect format của CSV Telegram Ads.
        Returns: 'overview', 'daily_stats', hoặc 'unknown'
        """
        columns_lower = [str(c).lower().strip() for c in df.columns]
        
        # Overview format: thường có Ad ID, Ad Title, Views, Amount
        if any('ad_id' in c or 'ad id' in c for c in columns_lower):
            if any('views' in c for c in columns_lower):
                self.detected_format = 'overview'
                return 'overview'
        
        # Daily stats format: thường có Date/Day column
        if any('date' in c or 'day' in c or 'period' in c for c in columns_lower):
            if any('views' in c or 'impressions' in c for c in columns_lower):
                self.detected_format = 'daily_stats'
                return 'daily_stats'
        
        self.detected_format = 'unknown'
        return 'unknown'
    
    def normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Chuẩn hóa tên cột từ Telegram Ads format sang CRM format.
        """
        column_mapping = {}
        columns_lower = [str(c).lower().strip() for c in df.columns]
        
        for i, col in enumerate(df.columns):
            col_lower = str(col).lower().strip()
            if col_lower in self.COLUMN_MAPPINGS:
                column_mapping[col] = self.COLUMN_MAPPINGS[col_lower]
        
        return df.rename(columns=column_mapping)
    
    def parse_overview_csv(self, df: pd.DataFrame) -> List[Dict]:
        """
        Parse overview CSV (Ad ID, Ad Title, Views, Amount).
        Thường là tổng hợp theo campaign.
        """
        df = self.normalize_columns(df)
        results = []
        
        for _, row in df.iterrows():
            try:
                campaign_id = str(row.get('campaign_id', '')).strip()
                campaign_name = str(row.get('campaign_name', '')).strip()
                
                if not campaign_id and not campaign_name:
                    continue
                
                impressions = int(float(row.get('impressions', 0) or 0))
                spend_str = str(row.get('spend', '0')).replace(',', '').strip()
                spend = float(spend_str) if spend_str else 0.0
                
                results.append({
                    'campaign_id': campaign_id,
                    'campaign_name': campaign_name,
                    'impressions': impressions,
                    'spend': spend,
                    'stat_date': datetime.now().date(),
                    'source': 'telegram_ads_overview',
                })
            except Exception as e:
                continue
        
        return results
    
    def parse_daily_stats_csv(self, df: pd.DataFrame) -> List[Dict]:
        """
        Parse daily stats CSV (Date, Views, Clicks, Spend...).
        Chi tiết theo ngày.
        """
        df = self.normalize_columns(df)
        results = []
        
        for _, row in df.iterrows():
            try:
                stat_date = row.get('stat_date')
                if pd.isna(stat_date):
                    stat_date = datetime.now().date()
                elif isinstance(stat_date, str):
                    try:
                        stat_date = pd.to_datetime(stat_date).date()
                    except:
                        stat_date = datetime.now().date()
                elif hasattr(stat_date, 'date'):
                    stat_date = stat_date.date()
                
                impressions = int(float(row.get('impressions', 0) or 0))
                clicks = int(float(row.get('clicks', 0) or 0))
                conversions = int(float(row.get('conversions', 0) or 0))
                
                spend_str = str(row.get('spend', '0')).replace(',', '').strip()
                spend = float(spend_str) if spend_str else 0.0
                
                ctr = float(row.get('ctr', 0) or 0)
                cpm = float(row.get('cpm', 0) or 0)
                
                results.append({
                    'stat_date': stat_date,
                    'impressions': impressions,
                    'clicks': clicks,
                    'conversions': conversions,
                    'spend': spend,
                    'ctr': ctr,
                    'cpm': cpm,
                    'source': 'telegram_ads_daily',
                })
            except Exception as e:
                continue
        
        return results
    
    def parse(self, content: bytes, filename: str = '') -> Dict:
        """
        Parse CSV content và trả về structured data.
        """
        try:
            # Đọc CSV
            df = pd.read_csv(BytesIO(content))
            
            # Detect format
            format_type = self.detect_format(df)
            
            # Parse theo format
            if format_type == 'overview':
                data = self.parse_overview_csv(df)
            elif format_type == 'daily_stats':
                data = self.parse_daily_stats_csv(df)
            else:
                return {
                    'success': False,
                    'error': f'Unknown CSV format. Columns: {list(df.columns)}',
                    'format': 'unknown',
                    'data': [],
                }
            
            return {
                'success': True,
                'format': format_type,
                'data': data,
                'row_count': len(data),
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'format': 'unknown',
                'data': [],
            }
    
    def parse_telegram_ads_dual_files(self, views_content: bytes, budget_content: bytes) -> Dict:
        """
        Parse và merge 2 file CSV từ Telegram Ads:
        - File 1 (views): date, Views, Opened video, Clicks, Actions
        - File 2 (budget): date, Spent budget, TON
        
        Đặc điểm format thực tế:
        - Tab separator (\t) thay vì comma
        - Date format: "DD Mon YYYY" (VD: "30 Dec 2025")
        - European decimal: comma thay cho dot (VD: "23,3973" = 23.3973)
        """
        try:
            # Parse views file
            df_views = pd.read_csv(BytesIO(views_content), sep='\t')
            df_views.columns = ['date', 'Views', 'Opened video', 'Clicks', 'Actions']
            
            # Parse budget file
            df_budget = pd.read_csv(BytesIO(budget_content), sep='\t')
            df_budget.columns = ['date', 'Spent budget']
            
            # Parse date - format "DD Mon YYYY"
            df_views['date'] = pd.to_datetime(df_views['date'], format='%d %b %Y')
            df_budget['date'] = pd.to_datetime(df_budget['date'], format='%d %b %Y')
            
            # Parse budget - European decimal (comma = decimal point)
            df_budget['Spent budget'] = (
                df_budget['Spent budget']
                .astype(str)
                .str.replace(',', '.')
                .astype(float)
            )
            
            # Merge by date
            df_merged = pd.merge(df_views, df_budget, on='date', how='outer').fillna(0)
            
            # Convert to int for counts
            df_merged['Views'] = df_merged['Views'].astype(int)
            df_merged['Clicks'] = df_merged['Clicks'].astype(int)
            df_merged['Actions'] = df_merged['Actions'].astype(int)
            
            # Convert to list of dicts
            data = []
            for _, row in df_merged.iterrows():
                data.append({
                    'stat_date': row['date'].date(),
                    'impressions': row['Views'],
                    'clicks': row['Clicks'],
                    'conversions': row['Actions'],
                    'spend': row['Spent budget'],
                })
            
            return {
                'success': True,
                'format': 'telegram_ads_dual_files',
                'data': data,
                'row_count': len(data),
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'format': 'unknown',
                'data': [],
            }
    
    def merge_overview_and_daily(self, overview_data: List[Dict], daily_data: List[Dict]) -> List[Dict]:
        """
        Merge 2 file CSV (overview + daily) thành 1 dataset.
        """
        merged = {}
        
        # Index daily data by date
        for item in daily_data:
            date_key = str(item.get('stat_date'))
            if date_key not in merged:
                merged[date_key] = {
                    'stat_date': item.get('stat_date'),
                    'impressions': 0,
                    'clicks': 0,
                    'conversions': 0,
                    'spend': 0,
                }
            
            merged[date_key]['impressions'] += item.get('impressions', 0)
            merged[date_key]['clicks'] += item.get('clicks', 0)
            merged[date_key]['conversions'] += item.get('conversions', 0)
            merged[date_key]['spend'] += item.get('spend', 0)
        
        return list(merged.values())
