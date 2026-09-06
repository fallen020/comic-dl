from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.manhwaz import (
    DOMAIN,
    ManhwazScraper,
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

SLUG = "nano-machine-how-038"
SERIES_URL = f"https://manhwaz.com/webtoon/{SLUG}"
CHAPTER_URL = f"https://manhwaz.com/webtoon/{SLUG}/chapter-326"
IMG = "https://cdn.manhwaz.com/manga/41/326/"
COVER = "https://manhwaz.com/storage/images/cover/f1a0a8698d02e0cb7fd3b77a1c708026.jpeg"

SERIES_PAGE = f"""
<html lang="en"><head>
    <title>Nano Machine - ManhwaZ</title>
    <meta property="og:image" content="{COVER}"/>
    <meta property="og:description" content="After being held in disdain, an orphan from the Demonic Cult&hellip;"/>
</head><body class="archive single postid-41">
<h1>Nano Machine</h1>
<div class="summary__content"><p>After being held in disdain and having his life put in danger, an orphan from the Demonic Cult, Cheon Yeo-Woon, has an unexpected visit from his descendant.</p></div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Alternative</h5></div>
    <div class="summary-content">Updating</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Author(s)</h5></div>
    <div class="summary-content">Updating</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>status</h5></div>
    <div class="summary-content">Ongoing</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Genre(s)</h5></div>
    <div class="summary-content"><div class="genres-content"><a href="/genre/fantasy" rel="tag">Fantasy</a><a href="/genre/manhwa" rel="tag">Manhwa</a></div></div>
</div>
<ul class="list-item box-list-chapter limit-height">
    <li class="wp-manga-chapter"><a href="{CHAPTER_URL}">Chapter 326</a></li>
    <li class="wp-manga-chapter"><a href="https://manhwaz.com/webtoon/{SLUG}/chapter-325">Chapter 325</a></li>
    <li class="wp-manga-chapter"><a href="https://manhwaz.com/webtoon/{SLUG}/chapter-0">Chapter supreme</a></li>
    <li class="wp-manga-chapter"><a href="https://manhwaz.com/webtoon/other-series/chapter-5">Chapter 5</a></li>
</ul>
</body></html>
"""

CHAPTER_PAGE = f"""
<html lang="en"><head>
    <title>Nano Machine Chapter 326 - ManhwaZ</title>
    <meta property="og:image" content="{COVER}"/>
</head><body class="single single-wp-manga postid-7001 reading-manga">
<h1>Nano Machine - Chapter 326</h1>
<div class="read-container">
    <div class="reading-content">
        <div class="page-break "><img id="image-0" src="{IMG}6a85f8738a610.jpg" class="chapter-img img-responsive"/></div>
        <div class="page-break "><img id="image-1" src="{IMG}6a85f87369457.jpg" class="chapter-img img-responsive"/></div>
        <div class="page-break "><img id="image-2" src="{IMG}6a85f8736b92d.jpg" class="chapter-img img-responsive"/></div>
    </div>
</div>
<img src="https://manhwaz.com/images/logo.png"/>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url(f"{SERIES_URL}/")
        assert is_series_url("https://www.manhwaz.com/webtoon/foo")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://manhwaz.com/")
        assert not is_series_url("https://manhwaz.com/webtoon/")
        assert not is_series_url("https://manhwaz.com/webtoon/foo/chapter-1")
        assert not is_series_url("https://other.com/webtoon/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://manhwaz.com/webtoon/foo/chapter-25/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://manhwaz.com/webtoon/foo")
        assert not is_chapter_url("https://manhwaz.com/webtoon/foo/bar")
        assert not is_chapter_url("https://other.com/webtoon/foo/chapter-1")

    def test_matches_url(self):
        scraper = ManhwazScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://manhwaz.com/")

    def test_series_slug(self):
        assert _extract_series_slug(CHAPTER_URL) == SLUG
        assert _extract_series_slug(SERIES_URL) == SLUG
        assert _extract_series_slug("https://manhwaz.com/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_url(CHAPTER_URL) == "326"
        assert _chapter_number_from_url("https://manhwaz.com/webtoon/x/chapter-25") == "25"
        assert _chapter_number_from_url(SERIES_URL) is None


class TestExtraction:
    def test_series_title_from_h1(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup, {}) == "Nano Machine"

    def test_meta_rows_keyed_lowercase(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        rows = _extract_meta_rows(soup)
        assert rows["author(s)"] == ["Updating"]
        assert rows["status"] == ["Ongoing"]
        assert rows["genre(s)"] == ["Fantasy", "Manhwa"]

    def test_authors_genres_status(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_authors(soup) == ["Updating"]
        assert _extract_genres(soup) == ["Fantasy", "Manhwa"]
        assert _extract_status(soup) == "Ongoing"

    def test_post_id(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _extract_post_id(soup) == "7001"

    def test_lang(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_lang(soup) == "en"

    def test_images_only_from_cdn(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3
        assert images[0].url == f"{IMG}6a85f8738a610.jpg"
        assert [i.page_number for i in images] == [1, 2, 3]

    def test_no_images(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []


class TestManhwazScraper:
    def test_domain_attr(self):
        scraper = ManhwazScraper()
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
        scraper = ManhwazScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "Nano Machine"
        assert meta.chapter_title == "Chapter 326"
        assert meta.chapter_number == "326"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.post_id == "7001"
        assert meta.authors == ["Updating"]
        assert meta.genres == ["Fantasy", "Manhwa"]
        assert meta.status == "Ongoing"
        assert meta.description.startswith("After being held in disdain")
        assert meta.cover_url == COVER
        assert meta.total_pages == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        page = CHAPTER_PAGE.replace(
            f'<img id="image-0" src="{IMG}6a85f8738a610.jpg"', ""
        ).replace(
            f'<img id="image-1" src="{IMG}6a85f87369457.jpg"', ""
        ).replace(
            f'<img id="image-2" src="{IMG}6a85f8736b92d.jpg"', ""
        )

        def handler(url):
            return _MockResponse(page)

        session = _MockSession(handler)
        scraper = ManhwazScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url == SERIES_URL:
                raise ConnectionError("boom")
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = ManhwazScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)
        assert meta.series_title == "Untitled"
        assert meta.chapter_title == "Nano Machine - Chapter 326"
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(SERIES_PAGE))
        scraper = ManhwazScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert series.series_title == "Nano Machine"
        assert series.description.startswith("After being held in disdain")
        assert series.cover_url == COVER
        assert series.title_no == SLUG
        assert len(series.chapters) == 3
        # ascending; other-series link is excluded
        assert series.chapters[0]["episode_no"] == "0"
        assert series.chapters[0]["title"] == "Chapter supreme"
        assert series.chapters[-1]["episode_no"] == "326"
        assert series.chapters[-1]["url"] == CHAPTER_URL

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        page = SERIES_PAGE.replace('<ul class="list-item box-list-chapter limit-height">', "")
        session = _MockSession(lambda url: _MockResponse(page))
        scraper = ManhwazScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, session)
