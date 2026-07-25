from __future__ import annotations

import socket

import pytest

from reclip_asr.utils import validate_public_url


def test_worker_dns_validation_rejects_private_aliases(monkeypatch) -> None:
    monkeypatch.setattr(
        "reclip_asr.utils.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    with pytest.raises(ValueError, match="private"):
        validate_public_url("https://public-looking.example/media", resolve_dns=True)


def test_worker_dns_validation_accepts_global_targets(monkeypatch) -> None:
    monkeypatch.setattr(
        "reclip_asr.utils.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    url = "https://media.example/speech"
    assert validate_public_url(url, resolve_dns=True) == url
