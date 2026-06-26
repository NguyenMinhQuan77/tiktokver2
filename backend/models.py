from dataclasses import dataclass, field
from typing import List, Optional
from pydantic import BaseModel


# --- Dataclasses for internal use ---

@dataclass
class ProductInfo:
    name: str
    description: str
    price: str
    currency: str
    images: List[str]
    shop_type: str  # "shopee" | "lazada" | "tiktok_shop" | "unknown"
    original_url: str


@dataclass
class ContentResult:
    video_script: str
    caption: str
    hashtags: List[str]
    hook: str
    audio_text: str  # Clean text for TTS


# --- Pydantic models for API request/response ---

class ProductAnalyzeRequest(BaseModel):
    url: str


class ProductAnalyzeResponse(BaseModel):
    name: str
    description: str
    price: str
    currency: str
    images: List[str]
    shop_type: str
    original_url: str


class VideoGenerateRequest(BaseModel):
    product_url: str
    product_info: dict
    custom_notes: Optional[str] = ""


class VideoGenerateResponse(BaseModel):
    script: str
    caption: str
    hashtags: List[str]
    video_path: str
    audio_text: str
    hook: str


class PublishRequest(BaseModel):
    video_path: str
    caption: str
    product_url: str


class PublishResponse(BaseModel):
    success: bool
    publish_id: Optional[str] = None
    share_url: Optional[str] = None
    message: str


class AuthStatusResponse(BaseModel):
    logged_in: bool
    username: Optional[str] = None
    avatar: Optional[str] = None
    open_id: Optional[str] = None


# --- New workflow models ---

class ProfileVideosRequest(BaseModel):
    url: str


class VideoInfo(BaseModel):
    id: str
    url: str
    title: str
    caption: str
    thumbnail: str
    duration: int
    product_url: Optional[str] = ""


class ScheduleItem(BaseModel):
    id: str
    video_info: VideoInfo
    delay_minutes: int
    product_url: str
    status: str  # "pending" | "downloading" | "posting" | "done" | "failed" | "cancelled"
    created_at: str
    post_at: str
    error: Optional[str] = None


class ScheduleCreateRequest(BaseModel):
    videos: list
    product_url: str
    profile_url: str


# In-memory session store
# session_id -> {access_token, open_id, username, avatar, expires_at}
sessions: dict = {}

# In-memory schedule store
schedule_store: list = []
