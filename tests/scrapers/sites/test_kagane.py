from __future__ import annotations

import json

import pytest

import comic_dl.scrapers.sites.kagane as kagane_module
from comic_dl.errors import ScrapeError
from comic_dl.scrapers.sites.kagane import (
    DOMAIN,
    KaganeScraper,
    _extract_ids,
    _series_fields,
    build_image_urls,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SERIES_ID = "3VZ9ZMQCIO6BNFJ8WAKIUR7RD5"
BOOK_1 = "11111111-1111-1111-1111-111111111111"
BOOK_2 = "22222222-2222-2222-2222-222222222222"

SERIES_JSON = {
    "series_id": SERIES_ID,
    "title": "Solo Leveling",
    "description": "A hunter rises from the lowest rank.",
    "format": "Manhwa",
    "content_rating": "Safe",
    "publication_status": "Ongoing",
    "upload_status": "Ongoing",
    "original_language": "ko",
    "translated_language": "en",
    "title_language": "en",
    "current_books": 2,
    "total_views": 12345,
    "average_rating": 9.6,
    "bayesian_rating": 9.4,
    "total_ratings": 1234,
    "start_year": 2018,
    "end_year": None,
    "genres": [
        {"genre_id": "g1", "genre_name": "Action", "is_spoiler": False},
        {"genre_id": "g2", "genre_name": "Fantasy", "is_spoiler": False},
    ],
    "tags": [],
    "series_alternate_titles": [],
    "series_books": [
        {
            "book_id": BOOK_1,
            "chapter_no": "1",
            "title": "The Weakest Hunter",
            "sort_no": 1,
            "page_count": 3,
            "views": 100,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-02T00:00:00Z",
            "volume_no": None,
            "published_on": None,
            "internal_release": False,
            "groups": [],
            "uploader": None,
        },
        {
            "book_id": BOOK_2,
            "chapter_no": "2",
            "title": "Rising from the Ruins",
            "sort_no": 2,
            "page_count": 8,
            "views": 90,
            "created_at": "2025-01-03T00:00:00Z",
            "updated_at": "2025-01-04T00:00:00Z",
            "volume_no": None,
            "published_on": None,
            "internal_release": False,
            "groups": [],
            "uploader": None,
        },
    ],
    "series_covers": [
        {
            "cover_id": "c1",
            "image_id": "cover-image-1",
            "chapter_number": "1",
            "volume_number": None,
            "language": "en",
            "note": None,
        }
    ],
    "series_links": [],
    "series_staff": [
        {"staff_id": "s1", "name": "Chugong", "role": "Author"},
        {"staff_id": "s2", "name": "Dubu", "role": "Artist"},
    ],
}

DRM_TOKENS = {
    "cache_url": "https://kstatic.to",
    "access_token": "tok123",
    "manifest": {
        "pages": [
            {"page_id": "p001", "ext": "webp", "page_no": 1},
            {"page_id": "p002", "ext": "webp", "page_no": 2},
            {"page_id": "p003", "ext": "webp", "page_no": 3},
        ]
    },
}

INTEGRITY_RESPONSE = {"token": "integrity-token-1"}

PAGE_URLS = [
    f"https://kstatic.to/api/v2/books/page/{BOOK_1}/p001.webp"
    f"?is_datasaver=false&token=tok123",
    f"https://kstatic.to/api/v2/books/page/{BOOK_1}/p002.webp"
    f"?is_datasaver=false&token=tok123",
    f"https://kstatic.to/api/v2/books/page/{BOOK_1}/p003.webp"
    f"?is_datasaver=false&token=tok123",
]

_FAKE_SESSION: _FakeWebViewSession | None = None


class _FakeWebViewSession:
    """Records session requests and returns handler responses as raw tuples.

    Mirrors ``WebViewSession.request``'s ``(status, headers, bytes)`` contract
    so the kagane session path can be exercised without a real webview.
    """

    def __init__(self, handler):
        self._handler = handler
        self.calls: list[tuple[str, str, dict, str | None]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: str | None = None,
        timeout: float = 60.0,
    ):
        self.calls.append((method, url, headers or {}, body))
        resp = self._handler(url)
        if resp._json_data is not None:
            content = json.dumps(resp._json_data).encode()
        else:
            content = resp.content
        return resp.status_code, resp.headers, content


async def _fake_ensure_session(url):
    return _FAKE_SESSION


def _session_enabled_true():
    return True


@pytest.fixture(autouse=True)
def _session_disabled_by_default(monkeypatch):
    """Keep plain-HTTP tests from spawning a real webview session.

    The ``webview`` extra may be installed in the dev environment, which makes
    ``session_enabled()`` return True and ``_api_fetch`` attempt to start a
    real ``webview_solver --serve`` subprocess. Tests that exercise the plain
    HTTP fallback rely on a mock client, so pin the session off unless a test
    explicitly enables it (see :class:`TestKaganeScraperSessionPath`).
    """
    monkeypatch.setattr(
        kagane_module.webview, "session_enabled", lambda: False
    )


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(f"https://kagane.to/series/{SERIES_ID}")
        assert is_series_url(f"https://www.kagane.to/series/{SERIES_ID}/")
        assert not is_series_url(f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://kagane.to/")
        assert not is_series_url("https://kagane.to/series/")
        assert not is_series_url("https://other.com/series/abc")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}")
        assert is_chapter_url(
            f"https://www.kagane.to/series/{SERIES_ID}/reader/{BOOK_1}/"
        )

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url(f"https://kagane.to/series/{SERIES_ID}")
        assert not is_chapter_url(f"https://kagane.to/series/{SERIES_ID}/reader/")
        assert not is_chapter_url("https://kagane.to/reader/some-book")
        assert not is_chapter_url("https://other.com/series/a/reader/b")

    def test_matches_url(self):
        scraper = KaganeScraper()
        assert scraper.matches_url(f"https://kagane.to/series/{SERIES_ID}")
        assert scraper.matches_url(f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}")


class TestExtractIds:
    def test_series_url(self):
        assert _extract_ids(f"https://kagane.to/series/{SERIES_ID}") == (SERIES_ID, None)

    def test_reader_url(self):
        assert _extract_ids(
            f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}"
        ) == (SERIES_ID, BOOK_1)

    def test_unrelated_url(self):
        assert _extract_ids("https://kagane.to/") == ("", None)


