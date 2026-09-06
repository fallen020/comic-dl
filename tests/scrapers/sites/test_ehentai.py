from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from comic_dl.models import ImageItem
from comic_dl.scrapers.sites.ehentai import (
    EHentaiScraper,
    _decode_json,
    _decode_text,
    _extract_bracket_prefix,
    _extract_series_chapter,
    _extract_tag_metadata,
    _fetch_gallery_page,
    _image_page_url,
    _iter_image_items,
    scrape_ehentai,
)


class TestExtractSeriesChapter:
    def test_with_part_number(self):
        s, c = _extract_series_chapter("My Series Part 5")
        assert c == "Chapter 5"
        assert "Series" in s
        assert "Part" not in s

    def test_no_part(self):
        s, c = _extract_series_chapter("Some Title")
        assert c == "Some Title"
        assert s == "Some Title"

    def test_leading_brackets(self):
        s, c = _extract_series_chapter("[Artist] Series Part 3")
        assert c == "Chapter 3"
        assert "Artist" not in s

    def test_trailing_brackets(self):
        _s, c = _extract_series_chapter("Series Part 2 [Digital]")
        assert c == "Chapter 2"
        assert "[Digital]" not in c

    def test_dash_separator(self):
        _s, c = _extract_series_chapter("Series - Chapter 1")
        assert "Chapter 1" in c

    def test_with_chapter_number(self):
        s, c = _extract_series_chapter("Gender Quota Chapter 1")
        assert c == "Chapter 1"
        assert "Chapter" not in s

    def test_lowercase_chapter(self):
        s, c = _extract_series_chapter("Series chapter 5")
        assert c == "Chapter 5"
        assert "chapter" not in s

    def test_chapter_with_hash(self):
        s, c = _extract_series_chapter("Series Chapter #10")
        assert c == "Chapter 10"
        assert "Chapter" not in s

    def test_empty_returns_untitled(self):
        s, c = _extract_series_chapter("")
        assert s in ("Untitled", "untitled")
        assert c in ("Untitled", "untitled")

    def test_sanitized_output(self):
        s, c = _extract_series_chapter("Bad: Title/With\\Chars")
        assert "/" not in s
        assert "\\" not in c

    def test_parenthetical_before_part(self):
        s, c = _extract_series_chapter("School Daze (Fanfiction Part 4 of 4) (Patreon)")
        assert s == "School Daze"
        assert c == "Chapter 4"

    def test_artist_bracket_and_trailing_tags(self):
        s, c = _extract_series_chapter(
            "[Sathorix] Family Of Two (In The Limbo) [AI Generated]"
        )
        assert s == "Family Of Two (In The Limbo)"
        assert c == "Family Of Two (In The Limbo)"

    def test_patreon_suffix_stripped(self):
        s, c = _extract_series_chapter("Asuna feminizes her boyfriend. (Patreon)")
        assert s == "Asuna feminizes her boyfriend"
        assert c == "Asuna feminizes her boyfriend"


class TestBracketPrefix:
    def test_group_only(self):
        g, a = _extract_bracket_prefix("[Circle Name] Some Title")
        assert g == "Circle Name"
        assert a is None

    def test_group_with_artist(self):
        g, a = _extract_bracket_prefix("[Trans Tribune (Wataya)] Dansei shikkaku [English]")
        assert g == "Trans Tribune"
        assert a == "Wataya"

    def test_no_bracket(self):
        g, a = _extract_bracket_prefix("Plain Title")
        assert g is None
        assert a is None

    def test_artist_only_bracket(self):
        g, a = _extract_bracket_prefix("[Wataya] Some Title")
        assert g == "Wataya"
        assert a is None

    def test_empty_title(self):
        g, a = _extract_bracket_prefix("")
        assert g is None
        assert a is None


