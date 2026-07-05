"""
Telegram Ads Auto-Export Script (v2)
=====================================
Tu dong export CSV tu ads.telegram.org cho nhieu accounts va organizations.

Cau truc thuc te:
  Account chinh (VD: "ERIC james")
    ├── Org 1: "ERIC james"
    ├── Org 2: "global gold traders"
    ├── Org 3: "Million GOLD"
    ├── Org 4: "Henrytrading - DNAprofit"
    └── Org 5: "ADS 1"

Moi org deu co trang Statistics rieng, can export rieng.

Cach su dung:
  1. LAN DAU - Login va luu cookie + detect orgs:
     python telegram_ads_auto_export.py --login --account "ERIC james"

  2. TU DONG export (dung cookie da luu, lap qua tat ca orgs):
     python telegram_ads_auto_export.py --account "ERIC james"
     python telegram_ads_auto_export.py --all

  3. Them account moi:
     python telegram_ads_auto_export.py --login --account "Thu Ha"
"""

import os
import sys
import json
import time
import re
import argparse
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("[ERROR] Playwright chua duoc cai.")
    print("  pip install playwright && python -m playwright install chromium")
    sys.exit(1)


# ============================================================
# CONFIG
# ============================================================
BASE_DIR = Path(__file__).parent
COOKIES_DIR = BASE_DIR / "telegram_ads_cookies"
ACCOUNTS_DIR = BASE_DIR / "telegram_ads_accounts"
ADS_URL = "https://ads.telegram.org"
STATS_URL = f"{ADS_URL}/account/stats"
DOWNLOAD_TIMEOUT = 60000   # 60s cho download CSV
NAV_TIMEOUT = 20000        # 20s cho navigation


def ensure_dirs():
    COOKIES_DIR.mkdir(exist_ok=True)
    ACCOUNTS_DIR.mkdir(exist_ok=True)


def safe_filename(name: str) -> str:
    """Convert ten thanh safe string cho file/folder."""
    return re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')


def get_cookie_path(account_name: str) -> Path:
    return COOKIES_DIR / f"{safe_filename(account_name)}.json"


def get_orgs_path(account_name: str) -> Path:
    """File luu danh sach organizations cua account."""
    return COOKIES_DIR / f"{safe_filename(account_name)}_orgs.json"


def get_account_dir(account_name: str) -> Path:
    account_dir = ACCOUNTS_DIR / safe_filename(account_name)
    account_dir.mkdir(exist_ok=True, parents=True)
    return account_dir


def get_org_dir(account_name: str, org_name: str) -> Path:
    """Thu muc luu CSV cho 1 organization cu the."""
    org_dir = get_account_dir(account_name) / safe_filename(org_name)
    org_dir.mkdir(exist_ok=True, parents=True)
    return org_dir


