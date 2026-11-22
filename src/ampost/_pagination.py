from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Generator, Generic, Iterator, List, Optional, TypeVar

T = TypeVar("T")

__all__ = ["SyncPage", "AsyncPage", "AsyncPaginator"]


class _PageBase(Generic[T]):
    def __init__(self, response: Dict[str, Any]) -> None:
        #: The raw response: ``{"data": [...], "pagination": {...}}``.
        self.response = response
        self.data: List[T] = response["data"]
        #: ``limit``, ``offset``, ``total`` and ``has_more`` as the API sends them (``hasMore``).
        self.pagination: Dict[str, Any] = response["pagination"]

    @property
    def total(self) -> int:
        return int(self.pagination["total"])

    def has_next_page(self) -> bool:
        return bool(self.pagination.get("hasMore")) and len(self.data) > 0

    def _next_offset(self) -> int:
        return int(self.pagination["offset"]) + len(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(data=<{len(self.data)} items>, pagination={self.pagination!r})"


class SyncPage(_PageBase[T]):
    """One page of a list. ``page.data`` is this page; iterating the page walks every item across pages::

        for post in client.posts.list(status="failed"):
            ...
    """

    def __init__(self, response: Dict[str, Any], fetch: Callable[[int], Dict[str, Any]]) -> None:
        super().__init__(response)
        self._fetch = fetch

    def get_next_page(self) -> "SyncPage[T]":
        if not self.has_next_page():
            raise LookupError("No next page")
        return SyncPage(self._fetch(self._next_offset()), self._fetch)

    def iter_pages(self) -> Iterator["SyncPage[T]"]:
        page: SyncPage[T] = self
        while True:
            yield page
            if not page.has_next_page():
                return
            page = page.get_next_page()

    def __iter__(self) -> Iterator[T]:
        for page in self.iter_pages():
            yield from page.data


class AsyncPage(_PageBase[T]):
    """One page of a list (async). ``async for`` over it walks every item across pages."""

    def __init__(self, response: Dict[str, Any], fetch: Callable[[int], Awaitable[Dict[str, Any]]]) -> None:
        super().__init__(response)
        self._fetch = fetch

    async def get_next_page(self) -> "AsyncPage[T]":
        if not self.has_next_page():
            raise LookupError("No next page")
        return AsyncPage(await self._fetch(self._next_offset()), self._fetch)

    async def iter_pages(self) -> AsyncIterator["AsyncPage[T]"]:
        page: AsyncPage[T] = self
        while True:
            yield page
            if not page.has_next_page():
                return
            page = await page.get_next_page()

    async def __aiter__(self) -> AsyncIterator[T]:
        async for page in self.iter_pages():
            for item in page.data:
                yield item


class AsyncPaginator(Generic[T]):
    """What async list methods return. Await it for one page, or iterate it for every item::

        page = await client.posts.list(limit=50)
        async for post in client.posts.list(status="failed"):
            ...
    """

    def __init__(self, fetch: Callable[[Optional[int]], Awaitable[Dict[str, Any]]]) -> None:
        self._fetch = fetch

    async def _first(self) -> AsyncPage[T]:
        return AsyncPage(await self._fetch(None), self._fetch)

    def __await__(self) -> Generator[Any, None, AsyncPage[T]]:
        return self._first().__await__()

    async def __aiter__(self) -> AsyncIterator[T]:
        page = await self._first()
        async for item in page:
            yield item

    async def to_list(self) -> List[T]:
        """Collects every item. Mind large accounts: prefer ``async for``."""
        return [item async for item in self]
