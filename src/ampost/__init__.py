"""The official Python client for the Ampost API: https://ampost.io/docs?ref=github"""

from ._client import DEFAULT_BASE_URL, Ampost, AsyncAmpost
from ._errors import (
    AmpostError,
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InternalServerError,
    MediaUploadError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from ._generated import models
from ._generated.resources import OPERATIONS
from ._media import AsyncMediaResource, MediaResource
from ._pagination import AsyncPage, AsyncPaginator, SyncPage
from ._types import IdempotentRequestOptions, RequestOptions
from ._version import __version__

__all__ = [
    "Ampost",
    "AsyncAmpost",
    "DEFAULT_BASE_URL",
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
    "MediaResource",
    "AsyncMediaResource",
    "SyncPage",
    "AsyncPage",
    "AsyncPaginator",
    "RequestOptions",
    "IdempotentRequestOptions",
    "OPERATIONS",
    "models",
    "__version__",
]
