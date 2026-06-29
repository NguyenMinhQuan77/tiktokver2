"""
Schedule router: create, list, and cancel scheduled TikTok posts.
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services import scheduler_service

router = APIRouter(prefix="/schedule", tags=["schedule"])


class ScheduleVideoItem(BaseModel):
    video_url: str
    caption: str
    product_url: str = ""   # manual URL input (used if product_id not set)
    product_id: str = ""    # TikTok Shop showcase product ID (preferred over product_url)
    shop_product: dict = {} # source video's original product — searched in My shop by ID
    delay_minutes: int
    thumbnail: str = ""
    title: str = ""
    show_browser: bool = True  # whether to show Chrome window during upload
    account_handle: str = ""   # which account to post with (empty = use active)


class ScheduleCreateRequest(BaseModel):
    videos: List[ScheduleVideoItem]


@router.post("/create")
async def create_schedule(req: ScheduleCreateRequest):
    if not req.videos:
        raise HTTPException(status_code=400, detail="Chưa chọn video nào để lên lịch")

    created = []
    for v in req.videos:
        item = scheduler_service.schedule_post(
            video_url=v.video_url,
            caption=v.caption,
            affiliate_url=v.product_url,
            product_id=v.product_id,
            shop_product=v.shop_product,
            delay_minutes=v.delay_minutes,
            thumbnail=v.thumbnail,
            title=v.title,
            show_browser=v.show_browser,
            account_handle=v.account_handle,
        )
        # Strip internal-only fields before returning
        created.append({k: val for k, val in item.items() if k not in ("task", "post_at")})

    return {"scheduled": len(created), "items": created}


@router.get("/list")
async def list_schedule():
    items = scheduler_service.get_all()
    now = datetime.now()
    result = []
    for item in items:
        d = {k: v for k, v in item.items() if k not in ("task", "post_at", "video_path")}
        if item["status"] == "pending":
            remaining = max(0, (item["post_at"] - now).total_seconds())
            d["seconds_remaining"] = int(remaining)
        else:
            d["seconds_remaining"] = 0
        result.append(d)
    return {"items": result}


@router.delete("/{item_id}")
async def cancel_schedule(item_id: str):
    ok = scheduler_service.cancel(item_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy hoặc không thể huỷ lịch đăng này",
        )
    return {"success": True}
