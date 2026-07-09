# ReClip

A self-hosted, open-source video and audio downloader with a clean web UI. Paste links from YouTube, TikTok, Instagram, Twitter/X, and 1000+ other sites — download as MP4 or MP3.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

https://github.com/user-attachments/assets/419d3e50-c933-444b-8cab-a9724986ba05

![ReClip MP3 Mode](assets/preview-mp3.png)

## Features

- Download videos from 1000+ supported sites (via [yt-dlp](https://github.com/yt-dlp/yt-dlp))
- MP4 video or MP3 audio extraction
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

## Usage

1. Paste one or more video URLs into the input box
2. Choose **MP4** (video) or **MP3** (audio)
3. Click **Fetch** to load video info and thumbnails
4. Select quality/resolution if available
5. Click **Download** on individual videos, or **Download All**

## Supported Sites

Anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including:

YouTube, TikTok, Instagram, Twitter/X, Reddit, Facebook, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Pinterest, Tumblr, Threads, LinkedIn, and many more.

## Stack

- **Backend:** Python + Flask (~150 lines)
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Download engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **Dependencies:** 2 (Flask, yt-dlp)

## Security

reclip has no login system by default — anyone who can reach the server can
trigger downloads. That's fine for `127.0.0.1`-only local use, but if you
expose it on your network or the internet, turn on these optional controls:

| Env var | Default | Purpose |
|---|---|---|
| `RECLIP_API_KEY` | unset (disabled) | If set, all `/api/*` requests must send a matching `X-API-Key: <key>` header. |
| `RECLIP_RATE_LIMIT` | `20` | Max requests per client IP per window on `/api/info` and `/api/playlist` (download uses a stricter limit). |
| `RECLIP_RATE_WINDOW` | `60` | Rate-limit window, in seconds. |
| `RECLIP_TRUST_PROXY` | unset (disabled) | Set to `1` only if reclip sits behind a proxy *you control* that sets `X-Forwarded-For`, so rate limiting keys on the real client IP instead of the proxy's. |

Additional built-in protections (always on, no config needed):

- **CSRF / cross-site request blocking** — state-changing `/api/*` requests are rejected unless their `Origin`/`Referer` matches the server's own host, preventing a third-party web page from silently triggering downloads through a user's browser.
- **Rate limiting** — an in-memory, per-IP sliding-window limiter throttles the `yt-dlp`-invoking endpoints (`/api/info`, `/api/playlist`, `/api/download`) to reduce abuse and resource exhaustion.

Example:

```bash
RECLIP_API_KEY=$(openssl rand -hex 32) ./reclip.sh
```

Then include `X-API-Key: <key>` on every request (the bundled web UI does not
currently prompt for one, so this mode is intended for API/script usage or a
reverse proxy that injects the header).

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