class TestBuildImageUrls:
    def test_builds_urls_in_manifest_order(self):
        urls = build_image_urls(DRM_TOKENS, BOOK_1)
        assert urls == PAGE_URLS

    def test_accepts_camel_case_fields(self):
        data = {
            "cacheUrl": "https://kstatic.to",
            "access_token": "tok123",
            "manifest": {
                "pages": [
                    {"pageId": "p001", "ext": "webp", "page_no": 1},
                ]
            },
        }
        urls = build_image_urls(data, BOOK_1)
        assert urls == [PAGE_URLS[0]]

    def test_defaults_extension_to_webp(self):
        data = dict(
            DRM_TOKENS,
            manifest={
                "pages": [{"page_id": "p001", "page_no": 1}],
            },
        )
        urls = build_image_urls(data, BOOK_1)
        assert urls[0].endswith("/p001.webp?is_datasaver=false&token=tok123")

    def test_malformed_input_returns_empty(self):
        assert build_image_urls(None, BOOK_1) == []
        assert build_image_urls({}, BOOK_1) == []
        assert build_image_urls("nope", BOOK_1) == []
        assert build_image_urls(
            {"cache_url": "https://x", "access_token": "t"}, BOOK_1
        ) == []
        assert build_image_urls(
            {"cache_url": "https://x", "access_token": "t", "manifest": {"pages": []}},
            BOOK_1,
        ) == []


