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
- Live download progress, speed, size and ETA
- Resumable task records for interrupted downloads
- Cancel active downloads while preserving their partial files for later continuation

## Quick Start

```bash
brew install yt-dlp ffmpeg    # or apt install ffmpeg && pip install yt-dlp
git clone https://github.com/averygan/reclip.git
cd reclip
./reclip.sh
```

Open **http://127.0.0.1:8899**.

Or with Docker:

```bash
docker compose up --build -d
```

The Compose configuration publishes the service on `127.0.0.1:8899`, so it is
available only on the local computer by default.

## Usage

1. Paste one or more video URLs into the input box
2. Choose **MP4** (video) or **MP3** (audio)
3. Click **Fetch** to load video info and thumbnails
4. Select quality/resolution if available
5. Click **Download** on individual videos, or **Download All**

### Interrupted downloads

Downloads are stored under the Docker volume mounted at `/app/downloads`.
Task records and resumable media are kept in `.reclip/jobs.sqlite3` and
`.reclip/jobs/<job_id>/`. If a download fails or the container is restarted,
open the page again and use **Continue** on the interrupted task. ReClip checks
the saved media identity before asking yt-dlp to reuse its partial files.

Continuation depends on the source and media protocol. If the source changes,
does not support byte ranges or no longer exposes the selected format, ReClip
will refuse to append to the old task; use **Restart** to create a new task.
The application does not automatically start interrupted downloads after a
container restart. Completed files can be saved again from the task list.

### Cancelling downloads

Click **Cancel** on an active task to stop its current downloader process. The
task first shows **Cancelling** while ReClip confirms that the process and its
children have stopped; only then is it marked **Cancelled**. Partial files and
the last known progress are preserved, and no completed file is published.

For a cancelled task, **Continue** creates the next attempt in the same task
and asks yt-dlp to reuse compatible partial files. **Restart** creates a new
task from the original URL. **Delete** removes the task record and its task
directory after confirmation.

Task history now retains the source URL, title and partial files until the task
is explicitly deleted. Do not expose this local service to the public network;
the first version has no user authentication.

## Supported Sites

Anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including:

YouTube, TikTok, Instagram, Twitter/X, Reddit, Facebook, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Pinterest, Tumblr, Threads, LinkedIn, and many more.

## Stack

- **Backend:** Python + Flask with a SQLite task store
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Download engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **Dependencies:** Flask, yt-dlp, ffmpeg and the Python standard library

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
