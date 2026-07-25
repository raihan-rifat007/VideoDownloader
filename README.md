# ReClip + ASR Pipeline

ReClip is a localhost-only Docker tool for downloading public media as MP4/MP3 and producing durable, timestamped transcripts from public URLs or local uploads. The original ReClip download workflow remains available; Transcript mode adds a persistent SQLite queue and one serial ASR worker.

The UI binds only to **<http://127.0.0.1:8899>**. No host Python, FFmpeg, Whisper, or Deno installation is required.

## Requirements

- Docker Desktop with the Linux/WSL2 backend
- For the default configuration: an NVIDIA GPU, current Windows driver, and Docker GPU passthrough
- Disk space for retained source media, derived audio/transcripts, and model caches

Defaults target an 8 GB RTX 3060 Ti: CUDA, `int8_float16`, VAD, and one job at a time. Explicit CPU mode is available but Large-v3 will be much slower.

## Start

```powershell
Copy-Item .env.example .env
docker compose build reclip-web
.\build-worker.ps1
docker compose up -d --no-build
docker compose logs -f asr-worker
```

Open <http://127.0.0.1:8899>. Check readiness in Transcript mode or with:

```powershell
Invoke-RestMethod http://127.0.0.1:8899/api/system/asr
```

The first Standard or Maximum job downloads the pinned Faster-Whisper Large-v3 model. The first enhanced job initializes DeepFilterNet3. These downloads are several gigabytes; named caches survive normal container rebuilds.

The heavyweight worker is deliberately excluded from the base Compose build configuration. `build-worker.ps1` is the supported worker build entry point: it requires 80 GB free on the drive that contains Docker Desktop's data VHDX, holds a machine-wide build lock, and forces Compose build parallelism to one. Normal startup uses `--no-build` so it cannot launch the worker build implicitly.

If Docker Desktop storage has been relocated, pass the new VHDX path so the free-space check follows it instead of assuming `C:`:

```powershell
.\build-worker.ps1 -DockerDataPath "E:\Docker\wsl\disk\docker_data.vhdx"
```

Do not invoke the worker build Compose file directly or start a second worker build while the wrapper is running. If the wrapper reports an abandoned build lock, run `docker buildx history ls` and retry only after it shows no `Running` build.

Explicit CPU mode:

```powershell
docker compose build reclip-web
.\build-worker.ps1
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --no-build
```

CPU mode uses `int8`. In normal GPU mode, GPU failure disables only Transcript mode; MP4/MP3 downloads remain available.

## Profiles

| Profile | Whisper input | Model | Enhancement |
| --- | --- | --- | --- |
| Fast | Non-enhanced 16 kHz | Medium, beam 1 | None |
| Standard (default) | Enhanced 16 kHz | Large-v3, beam 5 | DeepFilterNet3 in 60 s chunks with 0.5 s overlap |
| Maximum | Enhanced primary plus independent non-enhanced baseline | Large-v3, beam 5 for both | Same as Standard; disagreements are retained, never auto-merged |

All profiles prepare at 48 kHz, enable VAD with 500 ms silence, and condition on previous text. Language detection is automatic unless a language code is supplied. DeepFilter failure fails Standard/Maximum instead of silently bypassing enhancement.

## Usage

MP4/MP3 mode preserves ReClip's URL, playlist, quality-selection, and bulk-download flow.

In Transcript mode, paste one public URL per line or drag local audio/video files onto the upload area. Every URL/file becomes an independent persistent job. Choose a profile and optional language code, then follow queue position, stage, and progress in History. Completed jobs provide a read-only/copyable transcript and individual artifact downloads plus Cancel, Retry, and Delete actions.

Only public `http` and `https` sources are supported. Cookie-authenticated sources and browser-cookie import are out of scope. Only process media you are authorized to access.

## Persistence and backup

Compose creates four named volumes:

- `reclip-data`: SQLite queue, job media, derivatives, transcripts, metadata, checksums, and sanitized logs
- `whisper-models`: Faster-Whisper/Hugging Face cache
- `deepfilter-cache`: DeepFilterNet cache
- `reclip-downloads`: legacy MP4/MP3 downloads

Jobs have no TTL. Every artifact remains until that job is explicitly deleted.

> **Destructive:** `docker compose down -v` permanently deletes all four volumes, including history, media, transcripts, and model caches. Plain `docker compose down` preserves them.

Stop Compose and back up named volumes before destructive maintenance. Confirm their actual names with `docker volume ls`; Compose prefixes them with the project name.

## Pipeline and recovery

```text
queued -> acquiring/uploading -> probing -> preparing -> enhancing
       -> transcribing -> validating -> completed
```

`failed` and `canceled` are terminal until Retry. Web and worker share SQLite in WAL mode. Atomic claiming enforces one GPU job, heartbeats expose liveness, and enhancement/Whisper run as separate child processes so GPU memory is released between stages. Restart recovery resumes after the last checkpoint whose registered artifact checksum verifies.

Completed jobs retain source/source-info, prepared and Whisper-input WAVs, timestamped TXT, JSONL segments, metadata JSON, checksums, and sanitized logs. Maximum also retains baseline audio/transcript outputs. Metadata records source identity, language, duration, decoding/VAD settings, timings, exact tool/model identities, artifact checksums, and warnings.

V1 intentionally does not provide ZIP, SRT/VTT, transcript editing, diarization, or speaker labels.

## API

Legacy routes remain compatible:

- `POST /api/info`, `POST /api/playlist`, `POST /api/download`
- `GET /api/status/<id>`, `GET /api/file/<id>`

Transcript routes:

- `GET /api/system/asr`
- `POST /api/transcriptions/url`
- `POST /api/transcriptions/upload` (one streamed multipart file per request)
- `GET /api/transcriptions`, `GET /api/transcriptions/<id>`
- `POST /api/transcriptions/<id>/cancel`, `POST /api/transcriptions/<id>/retry`
- `DELETE /api/transcriptions/<id>`
- `GET /api/transcriptions/<id>/artifacts/<key>`

Artifact downloads accept registered keys only; URL path text is never treated as a filesystem path.

## Reproducibility and updates

The CUDA/Python ASR stack and model revisions are pinned; each job records the versions actually used. ReClip still performs a fail-soft startup update of only the fast-moving yt-dlp extractor components, including EJS support through Deno. Disable it for image-pinned operation:

```env
RECLIP_NO_UPDATE=1
```

If that update fails, containers continue with their pinned yt-dlp versions. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Troubleshooting

Check containers, logs, and passthrough:

```powershell
docker compose ps
docker compose logs --tail=200 asr-worker
docker run --rm --gpus all nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04 nvidia-smi
```

If Docker `nvidia-smi` fails, enable WSL2, update the NVIDIA Windows driver, and restart Docker Desktop. Do not install a Linux NVIDIA driver inside the container. If Large-v3 exhausts VRAM, close other GPU applications and retain `ASR_COMPUTE_TYPE=int8_float16` on an 8 GB card.

Standard/Maximum deliberately fail when DeepFilterNet fails; use Fast only when non-enhanced transcription is acceptable. For extractor failures, allow the normal startup update and inspect the logs.

## Development checks

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.cpu.yml config --quiet
docker compose -f docker-compose.worker-build.yml config --quiet
```

Host Python is needed only for development tests, not for normal use. Run `.\build-worker.ps1` separately only when a new heavyweight worker image is actually required and its disk preflight passes.

## License and use

[MIT](LICENSE). Respect copyright law and source-platform terms; the developers are not responsible for misuse.