class TestExtractTagMetadata:
    def test_basic_extraction(self):
        tags = ["artist:foo", "language:english", "full color"]
        artists, genres, language = _extract_tag_metadata(tags)
        assert artists == ["foo"]
        assert language == "en"
        assert genres == ["full color"]

    def test_language_iso_mapping(self):
        assert _extract_tag_metadata(["language:japanese"])[2] == "ja"
        assert _extract_tag_metadata(["language:Korean"])[2] == "ko"
        assert _extract_tag_metadata(["language:chinese"])[2] == "zh"

    def test_non_language_tag_not_leaked(self):
        artists, genres, language = _extract_tag_metadata(
            ["language:textless narrative"]
        )
        assert language is None
        assert artists == []
        assert genres == []

    def test_namespace_filtering(self):
        tags = [
            "artist:a",
            "parody:naruto",
            "character:sakura",
            "group:x",
            "female:big breasts",
            "action",
        ]
        artists, genres, language = _extract_tag_metadata(tags)
        assert genres == ["action"]
        assert artists == ["a"]
        assert language is None

    def test_duplicate_removal(self):
        tags = ["artist:a", "artist:a", "action", "action"]
        artists, genres, _language = _extract_tag_metadata(tags)
        assert artists == ["a"]
        assert genres == ["action"]

    def test_empty_tags(self):
        artists, genres, language = _extract_tag_metadata([])
        assert artists == []
        assert genres == []
        assert language is None

    def test_non_namespaced_tag_with_colon(self):
        tags = ["misc:anthology", "full color"]
        _artists, genres, _language = _extract_tag_metadata(tags)
        assert genres == ["anthology", "full color"]

    def test_first_language_wins(self):
        tags = ["language:english", "language:translated"]
        _artists, _genres, language = _extract_tag_metadata(tags)
        assert language == "en"


