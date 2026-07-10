---
name: reclip
description: >
  Use when the user wants to SAVE a media file from a URL - download a video as
  MP4, extract audio as MP3, grab everything in a playlist, or pull a video's
  subtitles as text. Works on YouTube, TikTok, Instagram, Twitter/X, and the
  1000+ sites yt-dlp supports.

  NOT for researching or summarizing web/social content - use agent-reach for
  that. Boundary: reclip SAVES files; agent-reach READS content.
---

# ReClip - media downloader tool

Requires the `reclip` command and `ffmpeg` on PATH.
Install ReClip with `pipx` (or `uv tool install`); the isolated CLI environment includes yt-dlp.
The project installs flat top-level modules, so a plain `pip install` into a shared environment is not supported.

Announce before use: "using reclip to download/transcribe <url>".

## Commands

Always pass `--json` when you need to consume the result programmatically.
Exit code is 0 on success, non-zero on failure (error on stderr, or a JSON
error object with `--json`).

```bash
# Inspect before downloading (title, duration, available qualities)
reclip info "<url>" --json

# Download video (MP4). --quality caps the height (video only;
# combining it with --audio is rejected with exit code 1).
reclip download "<url>" --quality 1080 -o /path/to/dir --json

# Pick the saved filename (single URL only; the extension is added for you)
reclip download "<url>" --name "my-clip" --json

# Each download is killed after 300s by default; raise the cap for long videos
reclip download "<url>" --timeout 1800 --json

# Extract audio (MP3)
reclip download "<url>" --audio -o /path/to/dir --json

# Bulk download several URLs at once
reclip download "<url1>" "<url2>" "<url3>" --json

# Expand a playlist, then download the URLs it returns
reclip playlist "<playlist-url>" --json

# Read a video's subtitles as plain text (no file saved)
reclip transcript "<url>" --lang en --json
```

## Output shapes

- `info` -> `{title, uploader, duration, thumbnail, formats:[{id,label,height}]}`
- `download` -> `{file, title, ext}`.
  With multiple URLs it is a JSON array with one entry per URL:
  `{file, title, ext}` on success or `{url, error}` for a failed URL.
  Remaining URLs still download after a failure; exit code is 0 only when
  every URL succeeded.
  Existing files are preserved and the new download receives a numbered suffix.
- `playlist` -> `{urls:[...]}`
- `transcript` -> `{text, lang, auto, available_langs}` (`text` is null when the
  video has no subtitles for that language; `available_langs` always lists every
  subtitle and auto-caption language the video offers).
  `lang` identifies the selected language, and `auto` reports whether ReClip used automatic captions.

## When to reach for reclip vs agent-reach

- The user wants a FILE they can keep (video/audio) -> reclip.
- The user wants to KNOW what is in a video/thread/article -> agent-reach.
  `reclip transcript` is the one overlap: use it when you specifically need a
  video's captions as text and reclip is already installed.
