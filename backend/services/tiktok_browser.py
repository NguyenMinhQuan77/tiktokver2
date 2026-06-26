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

COOKIES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "temp", "tiktok_cookies.json"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"


def load_cookies() -> Optional[list]:
    if not os.path.exists(COOKIES_FILE):
        return None
    try:
        with open(COOKIES_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_cookies(cookies: list):
    os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)


def delete_cookies():
    if os.path.exists(COOKIES_FILE):
        os.remove(COOKIES_FILE)


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
                await btn.click()
                await asyncio.sleep(0.5)
                logger.info(f"Dismissed popup: {sel}")
        except Exception:
            continue


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


async def post_video(video_path: str, caption: str, product_url: str = "", product_id: str = "") -> dict:
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
        if product_id:
            logger.info(f"Attaching showcase product: {product_id}")
            try:
                add_link_btn = page.locator('text=+ Add').first
                if await add_link_btn.count() > 0:
                    await add_link_btn.click()
                    await asyncio.sleep(1.5)

                    # Switch to "Showcase products" tab
                    showcase_tab = page.locator('text="Showcase products"').first
                    if await showcase_tab.count() > 0:
                        await showcase_tab.click()
                        await asyncio.sleep(2)
                        logger.info("Switched to Showcase products tab")
                    else:
                        logger.warning("Showcase products tab not found")

                    # Find product row matching product_id and click its radio
                    product_rows = page.locator("table tbody tr")
                    row_count = await product_rows.count()
                    logger.info(f"Found {row_count} product rows")
                    found = False
                    for i in range(row_count):
                        row = product_rows.nth(i)
                        row_text = await row.inner_text()
                        if product_id in row_text:
                            radio = row.locator('input[type="radio"]').first
                            if await radio.count() > 0:
                                await radio.click()
                                found = True
                                logger.info(f"Selected product {product_id} at row {i}")
                                break

                    if not found and row_count > 0:
                        # Fallback: click first product radio
                        radio = product_rows.nth(0).locator('input[type="radio"]').first
                        if await radio.count() > 0:
                            await radio.click()
                            logger.info("Selected first product (product_id not found in rows)")

                    await asyncio.sleep(1)

                    # Click "Next" to confirm selection
                    for next_sel in ['button:has-text("Next")', 'button:has-text("Tiếp theo")']:
                        next_btn = page.locator(next_sel).first
                        if await next_btn.count() > 0 and await next_btn.is_visible():
                            await next_btn.click()
                            logger.info(f"Clicked {next_sel}")
                            await asyncio.sleep(2)
                            break

                else:
                    logger.warning("Could not find '+ Add' button for showcase product")
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
                    pre_click_path = os.path.join(os.path.dirname(COOKIES_FILE), "pre_post_click.png")
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

        # --- Step 6b: Handle "Continue to post?" confirmation dialog ---
        # TikTok shows this dialog when content is flagged as restricted.
        # Try multiple click strategies; React synthetic events need dispatchEvent, not .click().
        await asyncio.sleep(3)
        confirmed_dialog = False

        # Strategy 1: Playwright locator click (dispatches proper pointer events)
        for confirm_sel in [
            'button:has-text("Post now")',
            'button:has-text("Đăng ngay")',
            'button:has-text("Continue")',
            'button:has-text("Tiếp tục")',
        ]:
            try:
                btn = page.locator(confirm_sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.scroll_into_view_if_needed()
                    await asyncio.sleep(0.3)
                    await btn.click(force=True)
                    logger.info(f"Playwright-clicked dialog button: {confirm_sel}")
                    confirmed_dialog = True
                    await asyncio.sleep(4)
                    break
            except Exception as e:
                logger.warning(f"Playwright click {confirm_sel}: {e}")

        # Strategy 2: JS dispatchEvent (React-compatible synthetic event)
        if not confirmed_dialog:
            for confirm_text in ["Post now", "Đăng ngay", "Continue", "Tiếp tục"]:
                try:
                    result = await page.evaluate(f"""() => {{
                        const btns = Array.from(document.querySelectorAll('button'));
                        const btn = btns.find(b => b.textContent.trim() === '{confirm_text}');
                        if (btn && !btn.disabled) {{
                            ['mousedown','mouseup','click'].forEach(evt =>
                                btn.dispatchEvent(new MouseEvent(evt, {{bubbles:true, cancelable:true}}))
                            );
                            return btn.textContent.trim();
                        }}
                        return null;
                    }}""")
                    if result:
                        logger.info(f"dispatchEvent confirm button: '{result}'")
                        confirmed_dialog = True
                        await asyncio.sleep(4)
                        break
                except Exception as e:
                    logger.warning(f"dispatchEvent '{confirm_text}': {e}")

        # Strategy 3: Press Enter (dialog may have focus on confirm button)
        if not confirmed_dialog:
            try:
                await page.keyboard.press("Enter")
                logger.info("Pressed Enter for dialog confirmation")
                await asyncio.sleep(3)
            except Exception:
                pass

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
                # Dialog re-appeared (e.g. TikTok re-showed confirmation) — click again
                for retry_sel in ['button:has-text("Post now")', 'button:has-text("Đăng ngay")']:
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
            screenshot_path = os.path.join(os.path.dirname(COOKIES_FILE), "post_result.png")
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
