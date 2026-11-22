from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

import httpx
import pytest

from ampost import Ampost, AsyncAmpost

Handler = Callable[[httpx.Request], Union[httpx.Response, Exception]]


class Recorder:
    """A mock API: answers each request with the next response in ``responses`` and records the requests."""

    def __init__(self, *responses: Union[httpx.Response, Exception, Handler]) -> None:
        self.responses: List[Union[httpx.Response, Exception, Handler]] = list(responses)
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")
        answer = self.responses.pop(0)
        if callable(answer) and not isinstance(answer, (httpx.Response, Exception)):
            answer = answer(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def handle_async(self, request: httpx.Request) -> httpx.Response:
        return self(request)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def bodies(self) -> List[Any]:
        return [json.loads(r.content) if r.content else None for r in self.requests]


def json_response(body: Any, status: int = 200, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def api_error(status: int, code: str, message: str = "nope", headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return json_response({"error": {"code": code, "message": message, "details": {"field": "x"}}}, status, headers)


def page(items: List[Any], offset: int = 0, total: Optional[int] = None, has_more: Optional[bool] = None) -> httpx.Response:
    total = len(items) if total is None else total
    has_more = offset + len(items) < total if has_more is None else has_more
    return json_response({"data": items, "pagination": {"limit": 2, "offset": offset, "total": total, "hasMore": has_more}})


def sync_client(recorder: Recorder, **kwargs: Any) -> Ampost:
    kwargs.setdefault("api_key", "amp_test")
    return Ampost(http_client=httpx.Client(transport=httpx.MockTransport(recorder)), **kwargs)


def async_client(recorder: Recorder, **kwargs: Any) -> AsyncAmpost:
    kwargs.setdefault("api_key", "amp_test")
    return AsyncAmpost(http_client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle_async)), **kwargs)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> Iterator[List[float]]:
    """Records waits instead of sleeping."""
    waits: List[float] = []

    def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async def fake_async_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(asyncio, "sleep", fake_async_sleep)
    monkeypatch.delenv("AMPOST_API_KEY", raising=False)
    monkeypatch.delenv("AMPOST_BASE_URL", raising=False)
    yield waits
