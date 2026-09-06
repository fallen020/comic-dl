from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.kodokustudio import (
    DOMAIN,
    KodokuStudioScraper,
    _chapter_number_from_url,
    _extract_images,
    _extract_series_slug,
    _extract_series_title,
    _extract_status,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "reverendo-da-insanidade"
SERIES_URL = f"https://kodokustudio.com/manhua/{SLUG}/"
CHAPTER_URL = f"https://kodokustudio.com/manhua/{SLUG}/capitulo-1/"
CHAPTERS_ENDPOINT = f"{SERIES_URL}ajax/chapters/?t=1"
IMG = "https://i0.wp.com/kodokustudio.com/wp-content/uploads/WP-manga/data/d06/"

SERIES_PAGE = f"""
<html lang="pt-BR"><head>
    <title>{SLUG} -</title>
    <meta property="og:image" content="https://kodokustudio.com/wp-content/themes/madara/images/logo.png"/>
</head><body class="wp-singular single single-wp-manga postid-137">
<h1>Reverendo da Insanidade</h1>
<div class="post-content_item">
    <div class="summary-heading"><h5>Rating</h5></div>
    <div class="summary-content">Reverendo da Insanidade Average 4.7 / 5</div>
</div>
<div class="post-content_item">
    <div class="summary-heading"><h5>Status</h5></div>
    <div class="summary-content">OnGoing</div>
</div>
<ul class="main version-chap">
    <li class="wp-manga-chapter"><a href="https://kodokustudio.com/manhua/{SLUG}/capitulo-20/">Read Last</a></li>
    <li class="wp-manga-chapter"><a href="https://kodokustudio.com/manhua/{SLUG}/capitulo-1/">Read First</a></li>
</ul>
</body></html>
"""

CHAPTERS_LIST = f"""
<div class="listing-chapters_wrap">
    <ul class="main version-chap">
        <li class="wp-manga-chapter"><a href="https://kodokustudio.com/manhua/{SLUG}/capitulo-3/">Capítulo 3</a></li>
        <li class="wp-manga-chapter"><a href="https://kodokustudio.com/manhua/{SLUG}/capitulo-2/">Capítulo 2</a></li>
        <li class="wp-manga-chapter"><a href="https://kodokustudio.com/manhua/{SLUG}/capitulo-1/">Capítulo 1</a></li>
        <li class="wp-manga-chapter"><a href="https://kodokustudio.com/manhua/outra-serie/capitulo-5/">Chapter 5 of another series</a></li>
    </ul>
</div>
"""

CHAPTER_PAGE = f"""
<html lang="pt-BR"><head>
    <title>{SLUG} -</title>
</head><body class="single single-wp-manga postid-7001 reading-manga">
<h1>Reverendo da Insanidade - Capítulo 1</h1>
<div class="read-container">
    <div class="reading-content">
        <div class="entry-content_wrap">
            <img src=" https://i0.wp.com/kodokustudio.com/wp-content/uploads/WP-manga/data/d06/ep1_001.jpeg?ssl=1" class="wp-manga-chapter-img"/>
            <img src=" https://i0.wp.com/kodokustudio.com/wp-content/uploads/WP-manga/data/d06/ep1_002.jpeg?ssl=1" class="wp-manga-chapter-img"/>
            <img src=" https://i0.wp.com/kodokustudio.com/wp-content/uploads/WP-manga/data/d06/ep1_003.jpeg?ssl=1" class="wp-manga-chapter-img"/>
            <img src="https://example.com/ads/banner.jpg"/>
        </div>
    </div>
</div>
<img src="https://kodokustudio.com/wp-content/themes/madara/images/logo.png"/>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url(f"https://kodokustudio.com/manhua/{SLUG}")
        assert is_series_url("https://www.kodokustudio.com/manhua/foo/")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://kodokustudio.com/")
        assert not is_series_url("https://kodokustudio.com/manhua/")
        assert not is_series_url("https://kodokustudio.com/manhua/foo/capitulo-1")
        assert not is_series_url("https://other.com/manhua/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://kodokustudio.com/manhua/foo/capitulo-25/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://kodokustudio.com/manhua/foo")
        assert not is_chapter_url("https://kodokustudio.com/manhua/foo/bar")
        assert not is_chapter_url("https://other.com/manhua/foo/capitulo-1")

    def test_matches_url(self):
        scraper = KodokuStudioScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://kodokustudio.com/")

    def test_series_slug(self):
        assert _extract_series_slug(CHAPTER_URL) == SLUG
        assert _extract_series_slug(SERIES_URL) == SLUG
        assert _extract_series_slug("https://kodokustudio.com/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_url(CHAPTER_URL) == "1"
        assert _chapter_number_from_url(
            "https://kodokustudio.com/manhua/x/capitulo-25"
        ) == "25"
        assert _chapter_number_from_url(SERIES_URL) is None


class TestExtraction:
    def test_series_title_from_h1(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup, {}) == "Reverendo da Insanidade"

    def test_status_row(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_status(soup) == "OnGoing"

    def test_images_only_from_cdn(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3
        # leading whitespace in the src attribute is stripped
        assert images[0].url == (
            "https://i0.wp.com/kodokustudio.com/wp-content/uploads/"
            "WP-manga/data/d06/ep1_001.jpeg"
        )
        assert [i.page_number for i in images] == [1, 2, 3]

    def test_no_images(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []


class TestKodokuStudioScraper:
    def test_domain_attr(self):
        scraper = KodokuStudioScraper()
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
        scraper = KodokuStudioScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "Reverendo da Insanidade"
        assert meta.chapter_title == "Capítulo 1"
        assert meta.chapter_number == "1"
        assert meta.language == "pt"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.post_id == "7001"
        assert meta.authors == []
        assert meta.genres == []
        assert meta.status == "OnGoing"
        assert meta.description == ""
        # no real cover exists; the site logo is not used
        assert meta.cover_url == ""
        assert meta.total_pages == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        page = CHAPTER_PAGE.replace(
            '<img src=" https://i0.wp.com/kodokustudio.com/wp-content/uploads/WP-manga/data/d06/ep1_001.jpeg?ssl=1"', ""
        ).replace(
            '<img src=" https://i0.wp.com/kodokustudio.com/wp-content/uploads/WP-manga/data/d06/ep1_002.jpeg?ssl=1"', ""
        ).replace(
            '<img src=" https://i0.wp.com/kodokustudio.com/wp-content/uploads/WP-manga/data/d06/ep1_003.jpeg?ssl=1"', ""
        )

        def handler(url):
            return _MockResponse(page)

        session = _MockSession(handler)
        scraper = KodokuStudioScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url == SERIES_URL:
                raise ConnectionError("boom")
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = KodokuStudioScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)
        assert meta.series_title == "Untitled"
        assert meta.chapter_title == "Reverendo da Insanidade - Capítulo 1"
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series_uses_ajax_chapter_list(self):
        def handler(url, **kwargs):
            if url == SERIES_URL:
                return _MockResponse(SERIES_PAGE)
            if url == CHAPTERS_ENDPOINT:
                return _MockResponse(CHAPTERS_LIST)
            raise AssertionError(f"unexpected URL: {url}")

        session = _MockSession(handler)
        scraper = KodokuStudioScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert series.series_title == "Reverendo da Insanidade"
        assert series.cover_url == ""
        assert series.title_no == SLUG
        assert [c["episode_no"] for c in series.chapters] == ["1", "2", "3"]
        assert series.chapters[0]["title"] == "Capítulo 1"
        assert series.chapters[-1]["url"] == (
            f"https://kodokustudio.com/manhua/{SLUG}/capitulo-3/"
        )
        # the foreign-series link did not leak in
        assert "outra-serie" not in " ".join(c["url"] for c in series.chapters)

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        def handler(url, **kwargs):
            if url == SERIES_URL:
                return _MockResponse(SERIES_PAGE)
            if url == CHAPTERS_ENDPOINT:
                return _MockResponse("<div></div>")
            raise AssertionError(f"unexpected URL: {url}")

        session = _MockSession(handler)
        scraper = KodokuStudioScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, session)
