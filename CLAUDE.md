# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
./run.sh          # install deps + start server at http://localhost:8000
```

Or manually:
```bash
pip install -r requirements.txt
python -m playwright install chromium
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

The frontend is served as a single HTML file at `/` (no separate build step).

## Environment

Copy `.env.example` → `.env` and fill in:
- `TIKTOK_USERNAME` / `TIKTOK_PASSWORD` / `TIKTOK_HANDLE` — TikTok account for posting
- `GROQ_API_KEY` — Groq LLM key (used in `ai_service.py`)

Cookies are stored at `temp/tiktok_cookies.json` in Playwright JSON format. The `temp/` directory also holds downloaded MP4s and screenshots.

## Architecture

**Single-page app**: FastAPI backend + one HTML file frontend (`frontend/index.html`). No JS framework — plain Alpine.js-style reactive state in vanilla JS.

**Request flow for reposting:**
1. `POST /profile/videos` → `profile_service.get_profile_videos()` — fetches video list from a TikTok profile URL using yt-dlp as primary method, with TikTok internal API and tikwm.com as fallbacks. Returns `shop_product` dict per video (extracted from API anchors or `[bracket]` caption heuristic).
2. `POST /schedule/create` → `scheduler_service.schedule_post()` — enqueues a background asyncio task. The task: downloads the video (`profile_service.download_video()`), then calls `tiktok_browser.post_video()`.
3. `tiktok_browser.post_video()` — opens real Chrome (not headless) via Playwright, navigates to TikTok Studio upload, sets file, fills caption, optionally calls `_attach_product()`, then clicks Post.

**Product attachment** (`_attach_product` in `tiktok_browser.py`): 4-strategy waterfall:
1. Find product by name/ID in visible showcase list
2. Type name in showcase search box
3. Switch to "Product link" tab and paste URL (fetched from TikTok Shop affiliate search if not already known)
4. Fallback: select first row in showcase

**Shop product extraction** (`_extract_shop_product` in `profile_service.py`): checks API `anchors` field first; falls back to regex `\[([^\[\]\n]{4,100})\]` on the video description (affiliate videos on accounts like @_xiangg__ embed product name in brackets).

**Key files:**
- `backend/services/tiktok_browser.py` — all Playwright automation: `login()`, `post_video()`, `_attach_product()` and helpers
- `backend/services/profile_service.py` — video list fetching, download, cookie conversion (Playwright JSON → Netscape for yt-dlp)
- `backend/services/scheduler_service.py` — in-memory job queue (not persisted across restarts)
- `backend/services/product_service.py` — TikTok Shop showcase product list (fetched via dummy upload flow; cached in-process)

**Cookie handling**: Login via `/auth/connect` opens Chrome for manual login. Cookies saved as Playwright JSON. `profile_service._get_netscape_cookies_file()` converts them on-the-fly for yt-dlp.

**Video download** (`download_video`): yt-dlp primary; falls back to tikwm.com API; converts non-H.264 video to H.264 with ffmpeg so TikTok Studio accepts it.

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/auth/connect` | Trigger browser login (uses `.env` credentials) |
| GET | `/auth/status` | Check if cookies exist |
| POST | `/profile/videos` | Fetch video list from a TikTok profile URL |
| POST | `/schedule/create` | Enqueue one or more videos to post |
| GET | `/schedule/list` | Poll status of all queued posts |
| DELETE | `/schedule/{id}` | Cancel a pending post |
| POST | `/publish/tiktok` | Immediate post (no queue) |
| GET | `/product/showcase/list` | Return cached showcase products |
| POST | `/product/showcase/refresh` | Re-fetch showcase from TikTok Studio |

## `shop_product` dict shape

Passed through the entire pipeline: profile → schedule → post_video → _attach_product.

```python
{
    "id": "",        # TikTok product ID (may be empty if only name is known)
    "name": "...",   # Product name (required for attachment)
    "price": "",
    "image": "",
    "url": "",       # Direct product URL if available
}
```

## Known constraints

- `tiktok_browser.py` requires real Chrome installed (`channel="chrome"`) — Chromium bundled with Playwright lacks video codecs TikTok Studio needs.
- `scheduler_service` is in-memory only; all queued jobs are lost on server restart.
- `product_service.fetch_products()` requires at least one downloaded MP4 ≥ 500 KB in `temp/` (used as dummy upload to open the showcase dialog).
- TikTok Studio UI changes break selector-based automation. Screenshots are saved to `temp/pre_post_click.png` on each post attempt for debugging.
