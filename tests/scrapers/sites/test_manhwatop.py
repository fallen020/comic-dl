from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.manhwatop import (
    DOMAIN,
    ManhwaTopScraper,
    _chapter_number_from_url,
    _extract_authors,
    _extract_genres,
    _extract_images,
    _extract_lang,
    _extract_meta_rows,
    _extract_post_id,
    _extract_series_slug,
    _extract_series_title,
    _extract_status,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "nano-machine-series-manhwa"
SERIES_URL = f"https://manhwatop.com/manga/{SLUG}/"
CHAPTER_URL = f"https://manhwatop.com/manga/{SLUG}/chapter-328/"
IMG_BASE = "https://c4.manhwatop.com/manga_5cd5058bca53951ffa7801bcdf421651/chapter_330/"
COVER = "https://manhwatop.com/wp-content/uploads/2020/04/Nano-Machine-cover-175x238.webp"

SERIES_PAGE = f"""
<html lang="en"><head>
    <title>Nano Machine - ManhwaTop</title>
    <meta property="og:image" content="{COVER}"/>
    <meta property="og:description" content="After being held in disdain and having his life put in danger..."/>
</head><body class="single single-wp-manga postid-12345">
<div class="post-title">
    <h1>Nano Machine</h1>
</div>
<div class="summary_image">
    <img src="{COVER}" alt="Nano Machine">
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Rating</h5></div>
    <div class="summary-content">4.6</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Author(s)</h5></div>
    <div class="summary-content"><a href="/manga-author/han-joong-wueol-ya/">Han joong wueol ya</a></div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Artist(s)</h5></div>
    <div class="summary-content"><a href="/manga-artist/guem-gang-bul-gae/">Guem-Gang-Bul-Gae</a></div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Genre(s)</h5></div>
    <div class="summary-content">
        <div class="genres-content">
            <a href="/manga-genre/genre-action-new-genre/" rel="tag">Action</a>,
            <a href="/manga-genre/adventure-genre-hot/" rel="tag">Adventure</a>,
            <a href="/manga-genre/fantasy-genre-hot/" rel="tag">Fantasy</a>
        </div>
    </div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Type</h5></div>
    <div class="summary-content">Manhwa</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Status</h5></div>
    <div class="summary-content">OnGoing</div>
</div>
<div class="summary__content">
    <p>After being held in disdain and having his life put in danger, an orphan from the Demonic Cult...</p>
</div>
<div class="page-content-listing single-page">
    <div class="listing-chapters_wrap cols-1">
        <ul class="main version-chap no-volumn">
            <li class="wp-manga-chapter">
                <a href="{CHAPTER_URL}">Chapter 328</a>
            </li>
            <li class="wp-manga-chapter">
                <a href="https://manhwatop.com/manga/{SLUG}/chapter-327/">Chapter 327</a>
            </li>
        </ul>
    </div>
</div>
</body></html>
"""

CHAPTER_PAGE = f"""
<html lang="en"><head>
    <title>Nano Machine - Lastest Chapter 328 - MANHWATOP</title>
    <meta property="og:image" content="{COVER}"/>
</head><body class="single single-wp-manga postid-67890 reading-manga">
<h1>Nano Machine - Chapter 328</h1>
<div class="read-container">
    <div class="reading-content">
        <div class="page-break "><img src="https://manhwatop.com/wp-content/themes/madara/images/loading6.svg" data-src="{IMG_BASE}ch_330_1.jpg" class="wp-manga-chapter-img"/></div>
        <div class="page-break "><img src="https://manhwatop.com/wp-content/themes/madara/images/loading6.svg" data-src="{IMG_BASE}ch_330_2.jpg" class="wp-manga-chapter-img"/></div>
        <div class="page-break "><img src="https://manhwatop.com/wp-content/themes/madara/images/loading6.svg" data-src="{IMG_BASE}ch_330_3.jpg" class="wp-manga-chapter-img"/></div>
    </div>
</div>
<img src="https://manhwatop.com/wp-content/uploads/2020/03/LOGO.png"/>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url("https://www.manhwatop.com/manga/foo")
        assert is_series_url("https://manhwatop.com/manga/foo/")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://manhwatop.com/")
        assert not is_series_url("https://manhwatop.com/manga/")
        assert not is_series_url(CHAPTER_URL)
        assert not is_series_url("https://other.com/manga/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://manhwatop.com/manga/foo/chapter-1/")
        assert is_chapter_url("https://manhwatop.com/manga/foo/chapter-25.5/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://manhwatop.com/manga/foo")
        assert not is_chapter_url("https://manhwatop.com/manga/foo/")
        assert not is_chapter_url("https://other.com/manga/foo/chapter-1/")

    def test_matches_url(self):
        scraper = ManhwaTopScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://manhwatop.com/")

    def test_series_slug(self):
        assert _extract_series_slug(CHAPTER_URL) == SLUG
        assert _extract_series_slug(SERIES_URL) == SLUG
        assert _extract_series_slug("https://manhwatop.com/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_url(CHAPTER_URL) == "328"
        assert _chapter_number_from_url("https://manhwatop.com/manga/foo/chapter-25/") == "25"
        assert _chapter_number_from_url("https://manhwatop.com/manga/foo/chapter-25.5/") == "25.5"
        assert _chapter_number_from_url(SERIES_URL) is None


class TestExtraction:
    def test_series_title_from_h1(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup, {}) == "Nano Machine"

    def test_meta_rows_keyed_lowercase(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        rows = _extract_meta_rows(soup)
        assert rows["author(s)"] == ["Han joong wueol ya"]
        assert rows["artist(s)"] == ["Guem-Gang-Bul-Gae"]
        assert rows["status"] == ["OnGoing"]
        assert rows["genre(s)"] == ["Action", "Adventure", "Fantasy"]

    def test_authors_genres_status(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        # manhwatop has separate author(s) and artist(s) - merged
        assert _extract_authors(soup) == ["Han joong wueol ya", "Guem-Gang-Bul-Gae"]
        assert _extract_genres(soup) == ["Action", "Adventure", "Fantasy"]
        assert _extract_status(soup) == "OnGoing"

    def test_post_id(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _extract_post_id(soup) == "67890"

    def test_lang(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_lang(soup) == "en"

    def test_images_only_from_host(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3
        assert images[0].url == f"{IMG_BASE}ch_330_1.jpg"
        assert images[1].url == f"{IMG_BASE}ch_330_2.jpg"
        assert images[2].url == f"{IMG_BASE}ch_330_3.jpg"
        assert [i.page_number for i in images] == [1, 2, 3]

    def test_no_images(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []


class TestManhwaTopScraper:
    def test_domain_attr(self):
        scraper = ManhwaTopScraper()
        assert scraper.domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_chapter_with_enrichment(self):
        def handler(url):
            if url == SERIES_URL:
                return _MockResponse(SERIES_PAGE)
            if url == CHAPTER_URL:
                return _MockResponse(CHAPTER_PAGE)
            raise AssertionError(f"unexpected URL: {url}")

        session = _MockSession(handler)
        scraper = ManhwaTopScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "Nano Machine"
        assert meta.chapter_title == "Chapter 328"
        assert meta.chapter_number == "328"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.post_id == "67890"
        assert meta.authors == ["Han joong wueol ya", "Guem-Gang-Bul-Gae"]
        assert meta.genres == ["Action", "Adventure", "Fantasy"]
        assert meta.status == "OnGoing"
        assert meta.description.startswith("After being held in disdain")
        assert meta.cover_url == COVER
        assert meta.total_pages == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        page = CHAPTER_PAGE.replace(
            f'data-src="{IMG_BASE}ch_330_1.jpg"', 'data-src=""'
        ).replace(
            f'data-src="{IMG_BASE}ch_330_2.jpg"', 'data-src=""'
        ).replace(
            f'data-src="{IMG_BASE}ch_330_3.jpg"', 'data-src=""'
        ).replace(
            'src="https://manhwatop.com/wp-content/themes/madara/images/loading6.svg"', 'src=""'
        )

        def handler(url):
            return _MockResponse(page)

        session = _MockSession(handler)
        scraper = ManhwaTopScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url == SERIES_URL:
                raise ConnectionError("boom")
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = ManhwaTopScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)
        assert meta.series_title == "Untitled"
        assert meta.chapter_title == "Nano Machine - Chapter 328"
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(SERIES_PAGE))
        scraper = ManhwaTopScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert series.series_title == "Nano Machine"
        assert series.description.startswith("After being held in disdain")
        assert series.cover_url == COVER
        assert series.title_no == SLUG
        assert len(series.chapters) == 2
        # ascending order
        assert series.chapters[0]["episode_no"] == "327"
        assert series.chapters[0]["title"] == "Chapter 327"
        assert series.chapters[-1]["episode_no"] == "328"
        assert series.chapters[-1]["url"] == CHAPTER_URL

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        page = SERIES_PAGE.replace('<div class="listing-chapters_wrap cols-1">', "")
        session = _MockSession(lambda url: _MockResponse(page))
        scraper = ManhwaTopScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, session)
