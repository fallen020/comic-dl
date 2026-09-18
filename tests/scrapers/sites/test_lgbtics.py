from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.lgbtics import (
    DOMAIN,
    LgbticsScraper,
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

SLUG = "secretary-scara2b"
SERIES_URL = f"https://lgbtics.com/comic/{SLUG}/"
CHAPTER_URL = f"https://lgbtics.com/comic/{SLUG}/1-secretary-chapter-1-scara2b/"
IMG_BASE = "https://lgbtics.com/wp-content/uploads/WP-manga/data/1-secretary-chapter-1-scara2b/"
COVER = "https://lgbtics.com/wp-content/uploads/2026/09/Secretary-scara2b-193x278.webp"

SERIES_PAGE = f"""
<html lang="en"><head>
    <title>Secretary! [scara2b] - Lgbtics</title>
    <meta property="og:image" content="{COVER}"/>
    <meta property="og:description" content="A femboy secretary porn comic by scara2b."/>
</head><body class="single single-wp-manga postid-7880">
<div class="post-title">
    <h1>Secretary! [scara2b]</h1>
</div>
<div class="summary_image">
    <img src="{COVER}" alt="Secretary! [scara2b]">
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Rating</h5></div>
    <div class="summary-content">5</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Rank</h5></div>
    <div class="summary-content">N/A, it has 281 views</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Artists</h5></div>
    <div class="summary-content"><a href="/comic-artist/scara2b/" rel="tag">Scara2b</a></div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Genres</h5></div>
    <div class="summary-content">
        <div class="genres-content">
            <a href="/comic-genre/ai-generated/" rel="tag">Ai Generated</a>,
            <a href="/comic-genre/anal/" rel="tag">Anal</a>,
            <a href="/comic-genre/blowjob/" rel="tag">Blowjob</a>
        </div>
    </div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Type</h5></div>
    <div class="summary-content">Ai Generated</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Status</h5></div>
    <div class="summary-content">Completed</div>
</div>
<div class="summary__content">
    <p>A femboy secretary porn comic by scara2b.</p>
    <p>patreon.com/cw/scara2b</p>
</div>
<div class="page-content-listing single-page">
    <div class="listing-chapters_wrap cols-1">
        <ul class="main version-chap no-volumn">
            <li class="wp-manga-chapter">
                <a href="{CHAPTER_URL}">1 . Secretary! - Chapter 1 [scara2b]</a>
            </li>
        </ul>
    </div>
</div>
</body></html>
"""

CHAPTER_PAGE = f"""
<html lang="en"><head>
    <title>Secretary! [scara2b] - 1 . Secretary! - Chapter 1 [scara2b] - Lgbtics</title>
    <meta property="og:image" content="{COVER}"/>
</head><body class="single single-wp-manga postid-5391 reading-manga">
<h1>1 . Secretary! - Chapter 1 [scara2b]</h1>
<div class="read-container">
    <div class="reading-content">
        <div class="page-break "><img id="image-0" src=" {IMG_BASE}001---Image.webp" class="wp-manga-chapter-img"/></div>
        <div class="page-break "><img id="image-1" src=" {IMG_BASE}002---Image.webp" class="wp-manga-chapter-img"/></div>
        <div class="page-break "><noscript><img src=" {IMG_BASE}003---Image.webp" class="wp-manga-chapter-img"/></noscript><img src="data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20viewBox=%220%200%20210%20140%22%3E%3C/svg%3E" id="image-2" data-src=" {IMG_BASE}003---Image.webp" class="lazyload wp-manga-chapter-img"/></div>
    </div>
</div>
<img src="https://lgbtics.com/wp-content/uploads/2024/08/Logo_2a.png"/>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url("https://www.lgbtics.com/comic/foo")
        assert is_series_url("https://lgbtics.com/comic/foo/")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://lgbtics.com/")
        assert not is_series_url("https://lgbtics.com/comic/")
        assert not is_series_url(CHAPTER_URL)
        assert not is_series_url("https://other.com/comic/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://lgbtics.com/comic/foo/bar-chapter-1/")
        assert is_chapter_url("https://lgbtics.com/comic/foo/part-5/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://lgbtics.com/comic/foo")
        assert not is_chapter_url("https://lgbtics.com/comic/foo/")
        assert not is_chapter_url("https://other.com/comic/foo/bar")

    def test_matches_url(self):
        scraper = LgbticsScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://lgbtics.com/")

    def test_series_slug(self):
        assert _extract_series_slug(CHAPTER_URL) == SLUG
        assert _extract_series_slug(SERIES_URL) == SLUG
        assert _extract_series_slug("https://lgbtics.com/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_url(CHAPTER_URL) == "1"
        assert _chapter_number_from_url("https://lgbtics.com/comic/x/chapter-25/") == "25"
        assert _chapter_number_from_url("https://lgbtics.com/comic/x/part-5/") == "5"
        assert _chapter_number_from_url(SERIES_URL) is None


class TestExtraction:
    def test_series_title_from_h1(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup, {}) == "Secretary! [scara2b]"

    def test_meta_rows_keyed_lowercase(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        rows = _extract_meta_rows(soup)
        assert rows["artists"] == ["Scara2b"]
        assert rows["status"] == ["Completed"]
        assert rows["genres"] == ["Ai Generated", "Anal", "Blowjob"]

    def test_authors_genres_status(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_authors(soup) == ["Scara2b"]
        assert _extract_genres(soup) == ["Ai Generated", "Anal", "Blowjob"]
        assert _extract_status(soup) == "Completed"

    def test_post_id(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _extract_post_id(soup) == "5391"

    def test_lang(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_lang(soup) == "en"

    def test_images_only_from_host(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3
        assert images[0].url == f"{IMG_BASE}001---Image.webp"
        assert images[1].url == f"{IMG_BASE}002---Image.webp"
        assert images[2].url == f"{IMG_BASE}003---Image.webp"
        assert [i.page_number for i in images] == [1, 2, 3]

    def test_no_images(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []


class TestLgbticsScraper:
    def test_domain_attr(self):
        scraper = LgbticsScraper()
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
        scraper = LgbticsScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "Secretary! [scara2b]"
        # Chapter title is the h1 text minus the series title prefix
        assert meta.chapter_title == "1 . Secretary! - Chapter 1 [scara2b]"
        assert meta.chapter_number == "1"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.post_id == "5391"
        assert meta.authors == ["Scara2b"]
        assert meta.genres == ["Ai Generated", "Anal", "Blowjob"]
        assert meta.status == "Completed"
        assert meta.description.startswith("A femboy secretary")
        assert meta.cover_url == COVER
        assert meta.total_pages == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        page = CHAPTER_PAGE.replace(
            f'<img id="image-0" src=" {IMG_BASE}001---Image.webp"', ""
        ).replace(
            f'<img id="image-1" src=" {IMG_BASE}002---Image.webp"', ""
        ).replace(
            f'<noscript><img src=" {IMG_BASE}003---Image.webp"', ""
        ).replace(
            f'data-src=" {IMG_BASE}003---Image.webp"', 'data-src=""'
        )

        def handler(url):
            return _MockResponse(page)

        session = _MockSession(handler)
        scraper = LgbticsScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url == SERIES_URL:
                raise ConnectionError("boom")
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = LgbticsScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)
        assert meta.series_title == "Untitled"
        assert meta.chapter_title == "1 . Secretary! - Chapter 1 [scara2b]"
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(SERIES_PAGE))
        scraper = LgbticsScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert series.series_title == "Secretary! [scara2b]"
        assert series.description.startswith("A femboy secretary")
        assert series.cover_url == COVER
        assert series.title_no == SLUG
        assert len(series.chapters) == 1
        assert series.chapters[0]["episode_no"] == "1"
        assert series.chapters[0]["title"] == "1 . Secretary! - Chapter 1 [scara2b]"
        assert series.chapters[0]["url"] == CHAPTER_URL

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        page = SERIES_PAGE.replace('<ul class="main version-chap no-volumn">', "")
        session = _MockSession(lambda url: _MockResponse(page))
        scraper = LgbticsScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, session)
