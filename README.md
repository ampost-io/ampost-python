# Ampost Python SDK

The official Python client for the [Ampost API](https://ampost.io/docs?ref=github): publish, schedule
and track posts on Instagram, Facebook, Threads, X, YouTube, Pinterest, Bluesky, Telegram and Discord
with one call.

- Typed requests and responses (`TypedDict`s) for every endpoint, generated from the [OpenAPI document](https://api.ampost.io/openapi.json)
- Sync and async clients on [httpx](https://www.python-httpx.org)
- Retries on 429 and 5xx with `Retry-After`, automatic `Idempotency-Key`s, typed errors
- Pagination by iteration and a one-call media upload
- Python 3.9+

## Install

```sh
pip install git+https://github.com/ampost-io/ampost-python
```

The package is coming to PyPI as `ampost`; until then, install it from this repo as above.

## Quick start

Create an API key in [Settings, API keys](https://ampost.io/app/settings/api-keys?ref=github), connect
Bluesky and Threads in the [dashboard](https://ampost.io/app?ref=github), then:

```python
import os
from ampost import Ampost

client = Ampost(api_key=os.environ["AMPOST_API_KEY"])

post = client.posts.create({
    "text": "Hello from the Ampost API 👋",
    "targets": [{"platforms": ["bluesky", "threads"]}],
    "publishNow": True,
})
print(post["id"], post["status"], [(p["platform"], p["status"]) for p in post["platformPosts"]])
```

Leave out `publishNow` for a draft, or set `"scheduledFor": "2026-12-01T09:00:00Z"` to schedule it
(paid plans). Request bodies and responses are plain dicts with the API's field names; your editor and
mypy check them against the typed models in `ampost.models`.

The async client has the same methods:

```python
import asyncio
from ampost import AsyncAmpost

async def main() -> None:
    async with AsyncAmpost() as client:
        post = await client.posts.create({"text": "Hello", "targets": [{"platforms": ["bluesky"]}]})
        print(post["id"])

asyncio.run(main())
```

## Authentication

Every request sends the key as `Authorization: Bearer <key>`. Without `api_key` the client reads
`AMPOST_API_KEY`, and `AMPOST_BASE_URL` overrides `https://api.ampost.io`.

```python
client = Ampost(
    api_key="amp_...",
    max_retries=2,   # after a 429, a 5xx or a network error
    timeout=60.0,    # seconds per attempt
)
```

A key has scopes (`posts:write`, `analytics:read`, ...) and can be limited to some platform sets. A
call outside them raises `PermissionDeniedError` with `code` `INSUFFICIENT_SCOPE` or
`PLATFORM_SET_NOT_ALLOWED`. See [scopes](https://ampost.io/docs?ref=github#scopes).

Use the client as a context manager (`with Ampost() as client:`) or call `client.close()`. To set
proxies or transports, pass your own `http_client=httpx.Client(...)`; you close that one yourself.

## Errors

Every API error is `{"error": {"code", "message", "details"}}`, raised as a subclass of `APIError`:

```python
from ampost import APIError, BadRequestError, RateLimitError

try:
    client.posts.create({"text": "x" * 500, "targets": [{"platforms": ["bluesky"]}]})
except BadRequestError as error:
    print(error.code, error.details)  # CONTENT_TOO_LONG, ...
except RateLimitError as error:
    print(f"retry in {error.retry_after} s")
except APIError as error:
    print(error.status, error.code, error.request_id)
```

| Status | Class | Typical `code` |
| ------ | ----- | -------------- |
| 400 | `BadRequestError` | `VALIDATION_FAILED`, `CONTENT_TOO_LONG`, `PLATFORM_NOT_CONNECTED` |
| 401 | `AuthenticationError` | `UNAUTHORIZED` |
| 403 | `PermissionDeniedError` | `INSUFFICIENT_SCOPE`, `UPGRADE_REQUIRED`, `PLATFORM_COMING_SOON` |
| 404 | `NotFoundError` | `POST_NOT_FOUND`, `ACCOUNT_NOT_FOUND` |
| 409 | `ConflictError` | `INVALID_STATUS_TRANSITION`, `IDEMPOTENCY_KEY_REUSED` |
| 413 | `PayloadTooLargeError` | `PAYLOAD_TOO_LARGE` |
| 422 | `UnprocessableEntityError` | `ANALYTICS_NOT_AVAILABLE_ON_PLATFORM` |
| 429 | `RateLimitError` | `RATE_LIMITED`, `QUOTA_EXCEEDED` |
| 5xx | `InternalServerError` | `INTERNAL_ERROR`, `PLATFORM_REQUEST_FAILED` |

No response at all is `APIConnectionError` (`APITimeoutError` after `timeout`). All of them extend
`AmpostError`. Quote `error.request_id` when you contact support. Every code is listed in
[the docs](https://ampost.io/docs?ref=github#errors).

A post that was accepted can still fail on one platform: check `post["platformPosts"][i]["status"]` and
`["error"]`, or subscribe to [webhooks](https://ampost.io/docs?ref=github#webhooks).

## Idempotency

`posts.create`, `posts.publish`, `posts.retry`, `posts.import_csv`, `accounts.connect`,
`platform_sets.create`, `webhooks.create` and `feedback.submit` take an `Idempotency-Key`. The SDK
sends a new key with each call and keeps it across that call's retries, so a retried request never
posts twice. To stay safe across restarts too (a cron job, a queue worker), pass your own key:

```python
client.posts.create(
    {"text": "v1.4.0 is out", "targets": [{"platforms": ["bluesky"]}], "publishNow": True},
    idempotency_key="release-v1.4.0",
)
```

The same key with the same request returns the first result for 24 hours; with a different request
it is `ConflictError` `IDEMPOTENCY_KEY_REUSED`. See
[idempotency](https://ampost.io/docs?ref=github#idempotency).

## Retries

| What happened | Retried? |
| ------------- | -------- |
| 429 `RATE_LIMITED`, `QUOTA_EXCEEDED` | Yes, after `Retry-After`, if it is under `max_retry_delay` (60 s) |
| 409 `IDEMPOTENCY_REQUEST_IN_PROGRESS` | Yes |
| 5xx, timeout, network error | Reads and calls with an `Idempotency-Key` only |
| Anything else | No |

Waits without `Retry-After` back off from 0.5 s to 8 s. Set `max_retries` on the client or per call:
`client.posts.get(post_id, max_retries=0)`. Every method also takes `timeout` and `extra_headers`.

## Pagination

List methods return the first page. Iterate it to get every item, page after page:

```python
page = client.posts.list(status="failed", limit=50)
print(page.data, page.total, page.has_next_page())

for post in client.posts.list(status="failed"):
    client.posts.retry(post["id"])
```

With `AsyncAmpost`, await the call for one page or `async for` over it for every item:

```python
page = await client.posts.list(limit=50)
async for account in client.accounts.list():
    print(account["platform"])
accounts = await client.accounts.list().to_list()
```

Query parameters are keyword arguments in snake_case: `platform_set_id=`, and `from_=` for `from`.

## Media upload

`media.upload()` takes a path or bytes, sends the file straight to storage (your API key is not sent
there), waits until Ampost has checked it and returns a `media` item:

```python
video = client.media.upload("launch.mp4")
client.posts.create({
    "text": "We launched 🚀",
    "media": [video],
    "targets": [{"platforms": ["youtube", "threads"]}],
    "options": {"youtube": {"title": "Launch day"}},
})
```

Public URLs work without an upload: `"media": [{"url": "https://example.com/a.jpg", "type": "image"}]`.
For files on a user's device, show `uploadPageUrl` from `media.create_upload()` and call
`media.wait_for_upload(upload_id)`. See [media upload](https://ampost.io/docs?ref=github#media-upload).

## Everything else

Each tag of the API is an attribute: `posts`, `accounts`, `platform_sets` (and posting queues),
`media`, `analytics`, `webhooks`, `platforms`, `docs`, `feedback`. The types of every request and
response are in `ampost.models`:

```python
from ampost import models

def caption(post: models.Post) -> str:
    return post["text"]
```

`client.platforms.list()` returns each platform's limits and options, and `client.posts.validate(body)`
checks a post without creating it.

AI agents can use the same API through the hosted MCP server at `https://api.ampost.io/mcp`
([setup](https://ampost.io/docs/mcp?ref=github)).

## Development

The code in `src/ampost/_generated/` comes from `openapi.json`; never edit it by hand. With
[uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run python scripts/generate.py                                              # from the committed openapi.json
uv run python scripts/generate.py --spec https://api.ampost.io/openapi.json    # refresh the snapshot, then generate
uv run pytest && uv run mypy
AMPOST_API_KEY=amp_... uv run pytest tests/test_smoke.py                       # reads and validates only
```

Why this generator: openapi-python-client and OpenAPI Generator emit a model class per schema and a
module per operation, with their own serialization layer; the result is large and reads unlike the
API docs. `scripts/generate.py` uses only the standard library and turns the spec into `TypedDict`s
(so requests and responses stay the plain JSON dicts the docs show) and one method per operation.
The hand-written client (`_client.py`) adds auth, retries, idempotency, errors and pagination on
httpx, the only runtime dependency besides `typing_extensions`.

## Links

- [API docs](https://ampost.io/docs?ref=github) and [llms.txt](https://ampost.io/llms.txt?ref=github)
- [MCP server](https://ampost.io/docs/mcp?ref=github)
- [Pricing](https://ampost.io/pricing?ref=github)
- [Node.js SDK](https://github.com/ampost-io/ampost-node)

## License

MIT
