"""
Full end-to-end test:
1. List 10 videos from @_xiangg__
2. Pick first video that has a shop_product with product ID
3. Download the video
4. Post it to levelshop with correct product (search by product ID in showcase)
"""
import asyncio
import json
import logging
import sys
sys.path.insert(0, "/home/quannm1/tiktok")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("test_xiang_full")

from backend.services import profile_service
from backend.services.tiktok_browser import post_video

PROFILE_URL = "https://www.tiktok.com/@_xiangg__"
MAX_VIDEOS = 10
TARGET_INDEX = 9  # 1-based: post the 9th video


async def main():
    # Step 1: List 10 videos from @_xiangg__
    logger.info(f"=== Step 1: Fetching {MAX_VIDEOS} videos from {PROFILE_URL} ===")
    videos, total = await profile_service.get_profile_videos(PROFILE_URL, MAX_VIDEOS)
    logger.info(f"Fetched {len(videos)} videos (total on channel: {total})")

    for i, v in enumerate(videos):
        sp = v.get("shop_product") or {}
        logger.info(
            f"  [{i+1}] id={v['id']} product_id={sp.get('id')!r} "
            f"product_name={sp.get('name','')[:50]!r}"
        )

    # Step 2: Pick the TARGET_INDEX-th video (1-based)
    if len(videos) < TARGET_INDEX:
        logger.error(f"Only {len(videos)} videos fetched, cannot pick #{TARGET_INDEX}")
        return
    target = videos[TARGET_INDEX - 1]

    if not target:
        logger.error("No video with product found — aborting")
        return

    sp = target.get("shop_product") or {}
    logger.info(f"\n=== Step 2: Selected video {target['id']} ===")
    logger.info(f"  URL: {target['url']}")
    logger.info(f"  Caption: {target.get('caption','')[:80]}")
    logger.info(f"  Product ID: {sp.get('id')}")
    logger.info(f"  Product name: {sp.get('name','')[:60]}")

    # Step 3: Download video
    logger.info(f"\n=== Step 3: Downloading video ===")
    local_path = await profile_service.download_video(target["url"])
    if not local_path:
        logger.error("Download failed — aborting")
        return
    logger.info(f"Downloaded to: {local_path}")

    # Step 4: Post with correct product
    caption = target.get("caption") or target.get("title") or "Video từ @_xiangg__"
    # Keep caption clean — strip affiliate links
    import re
    caption = re.sub(r'https?://\S+', '', caption).strip()
    if len(caption) > 200:
        caption = caption[:200]

    logger.info(f"\n=== Step 4: Posting video ===")
    logger.info(f"  Caption: {caption[:80]!r}")
    logger.info(f"  shop_product: {sp}")

    result = await post_video(
        video_path=local_path,
        caption=caption,
        shop_product=sp,
    )

    logger.info(f"\n=== RESULT ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