class TestSeriesParsing:
    def test_genres(self):
        assert kagane_module._genres(SERIES_JSON) == ["Action", "Fantasy"]

    def test_staff_names(self):
        assert kagane_module._staff_names(SERIES_JSON) == ["Chugong", "Dubu"]

    def test_cover_url(self):
        assert (
            kagane_module._cover_url(SERIES_JSON)
            == "https://kagane.to/api/v2/image/cover-image-1"
        )
        assert kagane_module._cover_url({}) == ""

    def test_series_fields(self):
        fields = _series_fields(SERIES_JSON)
        assert fields["series_title"] == "Solo Leveling"
        assert fields["description"].startswith("A hunter rises")
        assert fields["genres"] == ["Action", "Fantasy"]
        assert fields["artists"] == ["Chugong", "Dubu"]
        assert fields["status"] == "Ongoing"
        assert fields["language"] == "en"
        assert fields["year"] == 2018
        assert fields["community_rating"] == 9.6
        empty = _series_fields({})
        assert empty["series_title"] == ""
        assert empty["artists"] == []
        assert empty["genres"] == []

    def test_chapters_ordered_by_sort_no(self):
        chapters = kagane_module._chapters(SERIES_JSON, SERIES_ID)
        assert len(chapters) == 2
        assert chapters[0]["title"] == "The Weakest Hunter"
        assert chapters[0]["episode_no"] == "1"
        assert chapters[0]["url"] == f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}"
        assert chapters[1]["title"] == "Rising from the Ruins"
        assert chapters[1]["episode_no"] == "2"
        assert chapters[1]["url"] == f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_2}"


class TestKaganeScraper:
    def test_domain_attr(self):
        scraper = KaganeScraper()
        assert scraper.domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_chapter_success(self):
        def handler(url):
            if "/api/integrity" in url:
                return _MockResponse(json_data=INTEGRITY_RESPONSE)
            if "/api/v2/books/" in url:
                return _MockResponse(json_data=DRM_TOKENS)
            return _MockResponse(json_data=SERIES_JSON)

        session = _MockSession(handler)
        scraper = KaganeScraper()
        meta = await scraper.scrape(
            f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}", session,
        )

        assert meta.series_title == "Solo Leveling"
        assert meta.chapter_title == "The Weakest Hunter"
        assert meta.chapter_number == "1"
        assert meta.service == DOMAIN
        assert meta.post_id == BOOK_1
        assert meta.total_pages == 3
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.artists == ["Chugong", "Dubu"]
        assert meta.genres == ["Action", "Fantasy"]
        assert meta.status == "Ongoing"
        assert meta.community_rating == 9.6
        assert meta.year == 2018
        assert meta.cover_url == "https://kagane.to/api/v2/image/cover-image-1"
        assert meta.description.startswith("A hunter rises")
        assert [img.url for img in meta.images] == PAGE_URLS
        assert [img.page_number for img in meta.images] == [1, 2, 3]

        integrity_calls = [
            (u, k) for u, k in session.requests if "/api/integrity" in u
        ]
        books_calls = [
            (u, k) for u, k in session.requests if "/api/v2/books/" in u
        ]
        assert integrity_calls and books_calls
        assert "X-Integrity-Token" in books_calls[0][1]["headers"]
        assert books_calls[0][1]["json"] == {}

    @pytest.mark.asyncio
    async def test_scrape_chapter_missing_integrity_raises(self):
        def handler(url):
            if "/api/integrity" in url:
                return _MockResponse(status=500)
            return _MockResponse(json_data=DRM_TOKENS)

        session = _MockSession(handler)
        scraper = KaganeScraper()
        with pytest.raises(ValueError, match="API"):
            await scraper.scrape(
                f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}", session,
            )

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        def handler(url):
            if "/api/integrity" in url:
                return _MockResponse(json_data=INTEGRITY_RESPONSE)
            if "/api/v2/books/" in url:
                return _MockResponse(
                    json_data={
                        "cache_url": "https://kstatic.to",
                        "access_token": "t",
                        "manifest": {"pages": []},
                    }
                )
            return _MockResponse(json_data=SERIES_JSON)

        session = _MockSession(handler)
        scraper = KaganeScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(
                f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}", session,
            )

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if "/api/integrity" in url:
                return _MockResponse(json_data=INTEGRITY_RESPONSE)
            if "/api/v2/books/" in url:
                return _MockResponse(json_data=DRM_TOKENS)
            raise ConnectionError("boom")

        session = _MockSession(handler)
        scraper = KaganeScraper()
        meta = await scraper.scrape(
            f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}", session,
        )

        assert meta.series_title == "Untitled"
        assert meta.chapter_title == "Chapter"
        assert meta.chapter_number is None
        assert meta.artists == []
        assert meta.genres == []
        assert meta.status is None
        assert meta.cover_url == ""
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(json_data=SERIES_JSON))
        scraper = KaganeScraper()
        series = await scraper.scrape_series(
            f"https://kagane.to/series/{SERIES_ID}", session,
        )

        assert series.series_title == "Solo Leveling"
        assert series.title_no == SERIES_ID
        assert series.description.startswith("A hunter rises")
        assert series.cover_url == "https://kagane.to/api/v2/image/cover-image-1"
        assert len(series.chapters) == 2
        assert series.chapters[0]["episode_no"] == "1"
        assert series.chapters[0]["title"] == "The Weakest Hunter"
        assert series.chapters[0]["url"].endswith(f"/reader/{BOOK_1}")
        assert series.chapters[1]["episode_no"] == "2"
        assert series.chapters[1]["url"].endswith(f"/reader/{BOOK_2}")

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        empty = dict(SERIES_JSON, series_books=[])
        session = _MockSession(lambda url: _MockResponse(json_data=empty))
        scraper = KaganeScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(
                f"https://kagane.to/series/{SERIES_ID}", session,
            )


