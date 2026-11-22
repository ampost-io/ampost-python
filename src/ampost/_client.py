from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from typing import Any, Dict, Mapping, Optional, Tuple, cast

import httpx

from ._errors import AmpostError, APIConnectionError, APIError, APITimeoutError, parse_retry_after
from ._generated.resources import _AsyncResources, _SyncResources
from ._media import AsyncMediaResource, MediaResource
from ._pagination import AsyncPaginator, SyncPage
from ._types import IdempotentRequestOptions, RequestOptions
from ._version import __version__

__all__ = ["Ampost", "AsyncAmpost", "DEFAULT_BASE_URL"]

DEFAULT_BASE_URL = "https://api.ampost.io"


class _BaseClient:
    def __init__(
        self,
        api_key: Optional[str],
        base_url: Optional[str],
        max_retries: int,
        timeout: float,
        max_retry_delay: float,
        default_headers: Optional[Mapping[str, str]],
    ) -> None:
        api_key = api_key or os.environ.get("AMPOST_API_KEY")
        if not api_key:
            raise AmpostError(
                "Missing API key: pass api_key= or set AMPOST_API_KEY. "
                "Create one at https://ampost.io/app/settings/api-keys?ref=github"
            )
        self.__api_key = api_key
        self.base_url = (base_url or os.environ.get("AMPOST_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        #: A ``Retry-After`` longer than this many seconds is not waited for: the error is raised.
        self.max_retry_delay = max_retry_delay
        self._default_headers = dict(default_headers or {})

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r})"

    def _prepare(
        self,
        path: str,
        query: Optional[Mapping[str, Any]],
        body: Any,
        idempotent: bool,
        options: RequestOptions,
    ) -> Tuple[str, Dict[str, Any], Dict[str, str], Optional[bytes]]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"ampost-python/{__version__}",
            **self._default_headers,
            **dict(options.get("extra_headers") or {}),
            "Authorization": f"Bearer {self.__api_key}",
        }
        content = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(body, ensure_ascii=False, default=_json_default).encode("utf-8")
        if idempotent:
            headers["Idempotency-Key"] = cast(IdempotentRequestOptions, options).get("idempotency_key") or str(uuid.uuid4())
        params = {key: _query_value(value) for key, value in (query or {}).items() if value is not None}
        return f"{self.base_url}{path}", params, headers, content

    def _retry_delay(self, error: APIError, attempt: int, repeatable: bool) -> Optional[float]:
        """Seconds to wait before retrying, or None to give up."""
        retryable = (
            error.status == 429  # refused before anything ran
            or error.code == "IDEMPOTENCY_REQUEST_IN_PROGRESS"  # the first request with this key still runs
            or ((error.status >= 500 or error.status == 408) and repeatable)
        )
        if not retryable:
            return None
        retry_after = parse_retry_after(error.headers.get("retry-after"))
        if retry_after is None:
            return _backoff(attempt)
        # A quota reset days away is not worth blocking on.
        return retry_after if retry_after <= self.max_retry_delay else None

    @staticmethod
    def _parse(response: httpx.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise AmpostError(f"Expected JSON from the API, got: {response.text[:200]}") from exc


class Ampost(_BaseClient, _SyncResources):
    """The Ampost API client.

    ::

        from ampost import Ampost

        client = Ampost()  # reads AMPOST_API_KEY
        post = client.posts.create({"text": "Hello", "targets": [{"platforms": ["bluesky"]}], "publishNow": True})
    """

    #: Media uploads, with ``upload()`` on top of the generated methods.
    media: MediaResource

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        max_retries: int = 2,
        timeout: float = 60.0,
        max_retry_delay: float = 60.0,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        """
        :param api_key: An API key from https://ampost.io/app/settings/api-keys. Default: ``AMPOST_API_KEY``.
        :param base_url: Default: ``AMPOST_BASE_URL``, else ``https://api.ampost.io``.
        :param max_retries: Retries after a 429, a 5xx or a network error (see README, "Retries").
        :param timeout: Seconds per attempt.
        :param max_retry_delay: A longer ``Retry-After`` is not waited for: the error is raised.
        :param http_client: Your own ``httpx.Client`` (proxies, transports). You close it.
        """
        super().__init__(api_key, base_url, max_retries, timeout, max_retry_delay, default_headers)
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client()
        self._init_resources()
        self.media = MediaResource(self)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "Ampost":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        body: Any = None,
        idempotent: bool = False,
        options: RequestOptions,
    ) -> Any:
        """Sends one API call with auth, the idempotency key and retries. The generated methods call this."""
        url, params, headers, content = self._prepare(path, query, body, idempotent, options)
        # Without a key, a write that timed out may have been done: only reads and keyed writes repeat.
        repeatable = method == "GET" or idempotent
        max_retries = options.get("max_retries", self.max_retries)
        timeout = options.get("timeout", self.timeout)
        attempt = 0
        while True:
            try:
                response = self._http.request(
                    method, url, params=params, headers=headers, content=content, timeout=timeout
                )
            except httpx.TransportError as exc:
                if not repeatable or attempt >= max_retries:
                    raise _connection_error(exc, timeout) from exc
                time.sleep(_backoff(attempt))
                attempt += 1
                continue
            if response.is_success:
                return self._parse(response)
            error = APIError.from_response(response)
            delay = self._retry_delay(error, attempt, repeatable)
            if delay is None or attempt >= max_retries:
                raise error
            time.sleep(delay)
            attempt += 1

    def _paginate(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        options: RequestOptions,
    ) -> SyncPage[Any]:
        def fetch(offset: int) -> Dict[str, Any]:
            return self._request(method, path, query={**(query or {}), "offset": offset}, options=options)  # type: ignore[no-any-return]

        return SyncPage(self._request(method, path, query=query, options=options), fetch)

    def _send_raw(self, method: str, url: str, *, headers: Mapping[str, str], content: bytes, timeout: float) -> httpx.Response:
        """One request outside the API (the media PUT): no auth header, no retries."""
        try:
            return self._http.request(method, url, headers=headers, content=content, timeout=timeout)
        except httpx.TransportError as exc:
            raise _connection_error(exc, timeout) from exc


