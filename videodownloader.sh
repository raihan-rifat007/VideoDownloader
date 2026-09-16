#!/bin/bash
set -e
cd "$(dirname "$0")"

missing=""

if ! command -v python3 &> /dev/null; then
    missing="$missing python3"
fi

if ! command -v yt-dlp &> /dev/null; then
    missing="$missing yt-dlp"
fi

if ! command -v ffmpeg &> /dev/null; then
    missing="$missing ffmpeg"
fi

if [ -n "$missing" ]; then
    echo "Missing required tools:$missing"
    echo ""
    if command -v brew &> /dev/null; then
        echo "Install with:  brew install$missing"
    elif command -v apt &> /dev/null; then
        echo "Install with:  sudo apt install$missing"
    else
        echo "Please install:$missing"
    fi
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -q flask yt-dlp
else
    source venv/bin/activate
fi

if [ -z "$VIDEODOWNLOADER_NO_UPDATE" ]; then
    echo "Updating yt-dlp..."
    pip install -q -U yt-dlp || echo "  (couldn't update yt-dlp — continuing with the installed version)"
fi

PORT="${PORT:-8899}"
export PORT

echo ""
echo "  Video Downloader is running at http://localhost:$PORT"
echo ""
python3 app.py
