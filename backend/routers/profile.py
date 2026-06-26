"""
Profile router: fetch TikTok profile videos via yt-dlp.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services import profile_service

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileRequest(BaseModel):
    url: str


@router.post("/videos")
async def get_profile_videos(req: ProfileRequest):
    url = req.url.strip().split("?")[0].rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="Vui lòng nhập link profile TikTok")
    if "tiktok.com" not in url:
        raise HTTPException(
            status_code=400,
            detail="Link không hợp lệ. Vui lòng nhập link TikTok (ví dụ: https://www.tiktok.com/@username)",
        )
    try:
        videos = await profile_service.get_profile_videos(url)
        if not videos:
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy video nào. Kiểm tra lại link profile TikTok.",
            )
        return {"videos": videos}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi khi lấy video: {str(e)}",
        )
