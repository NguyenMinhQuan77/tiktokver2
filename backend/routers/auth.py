import asyncio
from fastapi import APIRouter, Cookie, Response, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import Optional

from backend.config import settings
from backend.models import AuthStatusResponse, sessions
from backend.services import tiktok_browser

router = APIRouter(prefix="/auth", tags=["auth"])

# Track background login task status
login_status: dict = {"running": False, "error": None}


@router.post("/connect")
async def connect_account(background_tasks: BackgroundTasks):
    """Trigger Playwright browser login in background."""
    if login_status["running"]:
        return {"status": "running", "message": "Đang mở trình duyệt đăng nhập..."}

    if not settings.TIKTOK_USERNAME or not settings.TIKTOK_PASSWORD:
        return JSONResponse(
            status_code=400,
            content={"error": "Chưa cấu hình TIKTOK_USERNAME và TIKTOK_PASSWORD trong file .env"},
        )

    login_status["running"] = True
    login_status["error"] = None

    async def do_login():
        try:
            await tiktok_browser.login(settings.TIKTOK_USERNAME, settings.TIKTOK_PASSWORD)
        except Exception as e:
            login_status["error"] = str(e)
        finally:
            login_status["running"] = False

    background_tasks.add_task(do_login)
    return {"status": "started", "message": "Đang mở trình duyệt TikTok. Hoàn tất đăng nhập trong cửa sổ hiện ra."}


@router.get("/login-status")
async def check_login_progress():
    """Poll status of background login task."""
    return {
        "running": login_status["running"],
        "error": login_status["error"],
    }


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status():
    """Check if TikTok cookies are saved (i.e., user is logged in)."""
    cookies = tiktok_browser.load_cookies()
    if not cookies:
        return AuthStatusResponse(logged_in=False)

    names = {c["name"] for c in cookies}
    # TikTok uses various session cookie names depending on platform/region
    session_cookies = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt"}
    logged_in = bool(names & session_cookies)

    username = settings.TIKTOK_USERNAME or "TikTok User"
    return AuthStatusResponse(logged_in=logged_in, username=username, avatar="")


@router.post("/logout")
async def logout():
    """Delete saved cookies."""
    tiktok_browser.delete_cookies()
    return {"success": True, "message": "Đã đăng xuất"}
