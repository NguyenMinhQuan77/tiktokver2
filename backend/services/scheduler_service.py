"""
Scheduler service: in-memory queue for scheduled TikTok posts.
Each item downloads a video with yt-dlp, waits the configured delay,
then posts to TikTok via Playwright.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from backend.services import profile_service, tiktok_browser

logger = logging.getLogger(__name__)

# In-memory schedule store — list of dicts
schedule_store: list = []


def get_all() -> list:
    """Return all schedule items (without asyncio Task object)."""
    return schedule_store


def get_by_id(item_id: str) -> Optional[dict]:
    return next((s for s in schedule_store if s["id"] == item_id), None)


def cancel(item_id: str) -> bool:
    """Cancel a pending item. Returns True if cancelled, False otherwise."""
    item = get_by_id(item_id)
    if not item:
        return False
    if item["status"] not in ("pending", "downloading"):
        return False
    item["status"] = "cancelled"
    task: Optional[asyncio.Task] = item.get("task")
    if task and not task.done():
        task.cancel()
    return True


async def _run_post(item_id: str):
    """Background coroutine: wait → download → post."""
    item = get_by_id(item_id)
    if not item:
        return

    try:
        # Sleep until scheduled post time
        now = datetime.now()
        post_at: datetime = item["post_at"]
        wait_seconds = max(0.0, (post_at - now).total_seconds())
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        if item["status"] == "cancelled":
            return

        # Download the video
        item["status"] = "downloading"
        video_path = await profile_service.download_video(item["video_url"])
        item["video_path"] = video_path

        if item["status"] == "cancelled":
            return

        # Build final caption: original caption + affiliate link
        caption = item.get("caption", "")
        affiliate_url = item.get("affiliate_url", "")
        if affiliate_url and affiliate_url not in caption:
            caption = f"{caption}\n\n{affiliate_url}".strip()

        # Post to TikTok via Playwright
        item["status"] = "posting"
        result = await tiktok_browser.post_video(
            video_path=video_path,
            caption=caption,
            product_url=affiliate_url,
            product_id=item.get("product_id", ""),
            shop_product=item.get("shop_product", {}),
        )
        item["status"] = "done"
        item["profile_url"] = result.get("profile_url", "")

    except asyncio.CancelledError:
        if item:
            item["status"] = "cancelled"
    except Exception as e:
        logger.error(f"Schedule task {item_id} failed: {e}", exc_info=True)
        if item:
            item["status"] = "failed"
            item["error"] = str(e)


def schedule_post(
    video_url: str,
    caption: str,
    affiliate_url: str,
    delay_minutes: int,
    product_id: str = "",
    shop_product: dict = {},
    thumbnail: str = "",
    title: str = "",
) -> dict:
    """
    Create and enqueue a new scheduled post.

    Returns the item dict (includes 'task' key with the asyncio.Task).
    """
    item_id = uuid.uuid4().hex[:8]
    now = datetime.now()
    post_at = now + timedelta(minutes=delay_minutes)

    item = {
        "id": item_id,
        "video_url": video_url,
        "title": title,
        "caption": caption,
        "affiliate_url": affiliate_url,
        "product_id": product_id,
        "shop_product": shop_product,
        "thumbnail": thumbnail,
        "delay_minutes": delay_minutes,
        "post_at": post_at,                               # datetime object (internal use)
        "post_at_str": post_at.strftime("%H:%M %d/%m/%Y"),
        "created_at": now.strftime("%H:%M:%S %d/%m/%Y"),
        "status": "pending",
        "error": None,
        "video_path": None,
        "task": None,                                      # filled below
    }

    schedule_store.append(item)

    # Create and store the background task
    task = asyncio.create_task(_run_post(item_id))
    item["task"] = task

    return item
