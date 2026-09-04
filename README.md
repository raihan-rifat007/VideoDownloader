# ReClip

A self-hosted, open-source video and audio downloader with a clean web UI. Paste links from YouTube, TikTok, Instagram, Twitter/X, and 1000+ other sites — download video as MP4, MKV, or MOV, or extract MP3 audio.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

https://github.com/user-attachments/assets/419d3e50-c933-444b-8cab-a9724986ba05

![ReClip MP3 Mode](assets/preview-mp3.png)

## Features

- Download videos from 1000+ supported sites (via [yt-dlp](https://github.com/yt-dlp/yt-dlp))
- MP4, MKV, or MOV video export
- MP3 audio extraction
- Quality/resolution picker
- Bulk downloads — paste multiple URLs at once
- Automatic URL deduplication
- Clean, responsive UI — no frameworks, no build step
- Single Python file backend (~150 lines)

## Quick Start

```bash
brew install yt-dlp ffmpeg    # or apt install ffmpeg && pip install yt-dlp
git clone https://github.com/averygan/reclip.git
cd reclip
./reclip.sh
```

Open **http://localhost:8899**.

Or with Docker:

```bash
docker build -t reclip . && docker run -p 8899:8899 reclip
```

The same Dockerfile works with Docker Desktop on Windows and with Docker on
Linux/macOS. Shell scripts are stored with LF endings, and the image build also
normalizes the entrypoint as a fallback for existing Windows checkouts.

### Bilibili HTTP 412

Bilibili may block a server IP or a request without a matching browser session.
ReClip uses yt-dlp 2026.08.19 or newer and supports these optional settings on
every metadata, playlist, and download request:

- `YTDLP_COOKIES_FILE`: path inside the container to a Netscape `cookies.txt`
- `YTDLP_PROXY`: HTTP, HTTPS, or SOCKS proxy URL used as the outbound connection
- `YTDLP_USER_AGENT`: user agent matching the browser that exported the cookies

Mount cookies read-only and never commit them:

```bash
docker run -p 8899:8899 \
  -v /absolute/path/cookies.txt:/run/secrets/reclip-cookies:ro \
  -e YTDLP_COOKIES_FILE=/run/secrets/reclip-cookies \
  -e YTDLP_USER_AGENT='Mozilla/5.0 ...' \
  reclip
```

With Docker Compose, create a local `compose.override.yml` (the environment
variables themselves are already passed through by `docker-compose.yml`):

```yaml
services:
  reclip:
    volumes:
      - /absolute/path/cookies.txt:/run/secrets/reclip-cookies:ro
```

Then set `YTDLP_COOKIES_FILE=/run/secrets/reclip-cookies` in the shell or `.env`
before starting Compose. On Docker Desktop for Windows, use an absolute path such
as `C:/Users/you/cookies.txt` on the left side of the mount.

If the deployment IP itself is blocked, also set `YTDLP_PROXY`. Export cookies
from a fresh Bilibili browser session using the same proxy/IP whenever possible.
After upgrading, rebuild the image so the fixed yt-dlp version is baked in:

```bash
docker compose up -d --build --force-recreate
```

## Usage

1. Paste one or more video URLs into the input box
2. Choose **MP4**, **MKV**, or **MOV** for video, or **MP3** for audio
3. Click **Fetch** to load video info and thumbnails
4. Select quality/resolution if available
5. Click **Download** on individual videos, or **Download All**

Container conversion is handled by yt-dlp and FFmpeg. MOV export can take
longer when the source codecs need to be re-encoded for compatibility.

## Supported Sites

Anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including:

YouTube, TikTok, Instagram, Twitter/X, Reddit, Facebook, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Pinterest, Tumblr, Threads, LinkedIn, and many more.

## Stack

- **Backend:** Python + Flask
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Download engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **Dependencies:** 2 (Flask, yt-dlp)

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
