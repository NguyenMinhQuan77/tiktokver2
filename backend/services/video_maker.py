"""
Video creation service:
  1. Download product images
  2. Resize to 720x1280 (9:16) and add text overlay using PIL
  3. Generate TTS audio with edge-tts
  4. Combine into MP4 using ffmpeg subprocess
"""
import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List

import httpx
from PIL import Image, ImageDraw, ImageFont

from backend.config import settings
from backend.models import ProductInfo

# TikTok vertical format
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280

FONT_SIZE_TITLE = 36
FONT_SIZE_PRICE = 28

# Vietnamese TTS voice
TTS_VOICE = "vi-VN-HoaiMyNeural"


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def check_edge_tts() -> bool:
    return shutil.which("edge-tts") is not None


async def download_image(url: str, dest_path: str, client: httpx.AsyncClient) -> bool:
    """Download an image to dest_path. Returns True on success."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://shopee.vn/",
        }
        resp = await client.get(url, headers=headers, timeout=20, follow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 500:
            with open(dest_path, "wb") as f:
                f.write(resp.content)
            return True
    except Exception:
        pass
    return False


def make_frame(
    image_path: str,
    product_name: str,
    price: str = "",
    output_path: str = "",
) -> str:
    """
    Open image, resize to 720x1280 (cover/crop), add text overlay.
    Returns output_path.
    """
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        # Create a gradient placeholder
        img = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), color=(30, 30, 30))

    # Resize to cover 720x1280
    img_ratio = img.width / img.height
    target_ratio = VIDEO_WIDTH / VIDEO_HEIGHT

    if img_ratio > target_ratio:
        # Image is wider — scale by height
        new_h = VIDEO_HEIGHT
        new_w = int(img_ratio * new_h)
    else:
        # Image is taller — scale by width
        new_w = VIDEO_WIDTH
        new_h = int(new_w / img_ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Center crop
    left = (new_w - VIDEO_WIDTH) // 2
    top = (new_h - VIDEO_HEIGHT) // 2
    img = img.crop((left, top, left + VIDEO_WIDTH, top + VIDEO_HEIGHT))

    draw = ImageDraw.Draw(img)

    # Semi-transparent bottom bar (simulate with a dark rectangle)
    bar_height = 200
    bar_top = VIDEO_HEIGHT - bar_height
    overlay = Image.new("RGBA", (VIDEO_WIDTH, bar_height), (0, 0, 0, 180))
    img_rgba = img.convert("RGBA")
    img_rgba.paste(overlay, (0, bar_top), overlay)
    img = img_rgba.convert("RGB")

    draw = ImageDraw.Draw(img)

    # Try to load a system font; fall back to default
    font_title = None
    font_price = None
    for font_path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ]:
        if os.path.exists(font_path):
            try:
                font_title = ImageFont.truetype(font_path, FONT_SIZE_TITLE)
                font_price = ImageFont.truetype(font_path, FONT_SIZE_PRICE)
                break
            except Exception:
                pass

    if font_title is None:
        font_title = ImageFont.load_default()
        font_price = ImageFont.load_default()

    # Wrap product name
    max_chars = 30
    words = product_name.split()
    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = f"{current} {word}".strip()
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    lines = lines[:3]  # max 3 lines

    y = bar_top + 15
    for line in lines:
        # Shadow
        draw.text((22, y + 2), line, font=font_title, fill=(0, 0, 0, 200))
        draw.text((20, y), line, font=font_title, fill=(255, 255, 255))
        y += FONT_SIZE_TITLE + 6

    # Price
    if price:
        price_text = f"Giá: {price}" if not price.lower().startswith("giá") else price
        draw.text((22, y + 2), price_text, font=font_price, fill=(0, 0, 0, 200))
        draw.text((20, y), price_text, font=font_price, fill=(254, 44, 85))  # TikTok red

    img.save(output_path, "JPEG", quality=92)
    return output_path


async def generate_tts(text: str, output_path: str) -> bool:
    """Generate TTS audio using edge-tts CLI."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "edge-tts",
            "--voice", TTS_VOICE,
            "--text", text,
            "--write-media", output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        return False