class TestKaganeScraperSessionPath:
    """Exercise the webview-session request path (preferred transport).

    The scraper routes API calls through ``WebViewSession.request`` when a
    session is enabled; these tests mock that seam with :class:
    `_FakeWebViewSession` and assert the same-scraper behavior holds.
    """

    @pytest.fixture(autouse=True)
    def _enable_session(self, monkeypatch):
        monkeypatch.setattr(
            kagane_module.webview, "session_enabled", _session_enabled_true
        )
        monkeypatch.setattr(
            kagane_module.webview, "ensure_session", _fake_ensure_session
        )

    @pytest.mark.asyncio
    async def test_scrape_chapter_success(self, monkeypatch):
        def handler(url):
            if "/api/integrity" in url:
                return _MockResponse(json_data=INTEGRITY_RESPONSE)
            if "/api/v2/books/" in url:
                return _MockResponse(json_data=DRM_TOKENS)
            return _MockResponse(json_data=SERIES_JSON)

        global _FAKE_SESSION
        _FAKE_SESSION = _FakeWebViewSession(handler)
        session = _MockSession(lambda url: _MockResponse(json_data={}))
        scraper = KaganeScraper()
        meta = await scraper.scrape(
            f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}", session,
        )

        assert meta.series_title == "Solo Leveling"
        assert meta.chapter_title == "The Weakest Hunter"
        assert meta.total_pages == 3
        assert [img.url for img in meta.images] == PAGE_URLS

        integrity_calls = [
            (m, u, h, b)
            for m, u, h, b in _FAKE_SESSION.calls
            if "/api/integrity" in u
        ]
        books_calls = [
            (m, u, h, b)
            for m, u, h, b in _FAKE_SESSION.calls
            if "/api/v2/books/" in u
        ]
        assert integrity_calls and books_calls
        assert integrity_calls[0][0] == "POST"
        assert books_calls[0][0] == "POST"
        assert "X-Integrity-Token" in books_calls[0][2]
        assert books_calls[0][2]["X-Integrity-Token"] == "integrity-token-1"
        assert books_calls[0][3] == "{}"

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self, monkeypatch):
        def handler(url):
            if "/api/integrity" in url:
                return _MockResponse(json_data=INTEGRITY_RESPONSE)
            if "/api/v2/books/" in url:
                return _MockResponse(
                    json_data={
                        "cache_url": "https://kstatic.to",
                        "access_token": "t",
                        "manifest": {"pages": []},
                    }
                )
            return _MockResponse(json_data=SERIES_JSON)

        global _FAKE_SESSION
        _FAKE_SESSION = _FakeWebViewSession(handler)
        session = _MockSession(lambda url: _MockResponse(json_data={}))
        scraper = KaganeScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(
                f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}", session,
            )

    @pytest.mark.asyncio
    async def test_scrape_series(self, monkeypatch):
        global _FAKE_SESSION
        _FAKE_SESSION = _FakeWebViewSession(
            lambda url: _MockResponse(json_data=SERIES_JSON)
        )
        session = _MockSession(lambda url: _MockResponse(json_data={}))
        scraper = KaganeScraper()
        series = await scraper.scrape_series(
            f"https://kagane.to/series/{SERIES_ID}", session,
        )

        assert series.series_title == "Solo Leveling"
        assert series.title_no == SERIES_ID
        assert len(series.chapters) == 2

    @pytest.mark.asyncio
    async def test_series_json_enrichment_failure_is_best_effort(
        self, monkeypatch
    ):
        def handler(url):
            if "/api/integrity" in url:
                return _MockResponse(json_data=INTEGRITY_RESPONSE)
            if "/api/v2/books/" in url:
                return _MockResponse(json_data=DRM_TOKENS)
            raise ConnectionError("boom")

        global _FAKE_SESSION
        _FAKE_SESSION = _FakeWebViewSession(handler)
        session = _MockSession(lambda url: _MockResponse(json_data={}))
        scraper = KaganeScraper()
        meta = await scraper.scrape(
            f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}", session,
        )

        assert meta.series_title == "Untitled"
        assert len(meta.images) == 3


