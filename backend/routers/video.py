from fastapi import APIRouter, HTTPException
from backend.models import (
    VideoGenerateRequest,
    VideoGenerateResponse,
    ProductInfo,
)
from backend.services.ai_service import generate_content
from backend.services.video_maker import create_video

router = APIRouter(prefix="/video", tags=["video"])


@router.post("/generate", response_model=VideoGenerateResponse)
async def generate_video(request: VideoGenerateRequest):
    """Generate AI content and create video from product info."""

    # Reconstruct ProductInfo from the dict sent by frontend
    pi_dict = request.product_info
    try:
        product_info = ProductInfo(
            name=pi_dict.get("name", "Sản phẩm"),
            description=pi_dict.get("description", ""),
            price=pi_dict.get("price", ""),
            currency=pi_dict.get("currency", "VND"),
            images=pi_dict.get("images", []),
            shop_type=pi_dict.get("shop_type", "unknown"),
            original_url=request.product_url,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Dữ liệu sản phẩm không hợp lệ: {e}")

    # 1. Generate content via Claude
    try:
        content = await generate_content(product_info, notes=request.custom_notes or "")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Không thể tạo nội dung AI: {str(e)}"
        )

    # 2. Create the video
    try:
        video_path = await create_video(product_info, content.audio_text)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Không thể tạo video: {str(e)}"
        )

    # Return relative URL path for the video
    import os
    video_filename = os.path.basename(video_path)
    video_url = f"/temp/{video_filename}"

    return VideoGenerateResponse(
        script=content.video_script,
        caption=content.caption,
        hashtags=content.hashtags,
        video_path=video_url,
        audio_text=content.audio_text,
        hook=content.hook,
    )
