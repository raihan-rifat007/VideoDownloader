<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                          VIDEO DOWNLOADER                          -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

<div align="center">

<img src="assets/banner.png" alt="Video Downloader Banner" width="100%" />

<br />
<br />

# Video Downloader

### Self-hosted, open-source media downloader for 1000+ sites

**Paste a link. Pick a format. Get your file. No ads. No tracking. No nonsense.**

<br />

<!-- ── Primary Badges ─────────────────────────────────────────────── -->

[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://github.com/yt-dlp/yt-dlp)
[![ffmpeg](https://img.shields.io/badge/ffmpeg-6.x-007808?style=for-the-badge&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)

<!-- ── Meta Badges ────────────────────────────────────────────────── -->

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](CONTRIBUTING.md)
[![Maintained](https://img.shields.io/badge/Maintained-yes-success?style=flat-square)](https://github.com/yourname/videodownloader/graphs/commit-activity)
[![Docker Pulls](https://img.shields.io/docker/pulls/yourname/videodownloader?style=flat-square&logo=docker)](https://hub.docker.com/r/yourname/videodownloader)
[![GitHub Stars](https://img.shields.io/github/stars/yourname/videodownloader?style=flat-square&logo=github)](https://github.com/yourname/videodownloader/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/yourname/videodownloader?style=flat-square&logo=github)](https://github.com/yourname/videodownloader/network)
[![GitHub Issues](https://img.shields.io/github/issues/yourname/videodownloader?style=flat-square&logo=github)](https://github.com/yourname/videodownloader/issues)
[![Last Commit](https://img.shields.io/github/last-commit/yourname/videodownloader?style=flat-square&logo=github)](https://github.com/yourname/videodownloader/commits/main)

<br />

<!-- ── Quick Links ────────────────────────────────────────────────── -->

[**Features**](#-features) &nbsp;·&nbsp;
[**Quick Start**](#-quick-start) &nbsp;·&nbsp;
[**API Reference**](#-api-reference) &nbsp;·&nbsp;
[**Supported Sites**](#-supported-sites) &nbsp;·&nbsp;
[**Deployment**](#-deployment) &nbsp;·&nbsp;
[**FAQ**](#-faq) &nbsp;·&nbsp;
[**Roadmap**](#-roadmap) &nbsp;·&nbsp;
[**Contributing**](#-contributing)

</div>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                            TABLE OF CONTENTS                       -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

<details open>
<summary><kbd>Table of Contents</kbd></summary>

<br />

- [Overview](#-overview)
- [Features](#-features)
- [Screenshots](#-screenshots)
- [Tech Stack](#-tech-stack)
- [Quick Start](#-quick-start)
  - [Prerequisites](#prerequisites)
  - [Method 1 — Docker](#method-1--docker-recommended)
  - [Method 2 — Docker Compose](#method-2--docker-compose)
  - [Method 3 — Local Script](#method-3--local-script)
  - [Method 4 — Manual Install](#method-4--manual-install)
- [Configuration](#-configuration)
- [API Reference](#-api-reference)
- [Supported Sites](#-supported-sites)
- [Project Structure](#-project-structure)
- [Development](#-development)
- [Deployment](#-deployment)
  - [Docker Production](#docker-production)
  - [Reverse Proxy (Nginx)](#reverse-proxy-nginx)
  - [Cloud Platforms](#cloud-platforms)
- [Troubleshooting](#-troubleshooting)
- [FAQ](#-faq)
- [Roadmap](#-roadmap)
- [Contributing](#-contributing)
- [Star History](#-star-history)
- [Acknowledgments](#-acknowledgments)
- [Disclaimer](#-disclaimer)
- [License](#-license)

</details>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                              OVERVIEW                              -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Overview

**Video Downloader** is a self-hosted, zero-dependency web application for downloading videos and extracting audio from **1000+ supported platforms**. Built on top of the battle-tested [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) engine, it provides a clean brutalist web interface for fetching, previewing, and saving media — without ads, telemetry, or account registration.

Whether you're archiving your own content, downloading public-domain media, or just want a private downloader for your home lab, Video Downloader gives you a fast, lightweight, and fully controllable solution.

> **Philosophy:** Self-hosted first. Minimal footprint. No bloat. Just a Python file, a shell script, and a beautiful UI.

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                              FEATURES                              -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Features

<table>
<tr>
<td width="50%" valign="top">

### Core

- **1000+ supported sites** via yt-dlp
- **MP4 video** or **MP3 audio** extraction
- **Quality picker** — 360p, 480p, 720p, 1080p, 1440p, 4K, highest
- **Bulk downloads** — paste multiple URLs at once
- **Automatic URL deduplication**
- **Playlist expansion** — auto-resolves YouTube playlists
- **Live progress polling** — per-job status updates
- **Friendly error mapping** — human-readable messages

</td>
<td width="50%" valign="top">

### Technical

- **Single-file backend** (~150 lines of Python)
- **No frontend frameworks** — vanilla HTML/CSS/JS
- **No build step** — clone and run
- **Zero database** — in-memory job registry
- **Threaded downloads** — non-blocking Flask workers
- **Auto yt-dlp updates** on container start
- **Docker-ready** — multi-stage build
- **Cross-platform** — Linux, macOS, Windows (WSL)

</td>
</tr>
<tr>
<td width="50%" valign="top">

### Interface

- **Brutalist UI** — bold borders, hard shadows
- **Responsive layout** — mobile-first design
- **Skeleton loaders** — smooth perceived performance
- **Thumbnail previews** — with fallback placeholders
- **Inline quality chips** — tap to switch resolution
- **Download-all bar** — batch save all ready items
- **SVG icons only** — no emoji, no icon fonts
- **Accessible** — keyboard nav, reduced-motion support

</td>
<td width="50%" valign="top">

### Privacy & Security

- **No ads, no tracking, no analytics**
- **Runs on your hardware** — your files, your rules
- **No account required**
- **No external API calls** — direct yt-dlp subprocess
- **Content sanitization** — safe filename generation
- **Timeout guards** — 60s info, 300s download
- **Configurable host binding** — localhost by default
- **Environment-variable driven** — 12-factor friendly

</td>
</tr>
</table>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                            SCREENSHOTS                             -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Screenshots

<div align="center">

### Main Interface

<img src="assets/screenshot-main.png" alt="Main Interface" width="80%" />

<br />
<br />

### MP4 Download with Quality Picker

<img src="assets/screenshot-mp4.png" alt="MP4 Download" width="80%" />

<br />
<br />

### MP3 Audio Mode

<img src="assets/screenshot-mp3.png" alt="MP3 Mode" width="80%" />

<br />
<br />

### Bulk Download

<img src="assets/screenshot-bulk.png" alt="Bulk Download" width="80%" />

</div>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                             TECH STACK                             -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Tech Stack

<div align="center">

| Layer | Technology | Purpose |
|:------|:-----------|:--------|
| **Backend** | ![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white) | Core language |
| **Web Framework** | ![Flask](https://img.shields.io/badge/Flask-000000?logo=flask&logoColor=white) | HTTP routing & templating |
| **Download Engine** | ![yt-dlp](https://img.shields.io/badge/yt--dlp-FF0000?logo=youtube&logoColor=white) | Site extractors & downloader |
| **Media Muxing** | ![ffmpeg](https://img.shields.io/badge/ffmpeg-007808?logo=ffmpeg&logoColor=white) | Audio/video merge & transcode |
| **Frontend** | ![HTML5](https://img.shields.io/badge/HTML5-E34F26?logo=html5&logoColor=white) ![CSS3](https://img.shields.io/badge/CSS3-1572B6?logo=css3&logoColor=white) ![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?logo=javascript&logoColor=black) | Vanilla stack, no build |
| **Container** | ![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white) | Packaging & deployment |
| **Typography** | Archivo Black + DM Mono | Brutalist UI fonts |

</div>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                            QUICK START                             -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Quick Start

### Prerequisites

Before installing, ensure you have the following tools available:

| Tool | Version | Required | Install |
|:-----|:--------|:--------:|:--------|
| **Python** | 3.8+ | ✅ Yes | [python.org](https://python.org) |
| **yt-dlp** | latest | ✅ Yes | `pip install yt-dlp` |
| **ffmpeg** | 4.0+ | ✅ Yes | [ffmpeg.org](https://ffmpeg.org) |
| **Docker** | 20+ | Optional | [docker.com](https://docker.com) |

**Install prerequisites on macOS:**
```bash
brew install python3 yt-dlp ffmpeg
```

**Install prerequisites on Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv ffmpeg
pip install yt-dlp
```

**Install prerequisites on Windows (WSL2):**
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv ffmpeg
pip install yt-dlp
```

---

### Method 1 — Docker (Recommended)

The fastest way to get running. Single command, no dependencies on the host.

```bash
docker run -d \
  --name videodownloader \
  -p 8899:8899 \
  -v videodownloader-downloads:/app/downloads \
  --restart unless-stopped \
  ghcr.io/yourname/videodownloader:latest
```

Open **http://localhost:8899**

---

### Method 2 — Docker Compose

For a declarative, reproducible setup. Ideal for home labs and NAS deployments.

**`docker-compose.yml`:**

```yaml
services:
  videodownloader:
    build: .
    image: videodownloader:latest
    container_name: videodownloader
    ports:
      - "8899:8899"
    volumes:
      - videodownloader-downloads:/app/downloads
    restart: unless-stopped

volumes:
  videodownloader-downloads:
```

**Run it:**

```bash
docker compose up -d
docker compose logs -f
```

**Stop it:**

```bash
docker compose down
```

---

### Method 3 — Local Script

Best for development or single-user desktop use.

```bash
git clone https://github.com/yourname/videodownloader.git
cd videodownloader
chmod +x videodownloader.sh
./videodownloader.sh
```

The script will:

1. Verify `python3`, `yt-dlp`, and `ffmpeg` are installed
2. Create a virtual environment (`.venv` or `venv`)
3. Install Python dependencies from `requirements.txt`
4. Optionally update `yt-dlp` to the latest version
5. Start the Flask server on port `8899`

Open **http://localhost:8899**

To skip the yt-dlp auto-update:

```bash
VIDEODOWNLOADER_NO_UPDATE=1 ./videodownloader.sh
```

---

### Method 4 — Manual Install

Full control over every step.

```bash
# Clone the repository
git clone https://github.com/yourname/videodownloader.git
cd videodownloader

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate          # Linux/macOS
# venv\Scripts\activate.bat       # Windows

# Install dependencies
pip install -r requirements.txt

# Run the server
python app.py
```

The server will start on **http://127.0.0.1:8899** by default.

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                          CONFIGURATION                             -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Configuration

Video Downloader is configured entirely through **environment variables**. No config files, no database, no surprises.

| Variable | Default | Description |
|:---------|:--------|:------------|
| `PORT` | `8899` | HTTP port the Flask server binds to |
| `HOST` | `127.0.0.1` | Interface to bind — use `0.0.0.0` for LAN access |
| `VIDEODOWNLOADER_NO_UPDATE` | *(unset)* | Set to `1` to skip yt-dlp auto-update on startup |
| `RECLIP_NO_UPDATE` | *(unset)* | Legacy alias for the above |

**Example — bind to all interfaces:**

```bash
HOST=0.0.0.0 PORT=8080 python app.py
```

**Example with Docker:**

```bash
docker run -d \
  -e PORT=8080 \
  -e HOST=0.0.0.0 \
  -e VIDEODOWNLOADER_NO_UPDATE=1 \
  -p 8080:8080 \
  videodownloader
```

> ⚠️ **Security note:** Binding to `0.0.0.0` exposes the service to your network. There is no authentication layer. Only do this on trusted networks, or place the app behind a reverse proxy with HTTP basic auth.

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                            API REFERENCE                           -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## API Reference

All endpoints accept and return JSON unless noted otherwise. Base URL is `http://localhost:8899`.

<details>
<summary><kbd>POST /api/info</kbd> — Fetch metadata for a video</summary>

**Request body:**

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}
```

**Response `200 OK`:**

```json
{
  "title": "Video Title",
  "thumbnail": "https://.../thumbnail.jpg",
  "duration": 212,
  "uploader": "Channel Name",
  "formats": [
    { "id": "137", "label": "1080p", "height": 1080 },
    { "id": "136", "label": "720p",  "height": 720  },
    { "id": "135", "label": "480p",  "height": 480  }
  ]
}
```

**Response `400 Bad Request`:**

```json
{ "error": "Unsupported URL: ..." }
```

</details>

<details>
<summary><kbd>POST /api/playlist</kbd> — Expand a playlist into individual URLs</summary>

**Request body:**

```json
{
  "url": "https://www.youtube.com/playlist?list=PLxxxxxxxx"
}
```

**Response `200 OK`:**

```json
{
  "urls": [
    "https://www.youtube.com/watch?v=aaa",
    "https://www.youtube.com/watch?v=bbb",
    "https://www.youtube.com/watch?v=ccc"
  ]
}
```

</details>

<details>
<summary><kbd>POST /api/download</kbd> — Start a download job</summary>

**Request body:**

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "format": "video",
  "format_id": "137",
  "title": "Video Title"
}
```

| Field | Type | Required | Description |
|:------|:-----|:--------:|:------------|
| `url` | string | ✅ | Source video URL |
| `format` | string | ✅ | `"video"` or `"audio"` |
| `format_id` | string | ❌ | yt-dlp format ID (from `/api/info`) |
| `title` | string | ❌ | Used for the final filename |

**Response `200 OK`:**

```json
{ "job_id": "a1b2c3d4e5" }
```

</details>

<details>
<summary><kbd>GET /api/status/&lt;job_id&gt;</kbd> — Poll job state</summary>

**Response `200 OK`:**

```json
{
  "status": "done",
  "error": null,
  "filename": "Video Title.mp4"
}
```

| `status` value | Meaning |
|:---------------|:--------|
| `downloading` | In progress |
| `done` | Complete, ready to save |
| `error` | Failed — see `error` field |

</details>

<details>
<summary><kbd>GET /api/file/&lt;job_id&gt;</kbd> — Download the finished file</summary>

Streams the completed file as an attachment. Returns `404` if the job is not in `done` state.

**Response headers:**

```
Content-Disposition: attachment; filename="Video Title.mp4"
Content-Type: application/octet-stream
```

</details>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                         SUPPORTED SITES                            -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Supported Sites

Video Downloader inherits the full extractor catalog from yt-dlp — **over 1000 sites** and counting. A selection of the most popular:

<table>
<tr>
<td valign="top" width="33%">

**Video Platforms**
- YouTube (incl. Shorts, Music)
- Vimeo
- Dailymotion
- Twitch (VODs & clips)
- Rumble
- Odysee
- PeerTube

</td>
<td valign="top" width="33%">

**Social Media**
- TikTok
- Instagram (Reels, Posts)
- Twitter / X
- Facebook
- Reddit
- Threads
- LinkedIn
- Tumblr

</td>
<td valign="top" width="33%">

**Audio & Other**
- SoundCloud
- Bandcamp
- Mixcloud
- Loom
- Streamable
- Pinterest
- Archive.org

</td>
</tr>
</table>

> 📖 **Full list:** See the official [yt-dlp supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) documentation.

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                        PROJECT STRUCTURE                           -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Project Structure

```
videodownloader/
├── app.py                    # Flask backend — single file, ~150 lines
├── requirements.txt          # Python dependencies (2 packages)
├── videodownloader.sh        # Local launcher script
├── docker-entrypoint.sh      # Container init — updates yt-dlp
├── docker-compose.yml        # Declarative container orchestration
├── Dockerfile                # Multi-stage production image
├── README.md                 # This file
├── LICENSE                   # MIT license
├── templates/
│   └── index.html            # Brutalist UI — single file
├── static/
│   └── favicon.svg           # App icon
├── assets/
│   ├── banner.png            # README banner
│   ├── preview-mp3.png       # Screenshot
│   └── screenshot-*.png      # More screenshots
└── downloads/                # Runtime download cache (gitignored)
```

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                           DEVELOPMENT                              -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Development

### Local dev environment

```bash
git clone https://github.com/yourname/videodownloader.git
cd videodownloader

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

FLASK_ENV=development FLASK_DEBUG=1 python app.py
```

Flask will auto-reload on file changes.

### Adding a new endpoint

1. Add a route handler in `app.py`
2. Wire up the frontend in `templates/index.html`
3. Test with `curl`
4. Update this README's [API Reference](#-api-reference)

### Code style

- **Python:** PEP 8, 4-space indents, type hints encouraged
- **JavaScript:** ES2020+, `const`/`let`, no semicolon-less style
- **CSS:** Custom properties for all colors/spacing, BEM-ish class names
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/)

### Running tests

```bash
# Not yet implemented — PRs welcome
pytest tests/
```

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                           DEPLOYMENT                               -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Deployment

### Docker Production

Build and run a production image:

```bash
docker build -t videodownloader:latest .
docker run -d \
  --name videodownloader \
  --restart unless-stopped \
  -p 8899:8899 \
  -v /srv/videodownloader/downloads:/app/downloads \
  -e HOST=0.0.0.0 \
  videodownloader:latest
```

For proper process management, run Flask behind **gunicorn**:

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8899 app:app
```

### Reverse Proxy (Nginx)

**`/etc/nginx/sites-available/videodownloader`:**

```nginx
server {
    listen 80;
    server_name downloader.example.com;

    client_max_body_size 0;

    location / {
        proxy_pass         http://127.0.0.1:8899;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/videodownloader /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### Cloud Platforms

| Platform | Notes |
|:---------|:------|
| **Fly.io** | Deploy with `fly launch` — attach a volume for `/app/downloads` |
| **Railway** | One-click deploy from GitHub — set `HOST=0.0.0.0` |
| **Render** | Use the Docker environment, expose port `8899` |
| **Heroku** | Requires a `Procfile` with `web: python app.py` |
| **Home NAS** | Use Docker Compose on Synology/QNAP/Unraid |

> ⚠️ **Cloud note:** Many free tiers block `yt-dlp` traffic. Prefer residential IPs or self-hosted.

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                        TROUBLESHOOTING                             -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Troubleshooting

<details>
<summary><b>Download fails with "Unsupported URL"</b></summary>

<br />

The extractor for that site is out of date. Update yt-dlp:

```bash
pip install -U yt-dlp
```

If you're running the Docker image, restart the container — it auto-updates on boot.

</details>

<details>
<summary><b>Downloads succeed but no file appears</b></summary>

<br />

Check that `ffmpeg` is installed and on your `PATH`:

```bash
ffmpeg -version
```

Without ffmpeg, yt-dlp cannot merge separate video and audio streams.

</details>

<details>
<summary><b>"HTTP Error 403: Forbidden" on YouTube</b></summary>

<br />

YouTube occasionally changes its anti-bot measures. Update yt-dlp to the latest version:

```bash
pip install -U yt-dlp
```

If the problem persists, try passing cookies via yt-dlp's `--cookies-from-browser` flag (requires a small code change).

</details>

<details>
<summary><b>Container exits immediately</b></summary>

<br />

Check the logs:

```bash
docker logs videodownloader
```

Common causes: port `8899` already in use, or the `downloads` volume is not writable.

</details>

<details>
<summary><b>Downloads are slow</b></summary>

<br />

yt-dlp downloads at the speed of your connection. You can speed things up by:

1. Using a lower quality (e.g. 720p instead of 1080p)
2. Switching to audio-only (MP3) when video is not needed
3. Running the app on a machine with a faster network

</details>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                                FAQ                                 -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## FAQ

<details>
<summary><b>Is this legal?</b></summary>

<br />

Video Downloader is a **tool**. Its legality depends entirely on how you use it. Downloading content you own, public-domain media, or content explicitly licensed for download is generally fine. Downloading copyrighted media without permission is not. See the [Disclaimer](#-disclaimer).

</details>

<details>
<summary><b>Does it work without an internet connection?</b></summary>

<br />

The web UI loads locally, but you obviously need internet access to reach the source video.

</details>

<details>
<summary><b>Can I download private / age-restricted videos?</b></summary>

<br />

Not by default. yt-dlp supports cookies, but Video Downloader does not currently expose this in the UI. You can extend `app.py` to pass `--cookies-from-browser` or `--cookies /path/to/cookies.txt`.

</details>

<details>
<summary><b>Where are files stored?</b></summary>

<br />

Inside the `downloads/` directory next to `app.py`. In Docker, this is mounted to a named volume (`videodownloader-downloads`). Files are automatically cleaned up when a new download for the same job ID starts.

</details>

<details>
<summary><b>Is there an authentication layer?</b></summary>

<br />

No. Video Downloader has no login. If you expose it publicly, put it behind HTTP basic auth (Nginx, Caddy, Traefik) or a VPN.

</details>

<details>
<summary><b>Can I run it on a Raspberry Pi?</b></summary>

<br />

Yes. It runs fine on ARM64 — the Python backend is tiny and yt-dlp is pure Python. ffmpeg is the only resource-heavy dependency, and even that runs comfortably on a Pi 4.

</details>

<details>
<summary><b>Why is the UI brutalist?</b></summary>

<br />

Because soft, glassmorphic, neumorphic dashboards are everywhere. Brutalism is honest, high-contrast, and memorable — and it pairs beautifully with a utility tool.

</details>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                             ROADMAP                                -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Roadmap

<table>
<tr>
<td width="50%" valign="top">

### Shipped ✅

- [x] Core download engine
- [x] MP4 / MP3 format selection
- [x] Quality picker
- [x] Bulk URL input
- [x] Playlist expansion
- [x] Docker support
- [x] Brutalist UI redesign
- [x] Skeleton loaders
- [x] Friendly error messages

</td>
<td width="50%" valign="top">

### Planned 🚧

- [ ] Subtitle download (SRT / VTT)
- [ ] Cookie authentication for private videos
- [ ] Download history persistence (SQLite)
- [ ] Webhook notifications on completion
- [ ] Progress bar with percentage
- [ ] Multi-language UI (i18n)
- [ ] Optional API key authentication
- [ ] Rate limiting
- [ ] Prometheus metrics endpoint
- [ ] Helm chart for Kubernetes

</td>
</tr>
</table>

Have a feature request? [Open an issue](https://github.com/yourname/videodownloader/issues/new?template=feature_request.md) — we love a good idea.

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                          CONTRIBUTING                              -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Contributing

Contributions are what make open source amazing. Any contribution you make is **greatly appreciated**.

### How to contribute

1. **Fork** the repository
2. **Create** your feature branch
   ```bash
   git checkout -b feature/amazing-feature
   ```
3. **Commit** your changes
   ```bash
   git commit -m "feat: add amazing feature"
   ```
4. **Push** to the branch
   ```bash
   git push origin feature/amazing-feature
   ```
5. **Open** a Pull Request

### Commit convention

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Use for |
|:-------|:--------|
| `feat:` | New features |
| `fix:` | Bug fixes |
| `docs:` | Documentation |
| `style:` | Formatting, no code change |
| `refactor:` | Code restructure |
| `perf:` | Performance improvements |
| `test:` | Adding tests |
| `chore:` | Build/tooling |

### Development guidelines

- Read the [Development](#-development) section
- Keep PRs focused — one feature or fix per PR
- Update the README for any user-visible change
- Add screenshots for UI changes
- Respect the existing brutalist design language

### Contributors

<a href="https://github.com/yourname/videodownloader/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=yourname/videodownloader" alt="Contributors" />
</a>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                          STAR HISTORY                              -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Star History

<div align="center">

<a href="https://star-history.com/#yourname/videodownloader&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=yourname/videodownloader&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=yourname/videodownloader&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=yourname/videodownloader&type=Date" width="80%" />
  </picture>
</a>

<br />
<br />

**If Video Downloader saved you time, consider giving it a star — it genuinely helps.**

[![Star this repo](https://img.shields.io/badge/⭐_Star_this_repo-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/yourname/videodownloader/stargazers)

</div>

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                        ACKNOWLEDGMENTS                             -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Acknowledgments

Video Downloader stands on the shoulders of giants:

- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — the engine that does all the heavy lifting. Without this extraordinary project, Video Downloader would not exist.
- **[ffmpeg](https://ffmpeg.org/)** — the universal media toolkit, quietly powering every merge and transcode.
- **[Flask](https://flask.palletsprojects.com/)** — the minimal, elegant web framework that keeps the backend tiny.
- **[Archivo Black](https://fonts.google.com/specimen/Archivo+Black)** and **[DM Mono](https://fonts.google.com/specimen/DM+Mono)** — the typefaces that give the UI its brutalist soul.
- **Everyone who files an issue, opens a PR, or stars the repo** — you make open source worth doing.

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                           DISCLAIMER                               -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## Disclaimer

> **This tool is provided for personal, educational, and legitimate use only.**

- Users are solely responsible for how they use this software.
- Downloading copyrighted content without permission is illegal in most jurisdictions.
- Always comply with the **terms of service** of the platforms you access.
- The maintainers assume **no liability** for misuse, legal consequences, or damages arising from use of this tool.
- If you are a content owner and believe this tool facilitates infringement of your rights, please open an issue and we will respond promptly.

**Do the right thing. Respect creators.**

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                              LICENSE                               -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for full text.

```
MIT License

Copyright (c) 2025 Video Downloader Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!--                              FOOTER                                -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

<div align="center">

<br />

**Built with stubbornness, brutalist taste, and a deep love for open source.**

<br />

[![GitHub](https://img.shields.io/badge/GitHub-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/yourname/videodownloader)
[![Issues](https://img.shields.io/badge/Issues-FF5722?style=for-the-badge&logo=github&logoColor=white)](https://github.com/yourname/videodownloader/issues)
[![Discussions](https://img.shields.io/badge/Discussions-B78AFF?style=for-the-badge&logo=github&logoColor=white)](https://github.com/yourname/videodownloader/discussions)

<br />

<sub>If this project helped you, consider buying the maintainer a coffee. No pressure — a star works too.</sub>

<br />
<br />

<a href="#top">
  <img src="https://img.shields.io/badge/⬆_Back_to_Top-0A0A0A?style=for-the-badge" alt="Back to top" />
</a>

<br />
<br />

<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=24&height=120&section=footer" width="100%" />

</div>