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
import sys
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
# Force stdout handler so Railway deploy logs capture everything
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)

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

        # Most reliable: URL shows dashboard
        if '/account' in current_url and 'ads.telegram.org' in current_url:
            return True

        # Dashboard-specific TEXT indicators (NOT generic CSS selectors)
        # These texts ONLY appear on the dashboard, never on the landing page
        dashboard_texts = [
            'text=Create a new ad',
            'text=Manage budget',
            'text=Total spend',
        ]
        match_count = 0
        for selector in dashboard_texts:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=1000):
                    match_count += 1
            except Exception:
                continue

        # Require at least 2 matches to avoid false positives
        if match_count >= 2:
            return True

        # NOTE: Do NOT check for stel_ssid cookie — it exists on the landing
        # page for ALL visitors (confirmed via Railway deploy logs).
        # Only stel_tsession or stel_token indicate real authentication.
        cookies = []
        try:
            cookies = await context.cookies()
        except Exception:
            pass
        has_auth_cookie = any(
            c['name'] in ('stel_tsession', 'stel_token', 'stel_web_session')
            for c in cookies
        )
        if has_auth_cookie:
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
        cookies = session.get('cookies', [])
        result['cookies_count'] = len(cookies) if cookies else 0
        if session.get('error'):
            result['error'] = session['error']
        return result

    page = session.get('page')
    context = session.get('context')
    if not page or not context:
        return {'status': 'error', 'error': 'Browser session khong con'}

    try:
        current_url = page.url

        # Check 1: URL changed to dashboard (most reliable indicator)
        if 'ads.telegram.org' in current_url and '/account' in current_url:
            return await _on_authenticated(session)

        # Check 2: Session cookies exist (reliable auth indicator)
        cookies = []
        try:
            cookies = await context.cookies()
        except Exception:
            pass
        has_session_cookie = any(
            c['name'] in (
                'stel_tsession', 'stel_token',
                'sessionid', 'stel_web_session',
            )
            for c in cookies
        )
        if has_session_cookie:
            return await _on_authenticated(session)

        # Check 3: Dashboard elements (VERY specific to Telegram Ads dashboard)
        # Only match text that UNIQUELY appears on the dashboard, not landing page
        dashboard_text_indicators = [
            'text=Create a new ad',
            'text=Manage budget',
            'text=Total spend',
            'text=Budget:',
        ]
        match_count = 0
        for selector in dashboard_text_indicators:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=300):
                    match_count += 1
            except Exception:
                continue

        # Require at least 2 dashboard indicators to avoid false positives
        if match_count >= 2:
            return await _on_authenticated(session)

        # Check 4: Check if there's a code input (2FA step)
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
    # Prevent race condition: mark as authenticated IMMEDIATELY
    session['status'] = 'authenticated'

    context = session.get('context')
    page = session.get('page')
    account_name = session['account_name']

    try:
        # ===== DIAGNOSTIC: Log current state =====
        current_url = 'unknown'
        if page:
            try:
                current_url = page.url
            except Exception:
                pass
        logger.info(f"[Login][DIAG] === Authenticated for '{account_name}' ===")
        logger.info(f"[Login][DIAG] URL: {current_url}")
        logger.info(f"[Login][DIAG] context alive: {context is not None}")
        logger.info(f"[Login][DIAG] page alive: {page is not None}")

        # Wait for cookies to settle
        await asyncio.sleep(5)

        # ===== METHOD 1: storage_state() — captures cookies + localStorage =====
        storage_state = {}
        if context:
            try:
                storage_state = await context.storage_state()
                ss_cookies = storage_state.get('cookies', [])
                ss_origins = storage_state.get('origins', [])
                logger.info(
                    f"[Login][DIAG] storage_state(): "
                    f"{len(ss_cookies)} cookies, {len(ss_origins)} origins"
                )
                # Log first few cookie names for debugging
                if ss_cookies:
                    names = [c['name'] for c in ss_cookies[:10]]
                    logger.info(f"[Login][DIAG] Cookie names: {names}")
            except Exception as e:
                logger.error(f"[Login][DIAG] storage_state() FAILED: {e}")

        # ===== METHOD 2: context.cookies() — traditional =====
        direct_cookies = []
        if context:
            try:
                direct_cookies = await context.cookies()
                logger.info(f"[Login][DIAG] context.cookies(): {len(direct_cookies)} cookies")
            except Exception as e:
                logger.error(f"[Login][DIAG] context.cookies() FAILED: {e}")

        # ===== METHOD 3: JavaScript fallbacks =====
        js_cookies_str = ''
        js_storage_str = ''
        if page:
            try:
                js_cookies_str = await page.evaluate('() => document.cookie')
                logger.info(f"[Login][DIAG] document.cookie: '{js_cookies_str[:300]}'")
            except Exception as e:
                logger.error(f"[Login][DIAG] document.cookie FAILED: {e}")

            try:
                js_storage_str = await page.evaluate('''() => {
                    const data = {};
                    try {
                        for (let i = 0; i < localStorage.length; i++) {
                            const key = localStorage.key(i);
                            data[key] = localStorage.getItem(key);
                        }
                    } catch(e) {}
                    try {
                        for (let i = 0; i < sessionStorage.length; i++) {
                            const key = sessionStorage.key(i);
                            data['ss_' + key] = sessionStorage.getItem(key);
                        }
                    } catch(e) {}
                    return JSON.stringify(data);
                }''')
                logger.info(f"[Login][DIAG] localStorage/sessionStorage: '{js_storage_str[:300]}'")
            except Exception as e:
                logger.error(f"[Login][DIAG] JS storage FAILED: {e}")

        # ===== Take diagnostic screenshot =====
        safe_name = safe_filename(account_name)
        screenshot_path = str(SCREENSHOTS_DIR / f"{safe_name}_auth_dashboard.png")
        if page:
            try:
                await page.screenshot(path=screenshot_path, full_page=False)
                logger.info(f"[Login][DIAG] Screenshot saved: {screenshot_path}")
            except Exception as e:
                logger.error(f"[Login][DIAG] Screenshot FAILED: {e}")

        # ===== Determine best cookie data =====
        # Use storage_state cookies if available (most complete)
        cookies = storage_state.get('cookies', []) if storage_state else []
        if not cookies:
            cookies = direct_cookies
        if not cookies and js_cookies_str:
            for pair in js_cookies_str.split(';'):
                pair = pair.strip()
                if '=' in pair:
                    name, _, value = pair.partition('=')
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.telegram.org',
                        'path': '/',
                    })

        # Parse localStorage/sessionStorage from JS
        storage_data = {}
        if js_storage_str:
            try:
                storage_data = json.loads(js_storage_str)
            except Exception:
                pass

        # Also check storage_state origins for localStorage
        if storage_state:
            for origin in storage_state.get('origins', []):
                for item in origin.get('localStorage', []):
                    storage_data[item['name']] = item['value']

        session['cookies'] = cookies

        logger.info(
            f"[Login][DIAG] FINAL: {len(cookies)} cookies, "
            f"{len(storage_data)} storage items"
        )

        # ===== Save everything to file =====
        cookie_path = COOKIES_DIR / f"{safe_name}.json"
        save_obj = {
            'cookies': cookies,
            'localStorage': storage_data,
            'storage_state': storage_state,  # full Playwright state for reuse
            'captured_at': datetime.now().isoformat(),
            'diagnostics': {
                'url': current_url,
                'ss_cookies_count': len(storage_state.get('cookies', [])) if storage_state else 0,
                'direct_cookies_count': len(direct_cookies),
                'js_cookie_str_length': len(js_cookies_str),
                'js_storage_str_length': len(js_storage_str),
            },
        }
        cookie_path.write_text(
            json.dumps(save_obj, indent=2, ensure_ascii=False),
            encoding='utf-8',
        )
        logger.info(f"[Login] Saved auth data to {cookie_path}")

        # ===== Detect organizations =====
        orgs = []
        try:
            if page:
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

        # Cleanup browser
        await _close_browser(session)

        return {
            'status': 'authenticated',
            'cookies_count': len(cookies),
            'orgs_count': len(orgs),
            'storage_count': len(storage_data),
        }

    except Exception as e:
        logger.error(f"[Login][DIAG] EXCEPTION in _on_authenticated: {e}", exc_info=True)
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
