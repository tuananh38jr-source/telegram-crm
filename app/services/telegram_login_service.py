"""
Telegram Ads Web Login Service
===============================
Login Telegram Ads truc tiep tu CRM web bang Playwright headless.
User quet QR code bang app Telegram tren dien thoai.
Server luu TAT CA cookies (ke ca httpOnly) ma bookmarklet khong the bat duoc.

Flow:
    1. Server mo Playwright headless -> chup QR code
    2. User quet QR code bang app Telegram
    3. Server phat hien login thanh cong -> luu cookies
    4. Auto-export hoat dong binh thuong
"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
COOKIES_DIR = BASE_DIR / "telegram_ads_cookies"
SCREENSHOTS_DIR = BASE_DIR / "telegram_ads_login_screenshots"
ADS_URL = "https://ads.telegram.org"

# Active login sessions (in-memory)
_sessions: dict = {}


def safe_filename(name: str) -> str:
    """Convert ten thanh safe string cho file/folder."""
    return re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')


async def start_login_session(account_name: str) -> dict:
    """
    Launch headless Playwright, navigate to ads.telegram.org,
    capture QR code screenshot for the user to scan.
    """
    session_id = str(uuid.uuid4())[:8]

    # Cleanup any previous session for this account
    for sid, sess in list(_sessions.items()):
        if sess.get('account_name') == account_name:
            await cancel_session(sid)

    COOKIES_DIR.mkdir(exist_ok=True)
    SCREENSHOTS_DIR.mkdir(exist_ok=True)

    session = {
        'id': session_id,
        'account_name': account_name,
        'status': 'starting',
        'created_at': datetime.now(),
        'browser': None,
        'context': None,
        'page': None,
        'playwright': None,
        'qr_path': str(SCREENSHOTS_DIR / f"{session_id}_qr.png"),
        'error': None,
        'cookies': None,
    }
    _sessions[session_id] = session

    try:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        session['playwright'] = pw

        browser = await pw.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-setuid-sandbox',
            ],
        )
        session['browser'] = browser

        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/125.0.0.0 Safari/537.36'
            ),
            locale='en-US',
        )
        session['context'] = context

        page = await context.new_page()
        session['page'] = page

        await page.goto(ADS_URL, wait_until='networkidle', timeout=30000)
        # Extra wait for QR code rendering
        await asyncio.sleep(3)

        # --- Capture QR code ---
        qr_captured = await _capture_qr_code(page, session['qr_path'])

        if qr_captured:
            session['status'] = 'qr_ready'
        else:
            session['status'] = 'page_loaded'

        logger.info(
            f"[Login] Session {session_id} for '{account_name}': "
            f"status={session['status']}"
        )

        return {
            'session_id': session_id,
            'status': session['status'],
            'account_name': account_name,
        }

    except ImportError:
        error = (
            "Playwright chua duoc cai tren server. "
            "Can: pip install playwright && playwright install chromium"
        )
        session['status'] = 'error'
        session['error'] = error
        return {'session_id': session_id, 'status': 'error', 'error': error}

    except Exception as e:
        error_msg = f"Khong the mo ads.telegram.org: {str(e)}"
        session['status'] = 'error'
        session['error'] = error_msg
        logger.error(f"[Login] Error starting session: {e}")
        return {'session_id': session_id, 'status': 'error', 'error': error_msg}


async def _capture_qr_code(page, qr_path: str) -> bool:
    """
    Try multiple strategies to capture the QR code image.
    Returns True if QR code was captured, False otherwise.
    """
    from playwright.async_api import TimeoutError as PwTimeout

    # Strategy 1: Known QR code selectors
    qr_selectors = [
        '.qr-code',
        '[class*="qr-code"]',
        '[class*="qrcode"]',
        '[class*="QRCode"]',
        '.tgme_qr',
        'img[alt*="QR"]',
        'img[src*="qr"]',
        'canvas',
        'svg[class*="qr"]',
        '#qr-code',
        '.login-qr',
        '[data-testid="qr"]',
    ]

    for selector in qr_selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=1000):
                await el.screenshot(path=qr_path)
                size = os.path.getsize(qr_path)
                if size > 500:
                    logger.info(f"[Login] QR captured via selector: {selector} ({size}b)")
                    return True
        except (PwTimeout, Exception):
            continue

    # Strategy 2: Find QR inside modal/dialog
    dialog_selectors = [
        '.modal', '.dialog', '[role="dialog"]',
        '[class*="modal"]', '[class*="popup"]',
        '[class*="login"]', '[class*="auth"]',
    ]

    for selector in dialog_selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=1000):
                await el.screenshot(path=qr_path)
                size = os.path.getsize(qr_path)
                if size > 500:
                    logger.info(f"[Login] QR captured via dialog: {selector} ({size}b)")
                    return True
        except (PwTimeout, Exception):
            continue

    # Strategy 3: Look for any large image that could be a QR code
    try:
        images = page.locator('img')
        count = await images.count()
        for i in range(count):
            img = images.nth(i)
            try:
                if await img.is_visible(timeout=500):
                    box = await img.bounding_box()
                    if box and box['width'] > 120 and box['height'] > 120:
                        await img.screenshot(path=qr_path)
                        size = os.path.getsize(qr_path)
                        if size > 500:
                            logger.info(f"[Login] QR captured via large img ({size}b)")
                            return True
            except (PwTimeout, Exception):
                continue
    except Exception:
        pass

    # Strategy 4: Full page screenshot as last resort
    try:
        await page.screenshot(path=qr_path, full_page=False)
        size = os.path.getsize(qr_path)
        if size > 1000:
            logger.info(f"[Login] Full page screenshot saved ({size}b)")
            return True
    except Exception as e:
        logger.warning(f"[Login] Full page screenshot failed: {e}")

    return False


async def check_auth_status(session_id: str) -> dict:
    """
    Check if the user has completed Telegram authentication.
    Detects URL changes, dashboard elements, and session cookies.
    """
    session = _sessions.get(session_id)
    if not session:
        return {'status': 'not_found', 'error': 'Session khong ton tai'}

    if session['status'] in ('authenticated', 'error', 'cancelled'):
        result = {'status': session['status']}
        if session.get('cookies'):
            result['cookies_count'] = len(session['cookies'])
        if session.get('error'):
            result['error'] = session['error']
        return result

    page = session.get('page')
    context = session.get('context')
    if not page or not context:
        return {'status': 'error', 'error': 'Browser session khong con'}

    try:
        current_url = page.url

        # Check 1: URL changed away from login page
        if 'ads.telegram.org' in current_url and '/account' in current_url:
            return await _on_authenticated(session)

        # Check 2: Dashboard elements present
        dashboard_indicators = [
            '[class*="campaign"]',
            '[class*="dashboard"]',
            '[class*="statistics"]',
            '[class*="overview"]',
            'a[href*="/account/"]',
            '[class*="header-user"]',
            '[class*="sidebar"]',
        ]
        for selector in dashboard_indicators:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=500):
                    return await _on_authenticated(session)
            except Exception:
                continue

        # Check 3: Session cookies exist
        cookies = await context.cookies()
        has_session_cookie = any(
            c['name'] in (
                'stel_ssid', 'stel_tsession', 'stel_token',
                'sessionid', 'stel_web_session',
            )
            for c in cookies
        )
        if has_session_cookie:
            return await _on_authenticated(session)

        # Check 4: Page title changed from login
        title = await page.title()
        if title and 'Telegram Ads' in title and 'Log in' not in title.lower():
            # Might be on dashboard already
            if len(cookies) > 3:
                return await _on_authenticated(session)

        return {'status': 'waiting'}

    except Exception as e:
        logger.warning(f"[Login] Error checking auth status: {e}")
        error_str = str(e).lower()
        if 'target closed' in error_str or 'been closed' in error_str:
            session['status'] = 'error'
            session['error'] = 'Trinh duyet da dong'
            return {'status': 'error', 'error': 'Trinh duyet da dong'}
        return {'status': 'waiting'}


async def _on_authenticated(session: dict) -> dict:
    """Handle successful authentication: save cookies + update DB."""
    context = session['context']
    account_name = session['account_name']

    try:
        cookies = await context.cookies()
        session['cookies'] = cookies
        session['status'] = 'authenticated'

        # Save cookies to file
        safe_name = safe_filename(account_name)
        cookie_path = COOKIES_DIR / f"{safe_name}.json"
        cookie_path.write_text(
            json.dumps(cookies, indent=2, ensure_ascii=False),
            encoding='utf-8',
        )

        # Try to detect organizations
        orgs = []
        try:
            page = session['page']
            orgs = await _detect_orgs_headless(page)
            if orgs:
                orgs_path = COOKIES_DIR / f"{safe_name}_orgs.json"
                orgs_data = {
                    "account_name": account_name,
                    "organizations": orgs,
                    "detected_at": datetime.now().isoformat(),
                }
                orgs_path.write_text(
                    json.dumps(orgs_data, indent=2, ensure_ascii=False),
                    encoding='utf-8',
                )
        except Exception as e:
            logger.warning(f"[Login] Could not detect orgs: {e}")

        # Update account in database
        _update_account_db(account_name, cookie_path, orgs)

        logger.info(
            f"[Login] Authenticated: '{account_name}' "
            f"({len(cookies)} cookies, {len(orgs)} orgs)"
        )

        # Cleanup browser
        await _close_browser(session)

        return {
            'status': 'authenticated',
            'cookies_count': len(cookies),
            'orgs_count': len(orgs),
        }

    except Exception as e:
        session['status'] = 'error'
        session['error'] = f'Loi khi luu cookies: {str(e)}'
        return {'status': 'error', 'error': session['error']}


async def _detect_orgs_headless(page) -> list:
    """
    Detect organizations from the dashboard dropdown (headless mode).
    """
    import asyncio
    orgs = []

    try:
        # Try to click on username/account area to open dropdown
        username_selectors = [
            '[class*="user"]',
            '[class*="account"]',
            '[class*="profile"]',
            '[class*="avatar"]',
            '[class*="header"] [class*="name"]',
        ]

        dropdown_opened = False
        for selector in username_selectors:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    dropdown_opened = True
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue

        if not dropdown_opened:
            return orgs

        # Read dropdown content
        dropdown_selectors = [
            '[class*="dropdown"]',
            '[class*="popup"]',
            '[class*="menu"]',
            '[role="menu"]',
            '[role="listbox"]',
        ]

        for selector in dropdown_selectors:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=2000):
                    text = await el.inner_text()
                    if text and ('Edit Account' in text or 'Log Out' in text or 'Help' in text):
                        lines = [l.strip() for l in text.split('\n') if l.strip()]
                        skip_items = {
                            'Edit Account Info', 'Create a new Organization',
                            'Create new Organization', 'Help', 'Log Out',
                            'Log out', 'Settings',
                        }
                        for line in lines:
                            clean = line.strip()
                            if clean and clean not in skip_items and clean not in orgs:
                                if len(clean) > 1 and not clean.startswith(('⚙', '🔧')):
                                    orgs.append(clean)
                        break
            except Exception:
                continue

    except Exception as e:
        logger.warning(f"[Login] detect_orgs error: {e}")

    return orgs


def _update_account_db(account_name: str, cookie_path, orgs: list = None):
    """Update TelegramAdsAccount record in the database."""
    try:
        from app.database import SessionLocal
        from app.models import TelegramAdsAccount

        db = SessionLocal()
        try:
            account = db.query(TelegramAdsAccount).filter(
                TelegramAdsAccount.name == account_name
            ).first()
            if account:
                account.login_status = 'logged_in'
                account.cookie_path = str(cookie_path)
                if orgs:
                    account.orgs_count = len(orgs)
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[Login] Could not update DB for '{account_name}': {e}")


async def cancel_session(session_id: str) -> dict:
    """Cancel an active login session and free resources."""
    session = _sessions.get(session_id)
    if not session:
        return {'success': False, 'error': 'Session not found'}

    session['status'] = 'cancelled'
    await _close_browser(session)

    # Clean up screenshot
    qr_path = Path(session.get('qr_path', ''))
    if qr_path.exists():
        try:
            qr_path.unlink()
        except Exception:
            pass

    del _sessions[session_id]
    return {'success': True, 'message': 'Session da huy'}


async def _close_browser(session: dict):
    """Safely close Playwright browser and context."""
    for key in ('browser', 'context', 'page'):
        obj = session.get(key)
        if obj:
            try:
                if key == 'page':
                    await obj.close()
                elif key == 'context':
                    await obj.close()
                else:
                    await obj.close()
            except Exception:
                pass
            session[key] = None

    pw = session.get('playwright')
    if pw:
        try:
            await pw.stop()
        except Exception:
            pass
        session['playwright'] = None


def get_session(session_id: str) -> dict | None:
    """Get session info (without sensitive browser objects)."""
    session = _sessions.get(session_id)
    if not session:
        return None
    return {
        'id': session['id'],
        'account_name': session['account_name'],
        'status': session['status'],
        'error': session.get('error'),
        'created_at': session['created_at'].isoformat(),
    }


def get_qr_path(session_id: str) -> str | None:
    """Get the QR code screenshot path for a session."""
    session = _sessions.get(session_id)
    if not session:
        return None
    qr_path = session.get('qr_path')
    if qr_path and os.path.exists(qr_path):
        return qr_path
    return None


async def cleanup_expired_sessions():
    """Remove sessions older than 10 minutes."""
    now = datetime.now()
    expired = []
    for sid, session in _sessions.items():
        age = (now - session['created_at']).total_seconds()
        if age > 600:  # 10 minutes
            expired.append(sid)

    for sid in expired:
        session = _sessions.get(sid)
        if session:
            await _close_browser(session)
            qr_path = Path(session.get('qr_path', ''))
            if qr_path.exists():
                try:
                    qr_path.unlink()
                except Exception:
                    pass
            del _sessions[sid]

    if expired:
        logger.info(f"[Login] Cleaned up {len(expired)} expired sessions")
