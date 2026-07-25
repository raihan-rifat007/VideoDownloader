"""Isolated Faster-Whisper process used by :mod:`reclip_asr.pipeline`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MODEL_REVISIONS = {
    "medium": (
        "Systran/faster-whisper-medium",
        "08e178d48790749d25932bbc082711ddcfdfbc4f",
    ),
    "large-v3": (
        "Systran/faster-whisper-large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    ),
}


def timestamp(seconds: float) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def serialize_segments(segments: Iterable[Any]) -> tuple[str, str, int]:
    """Serialize accepted segments once while the model generator is consumed."""
    txt_lines: list[str] = []
    jsonl_lines: list[str] = []
    count = 0
    for segment in segments:
        text = " ".join(str(getattr(segment, "text", "")).split())
        if not text:
            continue
        start = float(segment.start)
        end = float(segment.end)
        row: dict[str, Any] = {
            "id": count,
            "start": start,
            "end": end,
            "text": text,
        }
        for field in ("avg_logprob", "compression_ratio", "no_speech_prob"):
            value = _safe_float(getattr(segment, field, None))
            if value is not None:
                row[field] = value
        words = []
        for word in getattr(segment, "words", None) or []:
            word_text = str(getattr(word, "word", ""))
            word_start = _safe_float(getattr(word, "start", None))
            word_end = _safe_float(getattr(word, "end", None))
            if word_text and word_start is not None and word_end is not None:
                words.append(
                    {
                        "start": word_start,
                        "end": word_end,
                        "word": word_text,
                        "probability": _safe_float(getattr(word, "probability", None)),
                    }
                )
        if words:
            row["words"] = words
        txt_lines.append(f"[{timestamp(start)} --> {timestamp(end)}] {text}")
        jsonl_lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
        count += 1
        if count % 100 == 0:
            print(f"transcribed_segments={count}", flush=True)
    return "\n".join(txt_lines) + ("\n" if txt_lines else ""), "\n".join(jsonl_lines) + ("\n" if jsonl_lines else ""), count


def resolve_model_snapshot(
    model: str,
    repository: str | None,
    revision: str | None,
    models_dir: Path,
) -> tuple[Path, str, str]:
    default_repository, default_revision = MODEL_REVISIONS.get(model, (None, None))
    repository = repository or default_repository
    revision = revision or default_revision
    if not repository or not revision:
        raise ValueError("A pinned model repository and revision are required")
    from huggingface_hub import snapshot_download

    snapshot = snapshot_download(
        repo_id=repository,
        revision=revision,
        cache_dir=str(models_dir / "huggingface"),
        allow_patterns=[
            "config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.*",
            "preprocessor_config.json",
        ],
        local_files_only=os.environ.get("ASR_MODELS_LOCAL_ONLY") == "1",
    )
    return Path(snapshot), repository, revision


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transcribe one prepared WAV with Faster-Whisper")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--basename", required=True)
    parser.add_argument("--model", choices=sorted(MODEL_REVISIONS), required=True)
    parser.add_argument("--model-repository")
    parser.add_argument("--model-revision")
    parser.add_argument("--models-dir", type=Path, default=Path("/models"))
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--compute-type")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--language")
    parser.add_argument("--min-silence-ms", type=int, default=500)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.input.is_file():
        raise FileNotFoundError(f"Input does not exist: {args.input}")
    if not args.basename.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Output basename contains unsafe characters")
    if args.beam_size < 1 or args.min_silence_ms < 0:
        raise ValueError("Invalid decoder settings")

    compute_type = args.compute_type or ("int8_float16" if args.device == "cuda" else "int8")
    snapshot, repository, revision = resolve_model_snapshot(
        args.model, args.model_repository, args.model_revision, args.models_dir
    )
    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(snapshot),
        device=args.device,
        compute_type=compute_type,
        download_root=str(args.models_dir),
    )
    transcription_kwargs: dict[str, Any] = {
        "beam_size": args.beam_size,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": args.min_silence_ms},
        "condition_on_previous_text": True,
        "word_timestamps": True,
    }
    if args.language:
        transcription_kwargs["language"] = args.language
    segments, info = model.transcribe(str(args.input), **transcription_kwargs)
    transcript, jsonl, count = serialize_segments(segments)
    if count == 0:
        raise RuntimeError("Whisper produced no speech segments")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = args.output_dir / f"{args.basename}_transcript.txt"
    segments_path = args.output_dir / f"{args.basename}_segments.jsonl"
    metadata_path = args.output_dir / f"{args.basename}_asr_metadata.json"
    _atomic_text(transcript_path, transcript)
    _atomic_text(segments_path, jsonl)
    metadata = {
        "schema_version": 1,
        "source": str(args.input),
        "model": args.model,
        "model_repository": repository,
        "model_revision": revision,
        "device": args.device,
        "compute_type": compute_type,
        "beam_size": args.beam_size,
        "vad_filter": True,
        "min_silence_duration_ms": args.min_silence_ms,
        "condition_on_previous_text": True,
        "requested_language": args.language,
        "detected_language": info.language,
        "language_probability": _safe_float(info.language_probability),
        "duration_seconds": _safe_float(info.duration),
        "duration_after_vad_seconds": _safe_float(getattr(info, "duration_after_vad", None)),
        "segment_count": count,
    }
    _atomic_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True), flush=True)
    return metadata


def main(argv: list[str] | None = None) -> int:
    try:
        run(build_parser().parse_args(argv))
        return 0
    except Exception as exc:
        print(f"transcription_failed={type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
