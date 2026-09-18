from __future__ import annotations

import pytest

from comic_dl.scrapers.sites.stonescape import (
    BASE,
    DOMAIN,
    StoneScapeScraper,
    _absolute,
    _chapter_number_from_url,
    _find_chapter,
    _normalize_number,
    _page_images,
    _slug_from_url,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "ghost-story-work"
CHAPTER_ID = "85c5b003-4ff7-4f67-a42e-83ab8f1b1940"

COVER = "/pub/covers/b8719e60-bb1f-4a00-8678-24893d5dca08.webp"

PAGE_DIR = f"/pub/public-content/abba4935-42d8-4c35-bee1-79b328655222/{CHAPTER_ID}/v1"

SERIES_DETAIL = {
    "seriesId": "abba4935-42d8-4c35-bee1-79b328655222",
    "title": "Got Dropped Into a Ghost Story, Still Gotta Work",
    "slug": SLUG,
    "artist": None,
    "author": "Carrotoon",
    "coverUrl": COVER,
    "description": "Ghost Story Specialist Corporation\nDaydream Inc. (Ltd.)",
    "publicationStatus": "ongoing",
    "countryOfOrigin": "KR",
    "contentType": "manhwa",
    "genres": ["horror", "drama", "adaptation", "gore", "mature"],
    "chapterCount": 36,
    "averageRating": 5,
    "ratingCount": 13,
}

CHAPTERS = {
    "chapters": [
        {
            "chapterId": "16e8ec45-6cf7-45a8-a529-46df981613c8",
            "chapterNumber": "0.00",
            "title": "Prologue",
            "thumbnailUrl": "/pub/manhwa/ghost-story-work/0/thumb.webp",
            "accessMode": "free",
            "coinPrice": None,
            "isFreeNow": True,
            "locked": False,
        },
        {
            "chapterId": "263ac293-ba13-4c4b-810e-cd5748522f3a",
            "chapterNumber": "34.00",
            "title": None,
            "thumbnailUrl": "/pub/manhwa/ghost-story-work/34/thumb.webp",
            "accessMode": "free",
            "coinPrice": None,
            "isFreeNow": True,
            "locked": False,
        },
        {
            "chapterId": "c8c11250-8598-4af4-9441-8680a25b36df",
            "chapterNumber": "35.00",
            "title": "Ghostly Payday",
            "thumbnailUrl": "/pub/manhwa/ghost-story-work/35/thumb.webp",
            "accessMode": "free",
            "coinPrice": None,
            "isFreeNow": True,
            "locked": False,
        },
        {
            "chapterId": CHAPTER_ID,
            "chapterNumber": "36.00",
            "title": None,
            "thumbnailUrl": "/pub/manhwa/ghost-story-work/36/thumb.webp",
            "accessMode": "free",
            "coinPrice": None,
            "isFreeNow": True,
            "locked": False,
        },
        {
            "chapterId": "lock-37-0000-0000-000000000000",
            "chapterNumber": "37.00",
            "title": None,
            "thumbnailUrl": "/pub/manhwa/ghost-story-work/37/thumb.webp",
            "accessMode": "coins",
            "coinPrice": 5,
            "isFreeNow": False,
            "locked": True,
        },
    ]
}

PAGES = {
    "pages": [
        {
            "pageId": "ed01cfab-e854-40f1-841a-adc3a2a88943",
            "pageNumber": 1,
            "url": f"{PAGE_DIR}/72ac3cf5-02d1-42a3-a86e-0d6cbb2ab392.webp",
            "delivery": "public",
            "width": 690,
            "height": 9320,
        },
        {
            "pageId": "ebd999f4-3829-46b2-8c2e-468d6e23a86d",
            "pageNumber": 2,
            "url": f"{PAGE_DIR}/49013331-f5f0-43da-8909-f7ad9716fa19.webp",
            "delivery": "public",
            "width": 690,
            "height": 9500,
        },
        {
            "pageId": "7e8c7f4a-cbf7-4d52-b119-20e4c4c9f1b7",
            "pageNumber": 3,
            "url": f"{PAGE_DIR}/62db587c-d132-4b42-89cf-5f93a762a748.webp",
            "delivery": "public",
            "width": 690,
            "height": 9145,
        },
        {
            "pageId": "priv-0000-0000-0000-000000000000",
            "pageNumber": 4,
            "url": f"{PAGE_DIR}/0b11f8e1-0000-0000-0000-000000000000.webp",
            "delivery": "protected",
            "width": 690,
            "height": 9000,
        },
    ]
}


class TestUrlPatterns:
    def test_valid_chapter_urls(self):
        assert is_chapter_url(f"https://stonescape.xyz/series/{SLUG}/ch-36")
        assert is_chapter_url(f"https://stonescape.xyz/series/{SLUG}/ch-36/")
        assert is_chapter_url("https://stonescape.xyz/series/some-series/ch-0")
        assert is_chapter_url("https://stonescape.xyz/series/another/ch-5.5")
        assert is_chapter_url(f"https://www.stonescape.xyz/series/{SLUG}/ch-1")

    def test_invalid_chapter_urls(self):
        for url in (
            "",
            "https://stonescape.xyz/",
            "https://stonescape.xyz/series/",
            "https://stonescape.xyz/series/slug",
            "https://stonescape.xyz/series/slug/ch-/",
            "https://stonescape.xyz/series/slug/ch-abc",
            "https://stonescape.xyz/novels/slug/ch-1",
            "http://other.com/series/slug/ch-1",
        ):
            assert not is_chapter_url(url)

    def test_valid_series_urls(self):
        assert is_series_url(f"https://stonescape.xyz/series/{SLUG}")
        assert is_series_url(f"https://stonescape.xyz/series/{SLUG}/")
        assert is_series_url("https://www.stonescape.xyz/series/some-slug")

    def test_invalid_series_urls(self):
        for url in (
            "",
            "https://stonescape.xyz/",
            "https://stonescape.xyz/series/",
            "https://stonescape.xyz/browse",
            "https://stonescape.xyz/login",
            "https://stonescape.xyz/novels/slug",
            "https://other.com/series/slug",
        ):
            assert not is_series_url(url)

    def test_chapter_url_not_series_url_and_vice_versa(self):
        chapter = f"https://stonescape.xyz/series/{SLUG}/ch-36"
        series = f"https://stonescape.xyz/series/{SLUG}"
        assert is_chapter_url(chapter) and not is_series_url(chapter)
        assert is_series_url(series) and not is_chapter_url(series)

    def test_matches_url(self):
        scraper = StoneScapeScraper()
        assert scraper.matches_url(f"https://stonescape.xyz/series/{SLUG}/ch-36")
        assert scraper.matches_url(f"https://stonescape.xyz/series/{SLUG}")
        assert scraper.matches_series_url(f"https://stonescape.xyz/series/{SLUG}")
        assert not scraper.matches_url("https://stonescape.xyz/browse")
        assert not scraper.matches_series_url(
            f"https://stonescape.xyz/series/{SLUG}/ch-1"
        )


class TestHelpers:
    def test_normalize_number(self):
        assert _normalize_number("36.00") == "36"
        assert _normalize_number("0.00") == "0"
        assert _normalize_number("5.50") == "5.5"
        assert _normalize_number("abc") == "abc"

    def test_absolute(self):
        assert _absolute("/pub/covers/x.webp") == f"{BASE}/pub/covers/x.webp"
        assert _absolute("https://other.host/x.webp") == "https://other.host/x.webp"

    def test_slug_from_url(self):
        assert _slug_from_url(f"https://stonescape.xyz/series/{SLUG}/ch-36") == SLUG
        assert _slug_from_url(f"https://stonescape.xyz/series/{SLUG}") == SLUG
        assert _slug_from_url("https://stonescape.xyz/") == ""

    def test_chapter_number_from_url(self):
        assert _chapter_number_from_url(
            f"https://stonescape.xyz/series/{SLUG}/ch-36"
        ) == "36"
        assert _chapter_number_from_url(
            "https://stonescape.xyz/series/slug/ch-5.5"
        ) == "5.5"
        assert _chapter_number_from_url("https://stonescape.xyz/series/slug") is None

    def test_find_chapter_by_normalized_number(self):
        ch36 = _find_chapter(CHAPTERS["chapters"], "36")
        assert ch36 is not None
        assert ch36["chapterId"] == CHAPTER_ID
        ch0 = _find_chapter(CHAPTERS["chapters"], "0")
        assert ch0 is not None
        assert ch0["title"] == "Prologue"
        assert _find_chapter(CHAPTERS["chapters"], "999") is None
        assert _find_chapter("not-a-list", "36") is None

    def test_page_images_filter_public_delivery(self):
        images = _page_images(PAGES)
        assert len(images) == 3
        assert [img.page_number for img in images] == [1, 2, 3]
        assert all(img.url.startswith(f"{BASE}/pub/public-content/") for img in images)
        assert "protected" not in " ".join(img.url for img in images)

    def test_page_images_empty(self):
        assert _page_images({"pages": []}) == []

    def test_page_images_dedups(self):
        duplicated = dict(PAGES)
        duplicated["pages"] = [*list(PAGES["pages"]), dict(PAGES["pages"][0])]
        assert len(_page_images(duplicated)) == 3


class TestStoneScapeScraper:
    CHAPTER_URL = f"https://stonescape.xyz/series/{SLUG}/ch-36"
    SERIES_URL = f"https://stonescape.xyz/series/{SLUG}"

    @staticmethod
    def _session(handler):
        return _MockSession(handler)

    def _series_handler(self, pages=PAGES, chapters=CHAPTERS["chapters"]):
        def handler(url):
            if url.endswith(f"/api/series/by-slug/{SLUG}/chapters"):
                return _MockResponse(json_data={"chapters": chapters})
            if url.endswith(f"/api/series/by-slug/{SLUG}"):
                return _MockResponse(json_data=SERIES_DETAIL)
            if f"/api/chapters/{CHAPTER_ID}/pages" in url:
                return _MockResponse(json_data=pages)
            if "/api/chapters/" in url and url.endswith("/pages"):
                return _MockResponse(json_data=pages)
            raise AssertionError(f"unexpected request: {url}")

        return handler

    def test_domain_attr(self):
        assert StoneScapeScraper().domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_chapter(self):
        session = self._session(self._series_handler())
        meta = await StoneScapeScraper().scrape(self.CHAPTER_URL, session)

        assert meta.service == DOMAIN
        assert meta.series_title == "Got Dropped Into a Ghost Story, Still Gotta Work"
        assert meta.chapter_title == "Chapter 36"
        assert meta.chapter_number == "36"
        assert meta.total_pages == 3
        assert meta.cover_url == f"{BASE}{COVER}"
        assert meta.authors == ["Carrotoon"]
        assert meta.genres == ["horror", "drama", "adaptation", "gore", "mature"]
        assert meta.status == "ongoing"
        assert meta.community_rating == 5.0
        assert meta.description.startswith("Ghost Story Specialist")
        assert len(meta.images) == 3
        assert meta.images[0].page_number == 1
        assert meta.images[0].url.startswith(f"{BASE}/pub/public-content/")
        assert "protected" not in " ".join(img.url for img in meta.images)

    @pytest.mark.asyncio
    async def test_scrape_chapter_with_title(self):
        handler = self._series_handler()
        chapter_url = f"https://stonescape.xyz/series/{SLUG}/ch-35.0"
        session = self._session(handler)
        meta = await StoneScapeScraper().scrape(chapter_url, session)
        assert meta.chapter_number == "35"
        assert meta.chapter_title == "Ghostly Payday"

    @pytest.mark.asyncio
    async def test_scrape_chapter_unknown_number_raises(self):
        session = self._session(self._series_handler())
        with pytest.raises(ValueError, match="not found on StoneScape"):
            await StoneScapeScraper().scrape(
                f"https://stonescape.xyz/series/{SLUG}/ch-999", session
            )

    @pytest.mark.asyncio
    async def test_scrape_chapter_locked_raises(self):
        session = self._session(self._series_handler())
        with pytest.raises(ValueError, match="locked on StoneScape"):
            await StoneScapeScraper().scrape(
                f"https://stonescape.xyz/series/{SLUG}/ch-37", session
            )

    @pytest.mark.asyncio
    async def test_scrape_no_images_raises(self):
        session = self._session(self._series_handler(pages={"pages": []}))
        with pytest.raises(ValueError, match="No images found"):
            await StoneScapeScraper().scrape(self.CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_missing_series_404_is_friendly(self):
        session = _MockSession(
            lambda url: _MockResponse(
                json_data={"message": "Series not found"}, status=404
            )
        )
        with pytest.raises(ValueError, match="Not found on StoneScape"):
            await StoneScapeScraper().scrape(
                "https://stonescape.xyz/series/gone-series/ch-1", session
            )

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = self._session(self._series_handler())
        series = await StoneScapeScraper().scrape_series(self.SERIES_URL, session)

        assert series.series_title == "Got Dropped Into a Ghost Story, Still Gotta Work"
        assert series.description.startswith("Ghost Story Specialist")
        assert series.cover_url == f"{BASE}{COVER}"
        assert series.title_no == SLUG
        assert [c["episode_no"] for c in series.chapters] == ["0", "34", "35", "36", "37"]
        assert series.chapters[0]["title"] == "Prologue"
        assert series.chapters[-1]["title"] == "Chapter 37"
        assert series.chapters[-1]["url"] == (
            f"https://stonescape.xyz/series/{SLUG}/ch-37"
        )

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        session = self._session(self._series_handler(chapters=[]))
        with pytest.raises(ValueError, match="No chapters found"):
            await StoneScapeScraper().scrape_series(self.SERIES_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_series_missing_404_is_friendly(self):
        session = _MockSession(
            lambda url: _MockResponse(
                json_data={"message": "Series not found"}, status=404
            )
        )
        with pytest.raises(ValueError, match="Not found on StoneScape"):
            await StoneScapeScraper().scrape_series(
                "https://stonescape.xyz/series/gone-series", session
            )
