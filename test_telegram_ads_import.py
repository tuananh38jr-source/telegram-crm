"""
Test script để import 2 file CSV Telegram Ads thực tế từ Thu Hà.
"""
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Campaign, AdStat


def parse_telegram_ads_csvs(views_file: str, budget_file: str):
    """
    Parse 2 file CSV từ Telegram Ads:
    - views_file: date, Views, Opened video, Clicks, Actions
    - budget_file: date, Spent budget, TON
    
    Cả 2 đều dùng tab separator và European decimal format.
    """
    # Đọc file views
    df_views = pd.read_csv(views_file, sep='\t')
    df_views.columns = ['date', 'Views', 'Opened video', 'Clicks', 'Actions']
    
    # Đọc file budget
    df_budget = pd.read_csv(budget_file, sep='\t')
    # Column tên là "Spent budget, TON" - cần xử lý
    df_budget.columns = ['date', 'Spent budget']
    
    # Parse date
    df_views['date'] = pd.to_datetime(df_views['date'], format='%d %b %Y')
    df_budget['date'] = pd.to_datetime(df_budget['date'], format='%d %b %Y')
    
    # Parse budget - European decimal format (comma = decimal point)
    df_budget['Spent budget'] = df_budget['Spent budget'].astype(str).str.replace(',', '.').astype(float)
    
    # Merge 2 dataframes by date
    df_merged = pd.merge(df_views, df_budget, on='date', how='outer').fillna(0)
    
    # Convert to int for counts
    df_merged['Views'] = df_merged['Views'].astype(int)
    df_merged['Clicks'] = df_merged['Clicks'].astype(int)
    df_merged['Actions'] = df_merged['Actions'].astype(int)
    
    return df_merged


def import_to_crm(df: pd.DataFrame, campaign_name: str = "Thu Hà Ads"):
    """Import merged dataframe vào CRM."""
    db = SessionLocal()
    
    try:
        # Tìm hoặc tạo campaign
        campaign = db.query(Campaign).filter(Campaign.name == campaign_name).first()
        if not campaign:
            campaign = Campaign(name=campaign_name, status='active')
            db.add(campaign)
            db.flush()
        
        imported_count = 0
        for _, row in df.iterrows():
            stat_date = row['date'].date()
            
            # Check if already exists
            existing = db.query(AdStat).filter(
                AdStat.campaign_id == campaign.id,
                AdStat.stat_date == stat_date
            ).first()
            
            if existing:
                # Update
                existing.impressions = row['Views']
                existing.clicks = row['Clicks']
                existing.conversions = row['Actions']
                existing.spend = row['Spent budget']
                existing.updated_at = datetime.now()
            else:
                # Create new
                stat = AdStat(
                    campaign_id=campaign.id,
                    stat_date=stat_date,
                    impressions=row['Views'],
                    clicks=row['Clicks'],
                    conversions=row['Actions'],
                    spend=row['Spent budget'],
                )
                db.add(stat)
            
            imported_count += 1
        
        db.commit()
        print(f"Imported {imported_count} days into campaign '{campaign_name}'")
        
        # Print summary
        total_views = df['Views'].sum()
        total_clicks = df['Clicks'].sum()
        total_actions = df['Actions'].sum()
        total_spend = df['Spent budget'].sum()
        
        print(f"\nSummary:")
        print(f"  Views: {total_views:,}")
        print(f"  Clicks: {total_clicks:,}")
        print(f"  Actions: {total_actions:,}")
        print(f"  Spend: {total_spend:.2f} TON")
        
        return imported_count
        
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    views_file = r"C:\Users\hoang\Downloads\Thu_Hà_20260702_days (1).csv"
    budget_file = r"C:\Users\hoang\Downloads\Thu_Hà_budget_20260702_days (1).csv"
    
    print("Parsing CSV files...")
    df = parse_telegram_ads_csvs(views_file, budget_file)
    
    print(f"\nParsed {len(df)} days")
    print(df.head(10))
    
    print("\nImporting to CRM...")
    import_to_crm(df, campaign_name="Thu Hà Ads")
