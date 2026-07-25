"""Immutable transcription profile definitions shared by the API and worker."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class TranscriptionProfile:
    name: str
    model: str
    beam_size: int
    enhance: bool
    baseline: bool
    preparation_sample_rate: int = 48_000
    whisper_sample_rate: int = 16_000
    vad_filter: bool = True
    min_silence_duration_ms: int = 500
    condition_on_previous_text: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_PROFILE = "standard"

PROFILES: Mapping[str, TranscriptionProfile] = MappingProxyType(
    {
        "fast": TranscriptionProfile(
            name="fast",
            model="medium",
            beam_size=1,
            enhance=False,
            baseline=False,
        ),
        "standard": TranscriptionProfile(
            name="standard",
            model="large-v3",
            beam_size=5,
            enhance=True,
            baseline=False,
        ),
        "maximum": TranscriptionProfile(
            name="maximum",
            model="large-v3",
            beam_size=5,
            enhance=True,
            baseline=True,
        ),
    }
)


def get_profile(name: str | None) -> TranscriptionProfile:
    """Return a known profile, applying the product default for an empty value."""

    normalized = (name or DEFAULT_PROFILE).strip().lower()
    try:
        return PROFILES[normalized]
    except KeyError as exc:
        choices = ", ".join(PROFILES)
        raise ValueError(f"Unknown profile '{normalized}'. Expected one of: {choices}") from exc


def profiles_payload() -> dict[str, dict[str, Any]]:
    """Return JSON-ready public profile definitions."""

    return {name: profile.as_dict() for name, profile in PROFILES.items()}
