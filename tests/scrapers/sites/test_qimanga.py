from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.base import meta_index
from comic_dl.scrapers.sites.qimanga import (
    DOMAIN,
    QiMangaScraper,
    _breadcrumb_series_title,
    _chapter_number_from_url,
    _extract_authors,
    _extract_chapter_title,
    _extract_cover,
    _extract_description,
    _extract_genres,
    _extract_images,
    _extract_lang,
    _extract_rating,
    _extract_series_title,
    _extract_status,
    _extract_year,
    _series_slug_from_url,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "4190634673-eleceed"
SERIES_URL = f"https://qimanga.com/series/{SLUG}"
CHAPTER_URL = f"https://qimanga.com/series/{SLUG}/chapter-200"
IMG = f"https://media.qimanhwa.com/file/qiscans/scraper/{SLUG}/ch-200"
COVER = "https://media.qimanhwa.com/file/qiscans/upload/2026/04/28/1777389617457-l8Rwb5-19274.webp"

SERIES_PAGE = f"""
<html lang="en"><head>
    <title>Eleceed | Qi Manga</title>
    <meta property="og:title" content="Eleceed"/>
    <meta property="og:description" content="Jiwoo is a kind-hearted young man..."/>
    <meta property="og:image" content="{COVER}"/>
</head><body>
<div class="series-card">
  <div class="cover-col">
    <div class="cover-wrap"><img class="cover-img" src="{COVER}" alt="Eleceed"/></div>
  </div>
  <div class="info-col">
    <div class="series-info">
      <div class="title-row"><h1 class="series-title">Eleceed</h1></div>
      <div class="synopsis-status-row">
        <div class="synopsis-col">
          <div class="desc-wrap">
            <h3 class="section-heading">Synopsis</h3>
            <div class="description"><p>Jiwoo is a kind-hearted young man...</p></div>
          </div>
          <div class="bottom-info">
            <div>
              <h3 class="section-heading">Genres</h3>
              <div class="genres">
                <a class="genre-tag" href="/browse?genres=1">Action</a>
                <a class="genre-tag" href="/browse?genres=2">Comedy</a>
              </div>
            </div>
          </div>
        </div>
        <div class="status-section">
          <div class="status-item">
            <span class="status-label">Title Type</span>
            <a class="badge badge--type badge--link">MANHWA</a>
          </div>
          <div class="status-item">
            <span class="status-label">Title Status</span>
            <a class="badge badge--link badge--ongoing badge--status">Ongoing</a>
          </div>
          <div class="status-item">
            <span class="status-label">Author</span>
            <span class="status-value status-author">Qi Productions</span>
          </div>
          <div class="status-item">
            <span class="status-label">Release Year</span>
            <span class="status-value">2018</span>
          </div>
        </div>
      </div>
      <app-rating-display class="rating-display--desktop">
        <span class="rating-number">5.0</span>
      </app-rating-display>
    </div>
  </div>
</div>
<ul class="cl-list">
  <li><a class="cl-row" aria-label="Chapter 418" href="/series/{SLUG}/chapter-418">
    <span class="cl-num">Chapter 418</span></a></li>
  <li><a class="cl-row" aria-label="Chapter 417" href="/series/{SLUG}/chapter-417">
    <span class="cl-num">Chapter 417</span></a></li>
  <li><a class="cl-row" aria-label="Chapter 200" href="/series/{SLUG}/chapter-200">
    <span class="cl-num">Chapter 200</span></a></li>
</ul>
</body></html>
"""

CHAPTER_PAGE = f"""
<html lang="en"><head>
    <title>Eleceed \u2013 Ch. 200 | Qi Manga</title>
    <meta property="og:title" content="Eleceed \u2013 Ch. 200"/>
</head><body>
<nav class="r-breadcrumb">
  <a class="r-breadcrumb-link r-breadcrumb-home" href="/"></a>
  <a class="r-breadcrumb-link" href="/series/{SLUG}">Eleceed</a>
  <span class="r-breadcrumb-chapter">Ch. 200</span>
</nav>
<main class="main-content--reader">
  <div class="r-page-counter">1 / 3</div>
  <div class="r-page"><img class="r-page-img" src="{IMG}/0.webp" alt="Page 1"/></div>
  <div class="r-page"><img class="r-page-img" src="{IMG}/1.webp?v=9" alt="Page 2"/></div>
  <div class="r-page"><img class="r-page-img" src="{IMG}/2.webp" alt="Page 3"/></div>
  <img class="cover-img" src="https://media.qimanhwa.com/file/qiscans/upload/series/other/cover.webp"/>
</main>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url("https://www.qimanga.com/series/foo")
        assert is_series_url("https://qimanga.com/series/foo/")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://qimanga.com/")
        assert not is_series_url("https://qimanga.com/series/")
        assert not is_series_url(CHAPTER_URL)
        assert not is_series_url("https://other.com/series/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://qimanga.com/series/foo/chapter-1/")
        assert is_chapter_url("https://qimanga.com/series/foo/chapter-25")
        assert is_chapter_url("https://qimanga.com/series/foo/chapter-408.5/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://qimanga.com/series/foo")
        assert not is_chapter_url("https://qimanga.com/series/foo/")
        assert not is_chapter_url("https://other.com/series/foo/chapter-1/")

    def test_matches_url(self):
        scraper = QiMangaScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://qimanga.com/")

    def test_matches_series_url(self):
        scraper = QiMangaScraper()
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(CHAPTER_URL)

    def test_series_slug(self):
        assert _series_slug_from_url(CHAPTER_URL) == SLUG
        assert _series_slug_from_url(SERIES_URL) == SLUG
        assert _series_slug_from_url("https://qimanga.com/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_url(CHAPTER_URL) == "200"
        assert _chapter_number_from_url("https://qimanga.com/series/foo/chapter-25/") == "25"
        assert _chapter_number_from_url("https://qimanga.com/series/foo/chapter-408.5/") == "408.5"
        assert _chapter_number_from_url(SERIES_URL) is None


class TestExtraction:
    def test_series_title_from_h1(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup, {}) == "Eleceed"

    def test_series_title_from_og_fallback(self):
        soup = BeautifulSoup(
            '<html><head><meta property="og:title" content="Eleceed"/></head><body></body></html>',
            "lxml",
        )
        assert _extract_series_title(soup, meta_index(soup)) == "Eleceed"

    def test_description_from_panel(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_description(soup, meta_index(soup)).startswith("Jiwoo")

    def test_cover_prefers_panel_img_over_og(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_cover(soup, meta_index(soup)) == COVER

    def test_genres_authors_status_year_rating(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_genres(soup) == ["Action", "Comedy"]
        assert _extract_authors(soup) == ["Qi Productions"]
        assert _extract_status(soup) == "Ongoing"
        assert _extract_year(soup) == 2018
        assert _extract_rating(soup) == 5.0

    def test_placeholder_values_are_skipped(self):
        page = SERIES_PAGE.replace(">Qi Productions<", ">Unknown<").replace(">2018<", ">N/A<")
        soup = BeautifulSoup(page, "lxml")
        assert _extract_authors(soup) == []
        assert _extract_year(soup) is None

    def test_no_rating_returns_none(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_rating(soup) is None

    def test_lang(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _extract_lang(soup) == "en"

    def test_breadcrumb_series_title_skips_home(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _breadcrumb_series_title(soup) == "Eleceed"

    def test_chapter_title_normalized(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _extract_chapter_title(soup, "200") == "Chapter 200"

    def test_chapter_title_verbatim_when_not_ch(self):
        page = CHAPTER_PAGE.replace(">Ch. 200<", ">Side Story<")
        soup = BeautifulSoup(page, "lxml")
        assert _extract_chapter_title(soup, None) == "Side Story"

    def test_images_scoped_to_reader(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3
        assert images[0].url == f"{IMG}/0.webp"
        # cache-buster query stripped; non-reader cover excluded
        assert images[1].url == f"{IMG}/1.webp"
        assert all("upload/series/other" not in i.url for i in images)
        assert [i.page_number for i in images] == [1, 2, 3]

    def test_no_images_without_reader(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []


class TestQiMangaScraper:
    def test_domain_attr(self):
        scraper = QiMangaScraper()
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
        scraper = QiMangaScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "Eleceed"
        assert meta.chapter_title == "Chapter 200"
        assert meta.chapter_number == "200"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.authors == ["Qi Productions"]
        assert meta.genres == ["Action", "Comedy"]
        assert meta.status == "Ongoing"
        assert meta.year == 2018
        assert meta.community_rating == 5.0
        assert meta.description.startswith("Jiwoo")
        assert meta.cover_url == COVER
        assert meta.total_pages == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        page = CHAPTER_PAGE.replace('class="r-page-img" src="', 'class="cover-img" src="')

        def handler(url):
            return _MockResponse(page)

        session = _MockSession(handler)
        scraper = QiMangaScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url == SERIES_URL:
                raise ConnectionError("boom")
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = QiMangaScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)
        assert meta.series_title == "Eleceed"
        assert meta.chapter_title == "Chapter 200"
        assert meta.description == ""
        assert meta.cover_url == ""
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(SERIES_PAGE))
        scraper = QiMangaScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert series.series_title == "Eleceed"
        assert series.description.startswith("Jiwoo")
        assert series.cover_url == COVER
        assert series.title_no == SLUG
        assert len(series.chapters) == 3
        # ascending order
        assert series.chapters[0]["episode_no"] == "200"
        assert series.chapters[0]["title"] == "Chapter 200"
        assert series.chapters[-1]["episode_no"] == "418"
        assert series.chapters[-1]["url"] == f"https://qimanga.com/series/{SLUG}/chapter-418"

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        page = SERIES_PAGE.replace("cl-row", "other-row")
        session = _MockSession(lambda url: _MockResponse(page))
        scraper = QiMangaScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, session)
