from __future__ import annotations

import pytest

from comic_dl.scrapers.sites.pawchive import _probe_full_url, _try_full_resolution


class TestProbeBound:
    pytestmark = pytest.mark.asyncio

    async def test_probes_bounded(self, monkeypatch):
        """Thumbnail HEAD probes share one semaphore, not one burst per post."""
        import asyncio

        state = {"active": 0, "max_active": 0, "calls": 0}

        class HeadResp:
            status_code = 200
            headers = {"content-type": "image/jpeg"}

        async def fake_timeout_get(url, client, method="HEAD"):
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            state["calls"] += 1
            try:
                await asyncio.sleep(0.01)
                return HeadResp()
            finally:
                state["active"] -= 1

        monkeypatch.setattr("comic_dl.scrapers.base.BaseScraper._timeout_get", fake_timeout_get)
        urls = [f"https://pawchive.pw/thumbnail/{i}.jpg" for i in range(12)]
        out = await asyncio.gather(*[_probe_full_url(object(), u) for u in urls])  # type: ignore
        assert out == [u.replace("/thumbnail/", "/") for u in urls]
        assert state["calls"] == 12
        assert state["max_active"] <= 4

    async def test_non_thumbnail_passthrough_skips_fetch(self, monkeypatch):
        """Plain URLs return untouched without any network probe."""
        called = []

        async def fake_timeout_get(url, client, method="HEAD"):
            called.append(url)
            raise AssertionError("must not probe")

        monkeypatch.setattr("comic_dl.scrapers.base.BaseScraper._timeout_get", fake_timeout_get)
        url = "https://pawchive.pw/full/1.jpg"
        assert await _try_full_resolution(object(), url) == url  # type: ignore
        assert called == []
