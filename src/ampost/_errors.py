from __future__ import annotations

import email.utils
import time
from typing import Any, Dict, Mapping, Optional, Type

import httpx

__all__ = [
    "AmpostError",
    "APIError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "PayloadTooLargeError",
    "UnprocessableEntityError",
    "RateLimitError",
    "InternalServerError",
    "APIConnectionError",
    "APITimeoutError",
    "MediaUploadError",
]


class AmpostError(Exception):
    """Base class of every error the SDK raises."""


class APIError(AmpostError):
    """The API answered with an error status.

    The body is ``{"error": {"code", "message", "details"}}``: ``code`` is stable (for example
    ``VALIDATION_FAILED``, ``POST_NOT_FOUND``); the full list is at https://ampost.io/docs?ref=github#errors.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]],
        headers: Mapping[str, str],
    ) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = httpx.Headers(headers)
        #: ``X-Request-Id``: quote it when you contact support.
        self.request_id: Optional[str] = self.headers.get("x-request-id")

    @classmethod
    def from_response(cls, response: httpx.Response) -> "APIError":
        code, message, details = _parse_error_body(response)
        return _error_class(response.status_code)(response.status_code, code, message, details, response.headers)


class BadRequestError(APIError):
    """400: the request is invalid (``VALIDATION_FAILED``, ``CONTENT_TOO_LONG``, ...). ``details["issues"]`` lists the problems."""


class AuthenticationError(APIError):
    """401: the API key is missing, wrong or revoked."""


class PermissionDeniedError(APIError):
    """403: the key lacks a scope, the plan lacks a feature, or the account is suspended."""


class NotFoundError(APIError):
    """404: no such resource belongs to you."""


class ConflictError(APIError):
    """409: the resource's state does not allow it, or an idempotency key was reused."""


class PayloadTooLargeError(APIError):
    """413: the request body is too large; upload files with ``client.media.upload()``."""


class UnprocessableEntityError(APIError):
    """422: the request is well formed but cannot be done (for example analytics on Discord)."""


class RateLimitError(APIError):
    """429: ``RATE_LIMITED`` or ``QUOTA_EXCEEDED``. Retried automatically when ``Retry-After`` is short."""

    @property
    def retry_after(self) -> Optional[float]:
        """Seconds the API asked to wait (``Retry-After``)."""
        return parse_retry_after(self.headers.get("retry-after"))


class InternalServerError(APIError):
    """5xx: something failed on Ampost's side or at a platform."""


class APIConnectionError(AmpostError):
    """No response: DNS, TCP, TLS or a reset connection."""


class APITimeoutError(APIConnectionError):
    """No response within ``timeout``."""


class MediaUploadError(AmpostError):
    """``media.upload()`` did not end with a usable file.

    ``reason``: ``upload_rejected`` (storage refused the PUT), ``failed`` or ``expired`` (the upload's status),
    or ``timeout`` (still processing).
    """

    def __init__(self, message: str, reason: str, upload_id: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.upload_id = upload_id


_STATUS_CLASSES: Dict[int, Type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    413: PayloadTooLargeError,
    422: UnprocessableEntityError,
    429: RateLimitError,
}


def _error_class(status: int) -> Type[APIError]:
    if status >= 500:
        return InternalServerError
    return _STATUS_CLASSES.get(status, APIError)


def _parse_error_body(response: httpx.Response) -> "tuple[str, str, Optional[Dict[str, Any]]]":
    fallback_code = f"HTTP_{response.status_code}"
    fallback_message = response.reason_phrase or "Request failed"
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return fallback_code, (text[:500] if text else fallback_message), None
    if not isinstance(body, dict) or "error" not in body:
        return fallback_code, fallback_message, None
    error = body["error"]
    # A few edge routes answer {"error": "message"}.
    if isinstance(error, str):
        return fallback_code, error, None
    if not isinstance(error, dict):
        return fallback_code, fallback_message, None
    code = error.get("code")
    message = error.get("message")
    details = error.get("details")
    return (
        code if isinstance(code, str) else fallback_code,
        message if isinstance(message, str) else fallback_message,
        details if isinstance(details, dict) else None,
    )


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """``Retry-After`` in seconds: delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed.timestamp() - time.time())