class TestImagePageUrl:
    pytestmark = pytest.mark.asyncio
    async def test_bs4_parsing(self):
        """Verify _image_page_url uses BeautifulSoup (Bug F regression)."""

        class MockResponse:
            status_code = 200
            text = (
                '<html><body><img id="img" src="https://ehgt.org/123.jpg?abc"></body></html>'
            )

            def raise_for_status(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                return MockResponse()

        import asyncio

        result = await _image_page_url(
            "https://e-hentai.org/s/abc/123",
            MockClient(),  # type: ignore
            asyncio.Semaphore(1),
        )
        assert result is not None
        url, ext = result
        assert url == "https://ehgt.org/123.jpg?abc"
        assert ext == "jpg"

    async def test_no_img_element(self):
        class MockResponse:
            status_code = 200
            text = "<html><body>no image</body></html>"

            def raise_for_status(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                return MockResponse()

        import asyncio

        result = await _image_page_url(
            "https://e-hentai.org/s/abc/123",
            MockClient(),  # type: ignore
            asyncio.Semaphore(1),
        )
        assert result is None

    async def test_http_error_returns_none(self):
        class MockClient:
            async def get(self, url, **kwargs):
                raise Exception("http error")

        import asyncio

        result = await _image_page_url(
            "https://e-hentai.org/s/abc/123",
            MockClient(),  # type: ignore
            asyncio.Semaphore(1),
        )
        assert result is None


class TestApiGdata:
    pytestmark = pytest.mark.asyncio

    async def test_api_error_raises_valueerror(self):
        class MockClient:
            async def post(self, url, json=None, **kwargs):
                class MockResponse:
                    def json(self):
                        return {"error": "Gallery not found"}

                    def raise_for_status(self):
                        pass

                    @property
                    def status_code(self):
                        return 200

                return MockResponse()

        from comic_dl.scrapers.sites.ehentai import _api_gdata
        with pytest.raises(ValueError, match="Gallery not found"):
            await _api_gdata(0, "token", MockClient())


class TestScrapeEhentai:
    pytestmark = pytest.mark.asyncio
    async def test_invalid_url(self):
        with pytest.raises(ValueError, match="Invalid e-hentai"):
            await scrape_ehentai("https://example.com/g/123/abc", None)

    _api_response = {
        "gmetadata": [
            {
                "title": "My Series Part 3",
                "filecount": "10",
                "filesize": str(7 * 1024 ** 3),
                "rating": "4.50",
                "tags": [],
                "thumb": "https://ehgt.org/cover.jpg",
                "category": "Manga",
            }
        ]
    }

    _api_response_with_tags = {
        "gmetadata": [
            {
                "title": "Some Gallery Title Chapter 7",
                "filecount": "5",
                "tags": [
                    "parody:School Daze",
                    "artist:Great Artist",
                    "language:english",
                    "full color",
                    "action",
                ],
                "thumb": "https://ehgt.org/cover2.jpg",
                "category": "Doujinshi",
            }
        ]
    }

    _gallery_html = """
    <html>
    <body>
    <div id="gdt">
        <a href="/s/abc/1"><img src="thumb1.jpg"></a>
        <a href="/s/abc/2"><img src="thumb2.jpg"></a>
    </div>
    </body>
    </html>
    """

    _image_html = """
    <html>
    <body>
    <img id="img" src="https://ehgt.org/image.webp">
    </body>
    </html>
    """

    class MockClient:
        call_count = 0

        async def post(self, url, json=None, **kwargs):
            assert url == "https://api.e-hentai.org/api.php"
            self.__class__.call_count += 1
            return _MockJsonResponse(TestScrapeEhentai._api_response)  # type: ignore

        async def get(self, url, **kwargs):
            if "api.e-hentai" not in url:
                # Check if it's a gallery page or /s/ page
                if "/s/" in url:
                    return _MockHtmlResponse(TestScrapeEhentai._image_html)
                return _MockHtmlResponse(TestScrapeEhentai._gallery_html)
            return _MockJsonResponse(TestScrapeEhentai._api_response)

    class MockClientWithTags:
        call_count = 0

        async def post(self, url, json=None, **kwargs):
            assert url == "https://api.e-hentai.org/api.php"
            self.__class__.call_count += 1
            return _MockJsonResponse(TestScrapeEhentai._api_response_with_tags)  # type: ignore

        async def get(self, url, **kwargs):
            if "api.e-hentai" not in url:
                if "/s/" in url:
                    return _MockHtmlResponse(TestScrapeEhentai._image_html)
                return _MockHtmlResponse(TestScrapeEhentai._gallery_html)
            return _MockJsonResponse(TestScrapeEhentai._api_response_with_tags)

    async def test_full_scrape(self):
        meta = await scrape_ehentai(
            "https://e-hentai.org/g/123/abc/",
            self.MockClient(),  # type: ignore
        )
        assert meta.series_title is not None
        assert "My Series" in meta.series_title
        assert "Chapter 3" in meta.chapter_title
        assert len(meta.images) == 2
        for img in meta.images:
            assert img.url.startswith("https://ehgt.org/")
            assert img.filename.endswith(".webp")
        assert meta.total_pages == 10
        assert meta.service == "e-hentai"
        assert meta.user_id == "123"
        assert meta.post_id == "abc"

        # Verify enriched metadata
        assert meta.cover_url == "https://ehgt.org/cover.jpg"
        assert meta.estimated_size == 7 * 1024 ** 3
        # Manga category -> right-to-left; rating 4.5/5 -> 9.0/10
        assert meta.reading_direction == "rtl"
        assert meta.community_rating == 9.0

    async def test_full_scrape_with_tags(self):
        meta = await scrape_ehentai(
            "https://e-hentai.org/g/456/def/",
            self.MockClientWithTags(),  # type: ignore
        )
        assert meta.series_title == "Some Gallery Title"
        assert meta.chapter_title == "Chapter 7"
        assert len(meta.images) == 2
        assert meta.total_pages == 5
        assert meta.cover_url == "https://ehgt.org/cover2.jpg"
        # Doujinshi category -> right-to-left; no rating field -> None
        assert meta.reading_direction == "rtl"
        assert meta.community_rating is None


class _MockJsonResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass

    @property
    def status_code(self):
        return 200


class TestFetchGalleryPage:
    pytestmark = pytest.mark.asyncio

    async def test_blocks_private_url(self):
        """Scraped/user-derived page URLs must not fetch private hosts."""

        class MockClient:
            async def get(self, url, **kwargs):
                raise AssertionError("must not connect to a blocked URL")

        from comic_dl.utils import RequestBlockedError

        with pytest.raises(RequestBlockedError):
            await _fetch_gallery_page("http://127.0.0.1:80/g/123/abc/", MockClient())

    async def test_returns_image_urls(self):
        html = (
            "<html><body>"
            '<div id="gdt">'
            '<div class="gdt"><a href="https://e-hentai.org/s/abc123/1-1"><img src="https://ehgt.org/t/1.jpg"/></a></div>'
            '<div class="gdt"><a href="https://e-hentai.org/s/def456/2-1"><img src="https://ehgt.org/t/2.jpg"/></a></div>'
            "</div>"
            "</body></html>"
        )

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        urls = await _fetch_gallery_page("https://e-hentai.org/g/123/abc/", MockClient())
        assert urls == [
            "https://e-hentai.org/s/abc123/1-1",
            "https://e-hentai.org/s/def456/2-1",
        ]

    async def test_empty_gallery(self):
        html = "<html><body>No images</body></html>"

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        urls = await _fetch_gallery_page("https://e-hentai.org/g/123/abc/", MockClient())
        assert urls == []

    async def test_http_error(self):
        class MockClient:
            async def get(self, url, **kwargs):
                raise RuntimeError("http error")

        with pytest.raises(RuntimeError):
            await _fetch_gallery_page("https://e-hentai.org/g/123/abc/", MockClient())


class TestFetchGalleryPageWithRetry:
    pytestmark = pytest.mark.asyncio

    _HTML = (
        "<html><body><div id=\"gdt\">"
        "<div class=\"gdt\"><a href=\"https://e-hentai.org/s/abc/1-1\">"
        "<img src=\"https://ehgt.org/t/1.jpg\"/></a></div>"
        "</div></body></html>"
    )

    def _client(self, attempts: list[int], fail_on: set[int]):
        html = self._HTML

        class MockClient:
            async def get(self, url, **kwargs):
                attempts[0] += 1

                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                if attempts[0] in fail_on:
                    raise ConnectionError("flaky connection")
                return Resp()

        return MockClient()

    async def test_retries_transient_then_succeeds(self):
        """A one-off connection failure must not abort the page fetch."""
        from comic_dl.scrapers.sites.ehentai import _fetch_gallery_page_with_retry

        attempts = [0]
        urls = await _fetch_gallery_page_with_retry(
            "https://e-hentai.org/g/123/abc/", self._client(attempts, {1})
        )
        assert urls == ["https://e-hentai.org/s/abc/1-1"]
        assert attempts[0] == 2

    async def test_gives_up_after_retries(self):
        """A persistently failing page raises after the retry budget."""
        from comic_dl.scrapers.sites.ehentai import (
            _GALLERY_PAGE_RETRIES,
            _fetch_gallery_page_with_retry,
        )

        attempts = [0]
        with pytest.raises(ConnectionError):
            await _fetch_gallery_page_with_retry(
                "https://e-hentai.org/g/123/abc/",
                self._client(attempts, {1, 2, 3, 4}),
            )
        assert attempts[0] == _GALLERY_PAGE_RETRIES

    async def test_non_transient_error_not_retried(self):
        """Non-network failures (parse/HTTP) surface immediately."""
        from comic_dl.scrapers.sites.ehentai import _fetch_gallery_page_with_retry

        attempts = [0]

        class MockClient:
            async def get(self, url, **kwargs):
                attempts[0] += 1
                raise ValueError("bad response")

        with pytest.raises(ValueError):
            await _fetch_gallery_page_with_retry(
                "https://e-hentai.org/g/123/abc/", MockClient()
            )
        assert attempts[0] == 1


class _MockHtmlResponse:
    def __init__(self, html: str):
        self.text = html

    def raise_for_status(self):
        pass

    @property
    def status_code(self):
        return 200


class TestUtf8Decode:
    """Bug #3: mislabeled charset headers must not mangle non-ASCII titles."""

    TITLE = "Español 日本語"

    def test_decode_text_uses_utf8_bytes_not_text(self):
        class Resp:
            content = "Español 日本語".encode()
            text = "Español 日本語".encode().decode("latin-1", errors="replace")

        assert _decode_text(Resp()) == "Español 日本語"

    def test_decode_text_falls_back_on_invalid_utf8(self):
        class Resp:
            content = b"\xff\xfe\xfa"
            text = "fallback"

        assert _decode_text(Resp()) == "fallback"

    def test_decode_text_handles_str_content(self):
        class Resp:
            content = "plain"
            text = "plain"

        assert _decode_text(Resp()) == "plain"

    def test_decode_json_uses_utf8_bytes(self):
        class Resp:
            content = json.dumps({"gmetadata": [{"title": "Español"}]}).encode("utf-8")

            def json(self):
                raise AssertionError("_decode_json must not call resp.json()")

        data = _decode_json(Resp())
        assert data["gmetadata"][0]["title"] == "Español"

    async def test_api_gdata_roundtrips_non_ascii_title(self, monkeypatch):
        payload = json.dumps({
            "gmetadata": [{"title": self.TITLE, "filecount": "1"}]
        }).encode("utf-8")

        from comic_dl.scrapers.base import BaseScraper
        from comic_dl.scrapers.sites.ehentai import _api_gdata

        class Resp:
            status_code = 200
            content = payload
            headers = {}

            def raise_for_status(self):
                pass

        async def fake_timeout_get(url, client, **kwargs):
            return Resp()

        monkeypatch.setattr(BaseScraper, "_timeout_get", fake_timeout_get)

        meta = await _api_gdata(0, "token", object())
        assert meta["title"] == self.TITLE
        assert "\ufffd" not in meta["title"]

    async def test_api_gdata_uses_rate_limiter_and_no_redirects(self, monkeypatch):
        """The metadata API must be throttled like the rest of the site and
        must never follow redirects off-host."""

        import json as _json

        from comic_dl.scrapers.base import BaseScraper
        from comic_dl.scrapers.sites.ehentai import _api_gdata

        class Resp:
            status_code = 200
            headers = {}
            content = _json.dumps(
                {"gmetadata": [{"title": "X", "filecount": "1"}]}
            ).encode("utf-8")

            def raise_for_status(self):
                pass

        seen: list[tuple[str, dict]] = []

        async def fake_timeout_get(url, client, **kwargs):
            seen.append((url, kwargs))
            return Resp()

        monkeypatch.setattr(BaseScraper, "_timeout_get", fake_timeout_get)

        meta = await _api_gdata(0, "token", object())
        assert meta["title"] == "X"

        url, kwargs = seen[0]
        # Same throttled, redirect-refusing path as every other fetch, but POST
        # with the JSON body and a faster per-request rate for the API host.
        assert url == "https://api.e-hentai.org/api.php"
        assert kwargs["method"] == "POST"
        assert kwargs["rate"] == 2.0
        assert kwargs["json"] == {
            "method": "gdata",
            "gidlist": [[0, "token"]],
            "namespace": 1,
        }
        assert kwargs["use_cache"] is False

    async def test_fetch_gallery_page_uses_utf8_bytes(self):
        html = b'<div id="gdt"><a href="/s/abc/1"><img src="t.jpg"></a></div>'

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    headers = {}
                    content = html
                    text = html.decode("latin-1", errors="replace")

                    def raise_for_status(self):
                        pass

                return Resp()

        urls = await _fetch_gallery_page("https://e-hentai.org/g/123/abc/", MockClient())
        # relative hrefs are resolved against the gallery page URL
        assert urls == ["https://e-hentai.org/s/abc/1"]


class _StreamHtmlResponse:
    status_code = 200

    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


def _stream_gallery_html(num_links: int) -> str:
    links = "".join(
        f'<a href="/s/token/123-{i}"><img src="t{i}.jpg"></a>'
        for i in range(1, num_links + 1)
    )
    return f'<html><body><div id="gdt">{links}</div></body></html>'


class _StreamMockClient:
    """Gallery pages expose ``num_links`` /s/ URLs; /s/ pages serve img#img.

    ``delay_seconds`` slows page 1's image page so page 2 resolves first —
    proving the stream still yields in page order.
    """

    def __init__(self, num_links: int = 4, delay_seconds: float = 0.05):
        self._num_links = num_links
        self._delay = delay_seconds
        self.active = 0
        self.max_active = 0

    async def get(self, url, **kwargs):
        if "/s/" in url:
            page = url.rsplit("-", 1)[-1]
            if page == "1" and self._delay:
                await asyncio.sleep(self._delay)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                return _StreamHtmlResponse(
                    f'<html><body><img id="img" src="https://ehgt.org/img{page}.jpg"></body></html>'
                )
            finally:
                self.active -= 1
        return _StreamHtmlResponse(_stream_gallery_html(self._num_links))


class TestIterImageItems:
    pytestmark = pytest.mark.asyncio

    async def test_yields_in_page_order(self):
        client = _StreamMockClient(num_links=4)
        items = [
            item
            async for item in _iter_image_items(
                "https://e-hentai.org/g/123/abc123/", 4, client
            )
        ]
        assert [i.page_number for i in items] == [1, 2, 3, 4]
        assert [i.url for i in items] == [
            "https://ehgt.org/img1.jpg",
            "https://ehgt.org/img2.jpg",
            "https://ehgt.org/img3.jpg",
            "https://ehgt.org/img4.jpg",
        ]

    async def test_concurrency_bounded_by_semaphore(self):
        client = _StreamMockClient(num_links=8)
        sem = asyncio.Semaphore(2)
        count = 0
        async for _item in _iter_image_items(
            "https://e-hentai.org/g/123/abc123/", 8, client, sem=sem
        ):
            count += 1
        assert count == 8
        assert client.max_active <= 2

    async def test_skips_failed_pages(self):
        class FailingClient(_StreamMockClient):
            async def get(self, url, **kwargs):
                if url.endswith("-2"):
                    raise RuntimeError("boom")
                return await super().get(url, **kwargs)

        items = [
            item
            async for item in _iter_image_items(
                "https://e-hentai.org/g/123/abc123/", 4, FailingClient(num_links=4)
            )
        ]
        assert [i.page_number for i in items] == [1, 3, 4]


class TestStreamingScrape:
    pytestmark = pytest.mark.asyncio

    _api_response = {
        "gmetadata": [
            {
                "title": "My Series Part 3",
                "filecount": "4",
                "filesize": str(7 * 1024 ** 3),
                "rating": "4.50",
                "tags": [],
                "thumb": "https://ehgt.org/cover.jpg",
                "category": "Manga",
            }
        ]
    }

    class MockClient(_StreamMockClient):
        async def post(self, url, json=None, **kwargs):
            class Resp:
                status_code = 200

                def __init__(self, data):
                    self._data = data

                def json(self):
                    return self._data

                def raise_for_status(self):
                    pass

            return Resp(TestStreamingScrape._api_response)

    async def test_scrape_meta_returns_no_images_but_total(self):
        scraper = EHentaiScraper()
        meta = await scraper.scrape_meta(
            "https://e-hentai.org/g/123/abc123/",
            self.MockClient(num_links=2),  # type: ignore
        )
        assert meta.total_pages == 4
        assert meta.images == []
        assert meta.series_title is not None
        assert meta.cover_url == "https://ehgt.org/cover.jpg"

    async def test_iter_images_yields_download_image_items(self):
        from comic_dl.models import ImageItem as ModelImageItem

        scraper = EHentaiScraper()
        client = self.MockClient(num_links=2)  # type: ignore
        items = [
            item
            async for item in scraper.iter_images(
                "https://e-hentai.org/g/123/abc123/",
                client,
                total_pages=4,
            )
        ]
        assert len(items) == 2
        assert all(isinstance(i, ModelImageItem) for i in items)
        assert items[0].filename == "page_0001.jpg"
        assert items[0].page_number == 1

    async def test_scrape_chapter_matches_non_streaming(self):
        scraper = EHentaiScraper()
        client = self.MockClient(num_links=2)  # type: ignore
        chapter = await scraper._scrape_chapter(
            "https://e-hentai.org/g/123/abc123/", client
        )
        assert len(chapter.images) == 2
        assert [i.page_number for i in chapter.images] == [1, 2]


class TestSourceUrlPropagation:
    pytestmark = pytest.mark.asyncio

    async def test_iter_images_carries_source_page(self):
        scraper = EHentaiScraper()
        client = TestStreamingScrape.MockClient(num_links=2)  # type: ignore
        items = [
            item
            async for item in scraper.iter_images(
                "https://e-hentai.org/g/123/abc123/",
                client,
                total_pages=4,
            )
        ]
        assert len(items) == 2
        for idx, item in enumerate(items, start=1):
            assert item.source_url.startswith("https://e-hentai.org/s/")
            assert f"-{idx}" in item.source_url


class TestStaleImageRefresh:
    """Expired keystamp links are re-minted from their /s/ page."""

    pytestmark = pytest.mark.asyncio

    async def test_refresh_returns_item_with_new_url(self, monkeypatch):
        from comic_dl.scrapers.sites import ehentai as eh
        from comic_dl.scrapers.sites.ehentai import _refresh_stale_image

        async def fake_image_page(page_url, client, sem, use_cache=True):
            assert page_url == "https://e-hentai.org/s/tok/123-7"
            return ("https://node.hath.network/fresh.webp", "webp")

        monkeypatch.setattr(eh, "_image_page_url", fake_image_page)
        item = ImageItem(
            url="https://node.hath.network/stale.webp",
            page_number=7,
            filename="page_0007.jpg",
            source_url="https://e-hentai.org/s/tok/123-7",
        )
        out = await _refresh_stale_image(object(), item)  # type: ignore[arg-type]
        assert out is not None
        assert out.url == "https://node.hath.network/fresh.webp"
        # Filename/page/provenance survive; only the link changed.
        assert out.filename == "page_0007.jpg"
        assert out.page_number == 7
        assert out.source_url == "https://e-hentai.org/s/tok/123-7"

    async def test_refresh_none_when_page_unresolvable(self, monkeypatch):
        from comic_dl.scrapers.sites import ehentai as eh
        from comic_dl.scrapers.sites.ehentai import _refresh_stale_image

        async def fake_image_page(page_url, client, sem, use_cache=True):
            return None

        monkeypatch.setattr(eh, "_image_page_url", fake_image_page)
        item = ImageItem(
            url="https://n/x.webp", page_number=1,
            source_url="https://e-hentai.org/s/t/1",
        )
        assert await _refresh_stale_image(object(), item) is None  # type: ignore[arg-type]


class TestRefreshDispatch:
    """refresh_image_url routes by source-page host and never raises."""

    pytestmark = pytest.mark.asyncio

    async def test_unknown_host_returns_none_without_calling(self, monkeypatch):
        from comic_dl.scrapers.refresh import refresh_image_url

        called = []

        def spy(domain):
            def deco(fn):
                called.append(domain)
                return fn
            return deco

        item = ImageItem(url="https://n/a", page_number=1,
                         source_url="https://unregistered.example/p")
        assert await refresh_image_url(None, item) is None  # type: ignore[arg-type]
        assert called == []

    async def test_registered_host_dispatches(self, monkeypatch):
        from comic_dl.scrapers import refresh as rmod

        seen = {}

        @rmod.register_image_refresher("dispatch.test")
        async def _fake(client, item):
            seen["host_item"] = item
            return replace(item, url="https://n/new")

        item = ImageItem(url="https://n/old", page_number=3,
                         source_url="https://dispatch.test/s/3")
        out = await rmod.refresh_image_url(None, item)  # type: ignore[arg-type]
        assert out is not None and out.url == "https://n/new"
        assert seen["host_item"].url == "https://n/old"

    async def test_same_url_reissue_is_treated_as_no_refresh(self, monkeypatch):
        from comic_dl.scrapers import refresh as rmod

        @rmod.register_image_refresher("same.test")
        async def _same(client, item):
            return item

        item = ImageItem(url="https://n/keep", page_number=1,
                         source_url="https://same.test/s/1")
        assert await rmod.refresh_image_url(None, item) is None  # type: ignore[arg-type]

    async def test_refresher_exception_swallowed(self, monkeypatch):
        from comic_dl.scrapers import refresh as rmod

        @rmod.register_image_refresher("boom.test")
        async def _boom(client, item):
            raise RuntimeError("node exploded")

        item = ImageItem(url="https://n/x", page_number=1,
                         source_url="https://boom.test/s/1")
        assert await rmod.refresh_image_url(None, item) is None  # type: ignore[arg-type]

    async def test_empty_source_url_short_circuits(self):
        from comic_dl.scrapers.refresh import refresh_image_url

        item = ImageItem(url="https://n/x", page_number=1)
        assert await refresh_image_url(None, item) is None  # type: ignore[arg-type]
