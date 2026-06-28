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

VIDEO_PATH = "/home/quannm1/tiktok/temp/dl_b3399f3c43.mp4"
CAPTION = "Test đăng video với sản phẩm #cleanfit #thunnam"
PRODUCT_ID = "1734586165929477397"
SHOP_PRODUCT = {
    "id": "1734586165929477397",
    "name": "Phiên Bản Nâng Cấp Cleanfit áo thun",
    "price": "220000",
    "image": "",
    "url": "",
}

async def main():
    print(f"Posting: {VIDEO_PATH}")
    print(f"Caption: {CAPTION}")
    print(f"Product ID (showcase): {PRODUCT_ID}")
    print("---")
    # Use product_id only → triggers "Showcase products" tab search
    # (shop_product mode would search "My shop" tab instead)
    result = await post_video(
        video_path=VIDEO_PATH,
        caption=CAPTION,
        product_id=PRODUCT_ID,
    )
    print("Result:", json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
