import logging
import os
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import settings
from backend.routers import auth, profile, publish, schedule, product

# Ensure temp dir exists
os.makedirs(settings.TEMP_DIR, exist_ok=True)

app = FastAPI(
    title="TikTok Affiliate Tool",
    description="Repost video TikTok với link affiliate tự động",
    version="2.0.0",
)

# CORS — allow both frontend dev servers and same-origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount temp folder for video file serving
app.mount("/temp", StaticFiles(directory=settings.TEMP_DIR), name="temp")

# Include routers
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(publish.router)
app.include_router(schedule.router)
app.include_router(product.router)

# Frontend — resolve path relative to project root
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@app.get("/", include_in_schema=False)
async def serve_frontend():
    """Serve the single-page frontend."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "TikTok Affiliate Tool API is running. Frontend not found."}


@app.get("/health")
async def health():
    return {"status": "ok", "app": "TikTok Affiliate Tool v2"}