# ============================================================
# LOGIN + DETECT ORGANIZATIONS
# ============================================================
def login_and_save(account_name: str):
    """
    Mo browser, user login thu cong, detect organizations, luu cookies.
    """
    cookie_path = get_cookie_path(account_name)
    orgs_path = get_orgs_path(account_name)

    print(f"\n{'='*60}")
    print(f"  LOGIN: {account_name}")
    print(f"{'='*60}")
    print(f"\n  Browser se mo ra. Hay login vao ads.telegram.org.")
    print(f"  Sau khi thay dashboard (trang Ads), quay lai day va nhan ENTER.")
    print(f"  Script se tu dong detect tat ca organizations.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        # Mo ads.telegram.org
        page.goto(ADS_URL, wait_until="domcontentloaded")
        print("  [INFO] Da mo ads.telegram.org")
        print("  [INFO] Hay dang nhap bang Telegram account...")

        # Cho user login
        input("\n  >>> Nhan ENTER sau khi da login thanh cong <<<\n")

        # Luu cookies + storage_state (wrapped format)
        cookies = context.cookies()
        try:
            ss = context.storage_state()
        except Exception:
            ss = None
        save_obj = {
            'cookies': cookies,
            'storage_state': ss,
            'captured_at': datetime.now().isoformat(),
            'source': 'cli_login',
        }
        cookie_path.write_text(json.dumps(save_obj, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f"  [OK] Da luu {len(cookies)} cookies")

        # Detect organizations bang cach mo dropdown
        print("  [INFO] Dang detect organizations...")
        orgs = detect_organizations(page)

        if orgs:
            orgs_data = {
                "account_name": account_name,
                "organizations": orgs,
                "detected_at": datetime.now().isoformat(),
            }
            orgs_path.write_text(json.dumps(orgs_data, indent=2, ensure_ascii=False), encoding='utf-8')
            print(f"  [OK] Tim thay {len(orgs)} organizations:")
            for i, org in enumerate(orgs, 1):
                print(f"       {i}. {org}")
        else:
            print("  [WARN] Khong detect duoc orgs. Co the chi co 1 account duy nhat.")
            orgs_data = {
                "account_name": account_name,
                "organizations": [account_name],
                "detected_at": datetime.now().isoformat(),
            }
            orgs_path.write_text(json.dumps(orgs_data, indent=2, ensure_ascii=False), encoding='utf-8')

        # Navigate to stats page to confirm access
        print("  [INFO] Kiem tra truy cap trang Statistics...")
        page.goto(STATS_URL, wait_until="domcontentloaded")
        time.sleep(2)

        if "/account/stats" in page.url:
            print("  [OK] Da truy cap duoc trang Statistics")
        else:
            print(f"  [WARN] URL hien tai: {page.url}")
            print("  [WARN] Co the can login lai neu bi redirect")

        print(f"\n  [DONE] Account '{account_name}' da san sang!")
        print(f"  [TIP] Chay: python telegram_ads_auto_export.py --account \"{account_name}\"")
        print()
        browser.close()


def detect_organizations(page) -> list:
    """
    Mo account dropdown va doc danh sach organizations.
    Dua tren screenshot: dropdown hien thi ten org voi icon mau.
    """
    orgs = []

    try:
        # Tim va click vao username/account area de mo dropdown
        # Tren giao dien: "ERIC james ▼" o goc phai tren
        username_selectors = [
            # Click vao phan tu chua username o goc phai
            '.header-user',
            '[class*="user"]',
            '[class*="account"]',
            '[class*="profile"]',
            '[class*="avatar"]',
        ]

        dropdown_opened = False
        for selector in username_selectors:
            try:
                page.click(selector, timeout=3000)
                dropdown_opened = True
                time.sleep(1)
                break
            except:
                continue

        if not dropdown_opened:
            # Thu click bang text - tim ten bat ky o goc phai
            try:
                # Tim tat ca cac elements chua text co the la username
                elements = page.query_selector_all('[class*="dropdown"], [class*="menu"], [class*="nav"]')
                for el in elements:
                    text = el.inner_text().strip()
                    if text and len(text) < 50:
                        el.click()
                        dropdown_opened = True
                        time.sleep(1)
                        break
            except:
                pass

        if not dropdown_opened:
            # Last resort: click vao goc phai tren cua trang
            page.click('body', position={"x": 1300, "y": 25})
            time.sleep(1)

        # Doc noi dung dropdown
        # Dropdown chua: Edit Account Info, Create new Organization, Help, Log Out
        # Va danh sach orgs: ERIC james, global gold traders, Million GOLD, ...
        dropdown_selectors = [
            '[class*="dropdown"]',
            '[class*="popup"]',
            '[class*="menu"]',
            '[role="menu"]',
            '[role="listbox"]',
            '.popover',
        ]

        for selector in dropdown_selectors:
            try:
                elements = page.query_selector_all(selector)
                for el in elements:
                    text = el.inner_text()
                    if text and ('Edit Account' in text or 'Log Out' in text or 'Help' in text):
                        # Tim thay dropdown menu, parse orgs
                        lines = [l.strip() for l in text.split('\n') if l.strip()]

                        # Skip known menu items
                        skip_items = [
                            'Edit Account Info', 'Create a new Organization',
                            'Help', 'Log Out', 'Log out',
                        ]

                        for line in lines:
                            if line not in skip_items and len(line) > 1:
                                # Co the la org name
                                # Loai bo cac ky tu dac biet (icon initials)
                                clean_name = line.strip()
                                if clean_name and clean_name not in orgs:
                                    orgs.append(clean_name)

                        if orgs:
                            return orgs
            except:
                continue

        # Neu khong tim duoc qua selectors, thu parse toan bo page text
        if not orgs:
            page_text = page.inner_text('body')
            # Tim cac dong co the la org names trong dropdown
            # Thuong nam giua "Log Out" va cuoi dropdown
            pass

    except Exception as e:
        print(f"  [WARN] Loi khi detect orgs: {e}")

    return orgs


# ============================================================
# SWITCH ORGANIZATION
# ============================================================
def switch_organization(page, org_name: str) -> bool:
    """
    Chuyen sang organization khac qua dropdown.
    """
    try:
        # Mo dropdown
        username_selectors = [
            '.header-user',
            '[class*="user"]',
            '[class*="account"]',
            '[class*="profile"]',
            '[class*="avatar"]',
        ]

        for selector in username_selectors:
            try:
                page.click(selector, timeout=3000)
                time.sleep(1)
                break
            except:
                continue

        # Click vao org name trong dropdown
        try:
            page.click(f'text="{org_name}"', timeout=5000)
            time.sleep(3)  # Cho page reload
            return True
        except:
            # Thu click mot cach khac
            links = page.query_selector_all('a, button, [role="option"], [role="menuitem"]')
            for link in links:
                try:
                    text = link.inner_text().strip()
                    if text == org_name:
                        link.click()
                        time.sleep(3)
                        return True
                except:
                    continue

        return False

    except Exception as e:
        print(f"  [WARN] Khong the switch sang org '{org_name}': {e}")
        return False


# ============================================================
# EXPORT CSV FROM STATISTICS PAGE
# ============================================================
def export_statistics_csvs(page, account_name: str, org_name: str) -> dict:
    """
    Export 2 file CSV tu trang Statistics:
    1. Views/Clicks/Actions CSV
    2. Spent budget CSV
    
    Tren giao dien co 2 section rieng, moi section co 1 nut CSV download.
    """
    org_dir = get_org_dir(account_name, org_name)
    date_str = datetime.now().strftime("%Y%m%d")
    safe_org = safe_filename(org_name)

    result = {
        "org": org_name,
        "views_csv": False,
        "budget_csv": False,
        "files": [],
    }

    try:
        # Navigate to Statistics page
        page.goto(STATS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        time.sleep(3)

        if "/account/stats" not in page.url:
            print(f"    [WARN] Khong truy cap duoc stats page. URL: {page.url}")
            return result

        # Click "Days" neu chua selected
        try:
            page.click('text="Days"', timeout=3000)
            time.sleep(1)
        except:
            pass  # Co the da o che do Days

        # ============================================================
        # EXPORT 1: Views / Clicks / Actions CSV
        # ============================================================
        print(f"    [1/2] Dang export Views/Clicks/Actions CSV...")

        # Tim nut CSV download trong section "Views / Clicks / Actions"
        # Tren giao dien: co icon CSV download ben phai moi chart section
        views_downloaded = download_csv_from_section(
            page, org_dir, f"{safe_org}_{date_str}_days.csv",
            section_keywords=["Views", "Clicks", "Actions"],
        )
        result["views_csv"] = views_downloaded
        if views_downloaded:
            result["files"].append(f"{safe_org}_{date_str}_days.csv")

        # ============================================================
        # EXPORT 2: Spent budget CSV
        # ============================================================
        print(f"    [2/2] Dang export Spent Budget CSV...")

        # Cuon xuong de thay section Spent budget
        page.evaluate("window.scrollBy(0, 600)")
        time.sleep(1)

        budget_downloaded = download_csv_from_section(
            page, org_dir, f"{safe_org}_budget_{date_str}_days.csv",
            section_keywords=["Spent budget", "budget", "TON"],
        )
        result["budget_csv"] = budget_downloaded
        if budget_downloaded:
            result["files"].append(f"{safe_org}_budget_{date_str}_days.csv")

    except PlaywrightTimeout as e:
        print(f"    [ERROR] Timeout: {e}")
        try:
            ss = org_dir / f"error_{datetime.now().strftime('%H%M%S')}.png"
            page.screenshot(path=str(ss))
            print(f"    [DEBUG] Screenshot: {ss}")
        except:
            pass

    except Exception as e:
        print(f"    [ERROR] {e}")

    return result


def download_csv_from_section(page, save_dir: Path, filename: str, section_keywords: list) -> bool:
    """
    Tim va click nut CSV download trong 1 section cu the cua trang Statistics.
    
    Tren giao dien ads.telegram.org:
    - Moi chart section co 1 icon CSV download nho ben phai
    - Can tim dung section dua tren keywords (Views, Spent budget, etc.)
    """
    try:
        # Cach 1: Tim tat ca cac nut/icon CSV tren trang
        csv_elements = page.query_selector_all('[class*="csv"], [class*="download"], [title*="CSV"], [title*="csv"]')

        if csv_elements:
            for i, el in enumerate(csv_elements):
                try:
                    # Kiem tra xem element nay thuoc section nao
                    # Bang cach kiem tra parent element
                    parent = el.evaluate_handle("el => el.closest('section, [class*=chart], [class*=section], div')")
                    parent_text = parent.evaluate("el => el.textContent || ''")

                    is_target = any(kw.lower() in parent_text.lower() for kw in section_keywords)

                    if is_target or i == 0:  # Fallback: lay CSV dau tien
                        with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl_info:
                            el.click()

                        download = dl_info.value
                        save_path = save_dir / filename
                        download.save_as(str(save_path))
                        print(f"    [OK] Da luu: {filename}")
                        return True
                except:
                    continue

        # Cach 2: Tim CSV icon bang cach khac
        # Tren trang co text "CSV" hoac icon download
        all_links = page.query_selector_all('a, button, [role="button"]')
        for link in all_links:
            try:
                text = link.inner_text().strip().upper()
                title = (link.get_attribute('title') or '').upper()

                if text == 'CSV' or 'CSV' in title:
                    with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl_info:
                        link.click()

                    download = dl_info.value
                    save_path = save_dir / filename
                    download.save_as(str(save_path))
                    print(f"    [OK] Da luu: {filename}")
                    return True
            except:
                continue

        # Cach 3: Dung keyboard shortcut hoac direct download URL
        # Thuong thi CSV download icon la SVG, can click vao no
        svg_buttons = page.query_selector_all('svg, [class*="icon"]')
        for btn in svg_buttons:
            try:
                parent = btn.evaluate_handle("el => el.parentElement")
                parent_text = parent.evaluate("el => el.getAttribute('title') || el.getAttribute('aria-label') || ''")
                if 'csv' in parent_text.lower() or 'download' in parent_text.lower():
                    with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl_info:
                        parent.click()

                    download = dl_info.value
                    save_path = save_dir / filename
                    download.save_as(str(save_path))
                    print(f"    [OK] Da luu: {filename}")
                    return True
            except:
                continue

        print(f"    [WARN] Khong tim thay nut download CSV cho section {section_keywords}")
        return False

    except Exception as e:
        print(f"    [WARN] Loi khi download CSV: {e}")
        return False


# ============================================================
# EXPORT FULL FLOW (ALL ORGS)
# ============================================================
def export_account(account_name: str, headless: bool = True):
    """
    Export CSV cho tat ca organizations cua 1 account.
    """
    cookie_path = get_cookie_path(account_name)
    orgs_path = get_orgs_path(account_name)

    if not cookie_path.exists():
        print(f"\n  [ERROR] Chua co cookie cho '{account_name}'.")
        print(f"  [TIP] Chay: python telegram_ads_auto_export.py --login --account \"{account_name}\"")
        return

    # Load cookies — handle multiple file formats:
    # - Bare array (old): [{cookie1}, {cookie2}, ...]
    # - Wrapped dict (login service): {"cookies": [...], "storage_state": {...}, ...}
    # - Bookmarklet dict: {"cookies": "raw_string", ...}
    raw_data = json.loads(cookie_path.read_text(encoding='utf-8'))
    storage_state = None
    if isinstance(raw_data, dict):
        cookies = raw_data.get('cookies', [])
        storage_state = raw_data.get('storage_state')
        # Bookmarklet format: cookies is a string, not a list
        if isinstance(cookies, str):
            print(f"  [WARN] Cookie file is bookmarklet format (string), not usable.")
            print(f"  [TIP] Re-login via web login flow.")
            return
        print(f"  [INFO] Cookie file: wrapped format, {len(cookies)} cookies extracted.")
    else:
        cookies = raw_data
        print(f"  [INFO] Cookie file: bare array format, {len(cookies)} cookies.")

    if not cookies:
        print(f"  [ERROR] Cookie file is empty or invalid.")
        return

    # Load organizations list
    orgs = [account_name]  # Default: chi co 1 org
    if orgs_path.exists():
        orgs_data = json.loads(orgs_path.read_text(encoding='utf-8'))
        orgs = orgs_data.get("organizations", [account_name])

    print(f"\n{'='*60}")
    print(f"  EXPORT: {account_name}")
    print(f"  Organizations: {len(orgs)}")
    for i, org in enumerate(orgs, 1):
        print(f"    {i}. {org}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)

        # Prefer storage_state (restores cookies + localStorage together)
        if storage_state:
            print(f"  [INFO] Restoring session via storage_state (best method).")
            # Write temp storage_state file for Playwright
            ss_path = cookie_path.with_suffix('.storage_state.json')
            ss_path.write_text(json.dumps(storage_state, indent=2, ensure_ascii=False), encoding='utf-8')
            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                locale="en-US",
                accept_downloads=True,
                storage_state=str(ss_path),
            )
            # Clean up temp file
            try:
                ss_path.unlink()
            except Exception:
                pass
        else:
            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                locale="en-US",
                accept_downloads=True,
            )
            context.add_cookies(cookies)

        page = context.new_page()

        # Kiem tra login
        print(f"\n  [1] Dang truy cap ads.telegram.org...")
        page.goto(ADS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        time.sleep(3)

        # Check if still logged in
        current_url = page.url.lower()
        if "login" in current_url or "auth" in current_url or "fragment" in current_url:
            # Co the bi redirect den Fragment (login page)
            # Thu van access stats page
            page.goto(STATS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            time.sleep(2)

            if "/account/stats" not in page.url:
                print(f"  [ERROR] Cookie da het han. URL: {page.url}")
                print(f"  [TIP] Chay lai: python telegram_ads_auto_export.py --login --account \"{account_name}\"")
                browser.close()
                return

        print(f"  [OK] Da dang nhap. URL: {page.url}")

        # Export cho tung organization
        total_success = 0
        results = []

        for i, org in enumerate(orgs, 1):
            print(f"\n  [{i+1}/{len(orgs)+1}] Export org: {org}")

            if i > 1:
                # Switch sang org (org dau tien la default)
                switched = switch_organization(page, org)
                if not switched:
                    print(f"    [WARN] Khong the switch sang '{org}', bo qua")
                    continue
                time.sleep(2)

            # Export CSVs
            result = export_statistics_csvs(page, account_name, org)
            results.append(result)

            if result["views_csv"] or result["budget_csv"]:
                total_success += 1
                status = []
                if result["views_csv"]:
                    status.append("Views OK")
                if result["budget_csv"]:
                    status.append("Budget OK")
                print(f"    => {', '.join(status)}")
            else:
                print(f"    => FAIL (khong export duoc)")

            # Delay giua cac orgs de tranh rate limit
            if i < len(orgs):
                time.sleep(2)

        # Cap nhat cookies + storage_state (wrapped format)
        new_cookies = context.cookies()
        try:
            new_storage_state = context.storage_state()
        except Exception:
            new_storage_state = None
        save_obj = {
            'cookies': new_cookies,
            'storage_state': new_storage_state,
            'updated_at': datetime.now().isoformat(),
            'source': 'export_refresh',
        }
        cookie_path.write_text(json.dumps(save_obj, indent=2, ensure_ascii=False), encoding='utf-8')

        browser.close()

    # Tong ket
    print(f"\n{'='*60}")
    print(f"  TONG KET: {account_name}")
    print(f"{'='*60}")
    for r in results:
        v = "OK" if r["views_csv"] else "x"
        b = "OK" if r["budget_csv"] else "x"
        print(f"  [{v}|{b}] {r['org']}")
    print(f"  Thanh cong: {total_success}/{len(orgs)} orgs")
    print(f"  Thu muc:    {get_account_dir(account_name)}")
    print(f"{'='*60}\n")


# ============================================================
# MULTI-ACCOUNT
# ============================================================
def get_all_accounts() -> list:
    """Lay danh sach accounts da luu cookie."""
    ensure_dirs()
    accounts = []
    seen = set()
    for f in COOKIES_DIR.glob("*.json"):
        if f.stem.endswith("_orgs"):
            continue
        name = f.stem.replace("_", " ")
        if name not in seen:
            accounts.append(name)
            seen.add(name)
    return accounts


def export_all(headless: bool = True):
    """Export tat ca accounts."""
    accounts = get_all_accounts()
    if not accounts:
        print("[WARN] Chua co account nao. Hay --login truoc.")
        return

    print(f"\n[INFO] Tim thay {len(accounts)} accounts: {', '.join(accounts)}")

    for acc in accounts:
        export_account(acc, headless=headless)


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Telegram Ads Auto-Export Tool v2 (Multi-Org Support)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Vi du:
  # Login lan dau (mo browser, login thu cong, detect orgs)
  python telegram_ads_auto_export.py --login --account "ERIC james"

  # Export tu dong (dung cookie, lap qua tat ca orgs)
  python telegram_ads_auto_export.py --account "ERIC james"

  # Export tat ca accounts
  python telegram_ads_auto_export.py --all

  # Export voi browser hien thi (de debug)
  python telegram_ads_auto_export.py --account "ERIC james" --no-headless

  # Xem danh sach accounts da login
  python telegram_ads_auto_export.py --list
        """
    )
    parser.add_argument("--login", action="store_true",
                       help="Login thu cong va luu cookies + detect orgs")
    parser.add_argument("--account", type=str,
                       help="Ten account (VD: 'ERIC james', 'Thu Ha')")
    parser.add_argument("--all", action="store_true",
                       help="Export tat ca accounts")
    parser.add_argument("--no-headless", action="store_true",
                       help="Hien thi browser (de debug)")
    parser.add_argument("--list", action="store_true",
                       help="Xem danh sach accounts da login")

    args = parser.parse_args()
    ensure_dirs()

    if args.list:
        accounts = get_all_accounts()
        if accounts:
            print(f"\n  Accounts da login ({len(accounts)}):")
            for acc in accounts:
                cookie_file = get_cookie_path(acc)
                orgs_file = get_orgs_path(acc)
                mtime = datetime.fromtimestamp(cookie_file.stat().st_mtime)

                org_count = "?"
                if orgs_file.exists():
                    orgs_data = json.loads(orgs_file.read_text(encoding='utf-8'))
                    org_count = len(orgs_data.get("organizations", []))

                print(f"    - {acc} ({org_count} orgs, cookie: {mtime.strftime('%d/%m/%Y %H:%M')})")
        else:
            print("\n  Chua co account nao. Hay chay --login truoc.")
        return

    if args.login:
        if not args.account:
            print("[ERROR] Can --account khi login")
            print("  VD: python telegram_ads_auto_export.py --login --account \"ERIC james\"")
            return
        login_and_save(args.account)
        return

    if args.all:
        export_all(headless=not args.no_headless)
        return

    if args.account:
        export_account(args.account, headless=not args.no_headless)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