async def get_audio_duration(audio_path: str) -> float:
    """Get duration of audio file using ffprobe."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            audio_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        import json
        data = json.loads(stdout)
        for stream in data.get("streams", []):
            dur = float(stream.get("duration", 0))
            if dur > 0:
                return dur
    except Exception:
        pass
    return 30.0  # default 30 seconds


def create_placeholder_image(output_path: str, text: str = "Sản phẩm") -> str:
    """Create a simple placeholder image when no product images available."""
    img = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), color=(10, 10, 10))
    draw = ImageDraw.Draw(img)

    # TikTok gradient-like background
    for y in range(VIDEO_HEIGHT):
        r = int(1 + (y / VIDEO_HEIGHT) * 40)
        g = int(1 + (y / VIDEO_HEIGHT) * 10)
        b = int(20 + (y / VIDEO_HEIGHT) * 30)
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(r, g, b))

    # Center text
    try:
        font = ImageFont.load_default()
        draw.text((VIDEO_WIDTH // 2 - 50, VIDEO_HEIGHT // 2), text, fill=(255, 255, 255), font=font)
    except Exception:
        pass

    img.save(output_path, "JPEG")
    return output_path


async def create_video(product_info: ProductInfo, script: str) -> str:
    """
    Main video creation function.
    Downloads images, creates frames, generates TTS, combines with ffmpeg.
    Returns absolute path to the output MP4.
    """
    if not check_ffmpeg():
        raise RuntimeError(
            "ffmpeg không được cài đặt. Vui lòng cài ffmpeg: sudo apt install ffmpeg"
        )
    if not check_edge_tts():
        raise RuntimeError(
            "edge-tts không được cài đặt. Vui lòng cài: pip install edge-tts"
        )

    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    job_id = str(uuid.uuid4())[:8]
    work_dir = os.path.join(settings.TEMP_DIR, f"job_{job_id}")
    os.makedirs(work_dir, exist_ok=True)

    try:
        # 1. Download product images
        image_paths = []
        if product_info.images:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                for i, img_url in enumerate(product_info.images[:5]):
                    dest = os.path.join(work_dir, f"img_{i:02d}.jpg")
                    ok = await download_image(img_url, dest, client)
                    if ok:
                        image_paths.append(dest)

        # If no images downloaded, create placeholders
        if not image_paths:
            for i in range(3):
                dest = os.path.join(work_dir, f"placeholder_{i}.jpg")
                create_placeholder_image(dest, product_info.name)
                image_paths.append(dest)

        # 2. Generate TTS audio
        audio_path = os.path.join(work_dir, "voice.mp3")
        tts_ok = await generate_tts(script, audio_path)

        if not tts_ok:
            # Create silence using ffmpeg as fallback
            silence_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=mono",
                "-t", "30",
                "-q:a", "9",
                "-acodec", "libmp3lame",
                audio_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await silence_proc.wait()

        # 3. Get audio duration
        duration = await get_audio_duration(audio_path) if os.path.exists(audio_path) else 30.0
        duration = max(10.0, min(duration, 120.0))

        # 4. Create frame images with overlay
        frame_paths = []
        num_images = len(image_paths)
        for i, img_path in enumerate(image_paths):
            frame_out = os.path.join(work_dir, f"frame_{i:02d}.jpg")
            make_frame(
                image_path=img_path,
                product_name=product_info.name,
                price=product_info.price,
                output_path=frame_out,
            )
            frame_paths.append(frame_out)

        # 5. Build ffmpeg concat list
        # Each image shown for equal duration
        per_image_duration = duration / len(frame_paths)
        concat_file = os.path.join(work_dir, "concat.txt")
        with open(concat_file, "w") as f:
            for fp in frame_paths:
                f.write(f"file '{fp}'\n")
                f.write(f"duration {per_image_duration:.3f}\n")
            # ffmpeg concat demuxer needs the last file repeated without duration
            f.write(f"file '{frame_paths[-1]}'\n")

        # 6. Run ffmpeg to combine slideshow + audio into final MP4
        output_filename = f"video_{job_id}.mp4"
        output_path = os.path.join(settings.TEMP_DIR, output_filename)

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-i", audio_path,
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            output_path,
        ]

        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")[-500:]
            raise RuntimeError(f"ffmpeg thất bại: {err_msg}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            raise RuntimeError("ffmpeg tạo video thất bại hoặc file video rỗng")

        return output_path

    finally:
        # Clean up work directory (keep only the final output)
        try:
            import shutil as _shutil
            _shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
