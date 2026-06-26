#!/bin/bash
# Run from project root so "backend.*" imports resolve correctly
set -e
cd "$(dirname "$0")"
echo "Installing dependencies..."
pip install -r requirements.txt -q
echo "Installing Playwright Chromium..."
python -m playwright install chromium 2>/dev/null || true
mkdir -p temp
echo "Starting TikTok Affiliate Tool on http://localhost:8000"
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
