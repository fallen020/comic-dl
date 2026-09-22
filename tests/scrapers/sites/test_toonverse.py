from __future__ import annotations

import pytest

from comic_dl.scrapers.sites.toonverse import (
    DOMAIN,
    ToonVerseScraper,
    _chapter_number_from_url,
    _episode_no,
    _series_slug_from_url,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "excuse-me-this-is-my-room-uncensored"
SID = "2c6e8af9-a233-4cf9-ac59-59dbe56693e1"
SERIES_URL = f"https://toonverse.net/series/{SLUG}"
CHAPTER_URL = f"https://toonverse.net/read/{SLUG}/1"

DETAIL = {
    "success": True,
    "data": {
        "id": SID,
        "title": "Excuse me, This is my Room (Uncensored)",
        "slug": SLUG,
        "coverUrl": f"https://cdn.toonverse.net/covers/{SLUG}.webp",
        "author": "LObeam",
        "artist": "",
        "type": "manhwa",
        "status": "ongoing",
        "description": "Kim Jinsoo ends up sharing a home.",
        "genres": [{"name": "Comedy"}, {"name": "Drama"}],
        "tags": [{"name": "Uncensored"}],
        "chapterCount": 3,
    },
}
CHAPTERS_P1 = {
    "success": True,
    "data": {
        "chapters": [
            {"id": str(i), "number": i, "title": "" if i == 2 else f"Chapter {i}"}
            for i in range(1, 51)
        ],
        "total": 51,
        "limit": 50,
        "offset": 0,
    },
}
CHAPTERS_P2 = {
    "success": True,
    "data": {
        "chapters": [{"id": "c", "number": 51, "title": "Chapter 51"}],
        "total": 51,
        "limit": 50,
        "offset": 50,
    },
}
PAGES = {
    "success": True,
    "data": {
        "chapter": {
            "number": 1,
            "title": "Chapter 1",
            "pages": [
                {
                    "number": 1,
                    "imageUrl": f"https://cdn.toonverse.net/series/{SLUG}/ch-0001/001.jpg",
                },
                {
                    "number": 2,
                    "imageUrl": f"https://cdn.toonverse.net/series/{SLUG}/ch-0001/002.jpg",
                },
            ],
        },
        "series": {"title": "Excuse me, This is my Room (Uncensored)"},
    },
}


def _handler(url):
    if "/series/slug/" in url:
        return _MockResponse(json_data=DETAIL)
    if "/chapters" in url:
        if "offset=50" in url:
            return _MockResponse(json_data=CHAPTERS_P2)
        return _MockResponse(json_data=CHAPTERS_P1)
    if "/reading/chapter/" in url:
        return _MockResponse(json_data=PAGES)
    raise AssertionError(f"unexpected URL {url}")


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url(f"{SERIES_URL}/")
        assert is_series_url("https://www.toonverse.net/series/foo")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://toonverse.net/")
        assert not is_series_url("https://toonverse.net/series/")
        assert not is_series_url(CHAPTER_URL)
        assert not is_series_url("https://other.net/series/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url(f"{CHAPTER_URL}/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url(SERIES_URL)
        assert not is_chapter_url("https://toonverse.net/read/foo/bar")

    def test_matches(self):
        scraper = ToonVerseScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(CHAPTER_URL)

    def test_slugs(self):
        assert _series_slug_from_url(SERIES_URL) == SLUG
        assert _series_slug_from_url(CHAPTER_URL) == SLUG
        assert _series_slug_from_url("https://toonverse.net/") == ""
        assert _chapter_number_from_url(CHAPTER_URL) == "1"
        assert _chapter_number_from_url(SERIES_URL) is None

    def test_episode_no(self):
        assert _episode_no(1) == "1"
        assert _episode_no(1.0) == "1"
        assert _episode_no(1.5) == "1.5"
        assert _episode_no("2") == "2"
        assert _episode_no(True) is None
        assert _episode_no(None) is None
        assert _episode_no("x") is None


class TestToonVerseScraper:
    @pytest.mark.asyncio
    async def test_scrape_chapter_success(self):
        scraper = ToonVerseScraper()
        meta = await scraper.scrape(CHAPTER_URL, _MockSession(_handler))

        assert meta.series_title == "Excuse me, This is my Room (Uncensored)"
        assert meta.chapter_title == "Chapter 1"
        assert meta.chapter_number == "1"
        assert meta.total_pages == 2
        assert meta.images[0].url.endswith("ch-0001/001.jpg")

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_pages_raises(self):
        def handler(url):
            if "/reading/chapter/" in url:
                return _MockResponse(json_data={"success": True, "data": {}})
            return _handler(url)

        scraper = ToonVerseScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, _MockSession(handler))

    @pytest.mark.asyncio
    async def test_scrape_series_paginates(self):
        scraper = ToonVerseScraper()
        series = await scraper.scrape_series(SERIES_URL, _MockSession(_handler))

        assert series.series_title == "Excuse me, This is my Room (Uncensored)"
        assert series.title_no == SLUG
        assert len(series.chapters) == 51
        assert series.chapters[0]["episode_no"] == "1"
        assert series.chapters[1]["title"] == "Chapter 2"
        assert series.chapters[-1]["url"] == f"https://toonverse.net/read/{SLUG}/51"

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        def handler(url):
            if "/chapters" in url:
                return _MockResponse(json_data={"success": True, "data": {"chapters": []}})
            return _handler(url)

        scraper = ToonVerseScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, _MockSession(handler))

    @pytest.mark.asyncio
    async def test_homepage_url_rejected(self):
        from comic_dl.errors import ScrapeError

        scraper = ToonVerseScraper()
        with pytest.raises(ScrapeError, match="listing page"):
            await scraper.scrape(
                "https://toonverse.net/", _MockSession(lambda url: _MockResponse("<html></html>"))
            )

    def test_registered(self):
        from comic_dl.scrapers import list_sources

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain[DOMAIN].name == "toonverse"
        assert by_domain[DOMAIN].has_series
