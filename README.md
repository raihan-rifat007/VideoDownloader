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

## Local App Launcher

If you want to run ReClip locally without opening a terminal and navigating into the repo each time, install a one-click launcher:

```bash
cd reclip
./install-local.sh
```

After that, launch **ReClip** from your Linux applications menu. It starts the local server (if needed) and opens the app in its **own desktop window** — the same way X / WhatsApp are launched on this system (Chromium `--app`), instead of a browser tab.

Optionally, to keep starting the server but open it in a normal browser tab instead:

```bash
./launch-reclip.sh
```

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

## Configuration

ReClip reads a couple of optional environment variables so you can control where media lands and how it's processed:

| Env var | Default | Purpose |
|---------|---------|---------|
| `RECLIP_DOWNLOAD_DIR` | `<repo>/downloads/` | Where downloaded files are written. Set to something like `~/Downloads/ReClip` to keep them in your Downloads folder. |
| `RECLIP_WHATSAPP_COMPAT` | `1` | Re-encode downloads to WhatsApp-friendly H.264+AAC after download. Set `0` to keep the untouched original (faster, and never blocked by a failed re-encode). |
| `PORT` | `8899` | Local server port. |

### TikTok downloads

TikTok blocks plain HTTP downloads (`403 Access Denied`) and even yt-dlp's bare extractor (`Unable to extract universal data`). ReClip uses **yt-dlp with Chrome impersonation** (`--impersonate chrome`, backed by `curl-cffi`), which makes yt-dlp mimic a real Chrome TLS fingerprint and solve TikTok's anti-bot JS challenge itself — no browser window is spawned. Because the challenge is intermittent, ReClip retries a few times before giving up. Downloads run in a background thread (the UI never freezes) and show live progress.

> Install `curl-cffi` for TikTok support: `pip install curl-cffi`. `./reclip.sh` does this automatically.


Media is never blocked: if the optional WhatsApp re-encode fails, the original file is kept and a warning is shown instead of failing the download.

## Stack

- **Backend:** Python + Flask
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Download engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **TikTok:** yt-dlp + `--impersonate chrome` (via [curl-cffi](https://github.com/lexiforest/curl_cffi))
- **Dependencies:** 3 (Flask, yt-dlp, curl-cffi)

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
