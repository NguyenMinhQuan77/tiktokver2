"""
Test: post the 3rd video from @_xiangg__ with product attachment.
Run from /Users/quan.nm2/tiktok/tiktokver2:
  python test_xiang_v3.py
"""
import asyncio
import json
import logging
import re
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("test_xiang_v3")

from backend.services import profile_service
from backend.services.tiktok_browser import post_video

PROFILE_URL  = "https://www.tiktok.com/@_xiangg__"
MAX_VIDEOS   = 10
TARGET_INDEX = 1   # 1-based: pick first video (most likely to have product_id)


async def main():
    logger.info(f"=== Fetching {MAX_VIDEOS} videos from {PROFILE_URL} ===")
    videos, total = await profile_service.get_profile_videos(PROFILE_URL, MAX_VIDEOS)
    logger.info(f"Fetched {len(videos)} / total {total}")

    for i, v in enumerate(videos):
        sp = v.get("shop_product") or {}
        logger.info(
            f"  [{i+1}] id={v['id']}  product_id={sp.get('id')!r}  "
            f"product_name={sp.get('name','')[:50]!r}"
        )

    if len(videos) < TARGET_INDEX:
        logger.error(f"Only {len(videos)} videos — cannot pick #{TARGET_INDEX}")
        return

    target = videos[TARGET_INDEX - 1]
    sp = target.get("shop_product") or {}

    # If product_id not extracted (tikwm fallback has no anchor data), inject known ID
    if not sp.get("id") and sp.get("name"):
        sp = {**sp, "id": "1730113435668745075"}
        logger.info("product_id was empty — injected known ID for test")

    logger.info(f"\n=== Picked video #{TARGET_INDEX}: {target['id']} ===")
    logger.info(f"  URL:          {target['url']}")
    logger.info(f"  Caption:      {target.get('caption','')[:80]}")
    logger.info(f"  Product ID:   {sp.get('id')}")
    logger.info(f"  Product name: {sp.get('name','')[:60]}")

    logger.info(f"\n=== Downloading ===")
    local_path = await profile_service.download_video(target["url"])
    if not local_path:
        logger.error("Download failed")
        return
    logger.info(f"Downloaded: {local_path}")

    caption = target.get("caption") or target.get("title") or ""
    caption = re.sub(r'https?://\S+', '', caption).strip()
    if len(caption) > 200:
        caption = caption[:200]

    logger.info(f"\n=== Posting (show_browser=True) ===")
    logger.info(f"  Caption:     {caption[:80]!r}")
    logger.info(f"  shop_product: {sp}")

    result = await post_video(
        video_path=local_path,
        caption=caption,
        shop_product=sp,
        show_browser=True,
    )

    logger.info(f"\n=== RESULT ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
