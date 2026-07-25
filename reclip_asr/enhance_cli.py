"""Isolated DeepFilterNet3 enhancement with deterministic overlap-add chunks."""

from __future__ import annotations

import argparse
import os
import sys
import wave
from pathlib import Path
from typing import Any, Callable


TARGET_SAMPLE_RATE = 48_000


def chunk_ranges(total_samples: int, chunk_samples: int, overlap_samples: int) -> list[tuple[int, int]]:
    if total_samples <= 0 or chunk_samples <= 0:
        raise ValueError("Audio and chunk sizes must be positive")
    if overlap_samples < 0 or overlap_samples >= chunk_samples:
        raise ValueError("Overlap must be non-negative and shorter than a chunk")
    if total_samples <= chunk_samples:
        return [(0, total_samples)]
    step = chunk_samples - overlap_samples
    ranges = []
    start = 0
    while start < total_samples:
        end = min(total_samples, start + chunk_samples)
        ranges.append((start, end))
        if end == total_samples:
            break
        start += step
    return ranges


def overlap_weights(
    length: int,
    overlap_samples: int,
    fade_in: bool,
    fade_out: bool,
    torch_module: Any,
    device: Any,
    dtype: Any,
) -> Any:
    weights = torch_module.ones(length, device=device, dtype=dtype)
    fade = min(overlap_samples, length)
    if fade and fade_in:
        weights[:fade] = torch_module.linspace(0.0, 1.0, fade, device=device, dtype=dtype)
    if fade and fade_out:
        weights[-fade:] = torch_module.minimum(
            weights[-fade:],
            torch_module.linspace(1.0, 0.0, fade, device=device, dtype=dtype),
        )
    return weights


def enhance_chunked(
    audio: Any,
    processor: Callable[[Any], Any],
    sample_rate: int,
    chunk_seconds: float,
    overlap_seconds: float,
    torch_module: Any,
) -> Any:
    """Enhance channel-first audio and crossfade adjacent processed chunks."""
    if audio.ndim == 1:
        audio = audio.unsqueeze(0)
    total_samples = int(audio.shape[-1])
    chunk_samples = max(1, round(chunk_seconds * sample_rate))
    overlap_samples = max(0, round(overlap_seconds * sample_rate))
    ranges = chunk_ranges(total_samples, chunk_samples, overlap_samples)
    output = torch_module.zeros_like(audio)
    normalization = torch_module.zeros(
        total_samples, device=audio.device, dtype=audio.dtype
    )
    for index, (start, end) in enumerate(ranges):
        processed = processor(audio[..., start:end])
        if processed.ndim == 1:
            processed = processed.unsqueeze(0)
        processed = processed[..., : end - start]
        if processed.shape[-1] < end - start:
            processed = torch_module.nn.functional.pad(processed, (0, end - start - processed.shape[-1]))
        weight = overlap_weights(
            end - start,
            overlap_samples,
            fade_in=index > 0,
            fade_out=index < len(ranges) - 1,
            torch_module=torch_module,
            device=audio.device,
            dtype=audio.dtype,
        )
        output[..., start:end] += processed * weight
        normalization[start:end] += weight
        print(f"enhanced_chunk={index + 1}/{len(ranges)}", flush=True)
    return output / normalization.clamp_min(torch_module.finfo(audio.dtype).eps)


def validate_input(path: Path) -> None:
    try:
        with wave.open(str(path), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getframerate() != TARGET_SAMPLE_RATE
                or wav.getsampwidth() != 2
                or wav.getnframes() <= 0
            ):
                raise ValueError("DeepFilter input must be nonempty 48 kHz mono PCM16 WAV")
    except wave.Error as exc:
        raise ValueError("DeepFilter input is not a readable PCM WAV") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enhance one 48 kHz mono WAV with DeepFilterNet3")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--chunk-seconds", type=float, default=60.0)
    parser.add_argument("--overlap-seconds", type=float, default=0.5)
    parser.add_argument("--cache-dir", type=Path, default=Path("/cache/deepfilter"))
    return parser


def run(args: argparse.Namespace) -> None:
    validate_input(args.input)
    if args.chunk_seconds <= 0 or args.overlap_seconds < 0 or args.overlap_seconds >= args.chunk_seconds:
        raise ValueError("Invalid chunk/overlap settings")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", str(args.cache_dir.parent))
    os.environ.setdefault("DF_CACHE_DIR", str(args.cache_dir))
    if args.device == "cpu":
        # DeepFilterNet's get_device() follows CUDA visibility independently
        # of model.to(), so hide CUDA before importing torch for explicit CPU mode.
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    import torch
    from df.enhance import enhance, init_df, load_audio, save_audio

    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available to DeepFilterNet")
    model_dir = Path(os.environ.get(
        "DEEPFILTER_MODEL_DIR", "/opt/deepfilter-models/DeepFilterNet3"
    )).resolve()
    if not (model_dir / "config.ini").is_file():
        raise RuntimeError("Pinned DeepFilterNet3 model is missing from the worker image")
    # DeepFilterNet otherwise writes enhance.log beside the pinned model.
    # The model directory is intentionally immutable; ReClip keeps its own pipeline log.
    model, df_state, _ = init_df(
        model_base_dir=str(model_dir),
        log_file=None,
        log_level="none",
        config_allow_defaults=True,
    )
    model = model.to(args.device)
    model.eval()
    sample_rate = int(df_state.sr())
    if sample_rate != TARGET_SAMPLE_RATE:
        raise RuntimeError(f"DeepFilterNet model sample rate is {sample_rate}, expected 48000")
    audio, audio_metadata = load_audio(str(args.input), sr=sample_rate)
    loaded_sample_rate = int(getattr(audio_metadata, "sample_rate", audio_metadata))
    if loaded_sample_rate != sample_rate:
        raise RuntimeError("DeepFilterNet input was resampled unexpectedly")
    # libdf performs analysis/synthesis through NumPy, so time-domain chunks
    # stay on CPU; enhance() moves spectral features to the model device.
    audio = audio.to(device="cpu", dtype=torch.float32)

    def process(chunk: Any) -> Any:
        # The model state is reset for every overlapped chunk.  Crossfading hides
        # boundaries and avoids carrying recurrent noise state across an hour-long file.
        with torch.inference_mode():
            reset = getattr(df_state, "reset", None)
            if reset is not None:
                reset()
            return enhance(model, df_state, chunk, pad=True)

    enhanced = enhance_chunked(
        audio,
        process,
        sample_rate,
        args.chunk_seconds,
        args.overlap_seconds,
        torch,
    ).detach().cpu()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.stem}.{os.getpid()}.tmp.wav")
    save_audio(str(temporary), enhanced, sample_rate)
    validate_input(temporary)
    os.replace(temporary, args.output)


def main(argv: list[str] | None = None) -> int:
    try:
        run(build_parser().parse_args(argv))
        return 0
    except Exception as exc:
        print(f"enhancement_failed={type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
