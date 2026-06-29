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
    start_index: int = 1  # 1-based: first video to return


class ProfileCountRequest(BaseModel):
    url: str


@router.post("/count")
async def get_profile_video_count(req: ProfileCountRequest):
    """Quickly fetch total video count for a TikTok profile via tikwm user info API."""
    url = req.url.strip().split("?")[0].rstrip("/")
    if not url or "tiktok.com" not in url:
        raise HTTPException(status_code=400, detail="Link TikTok không hợp lệ")
    # Extract @handle from URL
    m = re.search(r"tiktok\.com/@([^/?#]+)", url)
    if not m:
        raise HTTPException(status_code=400, detail="Không tìm thấy username trong link")
    unique_id = m.group(1)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://www.tikwm.com/api/user/info",
                data={"unique_id": unique_id},
            )
        data = resp.json()
        stats = (data.get("data") or {}).get("stats", {})
        video_count = stats.get("videoCount", 0)
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
    start_index = max(1, req.start_index)
    fetch_count = start_index - 1 + max_count  # fetch enough to slice from start_index
    try:
        videos, total_count = await profile_service.get_profile_videos(url, fetch_count)
        videos = videos[start_index - 1:]  # slice from requested start
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
