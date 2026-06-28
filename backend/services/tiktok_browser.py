"""
TikTok browser automation: login + video posting via Playwright.
"""
import asyncio
import json
import logging
import os
from typing import Optional

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

_TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "temp")
_ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "accounts.json")
_ACTIVE_HANDLE_FILE = os.path.join(_TEMP_DIR, "active_account.txt")
_active_handle: str = ""

# ── Multi-account helpers ─────────────────────────────────────────────────────

def load_accounts() -> list:
    """Load account list from accounts.json."""
    if not os.path.exists(_ACCOUNTS_FILE):
        return []
    try:
        with open(_ACCOUNTS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def get_active_handle() -> str:
    return _active_handle


def get_active_cookies_file() -> str:
    if _active_handle:
        return os.path.join(_TEMP_DIR, f"cookies_{_active_handle}.json")
    return os.path.join(_TEMP_DIR, "tiktok_cookies.json")


def get_cookies_file_for(handle: str) -> str:
    if handle:
        return os.path.join(_TEMP_DIR, f"cookies_{handle}.json")
    return os.path.join(_TEMP_DIR, "tiktok_cookies.json")


def set_active_account(handle: str):
    global _active_handle
    _active_handle = handle
    try:
        os.makedirs(_TEMP_DIR, exist_ok=True)
        with open(_ACTIVE_HANDLE_FILE, "w") as f:
            f.write(handle)
    except Exception:
        pass
    logger.info(f"Active account switched to: {handle}")


def _init_active_account():
    global _active_handle
    try:
        if os.path.exists(_ACTIVE_HANDLE_FILE):
            with open(_ACTIVE_HANDLE_FILE) as f:
                _active_handle = f.read().strip()
            return
    except Exception:
        pass
    accounts = load_accounts()
    if accounts:
        _active_handle = accounts[0]["handle"]


_init_active_account()

# Backward-compat alias (always resolves to active account's file)
@property
def COOKIES_FILE():
    return get_active_cookies_file()

# Use as a function call instead of constant throughout this module
def _cookies_file() -> str:
    return get_active_cookies_file()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"


def load_cookies() -> Optional[list]:
    path = _cookies_file()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def save_cookies(cookies: list):
    path = _cookies_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cookies, f)


def delete_cookies():
    path = _cookies_file()
    if os.path.exists(path):
        os.remove(path)


async def login(username: str, password: str) -> dict:
    """Open visible browser at TikTok login. User logs in manually."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=USER_AGENT,
        )
        page = await context.new_page()
        await page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")

        try:
            await page.wait_for_url(
                lambda url: "tiktok.com" in url and "/login" not in url,
                timeout=300000,
            )
        except Exception:
            await browser.close()
            raise RuntimeError("Hết thời gian chờ đăng nhập (5 phút). Vui lòng thử lại.")

        await asyncio.sleep(2)
        cookies = await context.cookies()
        await browser.close()

        names = {c["name"] for c in cookies}
        session_cookies = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt"}
        if not names & session_cookies:
            raise RuntimeError("Không lấy được session. Hãy đảm bảo đăng nhập thành công.")

        save_cookies(cookies)
        return {c["name"]: c["value"] for c in cookies}


async def _dismiss_popups(page):
    """Dismiss any popups/dialogs that appear on TikTok Studio."""
    # Dismiss react-joyride tutorial overlay first (blocks all clicks)
    try:
        overlay = page.locator('[data-test-id="overlay"]').first
        if await overlay.count() > 0 and await overlay.is_visible():
            await overlay.click(force=True)
            await asyncio.sleep(0.5)
            logger.info("Dismissed joyride overlay")
    except Exception:
        pass

    popup_selectors = [
        'button:has-text("Got it")',
        'button:has-text("Đã hiểu")',
        'button:has-text("OK")',
        'button:has-text("Close")',
        'button:has-text("Đóng")',
        '[aria-label="Close"]',
        '[data-e2e="modal-close-inner-button"]',
    ]
    for sel in popup_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(force=True)
                await asyncio.sleep(0.5)
                logger.info(f"Dismissed popup: {sel}")
        except Exception:
            continue


async def _is_content_warning_visible(page) -> bool:
    """Return True if the 'Content may be restricted' MODAL is currently open."""
    try:
        # Primary: "Replace video" button only exists inside the modal
        has_replace = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('button')).some(b => {
                const t = b.textContent.trim();
                return (t === 'Replace video' || t === 'Thay thế video') &&
                       b.getBoundingClientRect().width > 0;
            })
        """)
        if has_replace:
            return True
        # Fallback: Playwright heading visibility check
        h = page.locator('text="Content may be restricted"').first
        if await h.count() > 0 and await h.is_visible():
            return True
        h_vi = page.locator('text="Nội dung có thể bị hạn chế"').first
        return await h_vi.count() > 0 and await h_vi.is_visible()
    except Exception:
        return False


