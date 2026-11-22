from __future__ import annotations

from typing import Mapping
from urllib.parse import quote

from typing_extensions import TypedDict


class RequestOptions(TypedDict, total=False):
    """Per-call options every method takes as keyword arguments."""

    #: Seconds per attempt. Default: the client's ``timeout``.
    timeout: float
    #: Default: the client's ``max_retries``.
    max_retries: int
    #: Extra headers for this request.
    extra_headers: Mapping[str, str]


class IdempotentRequestOptions(RequestOptions, total=False):
    #: Sent as ``Idempotency-Key``. The same key with the same request returns the first result instead of
    #: acting twice (kept 24 hours). Without one, the SDK generates a key per call and reuses it on retries.
    #: Pass your own to stay safe across process restarts, for example a hash of the content you post.
    idempotency_key: str


def _path(value: str) -> str:
    """Encodes one path parameter."""
    if not isinstance(value, str) or not value:
        raise TypeError(f"Expected a non-empty string path parameter, got {value!r}")
    return quote(value, safe="")
