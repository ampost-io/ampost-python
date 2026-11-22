from __future__ import annotations

import asyncio
import json
from typing import List, Type

import httpx
import pytest

from ampost import (
    Ampost,
    AmpostError,
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
    __version__,
    models,
)

from .conftest import Recorder, api_error, async_client, json_response, sync_client

POST = {"id": "p1", "status": "draft"}
CREATE: models.CreatePostRequest = {"text": "Hi", "targets": [{"platforms": ["bluesky"]}]}


class TestRequests:
    def test_sends_the_key_as_bearer_json_and_user_agent(self) -> None:
        api = Recorder(json_response(POST))
        result = sync_client(api).posts.create({"text": "Hi", "targets": [{"platforms": ["bluesky", "threads"]}]})
        assert result == POST
        request = api.last
        assert request.method == "POST"
        assert str(request.url) == "https://api.ampost.io/api/posts"
        assert request.headers["authorization"] == "Bearer amp_test"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["accept"] == "application/json"
        assert request.headers["user-agent"] == f"ampost-python/{__version__}"
        assert json.loads(request.content) == {"text": "Hi", "targets": [{"platforms": ["bluesky", "threads"]}]}

    def test_serializes_the_query_and_leaves_out_none(self) -> None:
        api = Recorder(json_response({"data": [], "pagination": {"limit": 5, "offset": 0, "total": 0, "hasMore": False}}))
        sync_client(api).posts.list(status="failed", platform_set_id="set 1", limit=5)
        assert api.last.url.params.multi_items() == [("status", "failed"), ("platformSetId", "set 1"), ("limit", "5")]

    def test_maps_python_names_and_booleans(self) -> None:
        api = Recorder(json_response({"data": [], "pagination": {"limit": 5, "offset": 0, "total": 0, "hasMore": False}}))
        sync_client(api).analytics.list_followers(from_="2026-11-01", to="2026-11-02")
        assert api.last.url.params["from"] == "2026-11-01"

    def test_encodes_path_parameters(self) -> None:
        api = Recorder(json_response(POST))
        sync_client(api).posts.get("a/b c")
        assert api.last.url.raw_path == b"/api/posts/a%2Fb%20c"

    def test_rejects_an_empty_path_parameter(self) -> None:
        with pytest.raises(TypeError):
            sync_client(Recorder()).posts.get("")

    def test_reads_key_and_base_url_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AMPOST_API_KEY", "amp_env")
        monkeypatch.setenv("AMPOST_BASE_URL", "http://localhost:3000/")
        api = Recorder(json_response(POST))
        client = Ampost(http_client=httpx.Client(transport=httpx.MockTransport(api)))
        client.posts.get("p1")
        assert str(api.last.url) == "http://localhost:3000/api/posts/p1"
        assert api.last.headers["authorization"] == "Bearer amp_env"

    def test_refuses_to_start_without_a_key(self) -> None:
        with pytest.raises(AmpostError, match="AMPOST_API_KEY"):
            Ampost()

    def test_does_not_show_the_key(self) -> None:
        client = sync_client(Recorder(), api_key="amp_secret_value")
        assert "amp_secret_value" not in repr(client)

    def test_extra_headers_cannot_replace_auth(self) -> None:
        api = Recorder(json_response(POST))
        sync_client(api).posts.get("p1", extra_headers={"X-Trace": "1", "Authorization": "Bearer other"})
        assert api.last.headers["x-trace"] == "1"
        assert api.last.headers["authorization"] == "Bearer amp_test"

    def test_returns_none_for_an_empty_body(self) -> None:
        api = Recorder(httpx.Response(204))
        assert sync_client(api).webhooks.delete("w1") is None

    def test_context_manager_closes_its_own_http_client(self) -> None:
        with Ampost(api_key="k") as client:
            http = client._http
        assert http.is_closed


class TestIdempotency:
    def test_generates_a_key_and_keeps_it_across_retries(self) -> None:
        api = Recorder(api_error(503, "INTERNAL_ERROR"), json_response(POST))
        sync_client(api).posts.create(CREATE)
        keys = [r.headers["idempotency-key"] for r in api.requests]
        assert len(keys) == 2 and keys[0] == keys[1] and len(keys[0]) >= 32

    def test_sends_the_callers_key(self) -> None:
        api = Recorder(json_response(POST))
        sync_client(api).posts.create(CREATE, idempotency_key="release-v1.4.0")
        assert api.last.headers["idempotency-key"] == "release-v1.4.0"

    def test_gives_each_call_its_own_key(self) -> None:
        api = Recorder(json_response(POST), json_response(POST))
        client = sync_client(api)
        client.posts.publish("p1")
        client.posts.publish("p1")
        assert api.requests[0].headers["idempotency-key"] != api.requests[1].headers["idempotency-key"]

    def test_sends_no_key_on_other_operations(self) -> None:
        api = Recorder(json_response(POST))
        sync_client(api).posts.update("p1", {"text": "x"})
        assert "idempotency-key" not in api.last.headers

    def test_waits_while_the_first_request_with_the_key_runs(self, sleeps: List[float]) -> None:
        api = Recorder(api_error(409, "IDEMPOTENCY_REQUEST_IN_PROGRESS", headers={"Retry-After": "1"}), json_response(POST))
        assert sync_client(api).posts.create(CREATE) == POST
        assert sleeps == [1.0]


