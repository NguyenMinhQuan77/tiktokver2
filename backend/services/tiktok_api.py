"""
TikTok Content Posting API wrapper.
Handles video upload (chunked) and publish.
Docs: https://developers.tiktok.com/doc/content-posting-api-get-started
"""
import os
import math
import asyncio
from typing import Optional

import httpx

from backend.config import settings

# Chunk size: 10 MB
CHUNK_SIZE = 10 * 1024 * 1024

# TikTok API base
TIKTOK_API_BASE = "https://open.tiktokapis.com"


class TikTokAPIError(Exception):
    pass


class TikTokAPI:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    async def _post(self, url: str, json_body: dict, client: httpx.AsyncClient) -> dict:
        resp = await client.post(url, json=json_body, headers=self.headers, timeout=60)
        data = resp.json()
        error = data.get("error", {})
        if error and error.get("code") not in (None, "ok", ""):
            raise TikTokAPIError(
                f"TikTok API lỗi [{error.get('code')}]: {error.get('message', 'Unknown error')}"
            )
        return data

    async def init_video_upload(
        self,
        video_size: int,
        caption: str,
        client: httpx.AsyncClient,
        is_tiktok_shop: bool = False,
        product_url: str = "",
    ) -> dict:
        """
        Initialize video upload (PULL_FROM_URL or FILE_UPLOAD).
        We use FILE_UPLOAD (chunked).
        """
        chunk_size = CHUNK_SIZE
        total_chunks = math.ceil(video_size / chunk_size)

        body = {
            "post_info": {
                "title": caption[:2200],  # TikTok caption limit
                "privacy_level": "SELF_ONLY",  # Start as private, user can change
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        }

        resp = await self._post(
            f"{TIKTOK_API_BASE}/v2/post/publish/video/init/",
            body,
            client,
        )
        return resp.get("data", {})

    async def upload_chunk(
        self,
        upload_url: str,
        chunk_data: bytes,
        chunk_index: int,
        total_chunks: int,
        video_size: int,
        client: httpx.AsyncClient,
    ) -> bool:
        """Upload a single chunk."""
        start = chunk_index * CHUNK_SIZE
        end = min(start + len(chunk_data) - 1, video_size - 1)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{video_size}",
            "Content-Type": "video/mp4",
            "Content-Length": str(len(chunk_data)),
        }

        resp = await client.put(
            upload_url,
            content=chunk_data,
            headers=headers,
            timeout=120,
        )
        return resp.status_code in (200, 201, 206)

    async def check_publish_status(self, publish_id: str, client: httpx.AsyncClient) -> dict:
        """Poll publish status."""
        resp = await self._post(
            f"{TIKTOK_API_BASE}/v2/post/publish/status/fetch/",
            {"publish_id": publish_id},
            client,
        )
        return resp.get("data", {})

    async def publish_video(
        self,
        video_path: str,
        caption: str,
        product_url: str = "",
    ) -> dict:
        """
        Full upload + publish flow:
        1. Init upload
        2. Upload chunks
        3. Poll status
        Returns dict with publish_id.
        """
        video_size = os.path.getsize(video_path)
        is_tiktok_shop = "tiktok" in product_url.lower() and "shop" in product_url.lower()

        async with httpx.AsyncClient(timeout=120) as client:
            # 1. Init
            init_data = await self.init_video_upload(
                video_size=video_size,
                caption=caption,
                client=client,
                is_tiktok_shop=is_tiktok_shop,
                product_url=product_url,
            )

            publish_id = init_data.get("publish_id")
            upload_url = init_data.get("upload_url")

            if not publish_id or not upload_url:
                raise TikTokAPIError("Không nhận được publish_id hoặc upload_url từ TikTok")

            # 2. Upload chunks
            total_chunks = math.ceil(video_size / CHUNK_SIZE)
            with open(video_path, "rb") as f:
                for i in range(total_chunks):
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    ok = await self.upload_chunk(
                        upload_url=upload_url,
                        chunk_data=chunk,
                        chunk_index=i,
                        total_chunks=total_chunks,
                        video_size=video_size,
                        client=client,
                    )
                    if not ok:
                        raise TikTokAPIError(f"Upload chunk {i + 1}/{total_chunks} thất bại")

            # 3. Poll status (max 30 polls × 5s = 2.5 minutes)
            for attempt in range(30):
                await asyncio.sleep(5)
                status_data = await self.check_publish_status(publish_id, client)
                status = status_data.get("status", "")
                if status == "PUBLISH_COMPLETE":
                    return {"publish_id": publish_id, "status": "published"}
                elif status in ("FAILED", "PUBLISH_FAILED"):
                    fail_reason = status_data.get("fail_reason", "unknown")
                    raise TikTokAPIError(f"Đăng video thất bại: {fail_reason}")
                # SEND_TO_USER_INBOX, PROCESSING_UPLOAD, etc. — keep polling

            # Timed out but may still be processing
            return {"publish_id": publish_id, "status": "processing"}
