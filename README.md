# ReClip

A self-hosted, open-source video and audio downloader with a clean web UI and a scriptable CLI. Paste links from YouTube, TikTok, Instagram, Twitter/X, and 1000+ other sites — download as MP4 or MP3.

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
- Agent-friendly `reclip` CLI - download, info, playlist expansion, subtitle transcripts
- Small Python backend - the web app and CLI share one engine

## Quick Start

```bash
brew install yt-dlp ffmpeg    # or apt install ffmpeg && pip install yt-dlp
git clone https://github.com/averygan/reclip.git
cd reclip
./reclip.sh
```

Open **http://localhost:8899**.

`reclip.sh` updates `yt-dlp` on startup (to keep extractors current). Set `RECLIP_NO_UPDATE=1` to skip the update step.

Or with Docker:

```bash
docker build -t reclip . && docker run -p 8899:8899 reclip
```

The container entrypoint also updates `yt-dlp` on startup by default. Disable it with `-e RECLIP_NO_UPDATE=1`.

## Usage

1. Paste one or more video URLs into the input box
2. Choose **MP4** (video) or **MP3** (audio)
3. Click **Fetch** to load video info and thumbnails
4. Select quality/resolution if available
5. Click **Download** on individual videos, or **Download All**

## CLI

ReClip also ships a command-line interface that shares the same engine as the
web app, so it works anywhere yt-dlp does - and is easy for scripts or AI
agents to call.

```bash
pipx install git+https://github.com/averygan/reclip

reclip info "<url>" --json
reclip download "<url>" --quality 1080 -o ~/Downloads
reclip download "<url>" --name "My clip"
reclip download "<url>" --audio
reclip download "<long-url>" --timeout 1800
reclip playlist "<playlist-url>"
reclip transcript "<url>" --lang en
```

Install with `pipx` (or `uv tool install`).
The isolated tool environment includes yt-dlp; install ffmpeg separately and ensure it is on `PATH`.
The project installs flat top-level modules (`core`, `cli`, `app`), so a plain `pip install` into a shared environment is not supported.

The agent skill is installed separately from the Python package.
For Claude Code, place it in the skill discovery directory:

```bash
skill_dir="$HOME/.claude/skills/reclip"
mkdir -p "$skill_dir"
curl -fsSL https://raw.githubusercontent.com/averygan/reclip/main/skill/SKILL.md \
  -o "$skill_dir/SKILL.md"
```

For Codex, set `skill_dir` to `$CODEX_HOME/skills/reclip`, or `$HOME/.codex/skills/reclip` when `CODEX_HOME` is unset.
For another compatible agent, copy the same `skill/SKILL.md` into its configured skill directory.

Every command takes `--json` for machine-readable output and exits non-zero on failure.
For bulk downloads, ReClip continues after an individual URL fails and exits non-zero if any URL failed.
Video is the default, `--quality` applies only to video, and `--name` can only be used with a single URL.
Each download is killed after 300 seconds by default; pass `--timeout <seconds>` to raise the cap for long videos.
Existing files are never overwritten; ReClip adds a numbered suffix to the new filename instead.
See the [agent-tool contract](skill/SKILL.md) for exact output shapes.

## Supported Sites

Anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including:

YouTube, TikTok, Instagram, Twitter/X, Reddit, Facebook, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Pinterest, Tumblr, Threads, LinkedIn, and many more.

## Stack

- **Backend:** Python - a shared yt-dlp engine (`core.py`) behind a thin Flask app (`app.py`) and CLI (`cli.py`)
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Download engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **Dependencies:** 2 (Flask, yt-dlp)

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
