from __future__ import annotations

import json

import pytest

from comic_dl.scrapers.sites.mangadex import (
    DOMAIN,
    MangadexScraper,
    _chapter_sort_key,
    _extract_chapter_id,
    _extract_series_id,
    _feed_url,
    _genre_names,
    _manga_fields,
    _page_files,
    _title_of,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

MANGA_ID = "11111111-2222-3333-4444-555555555555"
CHAPTER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

MANGA_DETAIL = {
    "result": "ok",
    "response": "entity",
    "data": {
        "id": MANGA_ID,
        "type": "manga",
        "attributes": {
            "title": {"en": "Test Series", "ja-ro": "Tesuto Shirizu"},
            "description": {"en": "A test series description."},
            "status": "ongoing",
            "year": 2020,
            "rating": 8.5,
            "tags": [
                {"id": "t1", "type": "tag",
                 "attributes": {"name": {"en": "Action"}, "group": "genre"}},
                {"id": "t2", "type": "tag",
                 "attributes": {"name": {"en": "Adventure"}, "group": "genre"}},
                {"id": "t3", "type": "tag",
                 "attributes": {"name": {"en": "Time Skip"}, "group": "theme"}},
            ],
        },
        "relationships": [
            {"id": "c1", "type": "cover_art",
             "attributes": {"fileName": "abc123.jpg"}},
            {"id": "a1", "type": "author", "attributes": {"name": "Author One"}},
            {"id": "a2", "type": "artist", "attributes": {"name": "Artist One"}},
        ],
    },
}

CHAPTER_DETAIL = {
    "result": "ok",
    "response": "entity",
    "data": {
        "id": CHAPTER_ID,
        "type": "chapter",
        "attributes": {
            "volume": "1",
            "chapter": "5",
            "title": "Test Chapter",
            "translatedLanguage": "en",
        },
        "relationships": [{"id": MANGA_ID, "type": "manga"}],
    },
}

AT_HOME = {
    "result": "ok",
    "response": "entity",
    "baseUrl": "https://node-a.mangadex.network",
    "chapter": {
        "hash": "deadbeefdeadbeefdeadbeefdeadbeef",
        "data": ["001.jpg", "002.jpg", "003.jpg"],
        "dataSaver": ["001.jpg", "002.jpg", "003.jpg"],
    },
}

FEED = {
    "result": "ok",
    "response": "collection",
    "data": [
        {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee1", "type": "chapter",
         "attributes": {"volume": "1", "chapter": "3", "title": None,
                        "translatedLanguage": "en"}},
        {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee2", "type": "chapter",
         "attributes": {"volume": "1", "chapter": "5", "title": "Test Chapter",
                        "translatedLanguage": "en"}},
        {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee3", "type": "chapter",
         "attributes": {"volume": "2", "chapter": "1", "title": "Vol Two",
                        "translatedLanguage": "en"}},
        {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee4", "type": "chapter",
         "attributes": {"volume": "1", "chapter": "10", "title": None,
                        "translatedLanguage": "en",
                        "externalUrl": "https://mangaplus.shueisha.co.jp/x"}},
    ],
    "limit": 500,
    "offset": 0,
    "total": 4,
}


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(
            f"https://mangadex.org/title/{MANGA_ID}"
        )
        assert is_series_url(
            f"https://www.mangadex.org/title/{MANGA_ID}/"
        )
        assert is_series_url(
            f"https://mangadex.org/manga/{MANGA_ID}"
        )

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://mangadex.org/")
        assert not is_series_url("https://mangadex.org/title/not-a-uuid")
        assert not is_series_url("https://mangadex.org/chapter/abc")
        assert not is_series_url("https://other.com/title/abc")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(
            f"https://mangadex.org/chapter/{CHAPTER_ID}"
        )
        assert is_chapter_url(
            f"https://www.mangadex.org/chapter/{CHAPTER_ID}/"
        )

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://mangadex.org/chapter/abc")
        assert not is_chapter_url("https://mangadex.org/title/abc")
        assert not is_chapter_url("https://other.com/chapter/abc")

    def test_matches_url(self):
        scraper = MangadexScraper()
        assert scraper.matches_url(f"https://mangadex.org/title/{MANGA_ID}")
        assert scraper.matches_url(f"https://mangadex.org/chapter/{CHAPTER_ID}")
        assert not scraper.matches_url("https://mangadex.org/")

    def test_id_extraction(self):
        assert _extract_series_id(f"https://mangadex.org/title/{MANGA_ID}/") == MANGA_ID
        assert _extract_series_id(f"https://mangadex.org/manga/{MANGA_ID}") == MANGA_ID
        assert _extract_chapter_id(f"https://mangadex.org/chapter/{CHAPTER_ID}/") == CHAPTER_ID
        assert _extract_series_id("https://mangadex.org/") is None
        assert _extract_chapter_id("https://mangadex.org/") is None


class TestHelpers:
    def test_title_of_prefers_en(self):
        assert _title_of({"ja-ro": "Foo", "en": "Bar"}) == "Bar"
        assert _title_of({"ja-ro": "Foo"}) == "Foo"
        assert _title_of(None) == ""
        assert _title_of({}) == ""

    def test_manga_fields(self):
        meta = _manga_fields(MANGA_DETAIL["data"], MANGA_ID)
        assert meta["series_title"] == "Test Series"
        assert meta["description"] == "A test series description."
        assert meta["genres"] == ["Action", "Adventure"]
        assert meta["authors"] == ["Author One"]
        assert meta["artists"] == ["Artist One"]
        assert meta["status"] == "ongoing"
        assert meta["year"] == 2020
        assert meta["community_rating"] == 8.5
        assert meta["cover_url"] == (
            f"https://uploads.mangadex.org/covers/{MANGA_ID}/abc123.jpg"
        )

    def test_manga_fields_missing_cover(self):
        data = {"id": MANGA_ID, "type": "manga", "attributes": {"title": {"en": "X"}},
                "relationships": []}
        meta = _manga_fields(data, MANGA_ID)
        assert meta["cover_url"] == ""
        assert meta["series_title"] == "X"

    def test_genre_names_excludes_themes(self):
        assert _genre_names(MANGA_DETAIL["data"]) == ["Action", "Adventure"]

    def test_page_files(self):
        assert _page_files(AT_HOME) == ["001.jpg", "002.jpg", "003.jpg"]
        assert _page_files({"chapter": {"data": []}}) == []
        assert _page_files(None) == []

    def test_chapter_sort_key(self):
        assert _chapter_sort_key("10") > _chapter_sort_key("2")
        assert _chapter_sort_key("1.5") > _chapter_sort_key("1")
        assert _chapter_sort_key("Extra") > _chapter_sort_key("10")
        assert _chapter_sort_key(None) > _chapter_sort_key("Extra")

    def test_feed_url_includes_all_ratings(self):
        url = _feed_url(MANGA_ID, 0)
        assert "translatedLanguage[]=en" in url
        assert "contentRating[]=safe" in url
        assert "contentRating[]=pornographic" in url
        assert "limit=500" in url


class TestMangadexScraper:
    SERIES_URL = f"https://mangadex.org/title/{MANGA_ID}"
    CHAPTER_URL = f"https://mangadex.org/chapter/{CHAPTER_ID}"

    def _handler(self, url):
        if "at-home" in url:
            return _MockResponse(json.dumps(AT_HOME))
        if f"/manga/{MANGA_ID}" in url and "includes[]=" in url:
            return _MockResponse(json.dumps(MANGA_DETAIL))
        if f"/chapter/{CHAPTER_ID}" in url:
            return _MockResponse(json.dumps(CHAPTER_DETAIL))
        if f"/manga/{MANGA_ID}/feed" in url:
            return _MockResponse(json.dumps(FEED))
        raise AssertionError(f"unexpected URL: {url}")

    def test_domain_attr(self):
        scraper = MangadexScraper()
        assert scraper.domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_chapter(self):
        session = _MockSession(self._handler)
        scraper = MangadexScraper()
        meta = await scraper.scrape(self.CHAPTER_URL, session)

        assert meta.series_title == "Test Series"
        assert meta.chapter_title == "Test Chapter"
        assert meta.chapter_number == "5"
        assert meta.volume_number == "1"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.post_id == CHAPTER_ID
        assert meta.authors == ["Author One"]
        assert meta.artists == ["Artist One"]
        assert meta.genres == ["Action", "Adventure"]
        assert meta.status == "ongoing"
        assert meta.community_rating == 8.5
        assert meta.year == 2020
        assert meta.total_pages == 3
        assert len(meta.images) == 3
        assert meta.images[0].page_number == 1
        assert meta.images[0].url == (
            "https://node-a.mangadex.network/data/"
            "deadbeefdeadbeefdeadbeefdeadbeef/001.jpg"
        )

    @pytest.mark.asyncio
    async def test_scrape_chapter_falls_back_to_chapter_number_title(self):
        detail = json.loads(json.dumps(CHAPTER_DETAIL))
        detail["data"]["attributes"]["title"] = None

        def handler(url):
            if "includes[]=" in url:
                return _MockResponse(json.dumps(MANGA_DETAIL))
            if "at-home" in url:
                return _MockResponse(json.dumps(AT_HOME))
            if "/chapter/" in url:
                return _MockResponse(json.dumps(detail))
            raise AssertionError(f"unexpected URL: {url}")

        session = _MockSession(handler)
        scraper = MangadexScraper()
        meta = await scraper.scrape(self.CHAPTER_URL, session)
        assert meta.chapter_title == "Chapter 5"

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        empty = json.loads(json.dumps(AT_HOME))
        empty["chapter"]["data"] = []

        def handler(url):
            if "includes[]=" in url:
                return _MockResponse(json.dumps(MANGA_DETAIL))
            if "at-home" in url:
                return _MockResponse(json.dumps(empty))
            if "/chapter/" in url:
                return _MockResponse(json.dumps(CHAPTER_DETAIL))
            raise AssertionError(f"unexpected URL: {url}")

        session = _MockSession(handler)
        scraper = MangadexScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(self.CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_series_orders_and_skips_external(self):
        session = _MockSession(self._handler)
        scraper = MangadexScraper()
        series = await scraper.scrape_series(self.SERIES_URL, session)

        assert series.series_title == "Test Series"
        assert series.description == "A test series description."
        assert series.cover_url == (
            f"https://uploads.mangadex.org/covers/{MANGA_ID}/abc123.jpg"
        )
        assert series.title_no == MANGA_ID
        assert len(series.chapters) == 3
        # volume asc, then chapter asc; external chapter skipped
        assert [c["episode_no"] for c in series.chapters] == ["3", "5", "1"]
        assert series.chapters[-1]["url"].endswith("eeeeeeeeeee3")
        assert all("externalUrl" not in c for c in series.chapters)

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        empty = json.loads(json.dumps(FEED))
        empty["data"] = []
        empty["total"] = 0

        def handler(url):
            if "includes[]=" in url:
                return _MockResponse(json.dumps(MANGA_DETAIL))
            if "/feed" in url:
                return _MockResponse(json.dumps(empty))
            raise AssertionError(f"unexpected URL: {url}")

        session = _MockSession(handler)
        scraper = MangadexScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(self.SERIES_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_series_unknown_manga_raises(self):
        err = {
            "result": "error",
            "errors": [{"detail": "Manga not found."}],
        }

        def handler(url):
            return _MockResponse(json.dumps(err))

        session = _MockSession(handler)
        scraper = MangadexScraper()
        with pytest.raises(ValueError, match="reported an error"):
            await scraper.scrape_series(self.SERIES_URL, session)
