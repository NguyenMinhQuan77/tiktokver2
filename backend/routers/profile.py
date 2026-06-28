"""
Profile router: fetch TikTok profile videos via yt-dlp.
"""
import re
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services import profile_service

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileRequest(BaseModel):
    url: str
    max_count: int = 10


class ProfileCountRequest(BaseModel):
    url: str


@router.post("/count")
async def get_profile_video_count(req: ProfileCountRequest):
    """Quickly fetch total video count for a TikTok profile."""
    url = req.url.strip().split("?")[0].rstrip("/")
    if not url or "tiktok.com" not in url:
        raise HTTPException(status_code=400, detail="Link TikTok không hợp lệ")
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        }
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
            resp = await client.get(url)
        html = resp.text
        # Try to extract videoCount from the page's JSON blob
        video_count = 0
        for pattern in [
            r'"videoCount"\s*:\s*(\d+)',
            r'"video_count"\s*:\s*(\d+)',
        ]:
            m = re.search(pattern, html)
            if m:
                video_count = int(m.group(1))
                break
        return {"total_count": video_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi kiểm tra profile: {str(e)}")


@router.post("/videos")
async def get_profile_videos(req: ProfileRequest):
    url = req.url.strip().split("?")[0].rstrip("/")
    max_count = max(1, min(req.max_count, 200))
    if not url:
        raise HTTPException(status_code=400, detail="Vui lòng nhập link profile TikTok")
    if "tiktok.com" not in url:
        raise HTTPException(
            status_code=400,
            detail="Link không hợp lệ. Vui lòng nhập link TikTok (ví dụ: https://www.tiktok.com/@username)",
        )
    try:
        videos, total_count = await profile_service.get_profile_videos(url, max_count)
        if not videos:
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy video nào. Kiểm tra lại link profile TikTok.",
            )
        return {"videos": videos, "total_count": total_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi khi lấy video: {str(e)}",
        )
