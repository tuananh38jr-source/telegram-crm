"""
Telegram Ads Web Login Service
===============================
Login Telegram Ads truc tiep tu CRM web bang Playwright headless.
Flow thuc te (tu screenshots):
    1. Server mo ads.telegram.org -> click "Log In to Start Advertising"
    2. Nhap so dien thoai -> click "Next"
    3. Telegram gui tin nhan xac nhan den app Telegram tren dien thoai
    4. User mo app Telegram -> bam xac nhan
    5. Server phat hien login thanh cong -> luu TAT CA cookies (ke ca httpOnly)

KHONG dung QR code — Telegram Ads dung phone + Telegram message confirmation.
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


async def start_login_session(account_name: str, phone: str = '') -> dict:
    """
    Launch headless Playwright, navigate to ads.telegram.org,
    click Log In, enter phone number, click Next.
    User then confirms via Telegram app.

    Args:
        account_name: CRM account name to associate cookies with
        phone: Phone number in international format (e.g. +84896399862)
    """
    session_id = str(uuid.uuid4())[:8]

    # Cleanup any previous session for this account
    for sid, sess in list(_sessions.items()):
        if sess.get('account_name') == account_name:
            await cancel_session(sid)

    COOKIES_DIR.mkdir(exist_ok=True)
    SCREENSHOTS_DIR.mkdir(exist_ok=True)

    screenshot_path = str(SCREENSHOTS_DIR / f"{session_id}_login.png")

    session = {
        'id': session_id,
        'account_name': account_name,
        'phone': phone,
        'status': 'starting',
        'created_at': datetime.now(),
        'browser': None,
        'context': None,
        'page': None,
        'playwright': None,
        'screenshot_path': screenshot_path,
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

        # Navigate to ads.telegram.org
        await page.goto(ADS_URL, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)

        # Check if already logged in (dashboard visible)
        already_logged_in = await _check_already_logged_in(page, context)
        if already_logged_in:
            logger.info(f"[Login] '{account_name}' already logged in!")
            result = await _on_authenticated(session)
            return {
                'session_id': session_id,
                'status': 'authenticated',
                'account_name': account_name,
                'cookies_count': result.get('cookies_count', 0),
                'orgs_count': result.get('orgs_count', 0),
            }

        # Click "Log In to Start Advertising" button
        login_clicked = await _click_login_button(page)

        if login_clicked:
            await asyncio.sleep(2)

        # Take screenshot of current state (login modal or page)
        await _take_screenshot(page, screenshot_path)

        # Enter phone number if provided
        if phone:
            phone_result = await _enter_phone_number(page, phone)
            session['status'] = phone_result['status']

            if phone_result['status'] == 'phone_submitted':
                # Take screenshot of the "waiting for confirmation" state
                await asyncio.sleep(1)
                await _take_screenshot(page, screenshot_path)
        else:
            # No phone provided, just show the login form
            session['status'] = 'login_form_ready'

        logger.info(
            f"[Login] Session {session_id} for '{account_name}': "
            f"status={session['status']}, phone={phone}"
        )

        return {
            'session_id': session_id,
            'status': session['status'],
            'account_name': account_name,
            'phone': phone,
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


async def _check_already_logged_in(page, context) -> bool:
    """Check if the browser is already logged in (dashboard visible)."""
    try:
        current_url = page.url
        if '/account' in current_url and 'ads.telegram.org' in current_url:
            return True

        # Check for dashboard indicators
        indicators = [
            'text=Create a new ad',
            'text=Manage budget',
            '[class*="campaign"]',
            '[class*="dashboard"]',
            'a[href*="/account/"]',
        ]
        for selector in indicators:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=1000):
                    return True
            except Exception:
                continue

        # Check cookies
        cookies = await context.cookies()
        if any(c['name'] in ('stel_ssid', 'stel_tsession', 'stel_token') for c in cookies):
            return True

    except Exception:
        pass
    return False


async def _click_login_button(page) -> bool:
    """Click the 'Log In to Start Advertising' button on the landing page."""
    # Strategy 1: Click by text content
    text_selectors = [
        'text=Log In to Start Advertising',
        'text=Log In to Start Advertizing',  # Telegram's typo variant
        'text=Log In',
        'button:has-text("Log In")',
        'a:has-text("Log In")',
    ]
    for selector in text_selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2000):
                await el.click()
                logger.info(f"[Login] Clicked login button via: {selector}")
                return True
        except Exception:
            continue

    # Strategy 2: Click any prominent button on the page
    try:
        buttons = page.locator('button, a.btn, a.button, [role="button"]')
        count = await buttons.count()
        for i in range(count):
            btn = buttons.nth(i)
            try:
                if await btn.is_visible(timeout=500):
                    text = await btn.inner_text()
                    if 'log in' in text.lower() or 'start' in text.lower():
                        await btn.click()
                        logger.info(f"[Login] Clicked login button with text: {text}")
                        return True
            except Exception:
                continue
    except Exception:
        pass

    logger.warning("[Login] Could not find login button — modal may already be open")
    return False


async def _enter_phone_number(page, phone: str) -> dict:
    """
    Find the phone number input field in the login modal,
    enter the phone number, and click Next.

    Returns dict with status: 'phone_submitted', 'login_form_ready', or 'error'
    """
    from playwright.async_api import TimeoutError as PwTimeout

    # Wait for the login modal to appear
    await asyncio.sleep(1)

    # Strategy 1: Find input by type="tel"
    input_selectors = [
        'input[type="tel"]',
        'input[name="phone_number"]',
        'input[name="phone"]',
        'input[placeholder*="phone"]',
        'input[placeholder*="Phone"]',
        'input[placeholder*="number"]',
        'input[autocomplete="tel"]',
    ]

    phone_input = None
    for selector in input_selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2000):
                phone_input = el
                logger.info(f"[Login] Found phone input via: {selector}")
                break
        except (PwTimeout, Exception):
            continue

    # Strategy 2: Find any visible text input inside a modal/dialog
    if not phone_input:
        try:
            modal_selectors = [
                '.modal-content input[type="text"]',
                '[class*="modal"] input[type="text"]',
                '[class*="popup"] input[type="text"]',
                '[class*="dialog"] input[type="text"]',
                '[class*="login"] input',
                '[role="dialog"] input[type="text"]',
            ]
            for selector in modal_selectors:
                try:
                    el = page.locator(selector).first
                    if await el.is_visible(timeout=1000):
                        phone_input = el
                        logger.info(f"[Login] Found phone input in modal via: {selector}")
                        break
                except (PwTimeout, Exception):
                    continue
        except Exception:
            pass

    # Strategy 3: Find any visible input on the page
    if not phone_input:
        try:
            all_inputs = page.locator('input:visible')
            count = await all_inputs.count()
            for i in range(count):
                inp = all_inputs.nth(i)
                try:
                    input_type = await inp.get_attribute('type') or ''
                    if input_type not in ('hidden', 'submit', 'button', 'checkbox'):
                        phone_input = inp
                        logger.info(f"[Login] Found phone input as visible input #{i}")
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if not phone_input:
        logger.warning("[Login] Could not find phone input field")
        return {'status': 'login_form_ready', 'message': 'Khong tim thay o nhap so dien thoai'}

    # Clear existing value and type the phone number
    try:
        await phone_input.click()
        await asyncio.sleep(0.3)
        # Select all and replace
        await phone_input.press('Control+A')
        await phone_input.fill(phone)
        await asyncio.sleep(0.5)
        logger.info(f"[Login] Entered phone: {phone}")
    except Exception as e:
        logger.warning(f"[Login] fill() failed, trying type(): {e}")
        try:
            await phone_input.click(click_count=3)
            await phone_input.type(phone, delay=50)
        except Exception as e2:
            return {'status': 'error', 'message': f'Khong the nhap so dien thoai: {str(e2)}'}

    # Click "Next" button
    next_clicked = await _click_next_button(page)

    if next_clicked:
        await asyncio.sleep(2)
        logger.info("[Login] Phone submitted, waiting for Telegram confirmation")
        return {'status': 'phone_submitted'}
    else:
        # Try pressing Enter as fallback
        try:
            await phone_input.press('Enter')
            await asyncio.sleep(2)
            logger.info("[Login] Phone submitted via Enter key")
            return {'status': 'phone_submitted'}
        except Exception:
            return {'status': 'phone_entered', 'message': 'Da nhap so DT nhung khong tim thay nut Next'}


async def _click_next_button(page) -> bool:
    """Click the 'Next' button in the login modal."""
    next_selectors = [
        'button:has-text("Next")',
        'button:has-text("next")',
        'text=Next',
        'button[type="submit"]',
        'input[type="submit"]',
    ]

    for selector in next_selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2000):
                await el.click()
                logger.info(f"[Login] Clicked Next via: {selector}")
                return True
        except Exception:
            continue

    # Try finding by button text in all visible buttons
    try:
        buttons = page.locator('button:visible')
        count = await buttons.count()
        for i in range(count):
            btn = buttons.nth(i)
            try:
                text = await btn.inner_text()
                if text.strip().lower() in ('next', 'continue', 'submit', 'go'):
                    await btn.click()
                    logger.info(f"[Login] Clicked Next button with text: '{text}'")
                    return True
            except Exception:
                continue
    except Exception:
        pass

    logger.warning("[Login] Could not find Next button")
    return False


async def _take_screenshot(page, path: str):
    """Take a screenshot of the current page."""
    try:
        await page.screenshot(path=path, full_page=False)
        logger.info(f"[Login] Screenshot saved: {path}")
    except Exception as e:
        logger.warning(f"[Login] Screenshot failed: {e}")


async def check_auth_status(session_id: str) -> dict:
    """
    Check if the user has completed Telegram authentication.
    After phone submission, Telegram sends a confirmation message.
    User confirms in Telegram app → page auto-navigates to dashboard.
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

        # Check 1: URL changed to dashboard
        if 'ads.telegram.org' in current_url and '/account' in current_url:
            return await _on_authenticated(session)

        # Check 2: Login modal disappeared (page navigated)
        modal_gone = False
        try:
            modal = page.locator('text=Log In').first
            if not await modal.is_visible(timeout=500):
                modal_gone = True
        except Exception:
            modal_gone = True  # Locator failed = modal likely gone

        if modal_gone and 'ads.telegram.org' in current_url:
            # Modal disappeared but URL didn't change to /account yet
            # Give it a moment and check again
            await asyncio.sleep(1)
            current_url = page.url
            if '/account' in current_url:
                return await _on_authenticated(session)

        # Check 3: Dashboard elements present
        dashboard_indicators = [
            'text=Create a new ad',
            'text=Manage budget',
            '[class*="campaign"]',
            '[class*="dashboard"]',
            '[class*="statistics"]',
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

        # Check 4: Session cookies exist
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

        # Check 5: Check if there's a code input (2FA step)
        try:
            code_input = page.locator('input[name="code"], input[type="text"][placeholder*="code"]').first
            if await code_input.is_visible(timeout=500):
                session['status'] = 'needs_code'
                return {
                    'status': 'needs_code',
                    'message': 'Telegram dang hoi ma xac nhan. Vui long nhap ma tu tin nhan Telegram.',
                }
        except Exception:
            pass

        return {'status': 'waiting'}

    except Exception as e:
        logger.warning(f"[Login] Error checking auth status: {e}")
        error_str = str(e).lower()
        if 'target closed' in error_str or 'been closed' in error_str:
            session['status'] = 'error'
            session['error'] = 'Trinh duyet da dong'
            return {'status': 'error', 'error': 'Trinh duyet da dong'}
        return {'status': 'waiting'}


async def submit_verification_code(session_id: str, code: str) -> dict:
    """
    Submit a verification code if Telegram requires it after phone confirmation.
    Some accounts may need a code entered on the web page in addition to
    the Telegram app confirmation.
    """
    session = _sessions.get(session_id)
    if not session:
        return {'status': 'error', 'error': 'Session not found'}

    page = session.get('page')
    if not page:
        return {'status': 'error', 'error': 'Browser session khong con'}

    try:
        # Find and fill the code input
        code_selectors = [
            'input[name="code"]',
            'input[type="text"]',
            'input[type="tel"]',
            'input[placeholder*="code"]',
            'input[placeholder*="Code"]',
        ]

        for selector in code_selectors:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=1000):
                    await el.fill(code)
                    await asyncio.sleep(0.5)

                    # Click submit/Next button
                    submitted = await _click_next_button(page)
                    if not submitted:
                        await el.press('Enter')

                    await asyncio.sleep(2)
                    logger.info(f"[Login] Code submitted via {selector}")
                    return {'status': 'code_submitted'}
            except Exception:
                continue

        return {'status': 'error', 'error': 'Khong tim thay o nhap ma xac nhan'}

    except Exception as e:
        return {'status': 'error', 'error': str(e)}


