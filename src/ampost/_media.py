from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Optional, Tuple, Union

from typing_extensions import Unpack

from ._errors import AmpostError, MediaUploadError
from ._generated import models
from ._generated.resources import AsyncMedia, Media
from ._types import RequestOptions

if TYPE_CHECKING:
    from ._client import Ampost, AsyncAmpost

__all__ = ["MediaResource", "AsyncMediaResource", "FileSource", "guess_content_type"]

#: A file to upload: a path, or the bytes.
FileSource = Union[str, "os.PathLike[str]", bytes, bytearray, memoryview]

_MIME_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
}

#: Large videos take a while to send; the API's per-request timeout would be too short.
_UPLOAD_TIMEOUT = 15 * 60.0


class MediaResource(Media):
    """``media`` operations, plus ``upload()`` and ``wait_for_upload()``."""

    _client: "Ampost"

    def upload(
        self,
        file: FileSource,
        *,
        file_name: Optional[str] = None,
        content_type: Optional[str] = None,
        poll_interval: float = 2.0,
        wait_timeout: float = 120.0,
        **options: Unpack[RequestOptions],
    ) -> models.MediaInput:
        """Uploads one image or video and returns it as a ``media`` item for ``posts.create``::

            video = client.media.upload("launch.mp4")
            client.posts.create({"text": "We launched", "media": [video], "targets": [{"platforms": ["youtube"]}]})

        Steps: ``create_upload``, a PUT of the bytes to the returned ``uploadUrl`` (straight to storage; your
        API key is not sent there), then ``get_upload`` until Ampost has checked the file.

        :param file: A path, or the file's bytes.
        :param file_name: Default: the path's name, else ``file``.
        :param content_type: MIME type, for example ``video/mp4``. Default: guessed from the file name.
        :param wait_timeout: Seconds to wait for processing before raising ``MediaUploadError("timeout")``.
        """
        content, name, mime = _read(file, file_name, content_type)
        created = self.create_upload({"fileName": name}, **options)
        response = self._client._send_raw(
            created["method"],
            created["uploadUrl"],
            headers={**created["headers"], "x-content-type": mime},
            content=content,
            timeout=options.get("timeout", _UPLOAD_TIMEOUT),
        )
        if not response.is_success:
            raise MediaUploadError(
                f"Storage refused the file: HTTP {response.status_code} {response.text[:300]}".strip(),
                "upload_rejected",
                created["id"],
            )
        upload = self.wait_for_upload(created["id"], poll_interval=poll_interval, wait_timeout=wait_timeout, **options)
        return _first_file(upload)

    def wait_for_upload(
        self,
        upload_id: str,
        *,
        poll_interval: float = 2.0,
        wait_timeout: float = 120.0,
        **options: Unpack[RequestOptions],
    ) -> models.MediaUpload:
        """Polls an upload until it is ``completed`` and returns it. Raises ``MediaUploadError`` when it
        ``failed`` or ``expired``, or is still pending after ``wait_timeout`` seconds. Use it after showing
        ``uploadPageUrl`` to a user."""
        deadline = time.monotonic() + wait_timeout
        while True:
            upload = self.get_upload(upload_id, **options)
            _check_status(upload, upload_id)
            if upload["status"] == "completed":
                return upload
            if time.monotonic() + poll_interval > deadline:
                raise _timeout(upload_id)
            time.sleep(poll_interval)


class AsyncMediaResource(AsyncMedia):
    """``media`` operations, plus ``upload()`` and ``wait_for_upload()`` (async)."""

    _client: "AsyncAmpost"

    async def upload(
        self,
        file: FileSource,
        *,
        file_name: Optional[str] = None,
        content_type: Optional[str] = None,
        poll_interval: float = 2.0,
        wait_timeout: float = 120.0,
        **options: Unpack[RequestOptions],
    ) -> models.MediaInput:
        """See ``Ampost.media.upload``. Reading a path blocks the event loop briefly; pass bytes to avoid it."""
        content, name, mime = _read(file, file_name, content_type)
        created = await self.create_upload({"fileName": name}, **options)
        response = await self._client._send_raw(
            created["method"],
            created["uploadUrl"],
            headers={**created["headers"], "x-content-type": mime},
            content=content,
            timeout=options.get("timeout", _UPLOAD_TIMEOUT),
        )
        if not response.is_success:
            raise MediaUploadError(
                f"Storage refused the file: HTTP {response.status_code} {response.text[:300]}".strip(),
                "upload_rejected",
                created["id"],
            )
        upload = await self.wait_for_upload(
            created["id"], poll_interval=poll_interval, wait_timeout=wait_timeout, **options
        )
        return _first_file(upload)

    async def wait_for_upload(
        self,
        upload_id: str,
        *,
        poll_interval: float = 2.0,
        wait_timeout: float = 120.0,
        **options: Unpack[RequestOptions],
    ) -> models.MediaUpload:
        deadline = time.monotonic() + wait_timeout
        while True:
            upload = await self.get_upload(upload_id, **options)
            _check_status(upload, upload_id)
            if upload["status"] == "completed":
                return upload
            if time.monotonic() + poll_interval > deadline:
                raise _timeout(upload_id)
            await asyncio.sleep(poll_interval)


def guess_content_type(file_name: str) -> Optional[str]:
    _, dot, extension = file_name.rpartition(".")
    return _MIME_TYPES.get(extension.lower()) if dot else None


def _read(file: FileSource, file_name: Optional[str], content_type: Optional[str]) -> Tuple[bytes, str, str]:
    name: Optional[str] = None
    if isinstance(file, (str, os.PathLike)):
        path = os.fspath(file)
        with open(path, "rb") as handle:
            content = handle.read()
        name = os.path.basename(path)
    elif isinstance(file, (bytes, bytearray, memoryview)):
        content = bytes(file)
    else:
        raise TypeError("upload() takes a file path or bytes")
    name = file_name or name or "file"
    mime = content_type or guess_content_type(name)
    if not mime:
        raise AmpostError(f"Cannot tell the MIME type of {name!r}: pass content_type=")
    return content, name, mime


def _check_status(upload: models.MediaUpload, upload_id: str) -> None:
    if upload["status"] == "failed":
        raise MediaUploadError(f"The upload failed: {upload.get('error') or 'the file was refused'}", "failed", upload_id)
    if upload["status"] == "expired":
        raise MediaUploadError("The upload expired before a file arrived; create a new one", "expired", upload_id)


def _timeout(upload_id: str) -> MediaUploadError:
    return MediaUploadError("The upload is still being processed; check it later with get_upload", "timeout", upload_id)


def _first_file(upload: models.MediaUpload) -> models.MediaInput:
    if not upload["files"]:
        raise MediaUploadError("The upload completed without a file", "failed", upload["id"])
    file = upload["files"][0]
    # The same keys, typed as the posts.create input (TypedDicts do not convert on their own).
    return {
        "url": file["url"],
        "type": file["type"],
        "mimeType": file["mimeType"],
        "sizeBytes": file["sizeBytes"],
        "width": file["width"],
        "height": file["height"],
        "durationSeconds": file["durationSeconds"],
    }