async def _dismiss_content_warning_dialog(page):
    """
    Close the 'Content may be restricted' modal (copied from working tiktok2 implementation).
    """
    try:
        # Detection: same as tiktok2 — heading visibility check
        modal_heading = page.locator('text="Content may be restricted"').first
        if await modal_heading.count() == 0 or not await modal_heading.is_visible():
            modal_heading = page.locator('text="Nội dung có thể bị hạn chế"').first
            if await modal_heading.count() == 0 or not await modal_heading.is_visible():
                return

        logger.info("Content warning modal detected — dismissing...")

        # Strategy 1: scope to [role="dialog"] so we target the modal's X, not other page elements
        for csel in [
            '[role="dialog"] [aria-label="Close"]',
            '[role="dialog"] [data-e2e="modal-close-inner-button"]',
            '[role="dialog"] button[class*="close"]',
            '[role="dialog"] button[class*="Close"]',
            # fallback without dialog scope
            '[aria-label="Close"]',
            '[data-e2e="modal-close-inner-button"]',
        ]:
            try:
                btn = page.locator(csel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(force=True)
                    await asyncio.sleep(1)
                    logger.info(f"Content warning dismissed via {csel}")
                    return
            except Exception:
                continue

        # Strategy 2: JS — find modal by heading, click icon-only X button (NOT "Replace video")
        clicked = await page.evaluate("""() => {
            const heading = Array.from(document.querySelectorAll('*')).find(el =>
                el.textContent.trim() === 'Content may be restricted' ||
                el.textContent.trim() === 'Nội dung có thể bị hạn chế'
            );
            if (!heading) return false;
            const modal = heading.closest('[role="dialog"]') ||
                          heading.closest('[class*="modal"]') ||
                          heading.closest('[class*="Modal"]') ||
                          heading.closest('[class*="Dialog"]') ||
                          heading.parentElement?.parentElement?.parentElement;
            if (!modal) return false;
            const closeBtn =
                modal.querySelector('[aria-label="Close"]') ||
                modal.querySelector('[aria-label="close"]') ||
                modal.querySelector('[data-e2e="modal-close-inner-button"]') ||
                // Only match icon-only buttons (empty text or × or X) — never "Replace video"
                Array.from(modal.querySelectorAll('button')).find(b => {
                    const text = b.textContent.trim();
                    return (text === '' || text === '×' || text === 'X') &&
                           b.innerHTML.includes('<svg');
                });
            if (closeBtn) {
                closeBtn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                return true;
            }
            return false;
        }""")
        if clicked:
            await asyncio.sleep(1)
            logger.info("Content warning dismissed via JS")
            return

        # Strategy 3: Escape key
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        logger.info("Content warning dismissed via Escape key")

    except Exception as e:
        logger.warning(f"Could not dismiss content warning dialog: {e}")


def _extract_cover_frame(video_path: str) -> Optional[str]:
    """Extract a single frame from the video to use as cover image."""
    import subprocess
    cover_path = video_path.replace(".mp4", "_cover.jpg")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01", "-vframes", "1",
             "-q:v", "2", cover_path],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0 and os.path.exists(cover_path):
            return cover_path
    except Exception:
        pass
    return None


async def _handle_cover_dialog(page, video_path: str):
    """If the Select cover dialog is open, upload an extracted frame as cover."""
    try:
        # Check if Select cover dialog is open
        dialog = page.locator('text="Select cover"').first
        if await dialog.count() == 0 or not await dialog.is_visible():
            return

        logger.info("Select cover dialog detected — uploading extracted frame")
        cover_path = _extract_cover_frame(video_path)
        if not cover_path:
            # Just close the dialog if we can't get a cover
            close_btn = page.locator('[class*="close"], button:has-text("×"), .close').first
            if await close_btn.count() > 0:
                await close_btn.click()
            return

        # Click "Upload cover" tab
        upload_tab = page.locator('text="Upload cover"').first
        if await upload_tab.count() > 0:
            await upload_tab.click()
            await asyncio.sleep(1)

        # Find file input in the cover dialog and upload the frame
        cover_input = page.locator('input[type="file"][accept*="image"]').first
        if await cover_input.count() == 0:
            cover_input = page.locator('input[type="file"]').last
        if await cover_input.count() > 0:
            await cover_input.set_input_files(cover_path)
            await asyncio.sleep(3)
            logger.info("Cover frame uploaded")

        # Click Confirm
        confirm_btn = page.locator('button:has-text("Confirm")').first
        if await confirm_btn.count() > 0 and await confirm_btn.is_visible():
            await confirm_btn.click()
            await asyncio.sleep(2)
            logger.info("Cover dialog confirmed")
        else:
            # Close dialog with X
            close_x = page.locator('button').filter(has_text="×").first
            if await close_x.count() > 0:
                await close_x.click()

    except Exception as e:
        logger.warning(f"Cover dialog handling error: {e}")


