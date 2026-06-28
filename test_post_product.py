"""
Quick test: post a video with showcase product 1734586165929477397 attached.
Run from project root: python3 test_post_product.py
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

from backend.services.tiktok_browser import post_video

VIDEO_PATH = "/home/quannm1/tiktok/temp/dl_07ad3f8eac.mp4"
CAPTION = "Test search by product ID #cleanfit"
# sp gốc from @_xiangg__ first video
SHOP_PRODUCT = {
    "id": "1730113435668745075",
    "name": "Áo Chống Nắng Nam Nữ Nón 2 Lớp",
    "price": "",
    "image": "",
    "url": "",
}

async def main():
    print(f"Posting: {VIDEO_PATH}")
    print(f"Caption: {CAPTION}")
    print(f"Shop product ID: {SHOP_PRODUCT['id']}")
    print("---")
    # sp gốc mode: shop_product with ID → _search_and_select_myshop searches by ID
    result = await post_video(
        video_path=VIDEO_PATH,
        caption=CAPTION,
        shop_product=SHOP_PRODUCT,
    )
    print("Result:", json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
