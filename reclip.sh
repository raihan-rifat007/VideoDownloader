#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Check prerequisites
missing=""

if ! command -v python3 &> /dev/null; then
    missing="$missing python3"
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

# Set up venv and install Python deps.
# A venv copied/moved from another machine has a broken `venv/bin/pip` shebang
# (it points at an interpreter that no longer exists) and a stale vendored
# certifi, which makes `pip install` fail. Detect that and rebuild fresh.
if [ -d "venv" ]; then
    pip_interp=$(sed -n '1s/^#!//p' venv/bin/pip 2>/dev/null)
    if [ -n "$pip_interp" ] && [ ! -e "$pip_interp" ]; then
        echo "Existing venv is broken (copied from another machine). Recreating it..."
        rm -rf venv
    fi
fi

if [ ! -d "venv" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv venv
    venv/bin/pip install -q --upgrade pip
    venv/bin/pip install -q flask yt-dlp curl-cffi
else
    # Make sure required deps are present (idempotent, fast when already there).
    venv/bin/python -m pip install -q curl-cffi 2>/dev/null || venv/bin/pip install -q curl-cffi
fi

# Keep yt-dlp fresh — sites (Instagram, Facebook, etc.) break its extractors
# frequently, and the usual fix is simply updating yt-dlp. Skip with RECLIP_NO_UPDATE=1.
if [ -z "$RECLIP_NO_UPDATE" ]; then
    echo "Updating yt-dlp..."
    venv/bin/pip install -q -U yt-dlp || echo "  (couldn't update yt-dlp — continuing with the installed version)"
fi

PORT="${PORT:-8899}"
export PORT

echo ""
echo "  ReClip is running at http://localhost:$PORT"
echo ""

if [ "${RECLIP_OPEN_BROWSER:-1}" = "1" ] && command -v xdg-open &> /dev/null; then
    (
        sleep 1
        xdg-open "http://localhost:$PORT" >/dev/null 2>&1 || true
    ) &
fi

exec venv/bin/python app.py
