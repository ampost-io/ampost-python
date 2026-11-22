from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

from ampost import AmpostError, MediaUploadError
from ampost._media import guess_content_type

from .conftest import Recorder, async_client, json_response, sync_client

CREATED = {
    "id": "up_1",
    "status": "pending",
    "uploadPageUrl": "https://ampost.io/upload/up_1#secret",
    "uploadUrl": "https://storage.example.com/put/up_1",
    "method": "PUT",
    "headers": {"x-upload-token": "tok"},
    "limits": {"maxFiles": 1, "maxSizeBytes": 1000, "mimeTypes": ["image/png"]},
    "createdAt": "2026-11-22T00:00:00Z",
    "expiresAt": "2026-11-22T01:00:00Z",
}
FILE = {"url": "https://cdn.example.com/a.png", "type": "image", "mimeType": "image/png", "sizeBytes": 3, "width": 1, "height": 1, "durationSeconds": None}


def status(state: str, files: Any = None, error: Any = None) -> httpx.Response:
    return json_response({"id": "up_1", "status": state, "files": files or [], "createdAt": "", "expiresAt": "", "completedAt": None, "error": error})


class TestUpload:
    def test_creates_puts_the_bytes_with_the_given_headers_only_and_waits(self, sleeps: List[float]) -> None:
        api = Recorder(json_response(CREATED), httpx.Response(200), status("pending"), status("completed", [FILE]))
        media = sync_client(api).media.upload(b"png", file_name="a.png", poll_interval=0.5)
        assert media == FILE
        create, put, *polls = api.requests
        assert json.loads(create.content) == {"fileName": "a.png"}
        assert put.method == "PUT" and str(put.url) == CREATED["uploadUrl"]
        assert put.content == b"png"
        assert put.headers["x-upload-token"] == "tok"
        assert put.headers["x-content-type"] == "image/png"
        assert "authorization" not in put.headers
        assert [p.url.path for p in polls] == ["/api/media/uploads/up_1"] * 2
        assert sleeps == [0.5]

    def test_reads_a_path_and_takes_the_name_and_type_from_it(self, tmp_path: Path) -> None:
        file = tmp_path / "launch.mp4"
        file.write_bytes(b"\x00\x01")
        api = Recorder(json_response(CREATED), httpx.Response(200), status("completed", [FILE]))
        sync_client(api).media.upload(file)
        assert json.loads(api.requests[0].content) == {"fileName": "launch.mp4"}
        assert api.requests[1].headers["x-content-type"] == "video/mp4"
        assert api.requests[1].content == b"\x00\x01"

    def test_refuses_an_unknown_type_before_calling_the_api(self) -> None:
        api = Recorder()
        with pytest.raises(AmpostError, match="content_type"):
            sync_client(api).media.upload(b"x", file_name="notes.txt")
        assert api.requests == []

    def test_raises_when_ampost_refuses_the_file(self) -> None:
        api = Recorder(json_response(CREATED), httpx.Response(200), status("failed", error="not an image"))
        with pytest.raises(MediaUploadError, match="not an image") as caught:
            sync_client(api).media.upload(b"x", file_name="a.png")
        assert (caught.value.reason, caught.value.upload_id) == ("failed", "up_1")

    def test_raises_when_storage_rejects_the_put(self) -> None:
        api = Recorder(json_response(CREATED), httpx.Response(403, text="denied"))
        with pytest.raises(MediaUploadError, match="403") as caught:
            sync_client(api).media.upload(b"x", file_name="a.png")
        assert caught.value.reason == "upload_rejected"

    def test_gives_up_waiting_after_wait_timeout(self) -> None:
        api = Recorder(json_response(CREATED), httpx.Response(200), status("pending"))
        with pytest.raises(MediaUploadError) as caught:
            sync_client(api).media.upload(b"x", file_name="a.png", poll_interval=5, wait_timeout=1)
        assert caught.value.reason == "timeout"

    def test_expired(self) -> None:
        api = Recorder(status("expired"))
        with pytest.raises(MediaUploadError) as caught:
            sync_client(api).media.wait_for_upload("up_1")
        assert caught.value.reason == "expired"

    def test_async_upload(self) -> None:
        api = Recorder(json_response(CREATED), httpx.Response(200), status("completed", [FILE]))

        async def run() -> Dict[str, Any]:
            return dict(await async_client(api).media.upload(b"png", file_name="a.png"))

        assert asyncio.run(run()) == FILE
        assert "authorization" not in api.requests[1].headers

    @pytest.mark.parametrize(
        "name,expected",
        [("a.JPG", "image/jpeg"), ("b.webp", "image/webp"), ("c.mov", "video/quicktime"), ("d.webm", "video/webm"), ("e", None), ("f.txt", None)],
    )
    def test_guesses_common_types(self, name: str, expected: Any) -> None:
        assert guess_content_type(name) == expected
