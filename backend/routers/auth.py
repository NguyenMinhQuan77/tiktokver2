import os
from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.models import AuthStatusResponse
from backend.services import tiktok_browser

router = APIRouter(prefix="/auth", tags=["auth"])

login_status: dict = {"running": False, "error": None, "handle": ""}


class SwitchAccountRequest(BaseModel):
    handle: str


class ConnectRequest(BaseModel):
    handle: str = ""


@router.get("/accounts")
async def list_accounts():
    """Return all configured accounts with login status."""
    accounts = tiktok_browser.load_accounts()
    result = []
    for acc in accounts:
        handle = acc["handle"]
        cookie_path = tiktok_browser.get_cookies_file_for(handle)
        logged_in = False
        if os.path.exists(cookie_path):
            cookies = []
            try:
                import json
                with open(cookie_path) as f:
                    cookies = json.load(f)
            except Exception:
                pass
            names = {c["name"] for c in cookies}
            session_cookies = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt"}
            logged_in = bool(names & session_cookies)
        result.append({
            "handle": handle,
            "username": acc.get("username", ""),
            "logged_in": logged_in,
            "active": handle == tiktok_browser.get_active_handle(),
        })
    return {"accounts": result, "active_handle": tiktok_browser.get_active_handle()}


@router.post("/switch")
async def switch_account(req: SwitchAccountRequest):
    """Switch the active TikTok account."""
    accounts = tiktok_browser.load_accounts()
    handles = [a["handle"] for a in accounts]
    if req.handle not in handles:
        return JSONResponse(status_code=400, content={"error": f"Account '{req.handle}' không tồn tại"})
    tiktok_browser.set_active_account(req.handle)
    return {"success": True, "active_handle": req.handle}


@router.post("/connect")
async def connect_account(req: ConnectRequest, background_tasks: BackgroundTasks):
    """Trigger Playwright browser login for the given account (or active account)."""
    if login_status["running"]:
        return {"status": "running", "message": "Đang mở trình duyệt đăng nhập..."}

    handle = req.handle or tiktok_browser.get_active_handle()
    accounts = tiktok_browser.load_accounts()
    acc = next((a for a in accounts if a["handle"] == handle), None)
    if not acc:
        return JSONResponse(
            status_code=400,
            content={"error": f"Không tìm thấy account '{handle}' trong accounts.json"},
        )

    login_status["running"] = True
    login_status["error"] = None
    login_status["handle"] = handle

    # Switch to this account before logging in so cookies are saved to the right file
    tiktok_browser.set_active_account(handle)

    async def do_login():
        try:
            await tiktok_browser.login(acc["username"], acc["password"])
        except Exception as e:
            login_status["error"] = str(e)
        finally:
            login_status["running"] = False

    background_tasks.add_task(do_login)
    return {"status": "started", "handle": handle, "message": f"Đang mở trình duyệt TikTok cho @{handle}. Hoàn tất đăng nhập trong cửa sổ hiện ra."}


@router.get("/login-status")
async def check_login_progress():
    return {
        "running": login_status["running"],
        "error": login_status["error"],
        "handle": login_status.get("handle", ""),
    }


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status():
    """Check login status for the currently active account."""
    cookies = tiktok_browser.load_cookies()
    if not cookies:
        return AuthStatusResponse(logged_in=False)
    names = {c["name"] for c in cookies}
    session_cookies = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt"}
    logged_in = bool(names & session_cookies)
    handle = tiktok_browser.get_active_handle()
    return AuthStatusResponse(logged_in=logged_in, username=f"@{handle}" if handle else "TikTok", avatar="")


@router.post("/logout")
async def logout():
    """Delete cookies for the active account."""
    tiktok_browser.delete_cookies()
    return {"success": True, "message": "Đã đăng xuất"}
