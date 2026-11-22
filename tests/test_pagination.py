from __future__ import annotations

import asyncio
from typing import Any, List

import pytest

from ampost import AsyncPage, SyncPage

from .conftest import Recorder, async_client, page, sync_client


def offsets(api: Recorder) -> List[Any]:
    return [r.url.params.get("offset") for r in api.requests]


class TestSyncPage:
    def test_returns_the_first_page(self) -> None:
        api = Recorder(page([{"id": "a"}, {"id": "b"}], total=5))
        first = sync_client(api).posts.list(limit=2)
        assert isinstance(first, SyncPage)
        assert first.data == [{"id": "a"}, {"id": "b"}]
        assert first.total == 5 and first.has_next_page() and len(first) == 2
        assert offsets(api) == [None]

    def test_iterates_every_item_moving_the_offset_by_the_page_size(self) -> None:
        api = Recorder(page([1, 2], 0, 5), page([3, 4], 2, 5), page([5], 4, 5))
        assert list(sync_client(api).posts.list(status="failed")) == [1, 2, 3, 4, 5]
        assert offsets(api) == [None, "2", "4"]
        assert all(r.url.params["status"] == "failed" for r in api.requests)

    def test_starts_from_the_callers_offset(self) -> None:
        api = Recorder(page([3, 4], 10, 14), page([5], 12, 13))
        assert list(sync_client(api).accounts.list(offset=10)) == [3, 4, 5]
        assert offsets(api) == ["10", "12"]

    def test_stops_on_an_empty_page_even_if_has_more(self) -> None:
        api = Recorder(page([], 0, 10, has_more=True))
        assert list(sync_client(api).posts.list()) == []

    def test_iter_pages_and_get_next_page(self) -> None:
        api = Recorder(page([1, 2], 0, 3), page([3], 2, 3))
        first = sync_client(api).posts.list()
        pages = list(first.iter_pages())
        assert [p.data for p in pages] == [[1, 2], [3]]
        with pytest.raises(LookupError):
            pages[-1].get_next_page()


class TestAsyncPaginator:
    def test_awaits_to_the_first_page(self) -> None:
        api = Recorder(page([1, 2], 0, 4))

        async def run() -> None:
            first = await async_client(api).posts.list(limit=2)
            assert isinstance(first, AsyncPage)
            assert first.data == [1, 2] and first.has_next_page()

        asyncio.run(run())
        assert offsets(api) == [None]

    def test_async_for_walks_every_page(self) -> None:
        api = Recorder(page([1, 2], 0, 3), page([3], 2, 3))

        async def run() -> List[Any]:
            return [item async for item in async_client(api).posts.list(status="failed")]

        assert asyncio.run(run()) == [1, 2, 3]
        assert offsets(api) == [None, "2"]

    def test_to_list_and_laziness(self) -> None:
        api = Recorder(page([1], 0, 2), page([2], 1, 2))

        async def run() -> List[Any]:
            paginator = async_client(api).platform_sets.list()
            assert api.requests == []
            return await paginator.to_list()

        assert asyncio.run(run()) == [1, 2]