class TestRetries:
    def test_retries_a_429_after_retry_after_even_for_a_write_without_a_key(self, sleeps: List[float]) -> None:
        api = Recorder(api_error(429, "RATE_LIMITED", headers={"Retry-After": "3"}), json_response(POST))
        assert sync_client(api).posts.update("p1", {"text": "x"}) == POST
        assert len(api.requests) == 2
        assert sleeps == [3.0]

    def test_does_not_wait_for_a_long_retry_after(self, sleeps: List[float]) -> None:
        api = Recorder(api_error(429, "QUOTA_EXCEEDED", headers={"Retry-After": "86400"}))
        with pytest.raises(RateLimitError) as caught:
            sync_client(api).posts.get("p1")
        assert caught.value.retry_after == 86400
        assert caught.value.code == "QUOTA_EXCEEDED"
        assert sleeps == []

    def test_retries_a_5xx_on_a_get_with_backoff(self, sleeps: List[float]) -> None:
        api = Recorder(api_error(502, "PLATFORM_REQUEST_FAILED"), api_error(500, "INTERNAL_ERROR"), json_response(POST))
        assert sync_client(api).posts.get("p1") == POST
        assert len(sleeps) == 2
        assert 0.375 <= sleeps[0] <= 0.625 and 0.75 <= sleeps[1] <= 1.25

    def test_does_not_repeat_a_write_without_a_key_after_a_5xx(self) -> None:
        api = Recorder(api_error(500, "INTERNAL_ERROR"))
        with pytest.raises(InternalServerError):
            sync_client(api).posts.update("p1", {"text": "x"})
        assert len(api.requests) == 1

    def test_retries_network_errors_on_reads_then_gives_up(self) -> None:
        error = httpx.ConnectError("refused")
        api = Recorder(error, error, error)
        with pytest.raises(APIConnectionError, match="refused"):
            sync_client(api).posts.get("p1")
        assert len(api.requests) == 3

    def test_honours_max_retries_per_request(self) -> None:
        api = Recorder(api_error(503, "INTERNAL_ERROR"))
        with pytest.raises(InternalServerError):
            sync_client(api).posts.get("p1", max_retries=0)
        assert len(api.requests) == 1

    def test_passes_the_timeout(self) -> None:
        seen: List[object] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.extensions["timeout"])
            return json_response(POST)

        sync_client(Recorder(handler), timeout=5).posts.get("p1", timeout=1.5)
        assert seen == [{"connect": 1.5, "read": 1.5, "write": 1.5, "pool": 1.5}]


ERRORS: List[tuple[int, Type[APIError]]] = [
    (400, BadRequestError),
    (401, AuthenticationError),
    (403, PermissionDeniedError),
    (404, NotFoundError),
    (409, ConflictError),
    (413, PayloadTooLargeError),
    (422, UnprocessableEntityError),
    (429, RateLimitError),
    (500, InternalServerError),
    (418, APIError),
]


class TestErrors:
    @pytest.mark.parametrize("status,error_class", ERRORS)
    def test_maps_the_status_to_a_class(self, status: int, error_class: Type[APIError]) -> None:
        api = Recorder(api_error(status, "SOME_CODE", "Something is off", headers={"X-Request-Id": "req_1"}))
        with pytest.raises(error_class) as caught:
            sync_client(api, max_retries=0).posts.get("p1")
        error = caught.value
        assert type(error) is error_class
        assert (error.status, error.code, error.message) == (status, "SOME_CODE", "Something is off")
        assert error.details == {"field": "x"}
        assert error.request_id == "req_1"
        assert str(error) == f"{status} SOME_CODE: Something is off"

    def test_reads_string_errors_and_non_json_bodies(self) -> None:
        api = Recorder(json_response({"error": "Unauthorized"}, 401), httpx.Response(400, text="Bad gateway"))
        client = sync_client(api)
        with pytest.raises(AuthenticationError) as caught:
            client.posts.get("p1")
        assert (caught.value.code, caught.value.message) == ("HTTP_401", "Unauthorized")
        with pytest.raises(BadRequestError, match="Bad gateway"):
            client.posts.get("p1")

    def test_parses_an_http_date_retry_after(self) -> None:
        api = Recorder(api_error(429, "RATE_LIMITED", headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), json_response(POST))
        assert sync_client(api).posts.get("p1") == POST

    def test_raises_api_timeout_error(self) -> None:
        api = Recorder(httpx.ReadTimeout("slow"))
        with pytest.raises(APITimeoutError, match="timed out"):
            sync_client(api, max_retries=0).posts.get("p1")

    def test_timeouts_of_writes_without_a_key_are_not_repeated(self) -> None:
        api = Recorder(httpx.ReadTimeout("slow"))
        with pytest.raises(APITimeoutError):
            sync_client(api).posts.update("p1", {"text": "x"})
        assert len(api.requests) == 1


class TestAsync:
    def test_same_request_shape(self) -> None:
        api = Recorder(json_response(POST))

        async def run() -> None:
            async with async_client(api) as client:
                assert await client.posts.create(CREATE, idempotency_key="k1") == POST

        asyncio.run(run())
        assert api.last.headers["authorization"] == "Bearer amp_test"
        assert api.last.headers["idempotency-key"] == "k1"
        assert json.loads(api.last.content) == CREATE

    def test_retries_and_raises_typed_errors(self, sleeps: List[float]) -> None:
        api = Recorder(api_error(429, "RATE_LIMITED", headers={"Retry-After": "2"}), api_error(404, "POST_NOT_FOUND"))

        async def run() -> None:
            with pytest.raises(NotFoundError):
                await async_client(api).posts.get("p1")

        asyncio.run(run())
        assert len(api.requests) == 2
        assert sleeps == [2.0]

    def test_network_errors(self) -> None:
        api = Recorder(httpx.ConnectError("refused"), json_response(POST))

        async def run() -> None:
            assert await async_client(api).posts.get("p1") == POST

        asyncio.run(run())
