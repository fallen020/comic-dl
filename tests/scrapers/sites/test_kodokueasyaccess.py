from __future__ import annotations

import json

import pytest

from comic_dl.errors import ScrapeError
from comic_dl.scrapers.sites.kodokueasyaccess import (
    DEFAULT_LANGUAGE,
    DOMAIN,
    KodokuEasyAccessScraper,
    _chapter_ref_from_url,
    _series_slug_from_url,
    _title_from_slug,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "reverend-insanity"
SERIES_URL = f"https://kodokueasyaccess.com/manhwa/{SLUG}"
CHAPTER_URL = f"https://kodokueasyaccess.com/read/{SLUG}/en/30"
COVER = "https://kodokueasyaccess.com/kodoku-chapters/series/abc/cover.webp?X-Amz-Expires=1800&X-Amz-Signature=dead"

DETAIL = {
    "id": "f9db7f58",
    "slug": SLUG,
    "title": "Reverend Insanity",
    "description": None,
    "coverUrl": COVER,
    "translationCount": 154,
}


def _page(sequence: int) -> dict:
    return {
        "sequence": sequence,
        "width": 1600,
        "height": 3820,
        "url": (
            f"https://kodokueasyaccess.com/kodoku-chapters/chapters/abc/{sequence:06d}.webp"
            "?X-Amz-Expires=1800&X-Amz-Signature=dead"
        ),
    }


CHAPTER = {
    "translationId": "4005c00c",
    "seriesSlug": SLUG,
    "chapterNumber": 30,
    "languageCode": "en",
    "title": None,
    "effectiveTier": "Free",
    "images": [_page(i) for i in range(3)],
}

LOCKED = {
    "available": False,
    "tier": "Free",
    "availableAt": "2026-10-07T00:00:00+00:00",
    "requiresAccessCode": False,
}


def _chapter_entry(number: str, *, available: bool = True, language: str = "en") -> dict:
    return {
        "id": f"id-{language}-{number}",
        "chapterNumber": number,
        "languageCode": language,
        "title": None,
        "available": available,
        "tier": "Free",
    }


CHAPTERS = [
    _chapter_entry("30"),
    _chapter_entry("31", available=False),
    _chapter_entry("1"),
    _chapter_entry("8", language="ru"),
    _chapter_entry("2", available=False),
]

_DETAIL_PATH = f"/api/series/{SLUG}"
_LIST_PATH = f"/api/series/{SLUG}/chapters"


def _handler(url):
    if url.endswith(_LIST_PATH):
        return _MockResponse(content=json.dumps(CHAPTERS))
    if url.endswith(_DETAIL_PATH):
        return _MockResponse(json_data=DETAIL)
    if "/chapters/" in url:
        return _MockResponse(json_data=CHAPTER)
    raise AssertionError(f"unexpected URL {url}")


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url(f"{SERIES_URL}/")
        assert is_series_url("https://www.kodokueasyaccess.com/manhwa/foo")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://kodokueasyaccess.com/")
        assert not is_series_url("https://kodokueasyaccess.com/manhwa/")
        assert not is_series_url("https://kodokueasyaccess.com/read/foo/en/1")
        assert not is_series_url("https://kodokustudio.com/manhua/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url(f"{CHAPTER_URL}/")
        assert is_chapter_url(f"https://kodokueasyaccess.com/read/{SLUG}/ru/8")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url(SERIES_URL)
        assert not is_chapter_url(f"https://kodokueasyaccess.com/read/{SLUG}/en")
        assert not is_chapter_url(f"https://kodokueasyaccess.com/read/{SLUG}/en/x")

    def test_matches(self):
        scraper = KodokuEasyAccessScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(CHAPTER_URL)

    def test_url_parts(self):
        assert _series_slug_from_url(SERIES_URL) == SLUG
        assert _series_slug_from_url(CHAPTER_URL) == SLUG
        assert _series_slug_from_url("https://kodokueasyaccess.com/") == ""
        assert _chapter_ref_from_url(CHAPTER_URL) == (SLUG, "en", "30")
        assert _chapter_ref_from_url(f"https://kodokueasyaccess.com/read/{SLUG}/RU/08") == (
            SLUG,
            "ru",
            "8",
        )
        assert _chapter_ref_from_url(SERIES_URL) == ("", "", "")

    def test_title_from_slug(self):
        assert _title_from_slug(SLUG) == "Reverend Insanity"


class TestScrapeChapter:
    @pytest.mark.asyncio
    async def test_success(self):
        meta = await KodokuEasyAccessScraper().scrape(CHAPTER_URL, _MockSession(_handler))

        assert meta.series_title == "Reverend Insanity"
        assert meta.chapter_title == "Chapter 30"
        assert meta.chapter_number == "30"
        assert meta.language == "en"
        assert meta.total_pages == 3
        assert [i.page_number for i in meta.images] == [1, 2, 3]
        # On-disk names come from the page number; the presigned query is dropped.
        assert meta.images[0].filename == "page_0001.webp"

    @pytest.mark.asyncio
    async def test_uses_chapter_title_when_present(self):
        def handler(url):
            if "/chapters/" in url:
                payload = dict(CHAPTER, title="Opening chapter")
                return _MockResponse(json_data=payload)
            return _handler(url)

        meta = await KodokuEasyAccessScraper().scrape(CHAPTER_URL, _MockSession(handler))
        assert meta.chapter_title == "Opening chapter"

    @pytest.mark.asyncio
    async def test_language_from_url(self):
        ru_url = f"https://kodokueasyaccess.com/read/{SLUG}/ru/8"
        meta = await KodokuEasyAccessScraper().scrape(ru_url, _MockSession(_handler))
        assert meta.language == "ru"
        assert meta.chapter_number == "8"

    @pytest.mark.asyncio
    async def test_locked_chapter_reports_unlock_date(self):
        def handler(url):
            if "/chapters/" in url:
                return _MockResponse(json_data=LOCKED)
            return _handler(url)

        with pytest.raises(ScrapeError) as exc:
            await KodokuEasyAccessScraper().scrape(CHAPTER_URL, _MockSession(handler))

        assert "not available yet" in str(exc.value)
        assert "30" in str(exc.value)
        assert "2026-10-07" in (exc.value.hint or "")
        assert "Patreon" in (exc.value.hint or "")

    @pytest.mark.asyncio
    async def test_locked_chapter_without_date(self):
        def handler(url):
            if "/chapters/" in url:
                return _MockResponse(json_data={"available": False})
            return _handler(url)

        with pytest.raises(ScrapeError) as exc:
            await KodokuEasyAccessScraper().scrape(CHAPTER_URL, _MockSession(handler))

        assert "2026" not in (exc.value.hint or "")

    @pytest.mark.asyncio
    async def test_no_images_raises(self):
        def handler(url):
            if "/chapters/" in url:
                return _MockResponse(json_data=dict(CHAPTER, images=[]))
            return _handler(url)

        with pytest.raises(ScrapeError, match="No images found"):
            await KodokuEasyAccessScraper().scrape(CHAPTER_URL, _MockSession(handler))

    @pytest.mark.asyncio
    async def test_non_json_body_raises(self):
        def handler(url):
            if "/chapters/" in url:
                return _MockResponse(content="<html>not json</html>")
            return _handler(url)

        with pytest.raises(ScrapeError, match="No images found"):
            await KodokuEasyAccessScraper().scrape(CHAPTER_URL, _MockSession(handler))

    @pytest.mark.asyncio
    async def test_series_url_rejected_for_chapter(self):
        with pytest.raises(ScrapeError, match="listing page"):
            await KodokuEasyAccessScraper().scrape(
                SERIES_URL, _MockSession(lambda url: _MockResponse(json_data={}))
            )


class TestScrapeSeries:
    @pytest.mark.asyncio
    async def test_filters_to_available_default_language(self):
        series = await KodokuEasyAccessScraper().scrape_series(SERIES_URL, _MockSession(_handler))

        assert series.series_title == "Reverend Insanity"
        assert series.title_no == SLUG
        assert series.description == ""
        assert series.cover_url == COVER
        # 1 and 30 only: 2/31 are unavailable, 8 is Russian.
        assert [c["episode_no"] for c in series.chapters] == ["1", "30"]
        assert series.chapters[0]["url"] == f"{CHAPTER_URL.rsplit('/', 1)[0]}/1"
        assert series.chapters[0]["title"] == "Chapter 1"

    @pytest.mark.asyncio
    async def test_no_default_language_chapters_raises(self):
        def handler(url):
            if url.endswith(_LIST_PATH):
                return _MockResponse(content=json.dumps([_chapter_entry("8", language="ru")]))
            return _handler(url)

        with pytest.raises(ScrapeError, match="No chapters found"):
            await KodokuEasyAccessScraper().scrape_series(SERIES_URL, _MockSession(handler))

    @pytest.mark.asyncio
    async def test_falls_back_to_slug_title_and_no_cover(self):
        def handler(url):
            if url.endswith(_DETAIL_PATH):
                return _MockResponse(json_data={"slug": SLUG})
            return _handler(url)

        series = await KodokuEasyAccessScraper().scrape_series(SERIES_URL, _MockSession(handler))
        assert series.series_title == "Reverend Insanity"
        assert series.cover_url == ""

    @pytest.mark.asyncio
    async def test_chapter_url_rejected_for_series(self):
        with pytest.raises(ScrapeError, match="listing page"):
            await KodokuEasyAccessScraper().scrape_series(
                CHAPTER_URL, _MockSession(lambda url: _MockResponse(json_data={}))
            )


class TestCachePolicy:
    @pytest.mark.asyncio
    async def test_never_caches_api_responses(self, monkeypatch):
        """Presigned URLs expire in 30 minutes, far inside the 6h cache TTL.

        ``_timeout_get`` gates both its lookup and its store on ``use_cache``,
        so a single lookup is enough to prove the response was not cached.
        """
        from comic_dl import cache as cache_mod

        lookups: list[str] = []
        real_lookup = cache_mod.lookup

        def spy(*args, **kwargs):
            lookups.append(str(args[0]) if args else "")
            return real_lookup(*args, **kwargs)

        monkeypatch.setattr(cache_mod, "lookup", spy)

        await KodokuEasyAccessScraper().scrape(CHAPTER_URL, _MockSession(_handler))
        assert lookups == [], "presigned page URLs must not be served from the metadata cache"

    @pytest.mark.asyncio
    async def test_series_never_caches_api_responses(self, monkeypatch):
        from comic_dl import cache as cache_mod

        lookups: list[str] = []
        real_lookup = cache_mod.lookup

        def spy(*args, **kwargs):
            lookups.append(str(args[0]) if args else "")
            return real_lookup(*args, **kwargs)

        monkeypatch.setattr(cache_mod, "lookup", spy)

        await KodokuEasyAccessScraper().scrape_series(SERIES_URL, _MockSession(_handler))
        assert lookups == [], "the presigned cover URL must not be served from the cache"


class TestRegistration:
    def test_registered(self):
        from comic_dl.scrapers import list_sources

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain[DOMAIN].name == "kodokueasyaccess"
        assert by_domain[DOMAIN].has_series
        assert DEFAULT_LANGUAGE == "en"
