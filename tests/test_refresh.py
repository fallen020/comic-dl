"""Stale-link refresher dispatch (offline, no network)."""

from __future__ import annotations

from comic_dl.models import ImageItem
from comic_dl.scrapers.refresh import refresh_image_url, register_image_refresher


def _item(**kwargs):
    base = {
        "url": "https://img.test/old.jpg",
        "page_number": 1,
        "filename": "page_0001.jpg",
        "source_url": "https://refresh-test-unregistered.test/page/1",
    }
    base.update(kwargs)
    return ImageItem(**base)


class TestRefreshDispatch:
    async def test_no_source_url_returns_none(self):
        assert await refresh_image_url(object(), _item(source_url="")) is None

    async def test_unregistered_host_returns_none(self):
        assert await refresh_image_url(object(), _item()) is None

    async def test_same_url_returns_none(self):
        @register_image_refresher("refresh-test-same.test")
        async def _same(client, item):
            return item

        item = _item(source_url="https://refresh-test-same.test/page/1")
        assert await refresh_image_url(object(), item) is None

    async def test_raising_refresher_returns_none(self):
        @register_image_refresher("refresh-test-boom.test")
        async def _boom(client, item):
            raise RuntimeError("nope")

        item = _item(source_url="https://refresh-test-boom.test/page/1")
        assert await refresh_image_url(object(), item) is None

    async def test_fresh_url_returned(self):
        @register_image_refresher("refresh-test-fresh.test")
        async def _fresh(client, item):
            return ImageItem(
                url="https://img.test/new.jpg",
                page_number=item.page_number,
                filename=item.filename,
                source_url=item.source_url,
            )

        item = _item(source_url="https://refresh-test-fresh.test/page/1")
        out = await refresh_image_url(object(), item)
        assert out is not None
        assert out.url == "https://img.test/new.jpg"
