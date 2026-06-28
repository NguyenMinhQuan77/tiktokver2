"""
Product service: fetch TikTok Shop showcase products from TikTok Studio.
"""
import asyncio
import json as _json
import logging
import os
from typing import List

logger = logging.getLogger(__name__)

_products_cache: List[dict] = []

def _get_cookies_file() -> str:
    from backend.services.tiktok_browser import get_active_cookies_file
    return get_active_cookies_file()
_PRODUCTS_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "temp", "showcase_products_cache.json",
)


def _load_cache_from_file():
    """Load persisted showcase products from disk on startup."""
    global _products_cache
    try:
        if os.path.exists(_PRODUCTS_CACHE_FILE):
            with open(_PRODUCTS_CACHE_FILE) as f:
                data = _json.load(f)
            if isinstance(data, list) and data:
                _products_cache = data
                logger.info(f"Loaded {len(_products_cache)} showcase products from cache file")
    except Exception:
        pass


def _save_cache_to_file(products: list):
    """Persist showcase products to disk."""
    try:
        os.makedirs(os.path.dirname(_PRODUCTS_CACHE_FILE), exist_ok=True)
        with open(_PRODUCTS_CACHE_FILE, "w") as f:
            _json.dump(products, f, ensure_ascii=False)
    except Exception:
        pass


# Auto-load from file when the module is first imported
_load_cache_from_file()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def get_cached_products() -> List[dict]:
    return _products_cache


def _load_cookies():
    import json
    if not os.path.exists(_get_cookies_file()):
        return None
    try:
        with open(_get_cookies_file()) as f:
            return json.load(f)
    except Exception:
        return None


def _find_small_video() -> str:
    """Find a small existing MP4 in temp to use as dummy upload."""
    temp_dir = os.path.dirname(_get_cookies_file())
    candidates = []
    for f in os.listdir(temp_dir):
        if f.endswith(".mp4"):
            p = os.path.join(temp_dir, f)
            candidates.append((os.path.getsize(p), p))
    candidates.sort()
    for size, path in candidates:
        if size > 100_000:
            return path
    return ""