class AsyncAmpost(_BaseClient, _AsyncResources):
    """The async Ampost API client: the same methods as ``Ampost``, awaited.

    ::

        async with AsyncAmpost() as client:
            post = await client.posts.create({"text": "Hello", "targets": [{"platforms": ["bluesky"]}]})
            async for account in client.accounts.list():
                print(account["platform"])
    """

    media: AsyncMediaResource

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        max_retries: int = 2,
        timeout: float = 60.0,
        max_retry_delay: float = 60.0,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(api_key, base_url, max_retries, timeout, max_retry_delay, default_headers)
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient()
        self._init_resources()
        self.media = AsyncMediaResource(self)

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "AsyncAmpost":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        body: Any = None,
        idempotent: bool = False,
        options: RequestOptions,
    ) -> Any:
        url, params, headers, content = self._prepare(path, query, body, idempotent, options)
        repeatable = method == "GET" or idempotent
        max_retries = options.get("max_retries", self.max_retries)
        timeout = options.get("timeout", self.timeout)
        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method, url, params=params, headers=headers, content=content, timeout=timeout
                )
            except httpx.TransportError as exc:
                if not repeatable or attempt >= max_retries:
                    raise _connection_error(exc, timeout) from exc
                await asyncio.sleep(_backoff(attempt))
                attempt += 1
                continue
            if response.is_success:
                return self._parse(response)
            error = APIError.from_response(response)
            delay = self._retry_delay(error, attempt, repeatable)
            if delay is None or attempt >= max_retries:
                raise error
            await asyncio.sleep(delay)
            attempt += 1

    def _paginate(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        options: RequestOptions,
    ) -> AsyncPaginator[Any]:
        async def fetch(offset: Optional[int]) -> Dict[str, Any]:
            page_query = dict(query or {}) if offset is None else {**(query or {}), "offset": offset}
            return await self._request(method, path, query=page_query, options=options)  # type: ignore[no-any-return]

        return AsyncPaginator(fetch)

    async def _send_raw(
        self, method: str, url: str, *, headers: Mapping[str, str], content: bytes, timeout: float
    ) -> httpx.Response:
        try:
            return await self._http.request(method, url, headers=headers, content=content, timeout=timeout)
        except httpx.TransportError as exc:
            raise _connection_error(exc, timeout) from exc


def _connection_error(exc: httpx.TransportError, timeout: float) -> APIConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError(f"Request timed out after {timeout} s")
    return APIConnectionError(f"Connection error: {exc}")


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter: about 0.5 s, 1 s, 2 s, ... up to 8 s."""
    return float(min(0.5 * 2**attempt, 8.0) * (0.75 + random.random() * 0.5))


def _query_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return [_query_value(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