async def _on_authenticated(session: dict) -> dict:
    """Handle successful authentication: save cookies + update DB."""
    context = session['context']
    account_name = session['account_name']

    try:
        # Wait a moment for all cookies to settle
        await asyncio.sleep(2)

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
            # Try clicking on Budget area (visible in screenshots)
            try:
                budget_el = page.locator('text=Budget:').first
                if await budget_el.is_visible(timeout=2000):
                    await budget_el.click()
                    dropdown_opened = True
                    await asyncio.sleep(1)
            except Exception:
                pass

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
    ss_path = Path(session.get('screenshot_path', ''))
    if ss_path.exists():
        try:
            ss_path.unlink()
        except Exception:
            pass

    del _sessions[session_id]
    return {'success': True, 'message': 'Session da huy'}


async def _close_browser(session: dict):
    """Safely close Playwright browser and context."""
    for key in ('page', 'context', 'browser'):
        obj = session.get(key)
        if obj:
            try:
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


def get_screenshot_path(session_id: str) -> str | None:
    """Get the login screenshot path for a session."""
    session = _sessions.get(session_id)
    if not session:
        return None
    ss_path = session.get('screenshot_path')
    if ss_path and os.path.exists(ss_path):
        return ss_path
    return None


# Keep backward compatibility
get_qr_path = get_screenshot_path


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
            ss_path = Path(session.get('screenshot_path', ''))
            if ss_path.exists():
                try:
                    ss_path.unlink()
                except Exception:
                    pass
            del _sessions[sid]

    if expired:
        logger.info(f"[Login] Cleaned up {len(expired)} expired sessions")