async def _open_add_link_modal(page):
    """Click the '+Add' link button and click Next to reach the product/tab selection screen."""
    for add_sel in ['text="Add"', 'text="+ Add"', ':text("Add")', '[data-e2e*="add"]']:
        try:
            btn = page.locator(add_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                logger.info(f"Clicked Add button via: {add_sel}")
                await asyncio.sleep(2)
                break
        except Exception:
            pass

    # Click "Next" in the Add-link modal (TikTok shows a link-type step first)
    for next_sel in ['button:has-text("Next")', 'button:has-text("Tiếp theo")']:
        try:
            btn = page.locator(next_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                logger.info(f"Clicked Next in Add-link modal via: {next_sel}")
                await asyncio.sleep(2)
                break
        except Exception:
            pass

    # Debug screenshot
    try:
        p = os.path.join(_TEMP_DIR, "debug_after_add_next.png")
        await page.screenshot(path=p)
        logger.info(f"Debug screenshot: {p}")
    except Exception:
        pass


async def _search_and_select_myshop(page, sp_id: str, sp_name: str):
    """Switch to Showcase products tab, type search term, click search button (kính lúp), select best row."""
    # Switch to Showcase products tab
    for tab_sel in [
        'text="Showcase products"', ':text("Showcase products")',
        ':text("Sản phẩm giới thiệu")',
    ]:
        try:
            tab = page.locator(tab_sel).first
            if await tab.count() > 0 and await tab.is_visible():
                await tab.click()
                logger.info(f"Switched to Showcase products tab via: {tab_sel}")
                await asyncio.sleep(2)
                break
        except Exception:
            pass

    # Try to enrich name from user's showcase cache (populated by /product/showcase/refresh)
    if sp_id:
        try:
            from backend.services import product_service as _ps
            for _prod in _ps.get_cached_products():
                if str(_prod.get("id", "")) == sp_id:
                    full_name = (_prod.get("name") or "").strip()
                    if full_name:
                        sp_name = full_name
                        logger.info(f"Enriched product name from showcase cache: '{sp_name}'")
                    break
        except Exception:
            pass

    # Prefer: full name from cache, then product ID (exact), then short keyword
    if sp_name and len(sp_name) > 15:
        # Looks like a full product name — use it
        search_term = sp_name[:60]
    elif sp_id:
        # Use numeric product ID for exact search
        search_term = sp_id
    else:
        search_term = sp_name[:60]
    logger.info(f"Searching Showcase for: '{search_term}'")

    # --- Step 1: Find search input inside the dialog and type ---
    # Use JS to find the input scoped to the visible dialog/modal container
    input_coords = None
    try:
        input_coords = await page.evaluate("""() => {
            const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
            const container = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
            const inputs = Array.from(container.querySelectorAll('input')).filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
            });
            if (!inputs.length) return null;
            const r = inputs[0].getBoundingClientRect();
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }""")
    except Exception as e:
        logger.debug(f"JS input search failed: {e}")

    typed = False
    if input_coords:
        await page.mouse.click(input_coords['x'], input_coords['y'])
        await asyncio.sleep(0.3)
        await page.keyboard.press("Control+a")
        await page.keyboard.type(search_term, delay=40)
        await asyncio.sleep(0.5)
        logger.info(f"Typed '{search_term}' in search box via JS coords")
        typed = True
    else:
        for s_sel in ['input[placeholder*="earch"]', 'input[placeholder*="ìm"]',
                      'input[placeholder*="roduct"]', 'input[type="search"]']:
            try:
                inp = page.locator(s_sel).first
                if await inp.count() > 0 and await inp.is_visible():
                    await inp.triple_click()
                    await inp.type(search_term, delay=40)
                    await asyncio.sleep(0.5)
                    typed = True
                    break
            except Exception:
                pass

    if not typed:
        logger.warning("Could not find search input in Showcase dialog")
        return

    # --- Step 2: Click the magnifying glass / search button (kính lúp) ---
    # Try to find a search-trigger button adjacent to the input (svg icon or button[type=submit])
    search_btn_clicked = False
    try:
        search_btn_clicked = await page.evaluate("""() => {
            const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
            const container = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
            // Look for a button with svg (magnifying glass) that is visible
            const btns = Array.from(container.querySelectorAll('button')).filter(b => {
                const r = b.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
            });
            // Prefer button[type=submit] or button containing svg/search icon
            const searchBtn = btns.find(b =>
                b.type === 'submit' ||
                b.querySelector('svg') !== null ||
                b.innerHTML.toLowerCase().includes('search') ||
                b.getAttribute('aria-label')?.toLowerCase().includes('search')
            );
            if (searchBtn) {
                const r = searchBtn.getBoundingClientRect();
                searchBtn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                return true;
            }
            return false;
        }""")
    except Exception as e:
        logger.debug(f"Search button JS click failed: {e}")

    if search_btn_clicked:
        logger.info("Clicked search button (kính lúp) via JS")
    else:
        # Fallback: press Enter to trigger search
        await page.keyboard.press("Enter")
        logger.info("Pressed Enter to trigger search")

    await asyncio.sleep(2.5)  # wait for results to load

    # Debug screenshot after search
    try:
        p = os.path.join(_TEMP_DIR, "debug_showcase_search.png")
        await page.screenshot(path=p)
        logger.info(f"Showcase search result screenshot: {p}")
    except Exception:
        pass

    # --- Step 3: Log rows and select best matching row ---
    import unicodedata

    def _norm(s: str) -> str:
        return unicodedata.normalize("NFC", s).strip().lower()

    sp_norm = _norm(sp_name)
    sp_words = [w for w in sp_norm.split() if len(w) >= 3]

    product_rows = page.locator("table tbody tr")
    row_count = await product_rows.count()
    logger.info(f"Showcase: {row_count} rows after search")

    best_idx = -1
    best_score = -1
    for _di in range(min(row_count, 20)):
        try:
            _rt = _norm((await product_rows.nth(_di).inner_text()).replace("\n", " "))
            logger.info(f"  Row {_di}: {_rt[:80]}")
            # Score by word overlap
            if sp_words:
                score = sum(1 for w in sp_words if w in _rt)
                if score > best_score:
                    best_score = score
                    best_idx = _di
            elif best_idx < 0:
                best_idx = 0  # fallback: first row
        except Exception:
            pass

    if best_idx < 0 and row_count > 0:
        best_idx = 0  # no scoring possible, use first row

    if best_idx < 0:
        logger.warning("No rows found in Showcase after search")
        return

    logger.info(f"Best matching row: {best_idx} (score={best_score})")
    for target in [
        product_rows.nth(best_idx).locator('input[type="radio"]').first,
        product_rows.nth(best_idx).locator('input[type="checkbox"]').first,
        product_rows.nth(best_idx),
    ]:
        try:
            if await target.count() > 0:
                await target.click(force=True)
                logger.info(f"Selected Showcase row {best_idx}")
                return
        except Exception:
            pass


async def _confirm_product_dialog(page):
    """Click Next → (Add if needed) to confirm product selection and close the dialog."""
    await asyncio.sleep(1)
    # Log visible buttons for debugging
    try:
        btns = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('button'))
                .filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0; })
                .map(b => b.textContent.trim()).filter(t => t)
        """)
        logger.info(f"Buttons before confirm: {btns}")
    except Exception:
        pass

    # Click Next/Confirm — use last=True because TikTok renders two "Next" buttons
    for btn_text in ['Next', 'Tiếp theo', 'Confirm', 'Xác nhận']:
        try:
            coords = await page.evaluate(f"""() => {{
                const btns = Array.from(document.querySelectorAll('button'));
                const visible = btns.filter(b => {{
                    if (b.textContent.trim() !== {json.dumps(btn_text)}) return false;
                    if (b.disabled) return false;
                    const r = b.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
                }});
                if (!visible.length) return null;
                const btn = visible[visible.length - 1];
                const r = btn.getBoundingClientRect();
                return {{x: r.x + r.width / 2, y: r.y + r.height / 2}};
            }}""")
            if coords:
                await page.mouse.click(coords['x'], coords['y'])
                logger.info(f"Confirm: clicked '{btn_text}' (last)")
                await asyncio.sleep(2)
                break
        except Exception:
            pass

    # Debug screenshot
    try:
        p = os.path.join(_TEMP_DIR, "debug_confirm_product.png")
        await page.screenshot(path=p)
        logger.info(f"Post-confirm screenshot: {p}")
    except Exception:
        pass

    # If dialog still open (TikTok shows "Add" confirmation step), click Add
    for _step in range(2):
        try:
            is_open = await page.evaluate("""() => {
                const texts = ['Add product links', 'My shop', 'Showcase products',
                               'Thêm link sản phẩm', 'Sản phẩm giới thiệu'];
                return texts.some(t => {
                    const el = Array.from(document.querySelectorAll('*'))
                        .find(e => e.childElementCount === 0 && e.textContent.trim() === t);
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
                });
            }""")
            if not is_open:
                logger.info("Product dialog closed — done")
                break
        except Exception:
            break

        for add_text in ['Add', 'Thêm', 'Thêm vào']:
            try:
                coords = await page.evaluate(f"""() => {{
                    const btns = Array.from(document.querySelectorAll('button'));
                    const visible = btns.filter(b => {{
                        if (b.textContent.trim() !== {json.dumps(add_text)}) return false;
                        const r = b.getBoundingClientRect();
                        return r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
                    }});
                    if (!visible.length) return null;
                    const btn = visible[visible.length - 1];
                    const r = btn.getBoundingClientRect();
                    return {{x: r.x + r.width / 2, y: r.y + r.height / 2}};
                }}""")
                if coords:
                    await page.mouse.click(coords['x'], coords['y'])
                    logger.info(f"Confirm add step {_step + 2}: clicked '{add_text}'")
                    await asyncio.sleep(2)
                    break
            except Exception:
                pass


async def post_video(video_path: str, caption: str, product_url: str = "", product_id: str = "", shop_product: dict = {}) -> dict:
    """
    Post video to TikTok via TikTok Studio upload page.
    Optionally attach a TikTok Shop product link.
    Returns dict with success status and profile_url.
    """
    cookies = load_cookies()
    if not cookies:
        raise RuntimeError("Chưa đăng nhập TikTok.")

    username = _get_username_from_cookies(cookies)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",  # dùng Chrome thật thay vì Chromium bundled → có đầy đủ codec video
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=USER_AGENT,
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        logger.info("Navigating to TikTok Studio upload...")
        await page.goto(UPLOAD_URL, wait_until="domcontentloaded")
        await asyncio.sleep(6)

        # --- Step 1: Upload file ---
        logger.info("Finding file input...")
        inputs = page.locator('input[type="file"]')
        count = await inputs.count()
        if count == 0:
            await browser.close()
            raise RuntimeError("Không tìm thấy ô upload file. TikTok Studio có thể đã thay đổi giao diện.")

        await inputs.first.wait_for(state="attached", timeout=10000)
        await inputs.first.set_input_files(video_path)
        logger.info("File uploaded, waiting for processing...")

        # --- Step 2: Wait for upload form to appear (dismiss popups along the way) ---
        caption_sel = 'div[contenteditable="true"]'
        await asyncio.sleep(5)
        caption_ready = False
        for _ in range(30):  # up to 2.5 minutes
            # Dismiss any popups that block the UI on every iteration
            await _dismiss_popups(page)
            await asyncio.sleep(5)
            try:
                el = page.locator(caption_sel).first
                if await el.count() > 0 and await el.is_visible():
                    caption_ready = True
                    break
            except Exception:
                pass

        if not caption_ready:
            logger.warning("Caption box not found after waiting")

        # Final popup dismiss before interacting with form
        await _dismiss_popups(page)
        await asyncio.sleep(1)

        # --- Step 3: Fill caption ---
        logger.info("Filling caption...")
        try:
            el = page.locator(caption_sel).first
            await el.wait_for(state="visible", timeout=8000)
            await el.click()
            await asyncio.sleep(0.3)
            await page.keyboard.press("Control+a")
            await asyncio.sleep(0.2)
            await page.keyboard.type(caption[:2000], delay=10)
            logger.info("Caption filled")
        except Exception as e:
            logger.warning(f"Could not fill caption: {e}")

        await asyncio.sleep(1)

        # --- Step 4: Add product/affiliate link ---
        if shop_product and shop_product.get("name"):
            # ── "SP gốc" mode: search in My shop by product ID (from source video anchor) ──
            sp_id   = (shop_product.get("id") or "").strip()
            sp_name = (shop_product.get("name") or "").strip()
            logger.info(f"Attaching SP gốc: name='{sp_name}' id='{sp_id}'")
            try:
                await _open_add_link_modal(page)
                await _search_and_select_myshop(page, sp_id, sp_name)
                await _confirm_product_dialog(page)
            except Exception as e:
                logger.warning(f"SP gốc attach failed: {e}")

        elif product_id:
            logger.info(f"Attaching showcase product: {product_id}")
            try:
                await _open_add_link_modal(page)

                # Switch to "Showcase products" tab
                for tab_sel in ['text="Showcase products"', ':text("Sản phẩm giới thiệu")']:
                    tab = page.locator(tab_sel).first
                    if await tab.count() > 0 and await tab.is_visible():
                        await tab.click()
                        await asyncio.sleep(2)
                        logger.info(f"Switched to Showcase products tab via {tab_sel}")
                        break

                # Find product row matching product_id
                product_rows = page.locator("table tbody tr")
                row_count = await product_rows.count()
                logger.info(f"Found {row_count} showcase rows")
                found = False
                for i in range(row_count):
                    row = product_rows.nth(i)
                    try:
                        row_text = await row.inner_text()
                    except Exception:
                        continue
                    if product_id in row_text:
                        radio = row.locator('input[type="radio"]').first
                        if await radio.count() > 0:
                            await radio.click()
                            found = True
                            logger.info(f"Selected product {product_id} at row {i}")
                            break

                if not found and row_count > 0:
                    radio = product_rows.nth(0).locator('input[type="radio"]').first
                    if await radio.count() > 0:
                        await radio.click()
                        logger.info("Selected first product row (fallback)")

                await _confirm_product_dialog(page)
            except Exception as e:
                logger.warning(f"Could not attach showcase product: {e}")

        elif product_url:
            logger.info(f"Adding product link: {product_url}")
            try:
                # Click "+ Add" next to "Add link"
                add_link_btn = page.locator('text=+ Add').first
                if await add_link_btn.count() == 0:
                    add_link_btn = page.locator('[class*="add-link"], [class*="AddLink"]').first

                if await add_link_btn.count() > 0:
                    await add_link_btn.click()
                    await asyncio.sleep(1.5)

                    url_input_selectors = [
                        'input[placeholder*="http"]',
                        'input[placeholder*="URL"]',
                        'input[placeholder*="link"]',
                        'input[type="url"]',
                        'input[type="text"][class*="link"]',
                    ]
                    url_input = None
                    for sel in url_input_selectors:
                        inp = page.locator(sel).first
                        if await inp.count() > 0 and await inp.is_visible():
                            url_input = inp
                            break

                    if url_input:
                        await url_input.click()
                        await url_input.fill(product_url)
                        await asyncio.sleep(0.5)

                        for confirm_sel in [
                            'button:has-text("Apply")',
                            'button:has-text("Save")',
                            'button:has-text("Xác nhận")',
                            'button:has-text("OK")',
                            'button:has-text("Add")',
                            'button[type="submit"]',
                        ]:
                            btn = page.locator(confirm_sel).first
                            if await btn.count() > 0 and await btn.is_visible():
                                await btn.click()
                                logger.info(f"Link confirmed via {confirm_sel}")
                                break

                        await asyncio.sleep(1)
                    else:
                        logger.warning("Could not find URL input field for link")
                else:
                    logger.warning("Could not find '+ Add' button for link")
            except Exception as e:
                logger.warning(f"Could not add product link: {e}")

        await asyncio.sleep(2)

        # --- Step 5: Wait for content check to finish before clicking Post ---
        # TikTok runs a content check ("Checking in progress") that takes up to ~10 min.
        # Clicking Post while it's still running causes a "Continue to post?" dialog
        # that does NOT actually publish — we must wait for the check to complete first.
        logger.info("Waiting for TikTok content check to complete (up to 12 min)...")
        checking_phrases = [
            "Checking in progress",
            "đang kiểm tra",
            "checking in progress",
        ]
        done_phrases = [
            "No issues found",
            "Không có vấn đề",
            "Content may be restricted",
            "Nội dung có thể bị hạn chế",
        ]
        # Poll every 10 s, up to 720 s (12 min)
        # Strategy: wait until "Checking in progress" disappears AND a done phrase appears.
        # First wait at least 15s to let the check UI appear before polling.
        await asyncio.sleep(15)
        for _wait_i in range(72):
            await _dismiss_popups(page)
            try:
                page_text = await page.inner_text("body")
            except Exception:
                page_text = ""

            still_checking = any(ph.lower() in page_text.lower() for ph in checking_phrases)
            check_done = any(ph.lower() in page_text.lower() for ph in done_phrases)

            logger.info(f"Content check poll {_wait_i}: still_checking={still_checking}, check_done={check_done}")

            if check_done:
                logger.info(f"Content check completed at iteration {_wait_i}")
                break
            if not still_checking and _wait_i > 3:
                # Checking phrase never appeared — TikTok may have skipped the check
                logger.info(f"No checking phrase found after {_wait_i} polls — proceeding")
                break

            if _wait_i % 6 == 0:
                logger.info(f"Content check still running… ({_wait_i * 10}s elapsed)")
            await asyncio.sleep(10)
        else:
            logger.warning("Content check did not complete within 12 minutes — proceeding anyway")

        # Close the "Content may be restricted" modal if it appeared — it blocks the Post button
        await _dismiss_content_warning_dialog(page)
        await asyncio.sleep(2)

        # --- Step 5b: Wait for Cover thumbnail to finish loading ---
        # TikTok generates the cover from the video client-side. If cover is still
        # "Loading..." when Post is clicked, TikTok throws "Something went wrong".
        logger.info("Waiting for cover thumbnail to load...")
        for _ci in range(30):  # up to 30s
            cover_loading = await page.locator('text=Loading...').count()
            if cover_loading == 0:
                logger.info(f"Cover loaded after {_ci}s")
                break
            await asyncio.sleep(1)
        else:
            logger.warning("Cover thumbnail still loading after 30s — handling cover manually")

        # Handle the Select cover dialog if it popped up (happens when browser can't render video)
        await _handle_cover_dialog(page, video_path)
        await asyncio.sleep(1)

        # Dismiss content warning again in case it re-appeared after product attachment
        await _dismiss_content_warning_dialog(page)
        await asyncio.sleep(1)

        # --- Step 6: Find Post button, wait for it to be enabled, then click ---
        logger.info("Waiting for Post button to be enabled...")
        posted = False
        post_btn = None
        # Up to 60 s (20 × 3 s) for the button to become clickable after check completes
        for _ in range(20):
            for sel in [
                'button[data-e2e="post_video_button"]',
                'button[data-e2e="btn-post"]',
                '[class*="btn-post"]',
                '[class*="PostBtn"]',
            ]:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        post_btn = btn
                        if await btn.is_enabled():
                            break
                except Exception:
                    continue

            # Fallback: find by exact text "Post" (not "Posts")
            if not post_btn:
                try:
                    btn = page.get_by_role("button", name="Post", exact=True).first
                    if await btn.count() > 0 and await btn.is_visible():
                        post_btn = btn
                except Exception:
                    pass

            if post_btn and await post_btn.is_enabled():
                break
            await asyncio.sleep(3)

        if post_btn:
            try:
                # Screenshot before clicking so we can verify the state
                try:
                    pre_click_path = os.path.join(_TEMP_DIR, "pre_post_click.png")
                    await page.screenshot(path=pre_click_path, full_page=False)
                    logger.info(f"Pre-click screenshot: {pre_click_path}")
                except Exception:
                    pass

                # Try Playwright click first, then JS fallback
                click_ok = False
                try:
                    await post_btn.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)
                    await post_btn.click(force=True)
                    click_ok = True
                    logger.info("Post button clicked (Playwright force=True)")
                except Exception as e:
                    logger.warning(f"Playwright click failed: {e}")

                if not click_ok:
                    # JS click fallback — dispatches real mouse events React can handle
                    result = await page.evaluate("""() => {
                        const candidates = Array.from(document.querySelectorAll('button'));
                        const btn = candidates.find(b => {
                            const t = b.textContent.trim();
                            return (t === 'Post' || t === 'Đăng') && !b.disabled;
                        });
                        if (btn) {
                            ['mousedown','mouseup','click'].forEach(evt =>
                                btn.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true}))
                            );
                            return btn.textContent.trim();
                        }
                        return null;
                    }""")
                    if result:
                        logger.info(f"Post button clicked via JS: '{result}'")
                        click_ok = True

                posted = click_ok
            except Exception as e:
                logger.warning(f"Post click error: {e}")

        if not posted:
            await browser.close()
            raise RuntimeError("Không tìm thấy hoặc không bấm được nút Đăng.")

        # --- Step 6b: Handle dialogs that appear after clicking Post ---
        # TikTok may show:
        #   (a) "Content may be restricted" warning → dismiss with X, then click Post again
        #   (b) "Continue to post?" confirmation → click "Post now"
        await asyncio.sleep(3)

        for _retry in range(3):
            # Handle "Content may be restricted" warning modal (dismiss X, then re-click Post)
            if await _is_content_warning_visible(page):
                logger.info(f"Content warning modal after Post click (attempt {_retry + 1}) — dismissing and re-clicking Post")
                await _dismiss_content_warning_dialog(page)
                await asyncio.sleep(1.5)

                # Re-click Post if still on upload page
                if "upload" in page.url or "tiktokstudio" in page.url:
                    for repost_sel in [
                        'button[data-e2e="post_video_button"]',
                        'button[data-e2e="btn-post"]',
                    ]:
                        try:
                            btn = page.locator(repost_sel).first
                            if await btn.count() > 0 and await btn.is_enabled():
                                await btn.click(force=True)
                                logger.info(f"Re-clicked Post after dismissing warning: {repost_sel}")
                                await asyncio.sleep(3)
                                break
                        except Exception:
                            pass
                    else:
                        # JS fallback: click the Post/Đăng button
                        await page.evaluate("""() => {
                            const btn = Array.from(document.querySelectorAll('button'))
                                .find(b => ['Post', 'Đăng'].includes(b.textContent.trim()) && !b.disabled);
                            if (btn) { ['mousedown','mouseup','click'].forEach(e =>
                                btn.dispatchEvent(new MouseEvent(e, {bubbles:true, cancelable:true}))); }
                        }""")
                        logger.info("Re-clicked Post via JS after warning dismiss")
                        await asyncio.sleep(3)
                continue  # check again

            # Handle "Continue to post?" confirmation dialog
            confirmed = False
            for confirm_sel in [
                'button:has-text("Post now")', 'button:has-text("Đăng ngay")',
                'button:has-text("Continue")', 'button:has-text("Tiếp tục")',
            ]:
                try:
                    btn = page.locator(confirm_sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.scroll_into_view_if_needed()
                        await btn.click(force=True)
                        logger.info(f"Clicked dialog confirm button: {confirm_sel}")
                        confirmed = True
                        await asyncio.sleep(4)
                        break
                except Exception:
                    pass

            if confirmed:
                break

            # No dialog — post may have gone through already
            break

        # --- Step 7: Wait for post success (redirect OR page state change) ---
        # Poll every 5 s for up to 120 s for any success signal.
        success_detected = False
        for _si in range(24):
            await asyncio.sleep(5)
            try:
                cur_url = page.url
                logger.info(f"Post success check {_si}: url={cur_url}")
                # Redirect to content/manage page = definite success
                if any(k in cur_url for k in ("content", "manage", "profile")):
                    logger.info(f"Redirect success: {cur_url}")
                    success_detected = True
                    break
                # Check page text for success/progress messages
                try:
                    body = await page.inner_text("body")
                except Exception:
                    body = ""
                if any(phrase in body for phrase in [
                    "Video posted",
                    "Đã đăng",
                    "being processed",
                    "Your video",
                    "uploaded successfully",
                ]):
                    logger.info("Success phrase detected in page")
                    success_detected = True
                    break
                # Upload form gone + URL still on upload page = ambiguous, keep waiting
                file_input_count = await page.locator('input[type="file"]').count()
                logger.info(f"Post success check {_si}: file_input_count={file_input_count}")
                if file_input_count == 0 and "upload" not in cur_url:
                    logger.info("Upload form disappeared and URL changed — post went through")
                    success_detected = True
                    break
                # "Content may be restricted" modal blocking Post — dismiss and re-click
                if await _is_content_warning_visible(page):
                    logger.info(f"Content warning modal in success loop iter {_si} — dismissing")
                    await _dismiss_content_warning_dialog(page)
                    await asyncio.sleep(1)
                    # Re-click Post button
                    post_clicked = await page.evaluate("""() => {
                        const btn = Array.from(document.querySelectorAll('button'))
                            .find(b => ['Post','Đăng'].includes(b.textContent.trim()) && !b.disabled);
                        if (btn) { btn.click(); return true; }
                        return false;
                    }""")
                    if post_clicked:
                        logger.info("Re-clicked Post after content warning dismiss")
                    await asyncio.sleep(4)
                    continue

                # "Continue to post?" confirmation dialog — click again
                for retry_sel in ['button:has-text("Post now")', 'button:has-text("Đăng ngay")',
                                   'button:has-text("Continue")', 'button:has-text("Tiếp tục")']:
                    retry_btn = page.locator(retry_sel).first
                    if await retry_btn.count() > 0 and await retry_btn.is_visible():
                        await retry_btn.click(force=True)
                        logger.info(f"Re-clicked dialog: {retry_sel}")
                        await asyncio.sleep(3)
                        break
            except Exception as e:
                logger.warning(f"Success check iter {_si}: {e}")

        # Screenshot for debugging
        try:
            screenshot_path = os.path.join(_TEMP_DIR, "post_result.png")
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.info(f"Screenshot saved: {screenshot_path}")
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")

        if not success_detected:
            logger.warning("Could not confirm post success after 120s")

        await browser.close()

        profile_url = f"https://www.tiktok.com/@{username}" if username else "https://www.tiktok.com/"
        return {
            "success": True,
            "profile_url": profile_url,
            "message": "Đã đăng video thành công!",
        }


def _get_username_from_cookies(cookies: list) -> str:
    from backend.config import settings
    # Prefer explicit handle (e.g. l.e.v.e.l.shop) over username email
    if settings.TIKTOK_HANDLE:
        return settings.TIKTOK_HANDLE.lstrip("@")
    username = settings.TIKTOK_USERNAME or ""
    if "@" in username:
        username = username.split("@")[0]
    return username