async def fetch_products() -> List[dict]:
    """
    Open TikTok Studio, upload a dummy video, open Showcase products dialog,
    parse the product list from DOM, then discard. Returns list of products.
    """
    global _products_cache

    cookies = _load_cookies()
    if not cookies:
        raise RuntimeError("Chưa đăng nhập TikTok.")

    dummy_video = _find_small_video()
    if not dummy_video:
        raise RuntimeError("Không tìm thấy video trong temp để tải thử.")

    products = []

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=USER_AGENT,
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            await page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded")
            await asyncio.sleep(5)

            # Upload dummy video to trigger the upload form
            file_input = page.locator('input[type="file"]').first
            if await file_input.count() == 0:
                raise RuntimeError("Không vào được TikTok Studio — kiểm tra đăng nhập.")
            await file_input.set_input_files(dummy_video)

            # Wait for upload form (caption box) — dismiss popups/overlays on every iteration
            popup_selectors = [
                'button:has-text("Got it")', 'button:has-text("Đã hiểu")',
                'button:has-text("OK")', '[aria-label="Close"]',
                '[data-e2e="modal-close-inner-button"]',
            ]
            caption_ready = False
            for _ in range(40):
                # Dismiss react-joyride tutorial overlay (blocks all clicks in TikTok Studio)
                try:
                    overlay = page.locator('[data-test-id="overlay"]').first
                    if await overlay.count() > 0 and await overlay.is_visible():
                        await overlay.click(force=True)
                        await asyncio.sleep(0.5)
                except Exception:
                    pass
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                for sel in popup_selectors:
                    try:
                        btn = page.locator(sel).first
                        if await btn.count() > 0 and await btn.is_visible():
                            await btn.click(force=True)
                            await asyncio.sleep(0.5)
                    except Exception:
                        pass
                await asyncio.sleep(3)
                el = page.locator('div[contenteditable="true"]').first
                if await el.count() > 0 and await el.is_visible():
                    caption_ready = True
                    break

            if not caption_ready:
                raise RuntimeError("Form upload không xuất hiện.")

            # Click "+ Add" / "Add link" button — dismiss joyride overlay first
            try:
                overlay = page.locator('[data-test-id="overlay"]').first
                if await overlay.count() > 0:
                    await overlay.click(force=True)
                    await asyncio.sleep(1)
            except Exception:
                pass
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
            except Exception:
                pass

            add_btn = None
            for add_sel in ['text="+ Add"', 'text="Add"', ':text("+ Add")', ':text("Add link")']:
                btn = page.locator(add_sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    add_btn = btn
                    break
            if add_btn is None:
                raise RuntimeError("Không tìm thấy nút '+ Add'.")
            await add_btn.click(force=True)
            await asyncio.sleep(2)

            # Some TikTok Studio versions show a "Next" step in the add-link dialog
            for next_sel in ['button:has-text("Next")', 'button:has-text("Tiếp theo")']:
                btn = page.locator(next_sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(2)
                    break

            # Click "Showcase products" tab
            for tab_sel in ['text="Showcase products"', ':text("Showcase products")', ':text("Sản phẩm giới thiệu")']:
                showcase_tab = page.locator(tab_sel).first
                if await showcase_tab.count() > 0 and await showcase_tab.is_visible():
                    await showcase_tab.click()
                    break
            await asyncio.sleep(3)

            # Parse all pages of products
            all_done = False
            while not all_done:
                products.extend(await _parse_product_page(page))

                # Try clicking "Next page" pagination button
                next_page = page.locator('button[aria-label="Go to next page"], li[title="Next Page"] button').first
                if await next_page.count() > 0 and await next_page.is_enabled():
                    await next_page.click()
                    await asyncio.sleep(2)
                else:
                    all_done = True

            logger.info(f"Fetched {len(products)} products from TikTok Shop")

        except Exception as e:
            logger.error(f"Product fetch error: {e}")
            raise RuntimeError(str(e))
        finally:
            # Discard the upload
            for sel in ['button:has-text("Cancel")', 'button:has-text("Hủy")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(1)
                        break
                except Exception:
                    pass
            for sel in ['button:has-text("Discard")', 'button:has-text("Huỷ upload")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(1)
                        break
                except Exception:
                    pass
            await browser.close()

    if products:
        _products_cache = products
        _save_cache_to_file(products)

    return products


async def _parse_product_page(page) -> List[dict]:
    """Parse the current page of the Showcase products table."""
    products = []
    try:
        rows = page.locator("table tbody tr")
        count = await rows.count()
        for i in range(count):
            row = rows.nth(i)
            try:
                cells = await row.locator("td").all_text_contents()
                img_el = row.locator("img").first
                img_src = await img_el.get_attribute("src") if await img_el.count() > 0 else ""

                # Columns: [radio] [image+name] [product_id] [price] [stock] [status]
                # cells[0] might be empty (radio), cells[1] = name, cells[2] = id, etc.
                name = ""
                product_id = ""
                price = ""
                stock = ""

                # Find the cell that looks like a product ID (long number)
                for cell in cells:
                    cell = cell.strip()
                    if cell.isdigit() and len(cell) > 10:
                        product_id = cell
                    elif cell and not cell.isdigit() and len(cell) > 3 and not product_id:
                        name = cell

                # Fallback: take cells in order
                non_empty = [c.strip() for c in cells if c.strip()]
                if len(non_empty) >= 2 and not name:
                    name = non_empty[0]
                if len(non_empty) >= 3:
                    price = non_empty[2] if non_empty[2] not in (name, product_id) else (non_empty[3] if len(non_empty) > 3 else "")

                if product_id or name:
                    products.append({
                        "id": product_id,
                        "name": name,
                        "price": price,
                        "stock": stock,
                        "image": img_src or "",
                    })
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Page parse error: {e}")
    return products
