from __future__ import annotations

import asyncio

import pytest

from comic_dl.scrapers.base import SeriesDataCache


class TestSeriesDataCache:
    pytestmark = pytest.mark.asyncio

    async def test_concurrent_same_key_loads_once(self):
        """Eight chapters racing one slug share a single series-page fetch."""
        cache = SeriesDataCache()
        calls = [0]

        async def loader():
            calls[0] += 1
            await asyncio.sleep(0.01)
            return {"title": "S"}

        results = await asyncio.gather(*(cache.get("slug", loader) for _ in range(8)))
        assert calls[0] == 1
        assert all(r is results[0] for r in results)

    async def test_cached_result_reused(self):
        cache = SeriesDataCache()
        calls = [0]

        async def loader():
            calls[0] += 1
            return {"title": "S"}

        first = await cache.get("slug", loader)
        second = await cache.get("slug", loader)
        assert calls[0] == 1
        assert second is first
        assert "slug" in cache

    async def test_loader_failure_cached_as_empty(self):
        """A dead series page is fetched once per key, not once per chapter."""
        cache = SeriesDataCache()
        calls = [0]

        async def failing():
            calls[0] += 1
            raise RuntimeError("down")

        assert await cache.get("slug", failing) == {}
        assert await cache.get("slug", failing) == {}
        assert calls[0] == 1

    async def test_different_keys_load_concurrently(self):
        """Per-key locks: distinct slugs never serialize each other.

        Each loader waits for proof the other is inside its own loader; a
        shared lock would deadlock this instead. The timeout only fires on
        failure, so there is no timing flake.
        """
        cache = SeriesDataCache()
        a_inside = asyncio.Event()
        b_inside = asyncio.Event()

        async def loader_a():
            a_inside.set()
            await asyncio.wait_for(b_inside.wait(), 5)
            return {"a": 1}

        async def loader_b():
            b_inside.set()
            await asyncio.wait_for(a_inside.wait(), 5)
            return {"b": 2}

        a, b = await asyncio.gather(cache.get("x", loader_a), cache.get("y", loader_b))
        assert (a, b) == ({"a": 1}, {"b": 2})