class TestCfChallengeClassification:
    """Challenge responses surface as Cloudflare errors, not 'HTTPError'."""

    def test_403_cf_headers_classified_as_challenge(self):
        from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

        resp = _MockResponse(b"", status=403)
        resp.headers = {"server": "cloudflare"}
        exc = CurlHTTPError("HTTP Error 403", response=resp)
        assert kagane_module._is_cf_challenge_error(exc) is True

    def test_403_without_cf_markers_is_not_challenge(self):
        from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

        resp = _MockResponse(b"nope", status=403)
        resp.headers = {"server": "nginx"}
        exc = CurlHTTPError("HTTP Error 403", response=resp)
        assert kagane_module._is_cf_challenge_error(exc) is False

    def test_non_http_errors_are_not_challenge(self):
        assert kagane_module._is_cf_challenge_error(ConnectionError("x")) is False

    @pytest.mark.asyncio
    async def test_scrape_raises_challenge_message(self):
        async def _tokens_fail(book_id, client):
            resp = _MockResponse(b"", status=503)
            resp.headers = {"server": "cloudflare"}
            from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

            raise CurlHTTPError("HTTP Error 503", response=resp)

        global _FAKE_SESSION
        _FAKE_SESSION = None  # force plain-HTTP path
        scraper = KaganeScraper()
        scraper._chapter_tokens = _tokens_fail  # type: ignore[method-assign]
        with pytest.raises(ScrapeError, match="Cloudflare challenged"):
            await scraper.scrape(
                f"https://kagane.to/series/{SERIES_ID}/reader/{BOOK_1}",
                _MockSession(lambda url: _MockResponse(json_data={})),
            )
