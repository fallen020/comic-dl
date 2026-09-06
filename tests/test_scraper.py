from __future__ import annotations

import asyncio

import pytest
from bs4 import BeautifulSoup

from comic_dl.errors import ScrapeTimeout
from comic_dl.scrapers.sites.pawchive import (
    _extract_images,
    _extract_meta,
    _extract_series_and_chapter,
    _try_full_resolution,
    scrape_post,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, 'lxml')


class TestExtractSeriesAndChapter:
    def test_from_tags(self):
        html = """
        <html>
        <h1 class="post__title"><span>My Comic Part 5</span></h1>
        <section id="post-tags">
            <a>Series Name</a>
            <a>Another Tag</a>
        </section>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series == "Series Name"
        assert chapter == "Chapter 5"

    def test_from_title_when_no_tags(self):
        html = """
        <html>
        <h1 class="post__title"><span>My Series Part 3</span></h1>
        <section id="post-tags"></section>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series == "My Series"
        assert chapter == "Chapter 3"

    def test_no_part_in_title(self):
        html = """
        <html>
        <h1 class="post__title"><span>Some One-Shot</span></h1>
        <section id="post-tags"><a>Series Title</a></section>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series == "Series Title"
        assert chapter == "Some One-Shot"

    def test_alternative_casing(self):
        html = """
        <html>
        <h1 class="post__title"><span>Comic Part 2</span></h1>
        <section id="post-tags"><a>Best Series</a></section>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series == "Best Series"
        assert chapter == "Chapter 2"

    def test_no_title_span(self):
        html = "<html><body></body></html>"
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series in ("", "untitled")
        assert chapter in ("Chapter 1",)

    def test_sanitized_output(self):
        html = """
        <html>
        <h1 class="post__title"><span>Bad: Title/With\\Slash Part 1</span></h1>
        <section id="post-tags"><a>Good Series</a></section>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert "/" not in series
        assert "/" not in chapter
        assert "\\" not in chapter

    def test_title_with_parenthetical_before_part(self):
        """'School Daze (Fanfiction Part 4 of 4) (Patreon)' → series='School Daze', chapter='Chapter 4'."""
        html = """
        <html>
        <h1 class="post__title"><span>School Daze (Fanfiction Part 4 of 4) (Patreon)</span></h1>
        <section id="post-tags"></section>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series == "School Daze"
        assert chapter == "Chapter 4"

    def test_skips_generic_category_tag(self):
        html = """
        <html>
        <h1 class="post__title"><span>School Daze Chapter 1</span></h1>
        <section id="post-tags">
            <a>Fanfiction</a>
            <a>School Daze</a>
        </section>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series == "School Daze"
        assert chapter == "Chapter 1"

    def test_only_generic_category_tags_falls_back_to_title(self):
        html = """
        <html>
        <h1 class="post__title"><span>Real Series Part 2</span></h1>
        <section id="post-tags">
            <a>Fanfiction</a>
            <a>Manga</a>
        </section>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series == "Real Series"
        assert chapter == "Chapter 2"

    def test_meta_series_and_chapter(self):
        html = """
        <html>
        <head>
            <meta name="series" content="Meta Series">
            <meta name="chapter" content="Meta Chapter 5">
        </head>
        <h1 class="post__title"><span>Something Else</span></h1>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series == "Meta Series"
        assert chapter == "Meta Chapter 5"

    def test_meta_chapter_fallback_to_title_missing(self):
        """When series meta exists but chapter meta is missing, fall back entirely."""
        html = """
        <html>
        <head>
            <meta name="series" content="Meta Series">
        </head>
        <h1 class="post__title"><span>Title Part 3</span></h1>
        <section id="post-tags"><a>School Daze</a></section>
        </html>
        """
        soup = _soup(html)
        series, chapter = _extract_series_and_chapter(soup)
        assert series == "School Daze"
        assert chapter == "Chapter 3"


class TestExtractImages:
    def test_basic_extraction(self):
        html = """
        <html>
        <div class="post__files">
            <div class="post__thumbnail">
                <figure><img src="https://x.com/img?f=Page%202.jpg"></figure>
            </div>
            <div class="post__thumbnail">
                <figure><img src="https://x.com/img?f=Page%201.jpg"></figure>
            </div>
        </div>
        </html>
        """
        soup = _soup(html)
        images = _extract_images(soup)
        assert len(images) == 2
        assert images[0].page_number == 1
        assert images[1].page_number == 2

    def test_empty(self):
        html = "<html><body></body></html>"
        soup = _soup(html)
        images = _extract_images(soup)
        assert images == []

    def test_no_src_skipped(self):
        html = """
        <html>
        <div class="post__files">
            <div class="post__thumbnail">
                <figure><img></figure>
            </div>
        </div>
        </html>
        """
        soup = _soup(html)
        images = _extract_images(soup)
        assert images == []

    def test_plain_url_extracts_page_number(self):
        html = """
        <html>
        <div class="post__files">
            <div class="post__thumbnail">
                <figure><img src="https://x.com/path/to/image.jpg"></figure>
            </div>
        </div>
        </html>
        """
        soup = _soup(html)
        images = _extract_images(soup)
        assert len(images) == 1
        assert images[0].page_number == 1
        assert images[0].url == "https://x.com/path/to/image.jpg"

    def test_dedup_by_url(self):
        html = """
        <html>
        <div class="post__files">
            <div class="post__thumbnail">
                <figure><img src="https://x.com/img?f=Page%201.jpg"></figure>
            </div>
            <div class="post__thumbnail">
                <figure><img src="https://x.com/img?f=Page%201.jpg"></figure>
            </div>
            <div class="post__thumbnail">
                <figure><img src="https://x.com/img?f=Page%202.jpg"></figure>
            </div>
        </div>
        </html>
        """
        soup = _soup(html)
        images = _extract_images(soup)
        assert len(images) == 2
        assert images[0].page_number == 1
        assert images[1].page_number == 2


class TestExtractMeta:
    def test_all_meta_tags(self):
        html = """
        <html>
        <head>
            <meta name="service" content="patreon">
            <meta name="user" content="12345">
            <meta name="id" content="67890">
        </head>
        <body></body>
        </html>
        """
        soup = _soup(html)
        service, user_id, post_id = _extract_meta(soup)
        assert service == "patreon"
        assert user_id == "12345"
        assert post_id == "67890"

    def test_no_meta(self):
        html = "<html><body></body></html>"
        soup = _soup(html)
        service, user_id, post_id = _extract_meta(soup)
        assert service == ""
        assert user_id == ""
        assert post_id == ""

    def test_partial_meta(self):
        html = """
        <html>
        <head>
            <meta name="service" content="fanbox">
        </head>
        <body></body>
        </html>
        """
        soup = _soup(html)
        service, user_id, post_id = _extract_meta(soup)
        assert service == "fanbox"
        assert user_id == ""
        assert post_id == ""


class TestScrapePost:
    pytestmark = pytest.mark.asyncio
    async def test_no_images_raises(self):
        html = """
        <html>
        <h1 class="post__title"><span>Test Part 1</span></h1>
        <div class="post__files"></div>
        </html>
        """

        class MockResponse:
            status_code = 200
            text = html

            def raise_for_status(self):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                return MockResponse()

        with pytest.raises(ValueError, match="No images found"):
            await scrape_post("https://pawchive.pw/p/user/1/post/2", MockClient())  # type: ignore

    async def test_pdf_post_raises_with_pdf_hint(self):
        html = """
        <html>
        <h1 class="post__title"><span>PDF Series Part 1</span></h1>
        <div class="post__content">
            <a href="https://cdn.pawchive.pw/files/pdf/book.pdf">PDF</a>
        </div>
        <div class="post__files"></div>
        </html>
        """

        class MockResponse:
            status_code = 200
            text = html

            def raise_for_status(self):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                return MockResponse()

        with pytest.raises(ValueError, match="PDF attachment"):
            await scrape_post("https://pawchive.pw/p/user/1/post/2", MockClient())  # type: ignore

    async def test_no_images_message_mentions_private_login(self):
        html = """
        <html>
        <h1 class="post__title"><span>Test Part 1</span></h1>
        <div class="post__files"></div>
        </html>
        """

        class MockResponse:
            status_code = 200
            text = html

            def raise_for_status(self):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                return MockResponse()

        with pytest.raises(ValueError, match="require login"):
            await scrape_post("https://pawchive.pw/p/user/1/post/2", MockClient())  # type: ignore

    async def test_text_only_post_returns_text_content(self):
        html = """
        <html>
        <head>
            <meta name="service" content="patreon">
            <meta name="user" content="77131681">
            <meta name="id" content="69403234">
        </head>
        <h1 class="post__title"><span>Announcement</span></h1>
        <div class="post__content">
            <p>Hello everyone, this is an announcement.</p>
            <p><b>Chapter 12</b> will be late.</p>
        </div>
        <div class="post__files"></div>
        </html>
        """

        class MockResponse:
            status_code = 200
            text = html

            def raise_for_status(self):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                return MockResponse()

        meta = await scrape_post("https://pawchive.pw/p/user/1/post/2", MockClient())  # type: ignore
        assert meta.images == []
        assert meta.text_content
        assert "announcement" in meta.text_content
        assert "Chapter 12" in meta.text_content

    async def test_successful_scrape(self):
        html = """
        <html>
        <head>
            <meta name="service" content="patreon">
            <meta name="user" content="77131681">
            <meta name="id" content="69403234">
        </head>
        <h1 class="post__title"><span>My Series Part 2</span></h1>
        <section id="post-tags"><a>Series Title</a></section>
        <div class="post__files">
            <div class="post__thumbnail">
                <figure><img src="https://img.pawchive.pw/thumbnail/data/ab/cd/image.jpg"></figure>
            </div>
        </div>
        </html>
        """

        class MockResponse:
            status_code = 200
            text = html

            def raise_for_status(self):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                return MockResponse()

            async def head(self, url, **kwargs):
                class HeadResp:
                    status_code = 404
                    headers = {"content-type": "text/html"}

                return HeadResp()

        meta = await scrape_post("https://pawchive.pw/p/user/1/post/2", MockClient())  # type: ignore
        assert meta.series_title == "Series Title"
        assert meta.chapter_title == "Chapter 2"
        assert len(meta.images) == 1
        assert meta.images[0].url == "https://img.pawchive.pw/thumbnail/data/ab/cd/image.jpg"
        assert meta.images[0].filename == "page_0001.jpg"
        assert meta.service == "patreon"
        assert meta.user_id == "77131681"
        assert meta.post_id == "69403234"

    async def test_http_error(self):
        class MockClient:
            async def get(self, url, **kwargs):
                from curl_cffi.requests import Response as CurlResponse
                from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
                resp = CurlResponse()
                resp.status_code = 404
                raise CurlHTTPError("not found", response=resp)

        from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

        with pytest.raises(CurlHTTPError):
            await scrape_post("https://pawchive.pw/p/user/1/post/2", MockClient())  # type: ignore


class TestTryFullResolution:
    pytestmark = pytest.mark.asyncio

    async def test_returns_full_resolution(self):
        class MockClient:
            async def head(self, url, **kwargs):
                class Resp:
                    status_code = 200

                    @property
                    def headers(self):
                        return {"content-type": "image/jpeg"}

                return Resp()

        result = await _try_full_resolution(
            MockClient(),
            "https://img.pawchive.pw/thumbnail/data/ab/cd/image.jpg",
        )
        assert result == "https://img.pawchive.pw/data/ab/cd/image.jpg"

    async def test_returns_thumbnail_on_not_image(self):
        class MockClient:
            async def head(self, url, **kwargs):
                class Resp:
                    status_code = 200

                    @property
                    def headers(self):
                        return {"content-type": "text/html"}

                return Resp()

        result = await _try_full_resolution(
            MockClient(),
            "https://img.pawchive.pw/thumbnail/data/ab/cd/image.jpg",
        )
        assert result == "https://img.pawchive.pw/thumbnail/data/ab/cd/image.jpg"

    async def test_returns_thumbnail_on_http_error(self):
        class MockClient:
            async def head(self, url, **kwargs):
                raise Exception("http error")

        result = await _try_full_resolution(
            MockClient(),
            "https://img.pawchive.pw/thumbnail/data/ab/cd/image.jpg",
        )
        assert result == "https://img.pawchive.pw/thumbnail/data/ab/cd/image.jpg"

    async def test_passes_through_non_thumbnail_url(self):
        result = await _try_full_resolution(None, "https://example.com/image.jpg")  # type: ignore
        assert result == "https://example.com/image.jpg"


class TestTimeoutGet:
    pytestmark = pytest.mark.asyncio

    @staticmethod
    async def _get_from(monkeypatch, timeout, url="https://pawchive.pw/p/user/1/post/2"):
        from comic_dl.scrapers.base import BaseScraper

        monkeypatch.setattr("comic_dl.scrapers.base.SCRAPE_TIMEOUT", timeout)

        class HangingClient:
            async def get(self, _url, **kwargs):
                await asyncio.sleep(60)
                raise AssertionError("unreachable")

        return await BaseScraper._timeout_get(url, HangingClient())  # type: ignore

    async def test_stalled_request_raises_scrape_timeout(self, monkeypatch):
        with pytest.raises(ScrapeTimeout) as ei:
            await self._get_from(monkeypatch, 0.05)
        assert ei.value.timeout == 0.05
        assert "pawchive.pw" in str(ei.value)

    async def test_scrape_timeout_is_exit_1_and_carry_url(self, monkeypatch):
        from comic_dl.errors import EXIT_ERROR

        with pytest.raises(ScrapeTimeout) as ei:
            await self._get_from(monkeypatch, 0.05)
        assert ei.value.exit_code == EXIT_ERROR
        assert ei.value.url == "https://pawchive.pw/p/user/1/post/2"


class TestTimeoutGetCache:
    pytestmark = pytest.mark.asyncio
    url = "https://kagane.to/manga/foo"

    @pytest.fixture(autouse=True)
    def _isolated_cache(self, tmp_path):
        from comic_dl import cache

        cache.set_cache_dir(tmp_path / "http")
        yield
        cache.set_cache_dir(None)

    def _client(self, calls, *, etag=None, last_modified=None):

        class Resp:
            status_code = 200

            def __init__(self, text):
                self.text = text
                self.content = text.encode()
                self.headers = {"content-type": "text/html"}

            def raise_for_status(self):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                calls.append((url, kwargs))
                if "If-None-Match" in (kwargs.get("headers") or {}) or "If-Modified-Since" in (kwargs.get("headers") or {}):
                    r = Resp("")
                    r.status_code = 304
                    return r
                r = Resp("<html>cached page</html>")
                if etag:
                    r.headers["etag"] = etag
                if last_modified:
                    r.headers["last-modified"] = last_modified
                return r

        return MockClient()

    async def test_fresh_hit_served_without_network(self):
        from comic_dl.scrapers.base import BaseScraper

        calls = []
        client = self._client(calls)
        first = await BaseScraper._timeout_get(self.url, client)
        assert first.text == "<html>cached page</html>"
        assert len(calls) == 1
        second = await BaseScraper._timeout_get(self.url, client)
        assert second.text == "<html>cached page</html>"
        assert len(calls) == 1  # fresh cache hit: no second request

    async def test_stale_entry_sends_conditional_request(self, monkeypatch):
        import time

        from comic_dl import cache
        from comic_dl.scrapers.base import BaseScraper

        calls = []
        client = self._client(calls, etag='"abc"', last_modified="Mon, 01 Jan 2024 00:00:00 GMT")
        await BaseScraper._timeout_get(self.url, client)
        assert len(calls) == 1
        path = cache._entry_path(self.url, "chrome146", {})
        entry = cache._read_entry(path)
        entry["created"] = time.time() - cache.cache_ttl_hours() * 3600 - 1
        cache._write_entry(path, entry)

        resp = await BaseScraper._timeout_get(self.url, client)
        assert resp.text == "<html>cached page</html>"
        assert resp.status_code == 200
        assert len(calls) == 2  # stale -> one conditional request
        sent = calls[1][1].get("headers", {})
        assert sent.get("If-None-Match") == '"abc"'
        assert sent.get("If-Modified-Since") == "Mon, 01 Jan 2024 00:00:00 GMT"

        # 304 refreshed the entry: a follow-up is a fresh hit (no network)
        resp2 = await BaseScraper._timeout_get(self.url, client)
        assert resp2.text == "<html>cached page</html>"
        assert len(calls) == 2

    async def test_cache_bypassed_when_disabled(self, monkeypatch):
        from comic_dl import config
        from comic_dl.scrapers.base import BaseScraper

        config.set_runtime_http(cache=False)
        try:
            calls = []
            client = self._client(calls)
            await BaseScraper._timeout_get(self.url, client)
            await BaseScraper._timeout_get(self.url, client)
            assert len(calls) == 2  # cache off: every call reaches the network
        finally:
            config._RUNTIME_HTTP.pop("cache", None)

    async def test_skips_caching_non_get(self):
        from comic_dl import cache
        from comic_dl.scrapers.base import BaseScraper

        calls = []

        class Resp:
            status_code = 200
            text = "x"
            content = b"x"
            headers = {}

            def raise_for_status(self):
                pass

        class MockClient:
            async def post(self, url, **kwargs):
                calls.append(url)
                return Resp()

        await BaseScraper._timeout_get(self.url, MockClient(), method="POST")
        assert cache._cache_root().is_dir() is False or not any(
            cache._cache_root().glob("*.dat")
        )

    async def test_cache_never_serves_unvalidated_url(self, monkeypatch):
        """Security invariant: the cache is consulted only after
        ``validate_request_url``. Even with a warm entry on disk, a URL that
        no longer passes validation must raise — never return cached bytes."""
        from comic_dl import cache
        from comic_dl.scrapers.base import BaseScraper
        from comic_dl.utils import RequestBlockedError

        cache.store(
            self.url,
            "chrome146",
            {},
            status=200,
            headers={"content-type": "text/html"},
            body=b"<html>cached</html>",
        )
        resp, stale = cache.lookup(self.url, "chrome146", {})
        assert resp is not None and stale is None  # entry is really warm

        async def _block(url):
            raise RequestBlockedError(f"blocked {url!r}")

        monkeypatch.setattr(
            "comic_dl.scrapers.base.validate_request_url_async", _block
        )
        calls = []
        client = self._client(calls)
        with pytest.raises(RequestBlockedError):
            await BaseScraper._timeout_get(self.url, client)
        assert calls == []  # validation fired before the cache was consulted
