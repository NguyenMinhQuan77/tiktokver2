import os
from fastapi import APIRouter, HTTPException

from backend.models import PublishRequest, PublishResponse
from backend.services import tiktok_browser
from backend.config import settings

router = APIRouter(prefix="/publish", tags=["publish"])


@router.post("/tiktok", response_model=PublishResponse)
async def publish_to_tiktok(request: PublishRequest):
    """Post video to TikTok using browser automation."""
    # Resolve video path
    video_path = request.video_path
    if video_path.startswith("/temp/"):
        video_path = os.path.join(settings.TEMP_DIR, os.path.basename(video_path))

    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file video: {video_path}")

    # Build caption with product URL
    caption = request.caption
    if request.product_url:
        caption = caption + f"\n\n🛒 Link sản phẩm: {request.product_url}"

    try:
        await tiktok_browser.post_video(video_path=video_path, caption=caption)
        return PublishResponse(
            success=True,
            message="Đã đăng video lên TikTok thành công! Kiểm tra tài khoản của bạn.",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi đăng video: {str(e)}")
