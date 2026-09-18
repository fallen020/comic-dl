from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.thunderscans import (
    DOMAIN,
    ThunderscansScraper,
    _chapter_number_from_url,
    _extract_chapter_title,
    _extract_cover,
    _extract_description,
    _extract_genres,
    _extract_images,
    _extract_rating,
    _extract_series_title,
    _extract_status,
    _series_slug_from_url,
    _ts_reader_index,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "shadow-slave"
SERIES_URL = f"https://en-thunderscans.com/comics/{SLUG}/"
CHAPTER_URL = f"https://en-thunderscans.com/{SLUG}-chapter-9/"
IMG_BASE = "https://en-thunderscans.com/wp-content/uploads/manga/4b5a2a1856b453aa7df04bba1249623f/"
COVER = "https://en-thunderscans.com/wp-content/uploads/2026/07/2026-08-20-01-25-42-1787189142561.webp"

TS_READER_JSON = (
    '{"post_id":671945,"noimagehtml":"<center><h4>NO IMAGE YET</h4></center>",'
    '"prevUrl":"https://en-thunderscans.com/shadow-slave-chapter-8/","nextUrl":"",'
    '"mode":"full","sources":['
    f'{{"source":"Server 1","images":['
    f'"{IMG_BASE}0001_1c3e7538.jpg?v=9",'
    f'"{IMG_BASE}0002_bd9bb336.jpg",'
    f'"https://i.ibb.co/8jQPQ2y/logo.png",'
    f'"{IMG_BASE}0003_e5ed5534.jpg"'
    "]},"
    f'{{"source":"Server 2","images":["{IMG_BASE}0002_bd9bb336.jpg"]}}'
    '],"lazyload":false,"defaultSource":"Server 1","protected":false,'
    '"is_novel":false,"unlock_token":null}'
)

SERIES_PAGE = f"""
<html lang="en"><head>
    <title>Shadow Slave &#8211; Thunderscans EN</title>
</head><body>
<div class="postbody full">
<article id="post-366632" class="post-366632 hentry">
    <div class="main-info">
        <div class="first-half">
            <div class="titles">
                <h1 class="entry-title" itemprop="name">Shadow Slave</h1>
            </div>
            <div class="extra-info a">
                <div class="mobile-rt"><div class="numscore">8.5</div></div>
                <div class="imptdt"><div class="status"><span class="status-dot Ongoing"></span> <i>Ongoing</i></div></div>
            </div>
            <div class="genres-container">
                <div class="wd-full"><span class="mgen"><a href="https://en-thunderscans.com/genres/action/" rel="tag">Action</a> <a href="https://en-thunderscans.com/genres/dark-fantasy/" rel="tag">Dark Fantasy</a></span></div>
            </div>
            <div class="summary">
                <div class="entry-content entry-content-single" itemprop="description">
                    <p>Growing up in poverty, Sunny never expected anything good from life.</p>
                </div>
            </div>
        </div>
        <div class="thumb-half">
            <div class="thumb"><img src="{COVER}" class="wp-post-image" alt="Shadow Slave"/></div>
        </div>
    </div>
    <div class="bixbox bxcl epcheck">
        <div class="eplister" id="chapterlist"><style>ul li a {{}}</style>
        <ul>
            <li data-num="9"><a href="{CHAPTER_URL}"><div class="chbox"><div class="eph-num"><span class="chapternum">Chapter 9</span><span class="chapterdate">September 17, 2026</span></div></div></a></li>
            <li data-num="9"><a href="{CHAPTER_URL}"><div class="chbox"><div class="eph-num"><span class="chapternum">Chapter 9</span></div></div></a></li>
            <li data-num="8"><a href="https://en-thunderscans.com/{SLUG}-chapter-8/"><div class="chbox"><div class="eph-num"><span class="chapternum">Chapter 8</span></div></div></a></li>
            <li data-num="7"><a href="https://en-thunderscans.com/{SLUG}-chapter-7/"><div class="chbox"><div class="eph-num"><span class="chapternum">Chapter 7</span></div></div></a></li>
        </ul>
        </div>
    </div>
</article>
</div>
</body></html>
"""

CHAPTER_PAGE = f"""
<html lang="en"><head>
    <title>{SLUG.title()} Chapter 9 &#8211; Thunderscans EN</title>
</head><body>
<h1 class="entry-title" itemprop="name">Shadow Slave Chapter 9</h1>
<div id="content" class="readercontent">
<div id="readerarea"></div>
</div>
<script>ts_reader.run({TS_READER_JSON});</script>
</body></html>
"""

CHAPTER_PAGE_NO_IMAGES = (
    '<html><head><title>Shadow Slave Chapter 9 &#8211; Thunderscans EN</title></head>'
    '<body><h1 class="entry-title">Shadow Slave Chapter 9</h1>'
    '<div id="content" class="readercontent"><div id="readerarea"></div></div></body></html>'
)


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url("https://www.en-thunderscans.com/comics/foo")
        assert is_series_url("https://en-thunderscans.com/comics/foo/")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://en-thunderscans.com/")
        assert not is_series_url("https://en-thunderscans.com/comics/")
        assert not is_series_url(CHAPTER_URL)
        assert not is_series_url("https://other.com/comics/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://en-thunderscans.com/foo-chapter-1/")
        assert is_chapter_url("https://en-thunderscans.com/foo-chapter-25")
        assert is_chapter_url("https://en-thunderscans.com/foo-chapter-33-1/")
        assert is_chapter_url("https://en-thunderscans.com/foo-chapter-408.5/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://en-thunderscans.com/foo")
        assert not is_chapter_url("https://en-thunderscans.com/comics/foo")
        assert not is_chapter_url("https://other.com/foo-chapter-1/")

    def test_matches_url(self):
        scraper = ThunderscansScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://en-thunderscans.com/")

    def test_matches_series_url(self):
        scraper = ThunderscansScraper()
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(CHAPTER_URL)

    def test_series_slug(self):
        assert _series_slug_from_url(CHAPTER_URL) == SLUG
        assert _series_slug_from_url(SERIES_URL) == SLUG
        assert _series_slug_from_url("https://en-thunderscans.com/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_url(CHAPTER_URL) == "9"
        assert _chapter_number_from_url(
            "https://en-thunderscans.com/foo-chapter-25/"
        ) == "25"
        assert _chapter_number_from_url(
            "https://en-thunderscans.com/foo-chapter-33-1/"
        ) == "33.1"
        assert _chapter_number_from_url(
            "https://en-thunderscans.com/foo-chapter-408.5/"
        ) == "408.5"
        assert _chapter_number_from_url(SERIES_URL) is None


class TestExtraction:
    def test_series_title_from_h1(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup) == "Shadow Slave"

    def test_series_title_from_title_fallback(self):
        soup = BeautifulSoup(
            "<html><head><title>Shadow Slave &#8211; Thunderscans EN</title></head>"
            "<body></body></html>",
            "lxml",
        )
        assert _extract_series_title(soup) == "Shadow Slave"

    def test_description_cover_genres_status_rating(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_description(soup).startswith("Growing up in poverty")
        assert _extract_cover(soup) == COVER
        assert _extract_genres(soup) == ["Action", "Dark Fantasy"]
        assert _extract_status(soup) == "Ongoing"
        assert _extract_rating(soup) == 8.5

    def test_rating_absent(self):
        soup = BeautifulSoup(
            '<html><body><div class="main-info"><div class="numscore">n/a</div>'
            "</div></body></html>",
            "lxml",
        )
        assert _extract_rating(soup) is None

    def test_chapter_title_strips_series_prefix(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _extract_chapter_title(soup, "Shadow Slave") == "Chapter 9"

    def test_chapter_title_kept_when_no_prefix(self):
        soup = BeautifulSoup(
            '<html><body><h1 class="entry-title">Chapter 9</h1></body></html>',
            "lxml",
        )
        assert _extract_chapter_title(soup, "Shadow Slave") == "Chapter 9"

    def test_ts_reader_index(self):
        data = _ts_reader_index(CHAPTER_PAGE)
        assert data is not None
        assert data["post_id"] == 671945

    def test_ts_reader_index_missing(self):
        assert _ts_reader_index("<html><body></body></html>") is None

    def test_images_from_ts_reader(self):
        images = _extract_images(CHAPTER_PAGE)
        assert len(images) == 3
        # query stripped, off-site image excluded, duplicate across sources dropped
        assert images[0].url == f"{IMG_BASE}0001_1c3e7538.jpg"
        assert images[1].url == f"{IMG_BASE}0002_bd9bb336.jpg"
        assert images[2].url == f"{IMG_BASE}0003_e5ed5534.jpg"
        assert all("ibb" not in i.url for i in images)
        assert [i.page_number for i in images] == [1, 2, 3]

    def test_no_images_without_ts_reader(self):
        assert _extract_images("<html><body></body></html>") == []


class TestThunderscansScraper:
    def test_domain_attr(self):
        scraper = ThunderscansScraper()
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
        scraper = ThunderscansScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "Shadow Slave"
        assert meta.chapter_title == "Chapter 9"
        assert meta.chapter_number == "9"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.post_id == "671945"
        assert meta.genres == ["Action", "Dark Fantasy"]
        assert meta.status == "Ongoing"
        assert meta.community_rating == 8.5
        assert meta.description.startswith("Growing up in poverty")
        assert meta.cover_url == COVER
        assert meta.total_pages == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        def handler(url):
            if url == SERIES_URL:
                return _MockResponse(SERIES_PAGE)
            return _MockResponse(CHAPTER_PAGE_NO_IMAGES)

        session = _MockSession(handler)
        scraper = ThunderscansScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url == SERIES_URL:
                raise ConnectionError("boom")
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = ThunderscansScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)
        assert meta.series_title == "Untitled"
        assert meta.chapter_title == "Shadow Slave Chapter 9"
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(SERIES_PAGE))
        scraper = ThunderscansScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert series.series_title == "Shadow Slave"
        assert series.description.startswith("Growing up in poverty")
        assert series.cover_url == COVER
        assert series.title_no == SLUG
        assert len(series.chapters) == 3
        # ascending order, duplicate link dropped
        assert series.chapters[0]["episode_no"] == "7"
        assert series.chapters[0]["title"] == "Chapter 7"
        assert series.chapters[-1]["episode_no"] == "9"
        assert series.chapters[-1]["url"] == CHAPTER_URL

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        page = SERIES_PAGE.replace("chapterlist", "chapterlist-gone")
        session = _MockSession(lambda url: _MockResponse(page))
        scraper = ThunderscansScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, session)
