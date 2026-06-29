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

    # Screenshot BEFORE clicking Next — capture step 1 (link type selection)
    try:
        await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_before_add_next.png"))
        logger.info(f"Before-Next screenshot saved")
    except Exception:
        pass

    # Log all visible elements at step 1 and check dropdown options
    try:
        step1_elems = await page.evaluate("""() => {
            const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
            const container = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
            const items = Array.from(container.querySelectorAll('button, input, a, [role="tab"], select, [role="option"]'))
                .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                .map(e => ({tag: e.tagName, text: e.textContent.trim().slice(0,60), type: e.type||'', placeholder: e.placeholder||''}));
            return items;
        }""")
        logger.info(f"Step 1 elements: {step1_elems}")
    except Exception:
        pass

    # We already know from prior runs that Link type only has "Products" — skip dropdown
    # exploration which leaves the dropdown open and blocks the "Next" button.

    # Click "Next" in the Add-link modal (TikTok shows a link-type step first)
    next_clicked = False
    for next_sel in ['button:has-text("Next")', 'button:has-text("Tiếp theo")']:
        try:
            btn = page.locator(next_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                logger.info(f"Clicked Next in Add-link modal via: {next_sel}")
                await asyncio.sleep(2)
                next_clicked = True
                break
        except Exception:
            pass

    if not next_clicked:
        # JS fallback — find the Next button inside the dialog by text
        clicked = await page.evaluate("""() => {
            const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
            const container = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
            const btn = Array.from(container.querySelectorAll('button')).find(b => {
                const t = b.textContent.trim();
                const r = b.getBoundingClientRect();
                return (t === 'Next' || t === 'Tiếp theo') && r.width > 0 && r.height > 0;
            });
            if (btn) { btn.click(); return true; }
            return false;
        }""")
        if clicked:
            logger.info("Clicked Next in Add-link modal via JS fallback")
            await asyncio.sleep(2)

    # Debug screenshot
    try:
        p = os.path.join(_TEMP_DIR, "debug_after_add_next.png")
        await page.screenshot(path=p)
        logger.info(f"Debug screenshot: {p}")
    except Exception:
        pass


async def _search_and_select_myshop(page, sp_id: str, sp_name: str):
    """
    Find product in showcase by product ID.

    Flow:
    1. Check via showcase_product/list API whether sp_id is in showcase
    2. If not → add via OEC add_targets:[2] (or fallback strategies)
    3. Switch to Showcase products tab
    4. Type product ID in search box (NOT name) → TikTok filters by ID
    5. Click row whose text contains sp_id — no name matching, no first-row fallback
    """
    _intercepted: list = []

    async def _capture_api(req):
        url = req.url
        if any(kw in url.lower() for kw in ['product', 'showcase', 'affiliate', 'item']):
            if not any(skip in url for skip in ['.js', '.css', '.png', '.jpg', '.woff']):
                _intercepted.append({'url': url, 'method': req.method, 'headers': dict(req.headers)})

    page.on("request", _capture_api)

    # --- Step 1: Check via API by product ID (and resolve ID from name if needed) ---
    logger.info(f"Checking showcase for product ID: {sp_id!r} name: {sp_name!r}")
    in_showcase = False
    _all_showcase_prods: list = []
    try:
        for _offset in range(0, 300, 20):
            _resp = await page.context.request.get(
                f"https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list?offset={_offset}&count=20",
                headers={"Accept": "application/json"},
            )
            _data = json.loads(await _resp.text())
            _prods = (_data.get("data") or {}).get("products") or []
            _all_showcase_prods.extend(_prods)
            for _p in _prods:
                _pid = str(_p.get("product_id", "") or _p.get("id", ""))
                if sp_id and _pid == sp_id:
                    in_showcase = True
            if in_showcase or not _prods or not (_data.get("data") or {}).get("has_more"):
                break
    except Exception as _e:
        logger.warning(f"Showcase API check: {_e}")

    # 1b. If still no ID but have name → try showcase list name-match to resolve ID
    if not sp_id and sp_name and _all_showcase_prods:
        _name_lower = sp_name.strip().lower()
        for _p in _all_showcase_prods:
            _pname = (_p.get("title") or "").strip().lower()
            if _pname and (_name_lower in _pname or _pname in _name_lower):
                sp_id = str(_p.get("product_id") or "")
                logger.info(f"Resolved product ID from showcase name match: {sp_id} (name: {sp_name[:60]})")
                in_showcase = True
                break
        if not sp_id:
            logger.info(f"Not in showcase by name — will try TikTok search API to resolve ID")

    # 1c. If still no ID but have name → call TikTok search APIs to find product ID
    if not sp_id and sp_name:
        import re as _re
        # Clean informal price suffixes like "1xx", "2xx", "150k", "100-200k" from name
        _clean_name = _re.sub(r'\s*[\d]+[xXkK]+[\w]*$', '', sp_name).strip()
        _clean_name = _re.sub(r'\s*\d{2,3}[kK]$', '', _clean_name).strip() or sp_name

        # Try multiple search endpoints and query variants
        _search_queries = list(dict.fromkeys([sp_name[:80], _clean_name[:80]]))  # deduplicate
        _search_endpoints = [
            "https://shop.tiktok.com/api/v1/streamer_desktop/search_product/list",
            "https://shop.tiktok.com/api/v1/creator/product/search",
        ]
        for _endpoint in _search_endpoints:
            if sp_id:
                break
            for _q in _search_queries:
                if sp_id:
                    break
                try:
                    import urllib.parse as _up
                    _search_resp = await page.context.request.get(
                        f"{_endpoint}?query={_up.quote(_q)}&count=20&offset=0",
                        headers={"Accept": "application/json"},
                    )
                    _search_data = json.loads(await _search_resp.text())
                    _search_prods = (
                        (_search_data.get("data") or {}).get("products")
                        or (_search_data.get("data") or {}).get("items")
                        or (_search_data.get("products")) or []
                    )
                    logger.info(f"Search API {_endpoint.split('/')[-1]} '{_q[:40]}': {len(_search_prods)} results")
                    if _search_prods:
                        _name_lower = _clean_name.lower()
                        _best = None
                        for _p in _search_prods:
                            _pname = (_p.get("title") or "").strip().lower()
                            if _pname and (_name_lower in _pname or _pname in _name_lower):
                                _best = _p
                                break
                        if not _best:
                            _best = _search_prods[0]
                        sp_id = str(_best.get("product_id") or "")
                        if sp_id:
                            logger.info(f"Resolved product ID from search API: {sp_id} title={(_best.get('title') or '')[:60]}")
                except Exception as _e:
                    logger.warning(f"Search API {_endpoint.split('/')[-1]}: {_e}")

    logger.info(f"Product {sp_id!r} in showcase: {in_showcase}")

    # --- Step 2: If not in showcase, add it ---
    if not in_showcase and sp_id:
        logger.info("Product not in showcase — adding via OEC add_targets:[2]")
        try:
            _oec_resp = await page.context.request.post(
                "https://shop.tiktok.com/aweme/v1/oec/content/creator/products?aid=1180&carrier_region=TH",
                data=json.dumps({"products": [{"product_id": sp_id}], "add_targets": [2]}),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Origin": "https://www.tiktok.com",
                    "Referer": "https://www.tiktok.com/tiktokstudio/upload",
                },
            )
            _oec_text = await _oec_resp.text()
            logger.info(f"OEC add_targets:[2]: {_oec_resp.status} {_oec_text[:200]}")
            _pid_res = (json.loads(_oec_text).get("add_results") or {}).get(sp_id, {})
            if _pid_res.get("is_in_showcase"):
                in_showcase = True
        except Exception as _e:
            logger.warning(f"OEC add: {_e}")

        if not in_showcase:
            logger.info("OEC did not add — trying showcase_product/add fallback")
            if await _add_product_to_showcase(page, sp_id, sp_name, _intercepted):
                in_showcase = True

        if not in_showcase:
            logger.warning(f"Could not add product {sp_id} to showcase")

    # --- Step 3: Switch to Showcase products tab (and wait for list to refresh) ---
    _tab_clicked = False
    for tab_sel in [
        'text="Showcase products"', ':text("Showcase products")',
        ':text("Sản phẩm giới thiệu")',
    ]:
        try:
            tab = page.locator(tab_sel).first
            if await tab.count() > 0 and await tab.is_visible():
                await tab.click()
                logger.info(f"Switched to Showcase products tab via: {tab_sel}")
                _tab_clicked = True
                # Wait longer if product was just added (needs time to refresh in dialog)
                await asyncio.sleep(4 if in_showcase else 2)
                break
        except Exception:
            pass

    async def _cancel_dialog():
        """Click Cancel to close the dialog cleanly."""
        for _sel in ['button:has-text("Cancel")', ':text("Cancel")', 'button:has-text("Hủy")']:
            try:
                _btn = page.locator(_sel).last
                if await _btn.count() > 0 and await _btn.is_visible():
                    await _btn.click()
                    logger.info("Closed product dialog via Cancel")
                    return
            except Exception:
                pass

    async def _click_search_icon() -> bool:
        """Click the magnifying glass search button inside the dialog."""
        # Find the icon by locating elements near the search input (not the back-arrow or Cancel/Next)
        coords = await page.evaluate("""() => {
            const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
            const container = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
            const inp = container.querySelector('input');
            if (!inp) return null;
            const ir = inp.getBoundingClientRect();

            // Walk up to parent and grandparent to find button/svg adjacent to input
            for (const wrapper of [inp.parentElement, inp.parentElement && inp.parentElement.parentElement]) {
                if (!wrapper) continue;
                const candidates = Array.from(wrapper.querySelectorAll('button, [role="button"], svg'));
                for (const el of candidates) {
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    // Must be vertically aligned with the input
                    if (Math.abs((r.top + r.height/2) - (ir.top + ir.height/2)) > 30) continue;
                    // Must be to the right of the input center (i.e. is the icon on the right)
                    if (r.left + r.width/2 < ir.left + ir.width/2) continue;
                    return {x: r.left + r.width/2, y: r.top + r.height/2, method: 'adjacent'};
                }
            }

            // Fallback: click just outside the right edge of the input
            return {x: ir.right + 10, y: ir.top + ir.height/2, method: 'right-of-input'};
        }""")
        if coords:
            await page.mouse.click(coords['x'], coords['y'])
            logger.info(f"Clicked search icon at ({coords['x']:.0f},{coords['y']:.0f}) [{coords.get('method')}]")
            return True
        return False

    async def _type_in_search(term: str) -> bool:
        """Fill search input, dispatch events, click search icon, then press Enter."""
        # 1. Try Playwright locator first (most reliable)
        for _sel in [
            '[role="dialog"] input:visible',
            '[aria-modal="true"] input:visible',
            'input[placeholder*="search" i]:visible',
            'input[placeholder*="tìm" i]:visible',
        ]:
            try:
                _inp = page.locator(_sel).first
                if await _inp.count() > 0:
                    await _inp.click()
                    await asyncio.sleep(0.2)
                    await _inp.fill("")
                    await asyncio.sleep(0.1)
                    await _inp.fill(term)
                    await _inp.dispatch_event("input")
                    await _inp.dispatch_event("change")
                    await asyncio.sleep(0.3)
                    # Press Enter directly on the input (guarantees focus stays on it)
                    await _inp.press("Enter")
                    await asyncio.sleep(0.5)
                    # Also click the magnifying glass icon as backup
                    await _click_search_icon()
                    await asyncio.sleep(0.3)
                    return True
            except Exception:
                pass

        # 2. Fallback: coordinate-based click then keyboard type
        try:
            coords = await page.evaluate("""() => {
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
            if coords:
                await page.mouse.click(coords['x'], coords['y'])
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
                await asyncio.sleep(0.1)
                await page.keyboard.type(term, delay=30)
                await asyncio.sleep(0.3)
                await page.keyboard.press("Enter")
                await asyncio.sleep(0.5)
                await _click_search_icon()
                await asyncio.sleep(0.3)
                return True
        except Exception:
            pass
        return False

    # --- Step 4: Search by product ID only — never by name ---
    if not sp_id:
        logger.warning("No product ID available — cancelling dialog, will post without product")
        try:
            page.remove_listener("request", _capture_api)
        except Exception:
            pass
        await _cancel_dialog()
        return False

    async def _fill_search_input(term: str) -> bool:
        """Fill search input WITHOUT triggering search (no icon click, no Enter)."""
        for _sel in [
            '[role="dialog"] input:visible',
            '[aria-modal="true"] input:visible',
            'input[placeholder*="search" i]:visible',
            'input[placeholder*="tìm" i]:visible',
        ]:
            try:
                _inp = page.locator(_sel).first
                if await _inp.count() > 0:
                    await _inp.click()
                    await asyncio.sleep(0.2)
                    await _inp.fill("")
                    await asyncio.sleep(0.1)
                    await _inp.fill(term)
                    await _inp.dispatch_event("input")
                    await _inp.dispatch_event("change")
                    return True
            except Exception:
                pass
        return False

    async def _check_current_rows() -> int:
        """Check currently visible rows for sp_id — no search triggered."""
        rows = page.locator("table tbody tr")
        cnt = await rows.count()
        for _i in range(min(cnt, 20)):
            try:
                _rt = await rows.nth(_i).inner_text()
                if sp_id in _rt:
                    logger.info(f"  → ID match on current page at row {_i}: {_rt[:80]}")
                    return _i
            except Exception:
                pass
        return -1

    async def _trigger_search_and_find_rows(wait_secs: float = 3.0) -> int:
        """Click magnifying glass + Enter to trigger search, wait, return matching row index."""
        await _click_search_icon()
        await asyncio.sleep(0.3)
        # Also press Enter in case icon click didn't trigger
        try:
            _inp = page.locator('[role="dialog"] input:visible').first
            if await _inp.count() > 0:
                await _inp.press("Enter")
        except Exception:
            pass
        await asyncio.sleep(wait_secs)
        await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_showcase_search.png"))
        rows = page.locator("table tbody tr")
        cnt = await rows.count()
        logger.info(f"Showcase rows after search: {cnt}")
        for _i in range(min(cnt, 20)):
            try:
                _rt = await rows.nth(_i).inner_text()
                logger.info(f"  Row {_i}: {_rt[:120]}")
                if sp_id in _rt:
                    logger.info(f"  → ID match at row {_i}")
                    return _i
            except Exception:
                pass
        return -1

    async def _search_and_find_rows(wait_secs: float = 3.0) -> int:
        """
        Two-phase search:
        1. Fill input + check page 1 immediately (newly added products appear here).
        2. If not found → click magnifying glass to search all pages.
        """
        filled = await _fill_search_input(sp_id)
        if filled:
            logger.info(f"Filled search box with '{sp_id}' — checking page 1 first")
            await asyncio.sleep(0.5)
            page1_idx = await _check_current_rows()
            if page1_idx >= 0:
                logger.info(f"Found product on page 1 without search at row {page1_idx}")
                return page1_idx
        else:
            logger.warning("Could not fill search input")

        # Page 1 didn't have it → trigger search via magnifying glass
        logger.info("Not found on page 1 — triggering search via magnifying glass")
        return await _trigger_search_and_find_rows(wait_secs=wait_secs)

    # --- Step 5: Search and select ---
    found_idx = await _search_and_find_rows(wait_secs=3.0)

    # If not found: retry up to 6 times.
    # - Odd retries: just wait 5s and retype (faster, avoids tab reload overhead)
    # - Even retries: re-click tab to force list refresh, then wait 7s
    # TikTok can take 10–50s to index a newly-added product into the dialog search.
    if found_idx < 0 and in_showcase:
        async def _retype_without_tab_click(wait_before: float) -> int:
            await asyncio.sleep(wait_before)
            return await _search_and_find_rows(wait_secs=3.0)

        async def _retype_with_tab_click(wait_after_click: float) -> int:
            for tab_sel in ['text="Showcase products"', ':text("Showcase products")', ':text("Sản phẩm giới thiệu")']:
                try:
                    _t = page.locator(tab_sel).first
                    if await _t.count() > 0 and await _t.is_visible():
                        await _t.click()
                        await asyncio.sleep(wait_after_click)
                        break
                except Exception:
                    pass
            return await _search_and_find_rows(wait_secs=3.0)

        for _retry in range(6):
            if _retry % 2 == 0:
                # Even: re-click tab (refresh list) + longer wait
                logger.info(f"Search retry {_retry+1}/6: re-clicking Showcase tab, waiting 7s")
                found_idx = await _retype_with_tab_click(wait_after_click=7)
            else:
                # Odd: just retype (no tab click) — avoids list reload delay
                logger.info(f"Search retry {_retry+1}/6: retype only, waiting 5s")
                found_idx = await _retype_without_tab_click(wait_before=5)
            if found_idx >= 0:
                break

    try:
        page.remove_listener("request", _capture_api)
    except Exception:
        pass

    # Fallback: clear search → browse ALL showcase rows to find by ID
    if found_idx < 0 and in_showcase:
        logger.info("Search returned 0 results — clearing search to browse all showcase products")
        for _tab_sel in ['text="Showcase products"', ':text("Showcase products")', ':text("Sản phẩm giới thiệu")']:
            try:
                _t = page.locator(_tab_sel).first
                if await _t.count() > 0 and await _t.is_visible():
                    await _t.click()
                    await asyncio.sleep(3)
                    break
            except Exception:
                pass
        # Clear search box so all products load
        for _sel in ['[role="dialog"] input:visible', '[aria-modal="true"] input:visible', 'input[placeholder*="search" i]:visible']:
            try:
                _inp = page.locator(_sel).first
                if await _inp.count() > 0:
                    await _inp.click()
                    await _inp.fill("")
                    await _inp.dispatch_event("input")
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(2)
                    break
            except Exception:
                pass
        # Scroll through ALL rows to find sp_id
        for _scroll_attempt in range(10):
            rows = page.locator("table tbody tr")
            cnt = await rows.count()
            logger.info(f"Browse all rows attempt {_scroll_attempt}: {cnt} rows")
            for _i in range(cnt):
                try:
                    _rt = await rows.nth(_i).inner_text()
                    if sp_id in _rt:
                        logger.info(f"Found product ID in unfiltered row {_i}")
                        found_idx = _i
                        break
                except Exception:
                    pass
            if found_idx >= 0:
                break
            # Scroll down inside dialog to load more rows
            try:
                await page.evaluate("""() => {
                    const tbodies = document.querySelectorAll('table tbody');
                    if (tbodies.length) {
                        const el = tbodies[tbodies.length-1];
                        el.scrollTop += 400;
                    }
                }""")
                await asyncio.sleep(1.5)
            except Exception:
                break

    if found_idx < 0:
        logger.warning(f"Product ID {sp_id} not found in search results after retry — cancelling dialog")
        await _cancel_dialog()
        return False

    product_rows = page.locator("table tbody tr")

    for target in [
        product_rows.nth(found_idx).locator('input[type="radio"]').first,
        product_rows.nth(found_idx).locator('input[type="checkbox"]').first,
        product_rows.nth(found_idx),
    ]:
        try:
            if await target.count() > 0:
                await target.click(force=True)
                logger.info(f"Selected Showcase row {found_idx} (product ID: {sp_id})")
                return True
        except Exception:
            pass

    logger.warning("Could not click any target in matching row — cancelling dialog")
    await _cancel_dialog()
    return False


async def _add_product_to_showcase(page, sp_id: str, sp_name: str, intercepted_api: list = None) -> bool:
    """
    When showcase search returns 0 results, try to add the product to the user's showcase.
    Returns True if the product was likely added.

    Strategies (in order):
      1. Try calling TikTok's internal API directly from the current page (using intercepted endpoints)
      2. Open new tab → navigate to TikTok Studio Monetization page, find showcase management
      3. Open new tab → TikTok Affiliate Center
      4. Open new tab → try direct product page URL variations
    """
    search_term = sp_id if sp_id else sp_name[:80]
    logger.info(f"_add_product_to_showcase: product '{search_term}'")
    if intercepted_api is None:
        intercepted_api = []

    added = False

    # Helper: JS snippet to verify product appears in showcase list
    _verify_js = """async (pid) => {
        for (let offset = 0; offset < 200; offset += 20) {
            try {
                const r = await fetch(
                    `https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list?offset=${offset}&count=20`,
                    {credentials: 'include'}
                );
                const data = await r.json();
                const prods = data?.data?.products || [];
                if (prods.some(p => [p.id, p.item_id, p.product_id].filter(Boolean).map(String).includes(pid)))
                    return true;
                if (!data?.data?.has_more || !prods.length) break;
            } catch(e) { break; }
        }
        return false;
    }"""

    # Common request body variants to try for add endpoints
    add_bodies = [
        {"product_id": sp_id},
        {"item_id": sp_id},
        {"product_id": sp_id, "item_id": sp_id},
        {"product_ids": [sp_id]},
    ]

    # ── Strategy 0: OEC direct API (add_targets:[2] = showcase) ─────────────────
    # Confirmed working: POST oec/content/creator/products with add_targets:[2]
    # returns is_in_showcase:True. Much faster than UI-based strategies.
    if sp_id:
        try:
            oec_url = "https://shop.tiktok.com/aweme/v1/oec/content/creator/products?aid=1180&carrier_region=TH"
            oec_resp = await page.context.request.post(
                oec_url,
                data=json.dumps({"products": [{"product_id": sp_id}], "add_targets": [2]}),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Origin": "https://www.tiktok.com",
                    "Referer": "https://www.tiktok.com/tiktokstudio/upload",
                },
            )
            oec_text = await oec_resp.text()
            logger.info(f"_add_product_to_showcase Strategy 0 OEC: {oec_resp.status} {oec_text[:300]}")
            oec_data = json.loads(oec_text)
            pid_result = (oec_data.get("add_results") or {}).get(sp_id, {})
            if pid_result.get("is_in_showcase"):
                await asyncio.sleep(2)
                verified = await page.evaluate(_verify_js, sp_id)
                logger.info(f"_add_product_to_showcase Strategy 0: is_in_showcase=True, list verify={verified}")
                if verified:
                    return True
        except Exception as oec_e:
            logger.warning(f"_add_product_to_showcase Strategy 0: {oec_e}")

    # ── Strategy 1: Discover add-product API from intercepted shop.tiktok.com calls ──
    # The showcase dialog calls shop.tiktok.com APIs.  Cross-origin fetch from
    # www.tiktok.com is blocked by CORS for most paths, so we open a new tab on
    # shop.tiktok.com first, then call the API same-origin (no CORS restriction).
    if sp_id and intercepted_api:
        import re as _re

        # Collect unique base URLs (strip query-string) for shop.tiktok.com
        seen_bases: set = set()
        shop_bases: list = []
        for item in intercepted_api:
            url = item['url']
            if 'shop.tiktok.com' not in url:
                continue
            # skip static assets
            if any(url.endswith(ext) for ext in ('.js', '.css', '.png', '.jpg', '.woff')):
                continue
            base = url.split('?')[0].rstrip('/')
            if base not in seen_bases:
                seen_bases.add(base)
                shop_bases.append({'url': base, 'method': item['method']})

        logger.info(f"Strategy 1: {len(shop_bases)} unique shop.tiktok.com endpoints")
        logger.info(f"  First 8: {[b['url'] for b in shop_bases[:8]]}")

        # Build candidate "add" endpoints from the captured list/search endpoints
        add_candidates: list = []
        for item in shop_bases[:15]:
            base = item['url']
            segs = base.split('/')
            # Replace the last segment that is a read keyword with 'add'
            for i in range(len(segs) - 1, -1, -1):
                if segs[i].lower() in ('list', 'get', 'search', 'query'):
                    new_base = '/'.join(segs[:i] + ['add'] + segs[i + 1:])
                    if new_base not in add_candidates:
                        add_candidates.append(new_base)
                    break
            # Also try the base itself if it looks like an "add" endpoint
            if any(kw in base.lower() for kw in ('add_product', 'add_showcase', 'promote')):
                if base not in add_candidates:
                    add_candidates.append(base)

        logger.info(f"Strategy 1 candidate add URLs: {add_candidates[:5]}")

        # Extract auth headers from the intercepted showcase_product/list call.
        # These include X-Secsdk-Csrf-Token, X-Tt-Logid, etc. needed by TikTok APIs.
        list_req_headers: dict = {}
        for item in intercepted_api:
            if 'showcase_product/list' in item['url'] and item.get('headers'):
                list_req_headers = {k: v for k, v in item['headers'].items()
                                    if k.lower() not in ('host', 'content-length', 'origin', 'referer')}
                logger.info(f"Using headers from showcase_product/list: {list(list_req_headers.keys())}")
                break

        # Step A: try calling the add endpoint directly from the current TikTok Studio page.
        # We pass the captured auth headers so TikTok's security checks pass.
        for add_url in add_candidates[:4]:
            if added:
                break
            for body_dict in add_bodies:
                try:
                    result = await page.evaluate("""async ([url, body_str, hdrs]) => {
                        try {
                            const resp = await fetch(url, {
                                method: 'POST',
                                credentials: 'include',
                                headers: {
                                    ...hdrs,
                                    'Content-Type': 'application/json',
                                    'Accept': 'application/json, text/plain, */*',
                                },
                                body: body_str,
                            });
                            const body = await resp.text();
                            return {status: resp.status, body: body.slice(0, 500)};
                        } catch(e) {
                            return {error: String(e)};
                        }
                    }""", [add_url, json.dumps(body_dict), list_req_headers])
                    logger.info(f"Direct-from-studio {add_url[-50:]} body={body_dict}: {result}")
                    resp_body = (result or {}).get('body', '')
                    if result and result.get('status') in (200, 201) and '"code":0' in resp_body:
                        # Verify the product actually appeared in the list
                        await asyncio.sleep(2)
                        verified = await page.evaluate(_verify_js, sp_id)
                        logger.info(f"Strategy 1A verification: product_in_list={verified}")
                        if verified:
                            added = True
                            logger.info(f"Strategy 1A SUCCESS: {add_url} body={body_dict}")
                            break
                        else:
                            logger.info(f"Strategy 1A: code:0 but product not in list yet")
                except Exception as e:
                    logger.debug(f"Direct API error: {e}")
            if added:
                break

        # Step B: if direct call failed (CORS), open a same-origin shop.tiktok.com tab
        # Navigate to the list API URL (JSON page, no CAPTCHA UI) so cookies are authenticated
        if not added and add_candidates:
            shop_tab = await page.context.new_page()
            try:
                # Navigate to the EXISTING list endpoint (returns JSON, not a CAPTCHA page)
                list_url = shop_bases[0]['url'] if shop_bases else \
                    "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list"
                await shop_tab.goto(list_url + "?offset=0&count=1",
                                    wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                # Verify we got an authenticated JSON response (not a captcha redirect)
                page_text = await shop_tab.inner_text("body")
                is_json = page_text.strip().startswith('{')
                logger.info(f"Shop list page: json={is_json}, url={shop_tab.url}")

                for add_url in add_candidates[:4]:
                    if added:
                        break
                    for body_dict in add_bodies:
                        try:
                            result = await shop_tab.evaluate("""async ([url, body_str]) => {
                                try {
                                    const resp = await fetch(url, {
                                        method: 'POST',
                                        credentials: 'include',
                                        headers: {
                                            'Content-Type': 'application/json',
                                            'Accept': 'application/json, text/plain, */*',
                                        },
                                        body: body_str,
                                    });
                                    const body = await resp.text();
                                    return {status: resp.status, body: body.slice(0, 500)};
                                } catch(e) {
                                    return {error: String(e)};
                                }
                            }""", [add_url, json.dumps(body_dict)])
                            logger.info(f"Shop-tab {add_url[-50:]} body={body_dict}: {result}")
                            resp_body = (result or {}).get('body', '')
                            if result and result.get('status') in (200, 201) and '"code":0' in resp_body:
                                # Verify by listing from the main page (dialog context)
                                await asyncio.sleep(2)
                                verified = await page.evaluate(_verify_js, sp_id)
                                logger.info(f"Strategy 1B verification: product_in_list={verified}")
                                if verified:
                                    added = True
                                    logger.info(f"Strategy 1B SUCCESS: {add_url}")
                                    break
                                else:
                                    logger.info(f"Strategy 1B: code:0 but product not verified in list")
                        except Exception as e:
                            logger.debug(f"Shop-tab API error: {e}")
                    if added:
                        break
            except Exception as e:
                logger.warning(f"Strategy 1B shop tab error: {e}")
            finally:
                try:
                    await shop_tab.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s1_shop.png"))
                except Exception:
                    pass
                await shop_tab.close()

    # ── Strategy 2: TikTok Studio Monetization → TikTok Shop for Creator ────
    if not added:
        mgmt_page = await page.context.new_page()
        try:
            logger.info("Strategy 2: TikTok Studio Monetization → TikTok Shop for Creator")
            await mgmt_page.goto(
                "https://www.tiktok.com/tiktokstudio/monetization",
                wait_until="domcontentloaded", timeout=30000,
            )
            await asyncio.sleep(8)
            await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s2.png"))

            # Log all visible buttons/links for diagnosis
            elems = await mgmt_page.evaluate("""() =>
                Array.from(document.querySelectorAll('a, button'))
                    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                    .map(e => ({text: e.textContent.trim().slice(0, 60), href: e.href || ''}))
                    .filter(e => e.text)
                    .slice(0, 40)
            """)
            logger.info(f"Monetization elements: {elems}")

            # Click "View" button for TikTok Shop for Creator row.
            # Use Playwright locators — the page has exactly two "View" buttons,
            # TikTok Shop for Creator is always first (above LIVE rewards).
            clicked_view = False
            for view_sel in [
                'button:has-text("View")', 'a:has-text("View")',
                'button:has-text("Xem")', 'a:has-text("Xem")',
            ]:
                try:
                    btn = mgmt_page.locator(view_sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        clicked_view = True
                        logger.info(f"Clicked View button via: {view_sel}")
                        break
                except Exception:
                    pass

            if not clicked_view:
                # Fallback: JS click on first View/Xem button
                clicked_view = await mgmt_page.evaluate("""() => {
                    const btn = Array.from(document.querySelectorAll('button, a'))
                        .find(b => {
                            const t = b.textContent.trim();
                            const r = b.getBoundingClientRect();
                            return (t === 'View' || t === 'Xem') && r.width > 0;
                        });
                    if (btn) { btn.click(); return true; }
                    return false;
                }""")
                logger.info(f"JS fallback View click: {clicked_view}")

            await asyncio.sleep(6)
            await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s2b.png"))

            cur_url = mgmt_page.url
            cur_title = await mgmt_page.title()
            logger.info(f"After View click: url={cur_url}, title={cur_title!r}")

            # Log elements on the new page
            sub_elems = await mgmt_page.evaluate("""() =>
                Array.from(document.querySelectorAll('a, button'))
                    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                    .map(e => ({text: e.textContent.trim().slice(0, 80), href: e.href || ''}))
                    .filter(e => e.text)
                    .slice(0, 60)
            """)
            logger.info(f"After View click elements: {sub_elems}")

            # Look for product search in this page
            added = await _search_and_add_in_page(mgmt_page, search_term)

            # If not found, click any "Showcase" / "Product" nav link to go deeper
            if not added:
                nav_keywords = ['showcase', 'product', 'sản phẩm', 'giới thiệu', 'add', 'thêm', 'find', 'tìm']
                for item in sub_elems:
                    item_text_lower = item.get('text', '').lower()
                    item_href_lower = item.get('href', '').lower()
                    if any(kw in item_text_lower or kw in item_href_lower for kw in nav_keywords):
                        try:
                            if item.get('href'):
                                await mgmt_page.goto(item['href'], wait_until="domcontentloaded", timeout=15000)
                            else:
                                el = mgmt_page.get_by_text(item['text'], exact=True).first
                                if await el.count() > 0 and await el.is_visible():
                                    await el.click()
                            await asyncio.sleep(4)
                            logger.info(f"Navigated to '{item['text']}' / '{item['href']}'")
                            await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s2c.png"))
                            added = await _search_and_add_in_page(mgmt_page, search_term)
                            if added:
                                break
                        except Exception:
                            pass
                    if added:
                        break

        except Exception as e:
            logger.warning(f"Strategy 2 error: {e}")
        finally:
            try:
                await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s2_final.png"))
            except Exception:
                pass
            await mgmt_page.close()

    # ── Strategy 3: TikTok Affiliate Center (new tab) ────────────────────────
    if not added:
        mgmt_page = await page.context.new_page()
        try:
            for aff_url in [
                "https://affiliate.tiktok.com/",
                "https://affiliate.tiktok.com/product/search",
                "https://affiliate.tiktok.com/product/list",
                "https://affiliate.tiktok.com/product",
            ]:
                try:
                    logger.info(f"Strategy 3: Trying {aff_url}")
                    await mgmt_page.goto(aff_url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(5)
                    await mgmt_page.screenshot(
                        path=os.path.join(_TEMP_DIR, f"debug_add_s3_{aff_url.split('/')[-1] or 'root'}.png"),
                    )
                    logger.info(f"Saved aff screenshot for {aff_url}")

                    # Log page title and URL after possible redirect
                    title = await mgmt_page.title()
                    current_url = mgmt_page.url
                    logger.info(f"Affiliate page title: {title!r}, url: {current_url}")

                    added = await _search_and_add_in_page(mgmt_page, search_term)
                    if added:
                        break
                except Exception as e:
                    logger.warning(f"Affiliate URL {aff_url}: {e}")
        except Exception as e:
            logger.warning(f"Strategy 3 error: {e}")
        finally:
            try:
                await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s3_final.png"))
            except Exception:
                pass
            await mgmt_page.close()

    # ── Strategy 4: Direct product page URL variations (new tab) ─────────────
    # shop.tiktok.com/view/product/{id} redirects to the actual product page.
    # If the user is logged in as a creator/affiliate, "Add to showcase" (or
    # Vietnamese equivalent) should be visible on the product detail page.
    if not added and sp_id:
        mgmt_page = await page.context.new_page()
        try:
            for idx, prod_url in enumerate([
                f"https://shop.tiktok.com/view/product/{sp_id}",
            ]):
                try:
                    logger.info(f"Strategy 4: {prod_url}")
                    await mgmt_page.goto(prod_url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(6)
                    title = await mgmt_page.title()
                    final_url = mgmt_page.url
                    logger.info(f"Strategy 4 loaded: title={title!r}, url={final_url}")
                    await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, f"debug_add_s4_{idx}.png"))

                    # Skip if redirected to TikTok main (not a product page)
                    if 'shop.tiktok.com' not in final_url and 'TikTok Shop' not in title:
                        logger.info(f"Strategy 4: not a product page ({final_url}), skipping")
                        continue

                    # Log all visible buttons to discover add-to-showcase button text
                    visible_btns = await mgmt_page.evaluate("""() =>
                        Array.from(document.querySelectorAll('button, [role="button"], a'))
                            .filter(e => {
                                const r = e.getBoundingClientRect();
                                return r.width > 0 && r.height > 0 && r.top < window.innerHeight;
                            })
                            .map(e => e.textContent.trim().replace(/\\s+/g, ' '))
                            .filter(t => t && t.length < 100)
                    """)
                    logger.info(f"Strategy 4 visible buttons: {visible_btns}")

                    # Try all plausible button texts (English + Vietnamese).
                    # Exclude sidebar/nav elements by checking the button is in the main content area.
                    add_sels = [
                        'button:has-text("Add to showcase")',
                        'button:has-text("Add to Showcase")',
                        'button:has-text("Thêm vào giới thiệu")',
                        'button:has-text("Thêm vào Showcase")',
                        'button:has-text("Thêm showcase")',
                        'button:has-text("Giới thiệu")',
                        '[data-e2e*="add-showcase"]',
                        '[data-e2e*="add_showcase"]',
                        '[data-e2e*="showcase"]',
                        'button:has-text("showcase")',
                        'button:has-text("Showcase")',
                    ]

                    for add_sel in add_sels:
                        try:
                            btn = mgmt_page.locator(add_sel).first
                            cnt = await btn.count()
                            if cnt > 0 and await btn.is_visible():
                                await btn.click()
                                await asyncio.sleep(3)
                                await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s4_after_sell.png"))
                                await _confirm_any_dialog(mgmt_page)
                                await asyncio.sleep(2)
                                await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s4_after_confirm.png"))
                                added = True
                                logger.info(f"Strategy 4 SUCCESS: {prod_url} via '{add_sel}'")
                                break
                        except Exception:
                            pass

                    if not added:
                        # Try by JS — look for product-specific "showcase" element in main content area
                        added_js = await mgmt_page.evaluate("""() => {
                            const keywords = ['showcase', 'giới thiệu', 'Showcase', 'Giới thiệu'];
                            // Only look in main content (not sidebar/nav - exclude elements in left 150px)
                            const candidates = Array.from(document.querySelectorAll('button, [role="button"]'))
                                .filter(e => {
                                    const t = e.textContent.trim();
                                    const r = e.getBoundingClientRect();
                                    return r.width > 0 && r.height > 0 && r.top < window.innerHeight &&
                                           r.left > 150 &&  // not in the left sidebar
                                           keywords.some(k => t === k || t.toLowerCase().includes(k.toLowerCase()));
                                });
                            if (candidates.length > 0) {
                                candidates[0].click();
                                return candidates[0].textContent.trim();
                            }
                            return null;
                        }""")
                        if added_js:
                            await asyncio.sleep(2)
                            await _confirm_any_dialog(mgmt_page)
                            added = True
                            logger.info(f"Strategy 4 JS SUCCESS: clicked '{added_js}'")

                    if added:
                        break
                except Exception as e:
                    logger.warning(f"Product page {prod_url}: {e}")
        except Exception as e:
            logger.warning(f"Strategy 4 error: {e}")
        finally:
            try:
                await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s4_final.png"))
            except Exception:
                pass
            await mgmt_page.close()

    # ── Strategy 5: Call showcase_product/add from TikTok Studio LIVE page context ──
    # The LIVE setup page (tiktokstudio/live) creates a different session context
    # than the upload dialog. Calling add from here may establish the right credentials.
    if not added and sp_id:
        mgmt_page = await page.context.new_page()
        try:
            logger.info("Strategy 5: TikTok Studio LIVE page context")
            await mgmt_page.goto(
                "https://www.tiktok.com/tiktokstudio/live",
                wait_until="domcontentloaded", timeout=30000,
            )
            await asyncio.sleep(5)
            await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s5_live.png"))
            logger.info(f"LIVE page url={mgmt_page.url}")

            # Log visible elements
            live_elems = await mgmt_page.evaluate("""() =>
                Array.from(document.querySelectorAll('a, button'))
                    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                    .map(e => e.textContent.trim().slice(0, 60)).filter(t => t).slice(0, 40)
            """)
            logger.info(f"LIVE page elements: {live_elems}")

            # Try add endpoint from this context with multiple body structures
            for add_url in [
                f"https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/add",
                f"https://shop.tiktok.com/api/v1/affiliate/creator/showcase/product/add",
                f"https://shop.tiktok.com/api/v1/creator/showcase/product/add",
            ]:
                if added:
                    break
                for body_dict in [
                    {"product_id": sp_id},
                    {"item_id": sp_id},
                    {"product_id": sp_id, "item_id": sp_id},
                    {"product_ids": [sp_id]},
                ]:
                    try:
                        result = await mgmt_page.evaluate("""async ([url, body_str]) => {
                            try {
                                const resp = await fetch(url, {
                                    method: 'POST',
                                    credentials: 'include',
                                    headers: {
                                        'Content-Type': 'application/json',
                                        'Accept': 'application/json, text/plain, */*',
                                    },
                                    body: body_str,
                                });
                                const text = await resp.text();
                                return {status: resp.status, body: text.slice(0, 500)};
                            } catch(e) {
                                return {error: String(e)};
                            }
                        }""", [add_url, json.dumps(body_dict)])
                        logger.info(f"Strategy 5 {add_url[-50:]} body={body_dict}: {result}")
                        resp_body = (result or {}).get('body', '')
                        if result and result.get('status') in (200, 201) and '"code":0' in resp_body:
                            # Verify by listing from dialog page
                            await asyncio.sleep(2)
                            verified = await page.evaluate(_verify_js, sp_id)
                            logger.info(f"Strategy 5 verification: {verified}")
                            if verified:
                                added = True
                                logger.info(f"Strategy 5 SUCCESS: {add_url}")
                                break
                            else:
                                logger.info(f"Strategy 5: code:0 but product not in list")
                    except Exception as e:
                        logger.debug(f"Strategy 5 API error: {e}")
                if added:
                    break

            # If still not added, try navigating to any product management page within LIVE
            if not added:
                for live_product_url in [
                    "https://www.tiktok.com/tiktokstudio/live?tab=product",
                    "https://www.tiktok.com/tiktokstudio/monetization",
                ]:
                    try:
                        await mgmt_page.goto(live_product_url, wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(4)
                        await mgmt_page.screenshot(
                            path=os.path.join(_TEMP_DIR, f"debug_add_s5_{live_product_url.split('?')[0].split('/')[-1]}.png")
                        )
                        # Try API from this context
                        result = await mgmt_page.evaluate("""async (pid) => {
                            try {
                                const resp = await fetch(
                                    'https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/add',
                                    {
                                        method: 'POST',
                                        credentials: 'include',
                                        headers: {'Content-Type': 'application/json'},
                                        body: JSON.stringify({product_id: pid}),
                                    }
                                );
                                const text = await resp.text();
                                return {status: resp.status, body: text.slice(0, 500)};
                            } catch(e) { return {error: String(e)}; }
                        }""", sp_id)
                        logger.info(f"Strategy 5b {live_product_url}: {result}")
                        if (result or {}).get('status') in (200, 201) and '"code":0' in (result or {}).get('body', ''):
                            await asyncio.sleep(2)
                            verified = await page.evaluate(_verify_js, sp_id)
                            if verified:
                                added = True
                                logger.info(f"Strategy 5b SUCCESS via {live_product_url}")
                                break
                    except Exception as e:
                        logger.warning(f"Strategy 5b {live_product_url}: {e}")
        except Exception as e:
            logger.warning(f"Strategy 5 error: {e}")
        finally:
            try:
                await mgmt_page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_s5_final.png"))
            except Exception:
                pass
            await mgmt_page.close()

    # ── Strategy 6: Call add API via TikTok main site (www.tiktok.com) context ──
    # Some TikTok APIs are only accessible from www.tiktok.com origin, not shop.tiktok.com
    if not added and sp_id:
        mgmt_page = await page.context.new_page()
        try:
            logger.info("Strategy 6: www.tiktok.com affiliate API paths")
            await mgmt_page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

            for tiktok_add_url in [
                "https://www.tiktok.com/api/creator/affiliate/showcase/product/add/",
                "https://www.tiktok.com/api/creator/shopaffiliate/showcase/add/",
                "https://www.tiktok.com/api/shop/creator/showcase/product/add/",
            ]:
                if added:
                    break
                for body_dict in [{"product_id": sp_id}, {"item_id": sp_id}]:
                    try:
                        result = await mgmt_page.evaluate("""async ([url, body_str]) => {
                            try {
                                const resp = await fetch(url, {
                                    method: 'POST',
                                    credentials: 'include',
                                    headers: {'Content-Type': 'application/json'},
                                    body: body_str,
                                });
                                const text = await resp.text();
                                return {status: resp.status, body: text.slice(0, 500), url: resp.url};
                            } catch(e) { return {error: String(e)}; }
                        }""", [tiktok_add_url, json.dumps(body_dict)])
                        logger.info(f"Strategy 6 {tiktok_add_url[-60:]} body={body_dict}: {result}")
                        resp_body = (result or {}).get('body', '')
                        if result and result.get('status') in (200, 201) and '"code":0' in resp_body:
                            await asyncio.sleep(2)
                            verified = await page.evaluate(_verify_js, sp_id)
                            if verified:
                                added = True
                                logger.info(f"Strategy 6 SUCCESS: {tiktok_add_url}")
                                break
                    except Exception as e:
                        logger.debug(f"Strategy 6 API error: {e}")
                if added:
                    break
        except Exception as e:
            logger.warning(f"Strategy 6 error: {e}")
        finally:
            await mgmt_page.close()

    logger.info(f"_add_product_to_showcase result: added={added}")
    return added


async def _search_and_add_in_page(page, search_term: str) -> bool:
    """
    On the currently loaded page, search for search_term in any visible search input
    and click 'Add to showcase' on the first matching result.
    """
    # Find a visible search input
    search_input = None
    for inp_sel in [
        'input[placeholder*="earch"]', 'input[placeholder*="roduct"]',
        'input[placeholder*="ìm"]', 'input[type="search"]', 'input[type="text"]',
    ]:
        try:
            inp = page.locator(inp_sel).first
            if await inp.count() > 0 and await inp.is_visible():
                search_input = inp
                break
        except Exception:
            pass

    if not search_input:
        return False

    try:
        await search_input.click()
        await search_input.fill(search_term)
        await page.keyboard.press("Enter")
        await asyncio.sleep(3)
    except Exception:
        return False

    # Look for "Add to showcase" button(s) in results
    for add_sel in [
        'button:has-text("Add to showcase")',
        'button:has-text("Add to Showcase")',
        'button:has-text("Thêm vào showcase")',
        'button:has-text("Promote")',
        '[data-e2e*="add-showcase"]',
        '[data-e2e*="add_showcase"]',
    ]:
        try:
            btn = page.locator(add_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await asyncio.sleep(2)
                await _confirm_any_dialog(page)
                logger.info(f"_search_and_add_in_page: clicked '{add_sel}'")
                return True
        except Exception:
            pass

    return False


async def _confirm_any_dialog(page):
    """Click Confirm / OK / Add in any confirmation dialog that appears."""
    for sel in [
        'button:has-text("Confirm")', 'button:has-text("OK")',
        'button:has-text("Add")', 'button:has-text("Xác nhận")',
        'button:has-text("Đồng ý")',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await asyncio.sleep(1)
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


async def post_video(video_path: str, caption: str, product_url: str = "", product_id: str = "", shop_product: dict = {}, show_browser: bool = True) -> dict:
    """
    Post video to TikTok via TikTok Studio upload page.
    Optionally attach a TikTok Shop product link.
    Returns dict with success status and profile_url.
    """
    cookies = load_cookies()
    if not cookies:
        raise RuntimeError("Chưa đăng nhập TikTok.")

    username = _get_username_from_cookies(cookies)

    # When show_browser=False, push window far off-screen instead of headless
    # (headless mode may be detected by TikTok; off-screen keeps full codec support)
    chrome_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--autoplay-policy=no-user-gesture-required",
    ]
    if not show_browser:
        chrome_args += ["--window-position=-32000,-32000", "--window-size=1440,900"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",  # dùng Chrome thật thay vì Chromium bundled → có đầy đủ codec video
            args=chrome_args,
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=USER_AGENT,
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        # On macOS, --window-position CLI flags are ignored for real Chrome.
        # Use CDP to move the upload window far off-screen so it's invisible.
        # Then restore focus to the user's Chrome (localhost:8000) via AppleScript.
        if not show_browser:
            try:
                _cdp = await page.context.new_cdp_session(page)
                _win = await _cdp.send("Browser.getWindowForTarget")
                if _win.get("windowId"):
                    await _cdp.send("Browser.setWindowBounds", {
                        "windowId": _win["windowId"],
                        "bounds": {"left": -32000, "top": -32000, "width": 1440, "height": 900},
                    })
                await _cdp.detach()
            except Exception:
                pass
            # Bring the user's Chrome (with localhost:8000) back to foreground
            import subprocess
            try:
                subprocess.Popen(["osascript", "-e", """
                    tell application "Google Chrome"
                        repeat with w in windows
                            repeat with t in tabs of w
                                if URL of t contains "localhost" then
                                    set active tab index of w to tab index of t
                                    set index of w to 1
                                    activate
                                    return
                                end if
                            end repeat
                        end repeat
                        activate
                    end tell
                """])
            except Exception:
                pass

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
                _selected = await _search_and_select_myshop(page, sp_id, sp_name)
                if _selected:
                    await _confirm_product_dialog(page)
                else:
                    logger.error(f"SP gốc: could not find/add product — aborting post")
                    await browser.close()
                    return {"success": False, "message": f"Không tìm được sản phẩm '{sp_name}' (ID: '{sp_id}') để gắn vào video. Video chưa được đăng."}
            except Exception as e:
                logger.error(f"SP gốc attach exception: {e}")
                await browser.close()
                return {"success": False, "message": f"Lỗi khi gắn sản phẩm '{sp_name}' (ID: '{sp_id}'): {e}. Video chưa được đăng."}

        elif product_id:
            logger.info(f"Attaching showcase product by ID: {product_id}")
            try:
                await _open_add_link_modal(page)
                _selected = await _search_and_select_myshop(page, product_id, "")
                if _selected:
                    await _confirm_product_dialog(page)
                else:
                    logger.warning("Showcase: no product selected — posting without product")
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
                err_msg = str(e)
                logger.warning(f"Success check iter {_si}: {e}")
                if "browser has been closed" in err_msg or "Target page" in err_msg or "context or browser" in err_msg:
                    logger.info("Browser closed — treating as post success")
                    break

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


def _find_dummy_mp4() -> Optional[str]:
    """Find any usable MP4 in temp/ for dummy upload."""
    try:
        for fname in os.listdir(_TEMP_DIR):
            if fname.endswith(".mp4"):
                fpath = os.path.join(_TEMP_DIR, fname)
                if os.path.getsize(fpath) > 100_000:
                    return fpath
    except Exception:
        pass
    return None


async def add_product_to_showcase(product_id: str, product_name: str = "") -> dict:
    """
    Standalone function: attempt to add product_id to the creator's affiliate showcase.

    Opens TikTok Studio upload page with a dummy video, navigates through the
    "Add product links" dialog to the "Showcase products" tab, and calls
    showcase_product/add with proper auth headers captured from the list call.

    Returns dict: {added: bool, verified: bool, attempts: [...], message: str}
    """
    cookies = load_cookies()
    if not cookies:
        raise RuntimeError("Chưa đăng nhập TikTok.")

    dummy_mp4 = _find_dummy_mp4()
    if not dummy_mp4:
        raise RuntimeError("Không tìm thấy file MP4 trong temp/ để upload thử.")

    result: dict = {
        "product_id": product_id,
        "added": False,
        "verified": False,
        "attempts": [],
        "message": "",
    }

    verify_js = """async (pid) => {
        for (let offset = 0; offset < 200; offset += 20) {
            try {
                const r = await fetch(
                    `https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list?offset=${offset}&count=20`,
                    {credentials: 'include'}
                );
                const data = await r.json();
                const prods = data?.data?.products || [];
                // API uses product_id field, not id/item_id
                if (prods.some(p => [p.id, p.item_id, p.product_id].filter(Boolean).map(String).includes(pid)))
                    return true;
                if (!data?.data?.has_more || !prods.length) break;
            } catch(e) { break; }
        }
        return false;
    }"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=USER_AGENT,
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            # Check if already in showcase (retry once on timeout)
            for _nav_attempt in range(2):
                try:
                    await page.goto("https://www.tiktok.com/tiktokstudio/upload",
                                    wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception as nav_err:
                    if _nav_attempt == 0:
                        logger.warning(f"Initial navigation timeout, retrying: {nav_err}")
                        await asyncio.sleep(5)
                    else:
                        raise
            await asyncio.sleep(4)

            already = await page.evaluate(verify_js, product_id)
            result["attempts"].append({"step": "pre_check", "in_showcase": already})
            if already:
                result["added"] = True
                result["verified"] = True
                result["message"] = "Product already in showcase"
                return result

            # ── Strategy 0: OEC direct API (add_targets:[2] = showcase) ──────────
            # Confirmed working: add_targets:[2] returns is_in_showcase:True
            # This bypasses all UI automation and is the fastest path.
            logger.info("=== Strategy 0: OEC direct API (add_targets:[2]) ===")
            try:
                oec_url = "https://shop.tiktok.com/aweme/v1/oec/content/creator/products?aid=1180&carrier_region=TH"
                oec_resp = await context.request.post(
                    oec_url,
                    data=json.dumps({"products": [{"product_id": product_id}], "add_targets": [2]}),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Origin": "https://www.tiktok.com",
                        "Referer": "https://www.tiktok.com/tiktokstudio/upload",
                    },
                )
                oec_text = await oec_resp.text()
                logger.info(f"Strategy 0 OEC: {oec_resp.status} {oec_text[:400]}")
                result["attempts"].append({"step": "oec_direct", "status": oec_resp.status, "body": oec_text[:400]})
                oec_data = json.loads(oec_text)
                pid_result = (oec_data.get("add_results") or {}).get(product_id, {})
                if pid_result.get("is_in_showcase"):
                    await asyncio.sleep(2)
                    verified = await page.evaluate(verify_js, product_id)
                    logger.info(f"Strategy 0: is_in_showcase=True, list verify={verified}")
                    if verified:
                        result["added"] = True
                        result["verified"] = True
                        result["message"] = "SUCCESS: OEC direct API add_targets=[2]"
                        return result
            except Exception as oec_e:
                logger.warning(f"Strategy 0 OEC direct: {oec_e}")

            # Upload dummy video to open the upload form
            inputs = page.locator('input[type="file"]')
            await inputs.first.wait_for(state="attached", timeout=10000)
            await inputs.first.set_input_files(dummy_mp4)
            logger.info(f"add_product_to_showcase: uploaded dummy {dummy_mp4}")

            # Wait for caption area to appear
            for _ in range(20):
                await asyncio.sleep(3)
                await _dismiss_popups(page)
                try:
                    el = page.locator('div[contenteditable="true"]').first
                    if await el.count() > 0 and await el.is_visible():
                        break
                except Exception:
                    pass

            await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_direct_upload.png"))

            # Set up interception BEFORE opening modal — capture ALL API calls
            intercepted: list = []
            async def _cap(req):
                url = req.url
                # Capture all API calls to shop.tiktok.com and TikTok API paths
                if any(skip in url for skip in ['.js', '.css', '.png', '.jpg', '.woff', '.svg', '.ico', '.woff2', '.ttf', '.gif']):
                    return
                if any(domain in url for domain in ['shop.tiktok.com/api', 'tiktok.com/api/', 'tiktok.com/aweme/']):
                    intercepted.append({'url': url, 'method': req.method, 'headers': dict(req.headers)})
            page.on("request", _cap)

            # Open Add product links modal
            await _open_add_link_modal(page)
            await asyncio.sleep(2)
            await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_direct_modal.png"))

            # Switch to Showcase products tab
            for tab_sel in ['text="Showcase products"', ':text("Showcase products")',
                            ':text("Sản phẩm giới thiệu")']:
                try:
                    tab = page.locator(tab_sel).first
                    if await tab.count() > 0 and await tab.is_visible():
                        await tab.click()
                        logger.info(f"add_product_to_showcase: switched to Showcase tab via {tab_sel}")
                        break
                except Exception:
                    pass
            await asyncio.sleep(3)
            await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_direct_showcase_tab.png"))

            # ── Log dialog initial state (before any search) ──────────────────
            # This tells us if the 20 LIVE showcase products appear in the table
            # on initial load (which would confirm showcase_product/list = dialog data)
            try:
                init_elems = await page.evaluate("""() => {
                    const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]'))
                        .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                    const cont = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
                    return Array.from(cont.querySelectorAll('tr'))
                        .filter(tr => { const r = tr.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                        .map(tr => tr.innerText.trim().replace(/\\s+/g, ' ').slice(0, 120))
                        .filter(t => t).slice(0, 15);
                }""")
                logger.info(f"Dialog initial rows (before search): {init_elems}")
                result["dialog_initial_rows"] = init_elems
            except Exception as e:
                logger.debug(f"dialog initial rows: {e}")

            # ── Fetch account_info to understand creator's affiliate status ─────
            try:
                acct_info = await page.evaluate("""async () => {
                    try {
                        const r = await fetch(
                            'https://shop.tiktok.com/api/v1/streamer_desktop/account_info/get',
                            {credentials: 'include'}
                        );
                        return await r.text();
                    } catch(e) { return 'error: ' + e; }
                }""")
                logger.info(f"account_info/get: {acct_info[:3000]}")
                result["account_info"] = acct_info[:3000]
            except Exception as e:
                logger.debug(f"account_info fetch: {e}")

            # Get auth headers from intercepted showcase_product/list
            list_headers: dict = {}
            for item in intercepted:
                if 'showcase_product/list' in item['url'] and item.get('headers'):
                    list_headers = {k: v for k, v in item['headers'].items()
                                    if k.lower() not in ('host', 'content-length', 'origin', 'referer')}
                    logger.info(f"add_product_to_showcase: captured list headers: {list(list_headers.keys())}")
                    break

            result["intercepted_count"] = len(intercepted)
            result["list_headers_found"] = bool(list_headers)
            # Log ALL intercepted API calls for diagnosis
            result["all_intercepted"] = [{"url": i["url"], "method": i["method"]} for i in intercepted]
            logger.info(f"All intercepted API calls ({len(intercepted)}):")
            for i in intercepted:
                logger.info(f"  {i['method']} {i['url'][:150]}")

            # ── Diagnostic: dump existing showcase products ────────────────────
            # Fetch both endpoints so we can compare which products are in each
            # and understand whether video showcase == LIVE showcase.
            diag_sel = await page.evaluate("""async () => {
                try {
                    const r = await fetch(
                        'https://shop.tiktok.com/api/v1/streamer_desktop/selection/search?search_type=3&keyword=&origin=2&cursor=0&count=20&aid=1180',
                        {credentials: 'include'}
                    );
                    return await r.text();
                } catch(e) { return ''; }
            }""")
            diag_live = await page.evaluate("""async () => {
                try {
                    const r = await fetch(
                        'https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list?offset=0&count=50',
                        {credentials: 'include'}
                    );
                    return await r.text();
                } catch(e) { return ''; }
            }""")
            logger.info(f"Diagnostic selection/search (empty): {diag_sel[:2000]}")
            logger.info(f"Diagnostic showcase_product/list: {diag_live[:2000]}")
            result["diag_selection_search"] = diag_sel[:2000]
            result["diag_live_list"] = diag_live[:2000]

            # Parse and compare
            existing_sel_ids: set = set()
            existing_live_ids: set = set()
            existing_live_products: list = []
            try:
                d1 = json.loads(diag_sel)
                for p in (d1.get('data', {}).get('products') or []):
                    existing_sel_ids.add(str(p.get('product_id', '') or p.get('id', '') or p.get('item_id', '')))
            except Exception:
                pass
            try:
                d2 = json.loads(diag_live)
                existing_live_products = d2.get('data', {}).get('products') or []
                for p in existing_live_products:
                    existing_live_ids.add(str(p.get('product_id', '') or p.get('id', '') or p.get('item_id', '')))
            except Exception:
                pass

            overlap = existing_sel_ids & existing_live_ids
            logger.info(f"Video showcase: {len(existing_sel_ids)} products, LIVE: {len(existing_live_ids)} products, overlap: {len(overlap)}")
            logger.info(f"Video showcase IDs: {list(existing_sel_ids)[:10]}")
            logger.info(f"LIVE showcase IDs: {list(existing_live_ids)[:10]}")
            result["showcase_counts"] = {"video": len(existing_sel_ids), "live": len(existing_live_ids), "overlap": len(overlap)}

            # ── Paginate through ALL showcase_product/list pages ──────────────
            # The initial dump may have been truncated. Get every page and check if
            # our product is already there (previous add calls may have succeeded
            # but verification was checking the wrong field name).
            all_live_ids_full: set = set()
            all_live_product_ids: list = []
            all_live_products_full: list = []   # store full objects for inject template
            already_in_live = False
            try:
                for pg_offset in range(0, 300, 20):
                    pg_resp = await page.evaluate("""async (offset) => {
                        try {
                            const r = await fetch(
                                `https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list?offset=${offset}&count=20`,
                                {credentials: 'include'}
                            );
                            return await r.text();
                        } catch(e) { return ''; }
                    }""", pg_offset)
                    try:
                        pg_data = json.loads(pg_resp)
                        pg_prods = pg_data.get('data', {}).get('products') or []
                        for pp in pg_prods:
                            pid_val = str(pp.get('product_id', '') or pp.get('id', '') or pp.get('item_id', ''))
                            all_live_ids_full.add(pid_val)
                            all_live_product_ids.append({"pid": pid_val, "title": str(pp.get('title', ''))[:60]})
                            all_live_products_full.append(pp)
                        if not pg_prods or not pg_data.get('data', {}).get('has_more'):
                            break
                    except Exception:
                        break
            except Exception as pe:
                logger.warning(f"Paginate live list: {pe}")

            logger.info(f"Full LIVE showcase: {len(all_live_ids_full)} unique products")
            for pp in all_live_product_ids[:40]:
                logger.info(f"  LIVE product: id={pp['pid']}, title={pp['title']}")
            already_in_live = product_id in all_live_ids_full
            logger.info(f"Product {product_id} already in LIVE showcase: {already_in_live}")
            result["full_live_count"] = len(all_live_ids_full)
            result["already_in_live"] = already_in_live

            # ── Test: add an ALREADY-EXISTING product to see what code:0 means ──
            # If adding an existing product also returns code:0 (same as our product),
            # then code:0 is a generic no-op. If it returns a different code (conflict),
            # then our product is genuinely rejected.
            if all_live_product_ids:
                existing_test_pid = all_live_product_ids[0]['pid']
                try:
                    test_add_r = await page.evaluate("""async ([url, pid]) => {
                        try {
                            const resp = await fetch(url, {
                                method: 'POST', credentials: 'include',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({product_id: pid}),
                            });
                            return {status: resp.status, body: (await resp.text()).slice(0, 300)};
                        } catch(e) { return {error: String(e)}; }
                    }""", [
                        "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/add",
                        existing_test_pid,
                    ])
                    logger.info(f"Test add EXISTING product {existing_test_pid}: {test_add_r}")
                    result["test_add_existing"] = {"pid": existing_test_pid, "response": test_add_r}
                except Exception as e:
                    logger.warning(f"Test add existing: {e}")

            if already_in_live:
                # The product is in showcase_product/list — it just didn't appear in
                # selection/search because those are different datasets.
                # The upload dialog's "Showcase products" tab should now show it.
                result["added"] = True
                result["verified"] = True
                result["message"] = "Product was already in showcase_product/list (verification key was wrong before)"

            # ── Step: call selection/search with product ID from dialog context (GET, CORS allowed) ──
            # The dialog itself calls this endpoint to search. From dialog context it has session.
            sel_search_result = await page.evaluate("""async (pid) => {
                const url = `https://shop.tiktok.com/api/v1/streamer_desktop/selection/search?search_type=3&keyword=${pid}&origin=2&cursor=0&count=10&aid=1180`;
                try {
                    const r = await fetch(url, {credentials: 'include'});
                    const text = await r.text();
                    return {status: r.status, body: text.slice(0, 3000)};
                } catch(e) { return {error: String(e)}; }
            }""", product_id)
            logger.info(f"selection/search from dialog context: status={sel_search_result.get('status')}, body={sel_search_result.get('body', '')[:500]}")
            result["selection_search_from_dialog"] = sel_search_result

            # ── Step: search by name terms in selection/search ─────────────────
            # The ID returns 10000 (not found). Try partial name terms in case the
            # product exists in showcase under a different ID or name variant.
            name_terms = ["Cleanfit", "áo thun", "Phiên Bản Nâng Cấp", "1734586"]
            for term in name_terms:
                try:
                    nm_result = await page.evaluate("""async (kw) => {
                        const url = `https://shop.tiktok.com/api/v1/streamer_desktop/selection/search?search_type=3&keyword=${encodeURIComponent(kw)}&origin=2&cursor=0&count=10&aid=1180`;
                        try {
                            const r = await fetch(url, {credentials: 'include'});
                            const text = await r.text();
                            return {status: r.status, body: text.slice(0, 1000)};
                        } catch(e) { return {error: String(e)}; }
                    }""", term)
                    logger.info(f"selection/search name='{term}': {nm_result.get('body','')[:200]}")
                except Exception as e:
                    logger.debug(f"name search '{term}': {e}")

            # ── Step: type product ID in dialog search and observe ALL elements ──
            try:
                # Find the search input in the dialog
                search_coords = await page.evaluate("""() => {
                    const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                        .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                    const container = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
                    const inp = Array.from(container.querySelectorAll('input')).find(i => {
                        const r = i.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    });
                    if (!inp) return null;
                    const r = inp.getBoundingClientRect();
                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                }""")
                if search_coords:
                    await page.mouse.click(search_coords['x'], search_coords['y'])
                    await page.keyboard.press("Control+a")
                    await page.keyboard.type(product_id, delay=40)
                    await asyncio.sleep(0.5)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(3)
                    await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_dialog_search.png"))
                    # Log ALL elements in the dialog after search
                    dialog_elems = await page.evaluate("""() => {
                        const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                            .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                        const container = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
                        return Array.from(container.querySelectorAll('*'))
                            .filter(e => {
                                const r = e.getBoundingClientRect();
                                const t = e.textContent.trim();
                                return r.width > 0 && r.height > 0 && t && t.length < 100 &&
                                       e.childElementCount === 0;
                            })
                            .map(e => ({tag: e.tagName, text: e.textContent.trim().slice(0, 80)}))
                            .filter(e => e.text.length > 1)
                            .slice(0, 80);
                    }""")
                    logger.info(f"Dialog elements after product ID search: {dialog_elems}")
                    result["dialog_elements_after_search"] = dialog_elems

                    # Check for any "Add" related button that might have appeared
                    add_btn_clicked = await page.evaluate("""(pid) => {
                        const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                            .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                        const container = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
                        const addKeywords = ['add', 'thêm', 'Add', 'Thêm', 'promote', 'Promote', 'link', 'Link'];
                        const btn = Array.from(container.querySelectorAll('button, [role="button"]')).find(b => {
                            const t = b.textContent.trim().toLowerCase();
                            const r = b.getBoundingClientRect();
                            return r.width > 0 && r.height > 0 && r.top < window.innerHeight &&
                                   addKeywords.some(k => t === k.toLowerCase() || t.includes('add') || t.includes('thêm'));
                        });
                        if (btn) { btn.click(); return btn.textContent.trim(); }
                        return null;
                    }""", product_id)
                    if add_btn_clicked:
                        logger.info(f"Clicked dialog button after search: '{add_btn_clicked}'")
                        await asyncio.sleep(2)
                        await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_dialog_clicked.png"))
            except Exception as e:
                logger.warning(f"Dialog search attempt error: {e}")

            # Try add with captured headers from dialog context
            add_url = "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/add"
            for body_dict in [
                {"product_id": product_id},
                {"item_id": product_id},
                {"product_id": product_id, "item_id": product_id},
                {"product_ids": [product_id]},
            ]:
                try:
                    add_result = await page.evaluate("""async ([url, body_str, hdrs]) => {
                        try {
                            const resp = await fetch(url, {
                                method: 'POST',
                                credentials: 'include',
                                headers: {
                                    ...hdrs,
                                    'Content-Type': 'application/json',
                                    'Accept': 'application/json, text/plain, */*',
                                },
                                body: body_str,
                            });
                            const text = await resp.text();
                            return {status: resp.status, body: text.slice(0, 500)};
                        } catch(e) {
                            return {error: String(e)};
                        }
                    }""", [add_url, json.dumps(body_dict), list_headers])
                    logger.info(f"add_product_to_showcase: add body={body_dict} → {add_result}")
                    result["attempts"].append({"body": body_dict, "response": add_result})

                    resp_body = (add_result or {}).get('body', '')
                    if add_result and add_result.get('status') in (200, 201) and '"code":0' in resp_body:
                        await asyncio.sleep(3)
                        verified = await page.evaluate(verify_js, product_id)
                        logger.info(f"add_product_to_showcase: verified={verified}")
                        if verified:
                            result["added"] = True
                            result["verified"] = True
                            result["message"] = f"SUCCESS with body={body_dict}"
                            break
                        else:
                            result["attempts"][-1]["verified"] = False
                            logger.info("add_product_to_showcase: code:0 but product not in list")
                except Exception as e:
                    logger.warning(f"add_product_to_showcase: attempt error: {e}")
                    result["attempts"].append({"body": body_dict, "error": str(e)})

                if result["added"]:
                    break

            # CORS blocks shop.tiktok.com calls from www.tiktok.com.
            # Open a shop.tiktok.com tab (same-origin) and use the CORRECT API:
            # selection/search for finding the product and then the add endpoint.
            if not result["added"]:
                shop_page = await context.new_page()
                try:
                    # Navigate to the list endpoint — authenticates cookies and puts us on shop.tiktok.com
                    list_endpoint = "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list?offset=0&count=1"
                    await shop_page.goto(list_endpoint, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(2)
                    page_text = await shop_page.inner_text("body")
                    is_json = page_text.strip().startswith('{')
                    logger.info(f"shop.tiktok.com list: json={is_json}, url={shop_page.url}")
                    result["attempts"].append({"step": "shop_tab_list_check", "is_json": is_json})

                    if not is_json:
                        raise RuntimeError("shop.tiktok.com not authenticated (redirected)")

                    # Step 1: Call selection/search to find the product in the affiliate catalog
                    search_url = f"https://shop.tiktok.com/api/v1/streamer_desktop/selection/search?search_type=3&keyword={product_id}&origin=2&cursor=0&count=6&aid=1180"
                    search_result = await shop_page.evaluate("""async (url) => {
                        try {
                            const r = await fetch(url, {credentials: 'include'});
                            const text = await r.text();
                            return {status: r.status, body: text.slice(0, 2000)};
                        } catch(e) { return {error: String(e)}; }
                    }""", search_url)
                    logger.info(f"selection/search for {product_id}: status={search_result.get('status')}")
                    logger.info(f"selection/search body: {search_result.get('body', '')[:500]}")
                    result["attempts"].append({"step": "selection_search", "response": search_result})

                    # Parse search result to extract product data
                    import json as _json
                    search_data = {}
                    product_item = None
                    try:
                        search_data = _json.loads(search_result.get('body', '{}'))
                        prods = search_data.get('data', {}).get('products') or []
                        for p in prods:
                            pid_val = str(p.get('id', '') or p.get('item_id', '') or p.get('product_id', ''))
                            if pid_val == product_id or product_id in pid_val:
                                product_item = p
                                break
                        if not product_item and prods:
                            product_item = prods[0]  # take first result if ID search didn't match
                    except Exception as parse_err:
                        logger.warning(f"search parse error: {parse_err}")

                    logger.info(f"selection/search product_item found: {bool(product_item)}, item={product_item}")
                    result["attempts"].append({"step": "selection_search_parsed", "found": bool(product_item), "item": product_item})

                    # Step 2: Try add endpoints using the product data from search
                    # The selection endpoint may use `selection/add` or `showcase_product/add`
                    add_endpoints = [
                        "https://shop.tiktok.com/api/v1/streamer_desktop/selection/add",
                        "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/add",
                        "https://shop.tiktok.com/api/v1/streamer_desktop/selection/confirm",
                        "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/add_by_selection",
                    ]
                    # Build bodies including data from search result
                    shop_bodies = [
                        {"product_id": product_id},
                        {"item_id": product_id},
                        {"product_ids": [product_id]},
                        {"item_ids": [product_id]},
                    ]
                    if product_item:
                        shop_bodies.append(product_item)
                        shop_bodies.append({"product_id": product_id, **{k: v for k, v in product_item.items() if k != 'product_id'}})

                    for ep in add_endpoints:
                        if result["added"]:
                            break
                        for body_dict in shop_bodies:
                            if result["added"]:
                                break
                            try:
                                add_result = await shop_page.evaluate("""async ([url, body_str]) => {
                                    try {
                                        const resp = await fetch(url, {
                                            method: 'POST',
                                            credentials: 'include',
                                            headers: {'Content-Type': 'application/json'},
                                            body: body_str,
                                        });
                                        const text = await resp.text();
                                        return {status: resp.status, body: text.slice(0, 500), url: url};
                                    } catch(e) { return {error: String(e), url: url}; }
                                }""", [ep, json.dumps(body_dict)])
                                logger.info(f"shop-tab {ep.split('/')[-1]} body_keys={list(body_dict.keys())[:3]}: {add_result}")
                                result["attempts"].append({"ep": ep.split('/')[-1], "body_keys": list(body_dict.keys()), "response": add_result})

                                resp_body = (add_result or {}).get('body', '')
                                if add_result and add_result.get('status') in (200, 201) and '"code":0' in resp_body:
                                    await asyncio.sleep(3)
                                    # Verify by selection/search — check if product now in showcase list
                                    verify_search = await shop_page.evaluate("""async ([url, pid]) => {
                                        try {
                                            const r = await fetch(url, {credentials: 'include'});
                                            const data = await r.json();
                                            const prods = data?.data?.products || [];
                                            return {
                                                found: prods.some(p => [p.id, p.item_id, p.product_id].filter(Boolean).map(String).includes(pid)),
                                                count: prods.length
                                            };
                                        } catch(e) { return {error: String(e)}; }
                                    }""", [
                                        f"https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list?offset=0&count=100",
                                        product_id
                                    ])
                                    logger.info(f"shop-tab verify after {ep.split('/')[-1]}: {verify_search}")
                                    if verify_search and verify_search.get('found'):
                                        result["added"] = True
                                        result["verified"] = True
                                        result["message"] = f"SUCCESS: {ep.split('/')[-1]} with body_keys={list(body_dict.keys())}"
                                        break
                                    else:
                                        logger.info(f"shop-tab: code:0 but not in list — response: {resp_body[:200]}")
                            except Exception as e:
                                logger.warning(f"shop-tab {ep} error: {e}")

                    # ── Strategy: delete oldest + add new (if LIVE showcase full) ──
                    # showcase_product/list has 32 products. If adding returns code:0 but
                    # the product doesn't appear, the list may be at capacity.
                    # Try removing the oldest product and adding ours.
                    if not result["added"] and existing_live_products:
                        logger.info("Strategy: delete oldest LIVE product + add new one")
                        # Try delete endpoint variants
                        oldest = existing_live_products[-1]
                        oldest_id = str(oldest.get('product_id', '') or oldest.get('id', '') or oldest.get('item_id', ''))
                        logger.info(f"Attempting to delete oldest product: id={oldest_id}, name={str(oldest.get('name',''))[:50]}")

                        if oldest_id:
                            # Try deleting with different body formats
                            del_ok = False
                            for del_body in [
                                {"product_id": oldest_id},
                                {"item_id": oldest_id},
                                {"product_ids": [oldest_id]},
                            ]:
                                if del_ok:
                                    break
                                try:
                                    del_r = await shop_page.evaluate("""async ([url, body_str]) => {
                                        try {
                                            const resp = await fetch(url, {
                                                method: 'POST', credentials: 'include',
                                                headers: {'Content-Type': 'application/json'},
                                                body: body_str,
                                            });
                                            return {status: resp.status, body: (await resp.text()).slice(0, 300)};
                                        } catch(e) { return {error: String(e)}; }
                                    }""", [
                                        "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/delete",
                                        json.dumps(del_body),
                                    ])
                                    logger.info(f"Delete body={del_body}: {del_r}")
                                    result["attempts"].append({"step": "delete", "body": del_body, "response": del_r})
                                    if del_r and '"code":0' in del_r.get('body', ''):
                                        del_ok = True
                                        logger.info(f"Delete succeeded! Now adding our product.")
                                except Exception as e:
                                    logger.warning(f"Delete body={del_body}: {e}")

                            if del_ok:
                                await asyncio.sleep(1)
                                for add_body in [{"product_id": product_id}, {"item_id": product_id}]:
                                    try:
                                        add_r = await shop_page.evaluate("""async ([url, body_str]) => {
                                            try {
                                                const resp = await fetch(url, {
                                                    method: 'POST', credentials: 'include',
                                                    headers: {'Content-Type': 'application/json'},
                                                    body: body_str,
                                                });
                                                return {status: resp.status, body: (await resp.text()).slice(0, 300)};
                                            } catch(e) { return {error: String(e)}; }
                                        }""", [
                                            "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/add",
                                            json.dumps(add_body),
                                        ])
                                        logger.info(f"Re-add body={add_body}: {add_r}")
                                        if add_r and '"code":0' in add_r.get('body', ''):
                                            await asyncio.sleep(3)
                                            v_r = await shop_page.evaluate("""async (pid) => {
                                                const r = await fetch(
                                                    'https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list?offset=0&count=100',
                                                    {credentials: 'include'}
                                                );
                                                const data = await r.json();
                                                const prods = data?.data?.products || [];
                                                return {
                                                    found: prods.some(p => [p.id, p.item_id, p.product_id].filter(Boolean).map(String).includes(pid)),
                                                    count: prods.length
                                                };
                                            }""", product_id)
                                            logger.info(f"Delete+re-add verify: {v_r}")
                                            if v_r and v_r.get('found'):
                                                result["added"] = True
                                                result["verified"] = True
                                                result["message"] = "SUCCESS: delete oldest + add new"
                                                break
                                    except Exception as e:
                                        logger.warning(f"Re-add body={add_body}: {e}")

                except Exception as e:
                    logger.warning(f"shop-tab error: {e}")
                    result["attempts"].append({"shop_tab_error": str(e)})
                finally:
                    await shop_page.close()

            # ── Strategy: Playwright context.request (Python HTTP — no CORS) ──────
            # context.request makes HTTP calls from Python using browser cookies.
            # Unlike browser fetch(), it has NO same-origin restriction.
            if not result["added"]:
                logger.info("=== context.request strategy (bypasses CORS entirely) ===")
                req_hdrs = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://www.tiktok.com",
                    "Referer": "https://www.tiktok.com/tiktokstudio/upload",
                }
                # Include captured auth headers (CSRF, session tokens, etc.)
                req_hdrs.update({k: v for k, v in list_headers.items()
                                 if k.lower() not in ('host', 'content-length')})

                # Step A: Probe selection/search with different search_type values.
                # search_type=3 searches the existing showcase. Other values may search
                # the broader TikTok Shop catalog, which is how products get added.
                catalog_product_item = None
                for st in [1, 2, 4, 5, 6]:
                    try:
                        s_url = (f"https://shop.tiktok.com/api/v1/streamer_desktop/selection/search"
                                 f"?search_type={st}&keyword={product_id}&origin=2&cursor=0&count=10&aid=1180")
                        s_resp = await context.request.get(s_url, headers=req_hdrs)
                        s_text = await s_resp.text()
                        logger.info(f"ctx search_type={st}: status={s_resp.status} body={s_text[:400]}")
                        result["attempts"].append({"step": f"ctx_st{st}", "status": s_resp.status, "body": s_text[:300]})
                        try:
                            s_data = json.loads(s_text)
                            prods = s_data.get('data', {}).get('products') or []
                            if prods:
                                logger.info(f"search_type={st}: Found {len(prods)} products!")
                                for p in prods:
                                    if str(p.get('product_id', '') or p.get('id', '') or p.get('item_id', '')) == product_id:
                                        catalog_product_item = p
                                        break
                                if not catalog_product_item:
                                    catalog_product_item = prods[0]
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"ctx search_type={st}: {e}")

                # Step B: Try add endpoints via context.request
                add_endpoints_ctx = [
                    "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/add",
                    "https://shop.tiktok.com/api/v1/streamer_desktop/selection/add",
                    "https://shop.tiktok.com/api/v1/streamer_desktop/selection/confirm",
                    "https://shop.tiktok.com/api/v1/streamer_desktop/selection/apply",
                    "https://shop.tiktok.com/api/v1/streamer_desktop/selection/batch_add",
                    "https://shop.tiktok.com/api/v1/streamer_desktop/video_product/add",
                    "https://shop.tiktok.com/api/v1/creator/selection/add",
                    "https://shop.tiktok.com/api/v1/affiliate/creator/showcase/product/add",
                    "https://shop.tiktok.com/api/v1/creator/affiliate/showcase/add",
                ]
                bodies_ctx = [
                    {"product_id": product_id},
                    {"item_id": product_id},
                    {"product_ids": [product_id]},
                ]
                if catalog_product_item:
                    bodies_ctx.append(catalog_product_item)

                for ep in add_endpoints_ctx:
                    if result["added"]:
                        break
                    for body_dict in bodies_ctx:
                        try:
                            resp = await context.request.post(
                                ep, headers=req_hdrs, data=json.dumps(body_dict)
                            )
                            text = await resp.text()
                            ep_short = "/".join(ep.split("/")[-2:])
                            logger.info(f"ctx.request POST {ep_short} body={list(body_dict.keys())}: {resp.status} {text[:300]}")
                            result["attempts"].append({
                                "strategy": "ctx_request",
                                "ep": ep_short,
                                "body_keys": list(body_dict.keys()),
                                "status": resp.status,
                                "body": text[:200],
                            })

                            if resp.status in (200, 201) and '"code":0' in text:
                                await asyncio.sleep(2)
                                v_resp = await context.request.get(
                                    "https://shop.tiktok.com/api/v1/streamer_desktop/showcase_product/list?offset=0&count=100",
                                    headers=req_hdrs,
                                )
                                v_text = await v_resp.text()
                                try:
                                    v_data = json.loads(v_text)
                                    prods = v_data.get('data', {}).get('products') or []
                                    found = any(
                                        str(p.get('product_id', '') or p.get('id', '') or p.get('item_id', '')) == product_id
                                        for p in prods
                                    )
                                    logger.info(f"ctx.request verify: found={found}, total_prods={len(prods)}")
                                    if found:
                                        result["added"] = True
                                        result["verified"] = True
                                        result["message"] = f"context.request SUCCESS: {ep_short}"
                                        break
                                except Exception as ve:
                                    logger.warning(f"ctx verify parse: {ve}")
                        except Exception as e:
                            logger.warning(f"ctx.request {ep.split('/')[-1]}: {e}")
                    if result["added"]:
                        break

            # ── Strategy: Navigate shop.tiktok.com creator affiliate UI ──────────
            # Open the TikTok Shop creator product page in the browser and look
            # for an "Add to showcase" or "Promote" button on the product detail page.
            if not result["added"]:
                shop_ui = await context.new_page()
                try:
                    ui_urls = [
                        f"https://shop.tiktok.com/view/product/{product_id}",
                        "https://shop.tiktok.com/business/en/creator/affiliate",
                        "https://shop.tiktok.com/en/creator/affiliate",
                        "https://shop.tiktok.com/creator/affiliate/product",
                    ]
                    for url in ui_urls:
                        if result["added"]:
                            break
                        try:
                            logger.info(f"shop.tiktok.com UI: {url}")
                            await shop_ui.goto(url, wait_until="domcontentloaded", timeout=20000)
                            await asyncio.sleep(5)
                            slug = url.rstrip('/').split('/')[-1]
                            await shop_ui.screenshot(path=os.path.join(_TEMP_DIR, f"debug_shop_ui_{slug}.png"))
                            title = await shop_ui.title()
                            final_url = shop_ui.url
                            logger.info(f"shop UI: {final_url}, title={title!r}")

                            visible = await shop_ui.evaluate("""() =>
                                Array.from(document.querySelectorAll('button,[role="button"],a'))
                                    .filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.top<window.innerHeight;})
                                    .map(e=>({text:e.textContent.trim().replace(/\\s+/g,' ').slice(0,80),href:e.href||''}))
                                    .filter(t=>t.text).slice(0,50)
                            """)
                            logger.info(f"shop UI buttons: {visible}")
                            result["attempts"].append({
                                "step": f"shop_ui_{slug}",
                                "url": final_url,
                                "title": title,
                                "buttons": [b['text'] for b in visible[:15]],
                            })

                            # Intercept API calls from this page for diagnosis
                            shop_ui_calls: list = []
                            async def _cap_ui(req):
                                u = req.url
                                if 'shop.tiktok.com/api' in u and not any(x in u for x in ('.js','.css','.png','.jpg')):
                                    shop_ui_calls.append({'url': u, 'method': req.method})
                            shop_ui.on("request", _cap_ui)

                            # Click any Add/Showcase button
                            add_kwds = ['showcase', 'add', 'thêm', 'giới thiệu', 'promote', 'affiliate']
                            for btn in visible:
                                if any(kw in btn['text'].lower() for kw in add_kwds):
                                    logger.info(f"shop UI: clicking '{btn['text']}'")
                                    try:
                                        if btn.get('href') and btn['href'].startswith('http'):
                                            await shop_ui.goto(btn['href'], wait_until="domcontentloaded", timeout=15000)
                                            await asyncio.sleep(4)
                                        else:
                                            el = shop_ui.get_by_text(btn['text']).first
                                            if await el.count() > 0 and await el.is_visible():
                                                await el.click()
                                                await asyncio.sleep(3)
                                        await shop_ui.screenshot(path=os.path.join(_TEMP_DIR, f"debug_shop_ui_{slug}_click.png"))
                                        logger.info(f"shop UI after click: {shop_ui.url}")
                                        logger.info(f"shop UI API calls after click: {shop_ui_calls[-5:]}")
                                        break
                                    except Exception:
                                        pass

                            await asyncio.sleep(2)
                            logger.info(f"shop UI total API calls: {len(shop_ui_calls)}")
                            for call in shop_ui_calls:
                                logger.info(f"  shop UI API: {call['method']} {call['url'][:150]}")

                        except Exception as e:
                            logger.warning(f"shop UI {url}: {e}")
                finally:
                    try:
                        await shop_ui.screenshot(path=os.path.join(_TEMP_DIR, "debug_shop_ui_final.png"))
                    except Exception:
                        pass
                    await shop_ui.close()

            # ── Strategy: Route response interception + dialog click ─────────
            # Previous runs failed because _open_add_link_modal clicks Next BEFORE we
            # switch tabs — so the tab switch happens at the wrong modal step and the
            # table is empty. Fix: open the modal manually (only click "Add"), then switch
            # tabs while still at Step 1 (product selection). Also pre-build the inject
            # response using real product structure so React renders it correctly.
            if not result["added"]:
                logger.info("=== Strategy: Route interception v2 (no premature Next click) ===")
                try:
                    # Build inject product using a real product as template (only change product_id)
                    if all_live_products_full:
                        injected_product = dict(all_live_products_full[0])
                        injected_product['product_id'] = product_id
                        injected_product['title'] = product_name or injected_product.get('title', 'Sản phẩm')
                    else:
                        injected_product = {
                            "product_id": product_id,
                            "title": product_name or "Sản phẩm",
                            "format_available_price": "",
                            "seller_info": {"seller_id": "", "shop_name": ""},
                            "cover": {"uri": "", "url_list": []},
                            "images": [],
                            "status": 2,
                        }
                    logger.info(f"inject template product_id={product_id}, title={injected_product.get('title','')[:50]}")

                    # Pre-build the full response body (avoids route.fetch() latency/failure)
                    inject_products = [injected_product] + [
                        p for p in all_live_products_full
                        if str(p.get('product_id', '') or p.get('id', '')) != product_id
                    ]
                    inject_body = json.dumps({
                        "code": 0, "msg": "success",
                        "data": {
                            "products": inject_products[:20],
                            "has_more": False,
                            "total": len(inject_products),
                        }
                    })

                    route_fired = []

                    async def inject_showcase_v2(route):
                        route_fired.append(route.request.url)
                        logger.info(f"inject_showcase_v2 FIRED for: {route.request.url[:100]}")
                        await route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=inject_body,
                        )

                    await page.route("**/showcase_product/list**", inject_showcase_v2)
                    logger.info("inject v2: route handler registered")

                    # Close any open modal
                    for close_sel in ['button:has-text("Cancel")', 'button:has-text("Huỷ")', '[aria-label="Close"]']:
                        try:
                            cl = page.locator(close_sel).first
                            if await cl.count() > 0 and await cl.is_visible():
                                await cl.click()
                                await asyncio.sleep(1.5)
                                logger.info(f"inject v2: closed modal via {close_sel}")
                                break
                        except Exception:
                            pass
                    await asyncio.sleep(1)

                    # Click "Add" to open modal — but do NOT click "Next" yet
                    add_opened = False
                    for add_sel in ['text="Add"', ':text("Add")', 'text="+ Add"']:
                        try:
                            btn = page.locator(add_sel).first
                            if await btn.count() > 0 and await btn.is_visible():
                                await btn.click()
                                add_opened = True
                                logger.info(f"inject v2: clicked Add via {add_sel}")
                                await asyncio.sleep(3)
                                break
                        except Exception:
                            pass

                    if not add_opened:
                        # JS fallback: find and click "Add" in the upload form area
                        add_opened = await page.evaluate("""() => {
                            const addBtn = Array.from(document.querySelectorAll('button,span,div'))
                                .filter(e => {
                                    const r = e.getBoundingClientRect();
                                    const t = e.textContent.trim();
                                    return r.width > 0 && r.height > 0 &&
                                           (t === 'Add' || t === '+ Add' || t === 'Add link');
                                })[0];
                            if (addBtn) { addBtn.click(); return true; }
                            return false;
                        }""")
                        if add_opened:
                            await asyncio.sleep(3)
                            logger.info("inject v2: clicked Add via JS fallback")

                    await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_inject_v2_modal.png"))

                    # Log all modal elements (to understand which step we're at)
                    modal_elems = await page.evaluate("""() => {
                        const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]'))
                            .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                        const cont = dialogs.length ? dialogs[dialogs.length-1] : document.body;
                        return Array.from(cont.querySelectorAll('button,input,[role="tab"]'))
                            .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                            .map(e => ({tag: e.tagName, text: e.textContent.trim().slice(0,50), type: e.type||''}));
                    }""")
                    logger.info(f"inject v2: modal elements = {modal_elems}")

                    # Check if we need to click "Next" first (if link-type step is shown)
                    at_link_type_step = any(
                        e.get('text', '') in ('Products', 'Next', 'Tiếp theo')
                        for e in modal_elems
                        if e.get('tag') == 'BUTTON' and e.get('text') not in ('My shop', 'Showcase products', 'Cancel')
                    )
                    has_showcase_tab = any(
                        'Showcase' in e.get('text', '') or 'showcase' in e.get('text', '').lower()
                        for e in modal_elems
                    )
                    logger.info(f"inject v2: at_link_type_step={at_link_type_step}, has_showcase_tab={has_showcase_tab}")

                    # If we're at the link-type step (only "Products" dropdown + Next), click Next first
                    if at_link_type_step and not has_showcase_tab:
                        next_js = await page.evaluate("""() => {
                            const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]'))
                                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                            const cont = dialogs.length ? dialogs[dialogs.length-1] : document.body;
                            const btn = Array.from(cont.querySelectorAll('button'))
                                .find(b => (b.textContent.trim() === 'Next' || b.textContent.trim() === 'Tiếp theo')
                                           && b.getBoundingClientRect().width > 0);
                            if (btn) { btn.click(); return true; }
                            return false;
                        }""")
                        if next_js:
                            logger.info("inject v2: clicked Next to reach product-selection step")
                            await asyncio.sleep(3)

                    # Now switch to "Showcase products" tab — route interceptor fires here
                    tab_switched = False
                    for tab_sel in ['text="Showcase products"', ':text("Showcase products")',
                                    ':text("Sản phẩm giới thiệu")']:
                        try:
                            tab = page.locator(tab_sel).first
                            if await tab.count() > 0 and await tab.is_visible():
                                await tab.click()
                                tab_switched = True
                                logger.info(f"inject v2: switched to Showcase tab via {tab_sel}")
                                await asyncio.sleep(4)  # more time for injected products to render
                                break
                        except Exception:
                            pass

                    if not tab_switched:
                        logger.warning("inject v2: could not find Showcase products tab")

                    await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_inject_v2_tab.png"))
                    logger.info(f"inject v2: route_fired={route_fired}")

                    # Log all visible rows in dialog
                    visible_rows = await page.evaluate("""(pid) => {
                        const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]'))
                            .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                        const cont = dialogs.length ? dialogs[dialogs.length-1] : document.body;
                        // Find tbody rows (skip header)
                        const tbody = cont.querySelector('tbody');
                        const target = tbody || cont;
                        const rows = Array.from(target.querySelectorAll('tr,li'))
                            .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                        return rows.map(r => ({
                            text: r.textContent.trim().replace(/\\s+/g,' ').slice(0,100),
                            hasPid: r.textContent.includes(pid),
                            hasRadio: !!r.querySelector('input[type="radio"]'),
                        })).slice(0, 10);
                    }""", product_id)
                    logger.info(f"inject v2: visible rows in dialog: {visible_rows}")

                    # Capture ALL API calls (not just shop.tiktok.com) during dialog interaction
                    inject_calls: list = []
                    async def _cap_inject(req):
                        u = req.url
                        if any(d in u for d in ['shop.tiktok.com/api', 'tiktok.com/api/', 'tiktok.com/aweme/']):
                            try:
                                body = req.post_data or ""
                            except Exception:
                                body = ""
                            inject_calls.append({'url': u, 'method': req.method, 'body': body[:500]})
                    page.on("request", _cap_inject)

                    # Also capture responses for oec endpoints
                    inject_responses: list = []
                    async def _cap_inject_resp(resp):
                        u = resp.url
                        if 'oec/content/creator' in u:
                            try:
                                body = await resp.text()
                            except Exception:
                                body = ""
                            inject_responses.append({'url': u, 'status': resp.status, 'body': body[:500]})
                    page.on("response", _cap_inject_resp)

                    # Find and click our injected product row
                    clicked = await page.evaluate("""(pid) => {
                        const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]'))
                            .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                        const cont = dialogs.length ? dialogs[dialogs.length-1] : document.body;
                        // Prefer tbody rows to avoid header
                        const tbody = cont.querySelector('tbody');
                        const container = tbody || cont;
                        const rows = Array.from(container.querySelectorAll('tr,li'))
                            .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                        // First: find row containing our product ID
                        let found = rows.find(r => r.textContent.includes(pid));
                        // Fallback: first data row (not header)
                        if (!found && rows.length > 0) found = rows[0];
                        if (found) {
                            const radio = found.querySelector('input[type="radio"]');
                            if (radio) { radio.click(); return {action:'radio', text:found.textContent.slice(0,80).trim()}; }
                            found.click();
                            return {action:'row', text:found.textContent.slice(0,80).trim()};
                        }
                        return null;
                    }""", product_id)
                    logger.info(f"inject v2: clicked row: {clicked}")

                    if clicked:
                        await asyncio.sleep(1)

                        # ── Step 1→2: Click "Next" from product-selection step ────
                        btns_step1 = await page.evaluate("""() => {
                            const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]'))
                                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                            const cont = dialogs.length ? dialogs[dialogs.length-1] : document.body;
                            return Array.from(cont.querySelectorAll('button'))
                                .filter(b => b.getBoundingClientRect().width > 0)
                                .map(b => b.textContent.trim());
                        }""")
                        logger.info(f"inject v2: step1 buttons: {btns_step1}")

                        # Click "Next" (goes from product-selection to confirmation step)
                        next_coords = await page.evaluate("""() => {
                            const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]'))
                                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                            const cont = dialogs.length ? dialogs[dialogs.length-1] : document.body;
                            const btns = Array.from(cont.querySelectorAll('button'))
                                .filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                            const btn = btns.find(b => {
                                const t = b.textContent.trim();
                                return (t === 'Next' || t === 'Tiếp theo') && !b.disabled;
                            });
                            if (btn) {
                                const r = btn.getBoundingClientRect();
                                btn.click();
                                return {text: btn.textContent.trim(), x: r.x, y: r.y};
                            }
                            return null;
                        }""")
                        logger.info(f"inject v2: step1 Next click: {next_coords}")
                        await asyncio.sleep(3)

                        # ── Step 2: Confirmation screen — screenshot and log elements ──
                        await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_inject_v2_step2.png"))
                        step2_elems = await page.evaluate("""() => {
                            const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]'))
                                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                            const cont = dialogs.length ? dialogs[dialogs.length-1] : document.body;
                            const all = Array.from(cont.querySelectorAll('button,input,span,p,h1,h2,h3'))
                                .filter(e => {
                                    const r = e.getBoundingClientRect();
                                    const t = e.textContent.trim();
                                    return r.width > 0 && r.height > 0 && t && t.length < 100 && e.childElementCount === 0;
                                })
                                .map(e => ({tag: e.tagName, text: e.textContent.trim().slice(0,60)}))
                                .filter(e => e.text).slice(0, 30);
                            return all;
                        }""")
                        logger.info(f"inject v2: step2 elements: {step2_elems}")

                        # ── Step 2→close: Click "Add" / "Thêm" to finalize ────────
                        add_step2 = await page.evaluate("""() => {
                            const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]'))
                                .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                            const cont = dialogs.length ? dialogs[dialogs.length-1] : document.body;
                            const btns = Array.from(cont.querySelectorAll('button'))
                                .filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                            const kws = ['add', 'thêm', 'confirm', 'xác nhận', 'apply', 'save', 'done'];
                            const btn = btns.find(b => {
                                const t = b.textContent.trim().toLowerCase();
                                return kws.some(k => t === k || (t.length < 20 && t.includes(k))) && !b.disabled;
                            });
                            if (btn) { btn.click(); return btn.textContent.trim(); }
                            // No "Add" found — log all buttons so we know what's there
                            return {notFound: true, allBtns: btns.map(b => b.textContent.trim())};
                        }""")
                        logger.info(f"inject v2: step2 Add click: {add_step2}")
                        await asyncio.sleep(4)

                        await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_inject_v2_confirm.png"))
                        logger.info(f"inject v2: ALL API calls after full flow: {inject_calls}")
                        for c in inject_calls:
                            logger.info(f"  inject API: {c['method']} {c['url'][:150]} body={c.get('body','')[:200]}")
                        logger.info(f"inject v2: OEC responses: {inject_responses}")
                        for r in inject_responses:
                            logger.info(f"  inject RESP: {r['status']} {r['url'][:100]} body={r.get('body','')[:300]}")

                        # Extract the oec/content/creator/products request body and replay directly
                        oec_call = next((c for c in inject_calls if 'oec/content/creator/products' in c['url']), None)
                        if oec_call:
                            logger.info(f"inject v2: OEC products request body: {oec_call.get('body','')[:500]}")
                            # Try replaying via context.request.post to see if it can be called standalone
                            try:
                                oec_replay = await context.request.post(
                                    oec_call['url'],
                                    data=oec_call.get('body', '{}'),
                                    headers={
                                        "Content-Type": "application/json",
                                        "Accept": "application/json",
                                        "Origin": "https://www.tiktok.com",
                                        "Referer": "https://www.tiktok.com/tiktokstudio/upload",
                                    }
                                )
                                oec_replay_text = await oec_replay.text()
                                logger.info(f"inject v2: OEC replay status={oec_replay.status} body={oec_replay_text[:500]}")
                                result["oec_replay"] = {"status": oec_replay.status, "body": oec_replay_text[:300]}
                            except Exception as re:
                                logger.warning(f"inject v2: OEC replay error: {re}")

                        result["attempts"].append({
                            "strategy": "route_injection_v2",
                            "route_fired": route_fired,
                            "tab_switched": tab_switched,
                            "visible_rows_count": len(visible_rows),
                            "clicked_row": clicked,
                            "step2_elems": step2_elems,
                            "add_step2": add_step2,
                            "api_calls": [{'url': c['url'], 'body_prefix': c.get('body', '')[:100]} for c in inject_calls],
                            "oec_responses": inject_responses,
                        })

                        # Unroute and verify against real list
                        try:
                            await page.unroute("**/showcase_product/list**")
                        except Exception:
                            pass
                        await asyncio.sleep(2)
                        verified_real = await page.evaluate(verify_js, product_id)
                        logger.info(f"inject v2: verified in real list = {verified_real}")

                        # Also check via context.request (no CORS) for alternate list endpoints
                        for check_ep in [
                            "https://shop.tiktok.com/api/v1/streamer_desktop/selection/list?offset=0&count=50",
                            "https://shop.tiktok.com/aweme/v1/oec/content/creator/products?aid=1180",
                        ]:
                            try:
                                cr = await context.request.get(check_ep, headers={
                                    "Origin": "https://www.tiktok.com",
                                    "Referer": "https://www.tiktok.com/tiktokstudio/upload",
                                })
                                ct = await cr.text()
                                logger.info(f"inject v2: {check_ep.split('/')[-1].split('?')[0]}: status={cr.status} body={ct[:300]}")
                                try:
                                    cd = json.loads(ct)
                                    prods = (cd.get('data') or {}).get('products') or []
                                    found = any(
                                        str(p.get('product_id','') or p.get('id','') or p.get('item_id','')) == product_id
                                        for p in prods
                                    )
                                    if found:
                                        result["added"] = True
                                        result["verified"] = True
                                        result["message"] = f"SUCCESS — found in {check_ep.split('/')[-1]}"
                                        break
                                except Exception:
                                    pass
                            except Exception as ce:
                                logger.warning(f"inject v2 check {check_ep}: {ce}")

                        if verified_real:
                            result["added"] = True
                            result["verified"] = True
                            result["message"] = "SUCCESS via route injection v2 + dialog click"
                    else:
                        try:
                            await page.unroute("**/showcase_product/list**")
                        except Exception:
                            pass
                        logger.info("inject v2: no product row found in dialog")
                        result["attempts"].append({
                            "strategy": "route_injection_v2",
                            "route_fired": route_fired,
                            "tab_switched": tab_switched,
                            "visible_rows": visible_rows,
                            "error": "no_row_found",
                        })
                except Exception as e:
                    logger.warning(f"Route injection v2 error: {e}")
                    try:
                        await page.unroute("**/showcase_product/list**")
                    except Exception:
                        pass
                    result["attempts"].append({"strategy": "route_injection_v2", "error": str(e)})

            # ── Strategy: OEC add_targets probe ────────────────────────────────
            # The oec/content/creator/products endpoint with add_targets:[5] links to video.
            # Response includes is_in_showcase:false. Try other add_targets values to find
            # the showcase target. Can be called directly via context.request (no CORS).
            if not result["added"]:
                logger.info("=== Strategy: OEC add_targets probe ===")
                oec_url = "https://shop.tiktok.com/aweme/v1/oec/content/creator/products?aid=1180&carrier_region=TH"
                oec_hdrs = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Origin": "https://www.tiktok.com",
                    "Referer": "https://www.tiktok.com/tiktokstudio/upload",
                }
                # Try different add_targets values (5=video confirmed; test 1-4,6-8,10,20)
                for targets in [[1], [2], [3], [4], [6], [7], [8], [10], [20], [1, 5], [2, 5]]:
                    try:
                        body_dict = {"products": [{"product_id": product_id}], "add_targets": targets}
                        resp = await context.request.post(oec_url, data=json.dumps(body_dict), headers=oec_hdrs)
                        text = await resp.text()
                        logger.info(f"OEC add_targets={targets}: {resp.status} {text[:400]}")
                        result["attempts"].append({
                            "strategy": "oec_targets",
                            "targets": targets,
                            "status": resp.status,
                            "body": text[:200],
                        })

                        # Check is_in_showcase field in response
                        try:
                            rdata = json.loads(text)
                            pid_result = (rdata.get('add_results') or {}).get(product_id, {})
                            in_showcase = pid_result.get('is_in_showcase', False)
                            add_status = pid_result.get('add_status')
                            logger.info(f"  → add_status={add_status}, is_in_showcase={in_showcase}")
                            if in_showcase:
                                # Verify by listing
                                await asyncio.sleep(2)
                                verified = await page.evaluate(verify_js, product_id)
                                logger.info(f"OEC targets={targets}: is_in_showcase=True, list verify={verified}")
                                if verified:
                                    result["added"] = True
                                    result["verified"] = True
                                    result["message"] = f"SUCCESS: oec add_targets={targets}"
                                    break
                        except Exception as pe:
                            logger.debug(f"OEC parse: {pe}")
                    except Exception as e:
                        logger.warning(f"OEC add_targets={targets}: {e}")

                    if result["added"]:
                        break

                # Even if is_in_showcase never became True, do a final verification
                if not result["added"]:
                    await asyncio.sleep(3)
                    final_verify = await page.evaluate(verify_js, product_id)
                    logger.info(f"OEC strategy: final showcase verify = {final_verify}")
                    if final_verify:
                        result["added"] = True
                        result["verified"] = True
                        result["message"] = "SUCCESS: OEC add_targets probe (found in showcase after probes)"

            # ── Strategy: LIVE Studio Monetization → product management ────────
            # Previous run: all live_urls redirect to tiktokstudio main dashboard.
            # The dashboard has a "Monetization" sidebar button. Click it to find
            # product/affiliate management pages.
            if not result["added"]:
                live_tab = await context.new_page()
                try:
                    logger.info("LIVE Studio: navigating to tiktokstudio")
                    await live_tab.goto("https://www.tiktok.com/tiktokstudio",
                                        wait_until="domcontentloaded", timeout=25000)
                    await asyncio.sleep(5)
                    await live_tab.screenshot(path=os.path.join(_TEMP_DIR, "debug_live_studio.png"))

                    # Click "Monetization" sidebar button
                    monetize_clicked = await live_tab.evaluate("""() => {
                        const btn = Array.from(document.querySelectorAll('button,[role="button"]'))
                            .find(e => {
                                const r = e.getBoundingClientRect();
                                const t = e.textContent.trim().toLowerCase();
                                return r.width > 0 && r.height > 0 &&
                                       (t.includes('monetiz') || t.includes('kiếm tiền') || t.includes('creator fund'));
                            });
                        if (btn) { btn.click(); return btn.textContent.trim(); }
                        return null;
                    }""")
                    logger.info(f"LIVE Studio: clicked Monetization: {monetize_clicked}")
                    if monetize_clicked:
                        await asyncio.sleep(5)
                        await live_tab.screenshot(path=os.path.join(_TEMP_DIR, "debug_live_monetize.png"))
                        title = await live_tab.title()
                        final_url = live_tab.url
                        logger.info(f"LIVE Studio after Monetization: url={final_url}, title={title!r}")

                        # Log all links/buttons now visible
                        mono_btns = await live_tab.evaluate("""() =>
                            Array.from(document.querySelectorAll('button,[role="button"],a'))
                                .filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.top<window.innerHeight;})
                                .map(e=>({text:e.textContent.trim().replace(/\\s+/g,' ').slice(0,80),href:e.href||'',tag:e.tagName}))
                                .filter(t=>t.text).slice(0,50)
                        """)
                        logger.info(f"LIVE Studio Monetization items ({len(mono_btns)}): {mono_btns}")
                        result["attempts"].append({
                            "step": "live_studio_monetization",
                            "url": final_url,
                            "items": [b['text'] for b in mono_btns[:20]],
                        })

                        # Click any TikTok Shop / affiliate / product item
                        kws = ['shop', 'affiliate', 'product', 'sản phẩm', 'giới thiệu', 'creator market']
                        live_calls: list = []
                        async def _cap_live2(req):
                            u = req.url
                            if any(d in u for d in ['shop.tiktok.com/api', 'tiktok.com/api/']) \
                                    and not any(x in u for x in ('.js', '.css')):
                                live_calls.append({'url': u, 'method': req.method})
                        live_tab.on("request", _cap_live2)

                        for btn in mono_btns:
                            if any(kw in btn['text'].lower() for kw in kws):
                                logger.info(f"LIVE Studio: clicking '{btn['text']}'")
                                try:
                                    if btn.get('href') and btn['href'].startswith('http'):
                                        await live_tab.goto(btn['href'], wait_until="domcontentloaded", timeout=15000)
                                        await asyncio.sleep(5)
                                    else:
                                        el = live_tab.get_by_text(btn['text'], exact=True).first
                                        if await el.count() > 0 and await el.is_visible():
                                            await el.click()
                                            await asyncio.sleep(5)
                                    await live_tab.screenshot(path=os.path.join(_TEMP_DIR, "debug_live_shop_click.png"))
                                    logger.info(f"After click: url={live_tab.url}")
                                    logger.info(f"API calls: {live_calls[-10:]}")
                                    break
                                except Exception as ce:
                                    logger.warning(f"LIVE Studio click '{btn['text']}': {ce}")

                        logger.info(f"LIVE Studio total API calls: {len(live_calls)}")
                        for c in live_calls[:20]:
                            logger.info(f"  LIVE API: {c['method']} {c['url'][:150]}")
                finally:
                    try:
                        await live_tab.screenshot(path=os.path.join(_TEMP_DIR, "debug_live_final.png"))
                    except Exception:
                        pass
                    await live_tab.close()

            if not result["added"]:
                result["message"] = "All strategies failed — product not added to showcase"

        except Exception as e:
            logger.error(f"add_product_to_showcase error: {e}")
            result["error"] = str(e)
        finally:
            await page.screenshot(path=os.path.join(_TEMP_DIR, "debug_add_direct_final.png"))
            await browser.close()

    return result
