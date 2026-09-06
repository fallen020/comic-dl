from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.toonily import (
    DOMAIN,
    ToonilyScraper,
    _chapter_number_from_url,
    _extract_genres,
    _extract_images,
    _extract_lang,
    _extract_meta_rows,
    _extract_post_id,
    _extract_rating,
    _extract_series_slug,
    _extract_series_title,
    _extract_status,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "ogh-reboot-fa534433"
SERIES_URL = f"https://toonily.com/serie/{SLUG}/"
CHAPTER_URL = f"https://toonily.com/serie/{SLUG}/chapter-7/"
IMG = "https://data.tnlycdn.com/chapters/manga_69bf365f88cba/53ea32b22c6af470bb4b7896e5975e5e/0"
COVER = "https://static.tnlycdn.com/2026/03/Ogh-Reboot-manhwa.jpg"

SERIES_PAGE = f"""
<html lang="en-US"><head>
    <title>Read Ogh Reboot Manhwa Online - All Chapters Free | Toonily</title>
    <meta property="og:title" content="Read Ogh Reboot Manhwa Online - All Chapters Free | Toonily"/>
    <meta property="og:image" content="{COVER}"/>
    <meta property="og:description" content="A synopsis of Ogh Reboot."/>
</head><body class="archive single postid-90000">
<div class="post-title"><h1>Ogh Reboot <span class="manga-title-badges hot">HOT</span></h1></div>
<div class="summary__content show-more"><p>What's important in living life is dopamine!</p></div>
<div class="manga-info-row"><span property="ratingValue" id="averagerate">4.2</span></div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Alt Name(s)</h5></div>
    <div class="summary-content">Ogok Reboot</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Writer(s)</h5></div>
    <div class="summary-content">Human Fodder</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Artist(s)</h5></div>
    <div class="summary-content">Human Fodder</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Genre(s)</h5></div>
    <div class="summary-content"><div class="genres-content"><a href="/genre/mature" rel="tag">Mature</a></div></div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Publisher</h5></div>
    <div class="summary-content">Updating</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Status</h5></div>
    <div class="summary-content">OnGoing</div>
</div>
<div class="listing-chapters_wrap"><ul class="main version-chap">
    <li class="wp-manga-chapter"><a href="{CHAPTER_URL}">Chapter 7</a></li>
    <li class="wp-manga-chapter"><a href="https://toonily.com/serie/{SLUG}/chapter-6/">Chapter 6</a></li>
    <li class="wp-manga-chapter"><a href="https://toonily.com/serie/{SLUG}/chapter-1/">Chapter 1</a></li>
</ul></div>
</body></html>
"""

CHAPTER_PAGE = f"""
<html lang="en-US"><head>
    <title>Read Ogh Reboot - Chapter 7 English Online Free - Toonily</title>
    <meta property="og:title" content="Read Ogh Reboot - Chapter 7 English Online Free - Toonily"/>
    <meta property="og:image" content="{COVER}"/>
</head><body class="single single-wp-manga postid-91625 reading-manga">
<div class="post-title"><h1>Ogh Reboot Chapter 7</h1></div>
<div class="read-container">
    <div class="reading-content">
        <div class="page-break no-gaps"><img id="image-0" src="{IMG}1.jpg" class="wp-manga-chapter-img img-responsive"/></div>
        <div class="page-break no-gaps"><img id="image-1" src="{IMG}2.jpg" class="wp-manga-chapter-img img-responsive"/></div>
        <div class="page-break no-gaps"><img id="image-2" src="{IMG}3.jpg" class="wp-manga-chapter-img img-responsive"/></div>
        <img src="https://toonily.com/images/logo.png" class="not-chapter"/>
    </div>
</div>
<img src="{COVER}" class="cover-something"/>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url("https://www.toonily.com/serie/foo-bar/")
        assert is_series_url("https://toonily.com/serie/foo-bar")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://toonily.com/")
        assert not is_series_url("https://toonily.com/serie/")
        assert not is_series_url("https://toonily.com/serie/foo/chapter-7/")
        assert not is_series_url("https://other.com/serie/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://toonily.com/serie/foo/chapter-25/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://toonily.com/serie/foo")
        assert not is_chapter_url("https://toonily.com/serie/foo/bar/")
        assert not is_chapter_url("https://other.com/serie/foo/chapter-1")

    def test_matches_url(self):
        scraper = ToonilyScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://toonily.com/")

    def test_series_slug(self):
        assert _extract_series_slug(CHAPTER_URL) == SLUG
        assert _extract_series_slug(SERIES_URL) == SLUG
        assert _extract_series_slug("https://toonily.com/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_url(CHAPTER_URL) == "7"
        assert _chapter_number_from_url("https://toonily.com/serie/x/chapter-25/") == "25"
        assert _chapter_number_from_url(SERIES_URL) is None


class TestExtraction:
    def test_series_title_strips_badge(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup, {}) == "Ogh Reboot"

    def test_meta_rows_keyed_lowercase(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        rows = _extract_meta_rows(soup)
        assert rows["genre(s)"] == ["Mature"]
        assert rows["status"] == ["OnGoing"]
        assert rows["publisher"] == ["Updating"]

    def test_genres_and_status(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_genres(soup) == ["Mature"]
        assert _extract_status(soup) == "OnGoing"

    def test_rating(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_rating(soup) == 4.2

    def test_rating_none_when_missing(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_rating(soup) is None

    def test_post_id(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _extract_post_id(soup) == "91625"

    def test_lang(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_lang(soup) == "en"

    def test_images_only_from_cdn_inside_reading_container(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3
        assert images[0].url == f"{IMG}1.jpg"
        assert [i.page_number for i in images] == [1, 2, 3]

    def test_no_images(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []


class TestToonilyScraper:
    def test_domain_attr(self):
        scraper = ToonilyScraper()
        assert scraper.domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_chapter_with_enrichment(self):
        def handler(url):
            if url == f"https://toonily.com/serie/{SLUG}/":
                return _MockResponse(SERIES_PAGE)
            if url == CHAPTER_URL:
                return _MockResponse(CHAPTER_PAGE)
            raise AssertionError(f"unexpected URL: {url}")

        session = _MockSession(handler)
        scraper = ToonilyScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "Ogh Reboot"
        assert meta.chapter_title == "Chapter 7"
        assert meta.chapter_number == "7"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.post_id == "91625"
        assert meta.artists == ["Human Fodder"]
        assert meta.genres == ["Mature"]
        assert meta.status == "OnGoing"
        assert meta.publisher == "Updating"
        assert meta.community_rating == 4.2
        assert meta.description == "What's important in living life is dopamine!"
        assert meta.cover_url == COVER
        assert meta.total_pages == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        page = CHAPTER_PAGE.replace(f"<img id=\"image-0\" src=\"{IMG}1.jpg\"", "")
        page = page.replace(f"<img id=\"image-1\" src=\"{IMG}2.jpg\"", "")
        page = page.replace(f"<img id=\"image-2\" src=\"{IMG}3.jpg\"", "")

        def handler(url):
            return _MockResponse(page)

        session = _MockSession(handler)
        scraper = ToonilyScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url == f"https://toonily.com/serie/{SLUG}/":
                raise ConnectionError("boom")
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = ToonilyScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)
        assert meta.series_title == "Untitled"
        assert meta.chapter_title == "Ogh Reboot Chapter 7"
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(SERIES_PAGE))
        scraper = ToonilyScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert series.series_title == "Ogh Reboot"
        assert series.description == "What's important in living life is dopamine!"
        assert series.cover_url == COVER
        assert series.title_no == SLUG
        assert len(series.chapters) == 3
        # ascending: chapter 1 first
        assert series.chapters[0]["episode_no"] == "1"
        assert series.chapters[-1]["episode_no"] == "7"
        assert series.chapters[-1]["url"] == CHAPTER_URL

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        page = SERIES_PAGE.replace('<div class="listing-chapters_wrap">', "")
        session = _MockSession(lambda url: _MockResponse(page))
        scraper = ToonilyScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, session)
