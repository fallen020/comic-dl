from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.gedecomix import (
    DOMAIN,
    GedecomixScraper,
    _chapter_number_from_slug,
    _clean_image_url,
    _extract_artists,
    _extract_chapter_number,
    _extract_genres,
    _extract_images,
    _extract_post_id,
    _extract_series_title,
    _extract_status,
    _extract_titles,
    _extract_year,
    _get_image_ext,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession


class TestUrlPatterns:
    def test_valid_chapter_urls(self):
        assert is_chapter_url(
            "https://gedecomix.com/porncomic/hell-village/5-hell-village-ch-05/"
        )
        assert is_chapter_url(
            "https://gedecomix.com/porncomic/unethical-hacker-indiantgstories/unethical-hacker/"
        )
        assert is_chapter_url(
            "https://www.gedecomix.com/porncomic/animated-tales-wc-tf/what-i-did-to-become-famous/"
        )

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://gedecomix.com/")
        assert not is_chapter_url("https://gedecomix.com/porncomic/")
        assert not is_chapter_url("https://gedecomix.com/porncomic/series-only/")
        assert not is_chapter_url("https://gedecomix.com/comics-tag/feminization/")
        assert not is_chapter_url("https://other.com/porncomic/series/chapter/")

    def test_valid_series_urls(self):
        assert is_series_url("https://gedecomix.com/porncomic/hell-village/")
        assert is_series_url("https://www.gedecomix.com/porncomic/animated-tales-wc-tf/")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://gedecomix.com/")
        assert not is_series_url("https://gedecomix.com/porncomic/")
        assert not is_series_url("https://gedecomix.com/porncomic/series/chapter/")
        assert not is_series_url("https://other.com/porncomic/hello/")


class TestImageUrlCleaning:
    def test_no_resize_suffix(self):
        url = "https://gedecomix.com/static/WP-manga/data/2d091f66d7f80e270/page1.webp"
        assert _clean_image_url(url) == url

    def test_strips_resize_suffix(self):
        assert _clean_image_url(
            "https://gedecomix.com/static/WP-manga/data/2d091f66d7f80e270/page2-768x768.webp"
        ) == "https://gedecomix.com/static/WP-manga/data/2d091f66d7f80e270/page2.webp"

    def test_strips_cover_resize(self):
        assert _clean_image_url(
            "https://gedecomix.com/static/2026/06/Hell-Village-My-Sweet-Seduction-386x556.webp"
        ) == "https://gedecomix.com/static/2026/06/Hell-Village-My-Sweet-Seduction.webp"

    def test_strips_query_string(self):
        assert _clean_image_url(
            "https://gedecomix.com/static/WP-manga/data/hash/page1.webp?w=800"
        ) == "https://gedecomix.com/static/WP-manga/data/hash/page1.webp"


class TestGetImageExt:
    def test_valid_extensions(self):
        assert _get_image_ext("https://gedecomix.com/img.jpg") == "jpg"
        assert _get_image_ext("https://gedecomix.com/img.jpeg") == "jpeg"
        assert _get_image_ext("https://gedecomix.com/img.png") == "png"
        assert _get_image_ext("https://gedecomix.com/img.webp") == "webp"

    def test_fallback_extension(self):
        assert _get_image_ext("https://gedecomix.com/img") == "jpg"
        assert _get_image_ext("https://gedecomix.com/img.unknown") == "jpg"


class TestTitleExtraction:
    def test_chapter_title_split_from_og_title(self):
        html = """
        <html><head>
            <title>Unethical Hacker - Unethical Hacker - GEDE Comix</title>
            <meta property="og:title" content="Unethical Hacker - Unethical Hacker - GEDE Comix"/>
            <meta property="og:site_name" content="GEDE Comix"/>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        idx = {f"prop:{k}": v for k, v in {
            "og:title": ["Unethical Hacker - Unethical Hacker - GEDE Comix"],
            "og:site_name": ["GEDE Comix"],
        }.items()}
        series, chapter = _extract_titles(soup, idx)
        assert series == "Unethical Hacker"
        assert chapter == "Unethical Hacker"

    def test_chapter_title_may_contain_dash(self):
        html = """
        <html><head>
            <title>Series Name - My Wife - The Vampire - GEDE Comix</title>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        idx = {"prop:og:site_name": ["GEDE Comix"]}
        series, chapter = _extract_titles(soup, idx)
        assert series == "Series Name"
        assert chapter == "My Wife - The Vampire"

    def test_jsonld_headline_fallback(self):
        html = """
        <html><head>
            <title>Only A Title</title>
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Article",
             "headline":"Animated Tales [WC | TF]","datePublished":"2026-01-01"}
            </script>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        series, chapter = _extract_titles(soup, {})
        assert series == "Animated Tales [WC | TF]"
        assert chapter == "Only A Title"

    def test_series_title_strips_porn_comic_suffix(self):
        html = """
        <html><head>
            <title>Hell Village - My Sweet Seduction - Porn Comic</title>
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Article",
             "headline":"Hell Village - My Sweet Seduction"}
            </script>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _extract_series_title(soup, {}) == "Hell Village - My Sweet Seduction"

    def test_series_title_single_part(self):
        html = """
        <html><head>
            <title>Animated Tales [WC | TF]</title>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _extract_series_title(soup, {}) == "Animated Tales [WC | TF]"


class TestMetaRowExtraction:
    SAMPLE = """
    <html><body>
    <div class="post-content">
        <div class="post-content_item">
            <h5>Artist(s)</h5>
            <div class="summary-content"><a href="/x/">Crazydad3d</a><a href="/y/">PigKing</a></div>
        </div>
        <div class="post-content_item">
            <h5>Genre(s)</h5>
            <div class="summary-content"><a href="/g/">3D</a></div>
        </div>
        <div class="post-content_item">
            <h5>Tag(s)</h5>
            <div class="summary-content"><a href="/t/1/">3D</a><a href="/t/2/">Big Ass</a></div>
        </div>
        <div class="post-content_item">
            <h5>Status</h5>
            <div class="summary-content">OnGoing</div>
        </div>
    </div>
    </body></html>
    """

    def test_artists_from_links(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        assert _extract_artists(soup) == ["Crazydad3d", "PigKing"]

    def test_genres_merge_and_dedupe(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        genres = _extract_genres(soup)
        assert genres == ["3D", "Big Ass"]

    def test_status_from_text(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        assert _extract_status(soup) == "OnGoing"

    def test_status_none_when_missing(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_status(soup) is None


class TestChapterNumber:
    def test_chapter_word(self):
        assert _extract_chapter_number("My Comic Chapter 5") == "5"

    def test_chapter_dot_padded(self):
        assert _extract_chapter_number("My Comic Ch. 05") == "05"

    def test_no_chapter_number(self):
        assert _extract_chapter_number("My Comic") is None

    def test_from_slug_prefix(self):
        assert _chapter_number_from_slug(
            "https://gedecomix.com/porncomic/series/5-ch-05/"
        ) == "5"

    def test_from_slug_no_prefix(self):
        assert _chapter_number_from_slug(
            "https://gedecomix.com/porncomic/series/what-i-did-to-become-famous/"
        ) is None


class TestImageExtraction:
    SAMPLE = """
    <html><body>
    <div class="read-container">
        <img src="https://gedecomix.com/static/WP-manga/data/hash/page1.webp"/>
        <img src="https://gedecomix.com/static/WP-manga/data/hash/page2-768x768.webp"/>
        <img data-src="https://gedecomix.com/static/WP-manga/data/hash/page3.webp"/>
        <img data-lazy-src="https://gedecomix.com/static/WP-manga/data/hash/page4.webp"/>
        <img src="https://gedecomix.com/static/WP-manga/data/hash/page1.webp"/>
        <img src="https://ads.example.com/tracker.gif"/>
        <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="/>
    </div>
    </body></html>
    """

    def test_extracts_reading_images(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 4

    def test_resize_suffix_stripped(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        urls = [img.url for img in _extract_images(soup)]
        assert "https://gedecomix.com/static/WP-manga/data/hash/page2.webp" in urls
        assert "page2-768x768" not in " ".join(urls)

    def test_excludes_external_and_data_uris(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        urls = " ".join(img.url for img in _extract_images(soup))
        assert "ads.example.com" not in urls
        assert "data:" not in urls

    def test_dedup_and_sequential_numbering(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        images = _extract_images(soup)
        for i, img in enumerate(images, start=1):
            assert img.page_number == i
        assert len({img.url for img in images}) == len(images)

    def test_no_images(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []


class TestMetadataExtractors:
    def test_post_id_from_body_class(self):
        soup = BeautifulSoup(
            '<html><body class="wp-singular single single-wp-manga postid-33489"></body></html>',
            "lxml",
        )
        assert _extract_post_id(soup) == "33489"

    def test_post_id_empty_when_missing(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_post_id(soup) == ""

    def test_year_from_jsonld_date_published(self):
        html = """
        <html><head>
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Article",
             "datePublished":"2026-06-26 10:06:30"}
            </script>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _extract_year(soup) == 2026

    def test_year_none_when_missing(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_year(soup) is None

    def test_list_form_jsonld_type(self):
        html = """
        <html><head>
            <title>Some Series - Some Chapter - GEDE Comix</title>
            <script type="application/ld+json">
            {"@context":"https://schema.org",
             "@type":["NewsArticle","Article"],
             "headline":"Some Series",
             "datePublished":"2026-06-26 10:06:30"}
            </script>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _extract_year(soup) == 2026
        idx = {"prop:og:site_name": ["GEDE Comix"]}
        series, chapter = _extract_titles(soup, idx)
        assert series == "Some Series"
        assert chapter == "Some Chapter"


class TestGedecomixScraper:
    SERIES_PAGE = b"""
    <html lang="en-US"><head>
        <title>Hell Village - My Sweet Seduction - Porn Comic</title>
        <meta property="og:image" content="https://gedecomix.com/static/2026/06/Hell-Village-My-Sweet-Seduction.webp"/>
        <meta property="og:description" content="Porn Comics chapters of Hell Village."/>
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Article",
         "headline":"Hell Village - My Sweet Seduction",
         "description":"A 3D MILF mother incest porn comic by PigKing.",
         "datePublished":"2026-06-26 10:06:30"}
        </script>
    </head><body class="single single-wp-manga postid-33489">
    <h1>Hell Village - My Sweet Seduction</h1>
    <div class="post-content">
        <div class="post-content_item"><h5>Artist(s)</h5>
            <div class="summary-content"><a href="/a/">PigKing</a></div></div>
        <div class="post-content_item"><h5>Genre(s)</h5>
            <div class="summary-content"><a href="/g/">3D</a></div></div>
        <div class="post-content_item"><h5>Tag(s)</h5>
            <div class="summary-content"><a href="/t/">3D</a><a href="/t/">Big Ass</a></div></div>
        <div class="post-content_item"><h5>Status</h5>
            <div class="summary-content">OnGoing</div></div>
    </div>
    <div class="listing-chapters_wrap">
        <a href="/porncomic/hell-village/5-ch-05/">5. Hell Village Ch. 05 - [PigKing]</a>
        <a href="/porncomic/hell-village/4-ch-04/">4. Hell Village Ch. 04 - [PigKing]</a>
        <a href="/porncomic/hell-village/3-ch-03/">3. Hell Village Ch. 03 - [PigKing]</a>
        <a href="/porncomic/hell-village/2-ch-02/">2. Hell Village Ch. 02 - [PigKing]</a>
        <a href="/porncomic/hell-village/1-ch-01/">1. Hell Village Ch. 01 - [PigKing]</a>
    </div>
    </body></html>
    """

    CHAPTER_PAGE = b"""
    <html lang="en-US"><head>
        <title>Hell Village - My Sweet Seduction - Hell Village Ch. 05 - GEDE Comix</title>
        <meta property="og:title" content="Hell Village - Ch. 05 - GEDE Comix"/>
        <meta property="og:description" content="Porn Comics chapters of Hell Village."/>
        <meta property="og:image" content="https://gedecomix.com/static/2026/06/Hell-Village-My-Sweet-Seduction-386x556.webp"/>
        <meta property="og:site_name" content="GEDE Comix"/>
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Article",
         "headline":"Hell Village - My Sweet Seduction",
         "datePublished":"2026-06-26 10:06:30"}
        </script>
    </head><body class="wp-singular single single-wp-manga postid-30601 reading-manga">
    <h1>Hell Village Ch. 05</h1>
    <div class="read-container">
        <img src="https://gedecomix.com/static/WP-manga/data/hash/page1.webp"/>
        <img src="https://gedecomix.com/static/WP-manga/data/hash/page2-768x768.webp"/>
        <img data-src="https://gedecomix.com/static/WP-manga/data/hash/page3.webp"/>
        <img src="https://gedecomix.com/static/WP-manga/data/hash/page1.webp"/>
    </div>
    </body></html>
    """

    ARCHIVE_PAGE = b"""
    <html><head><title>Feminization Porn Comics - GEDE Comix</title></head>
    <body class="archive tax-wp-manga-tag term-feminization">
    <div class="read-container">
        <img src="https://gedecomix.com/static/2026/06/thumb.webp"/>
    </div>
    </body></html>
    """

    def test_domain_attr(self):
        scraper = GedecomixScraper()
        assert scraper.domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_rejects_archive_page(self):
        session = _MockSession(lambda url: _MockResponse(self.ARCHIVE_PAGE))
        scraper = GedecomixScraper()
        with pytest.raises(ValueError, match="category/tag listing"):
            await scraper.scrape(
                "https://gedecomix.com/comics-tag/feminization/", session,
            )

    @pytest.mark.asyncio
    async def test_scrape_raises_on_no_images(self):
        html = b"<html><head><title>A - B - GEDE Comix</title></head><body></body></html>"
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = GedecomixScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(
                "https://gedecomix.com/porncomic/series/chapter/", session,
            )

    @pytest.mark.asyncio
    async def test_scrape_chapter_with_enrichment(self):
        def handler(url):
            if url.endswith("porncomic/hell-village/"):
                return _MockResponse(self.SERIES_PAGE)
            return _MockResponse(self.CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = GedecomixScraper()
        meta = await scraper.scrape(
            "https://gedecomix.com/porncomic/hell-village/5-hell-village-ch-05/",
            session,
        )

        assert meta.series_title == "Hell Village - My Sweet Seduction"
        assert meta.chapter_title == "Hell Village Ch. 05"
        assert meta.chapter_number == "05"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.year == 2026
        assert meta.post_id == "30601"
        assert meta.service == DOMAIN
        assert meta.cover_url == (
            "https://gedecomix.com/static/2026/06/Hell-Village-My-Sweet-Seduction.webp"
        )
        assert meta.artists == ["PigKing"]
        assert meta.genres == ["3D", "Big Ass"]
        assert meta.status == "OnGoing"
        assert meta.total_pages == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_cache_series_fetch(self):
        from comic_dl import config

        config.set_runtime_http(cache=False)
        try:
            await self._scrape_chapter_cache_series_fetch_body()
        finally:
            config._RUNTIME_HTTP.pop("cache", None)

    async def _scrape_chapter_cache_series_fetch_body(self):
        def handler(url):
            if url.endswith("porncomic/hell-village/"):
                return _MockResponse(self.SERIES_PAGE)
            return _MockResponse(self.CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = GedecomixScraper()
        url = (
            "https://gedecomix.com/porncomic/hell-village/"
            "5-hell-village-ch-05/"
        )
        await scraper.scrape(url, session)
        assert "hell-village" in scraper._series_cache
        await scraper.scrape(url, session)
        assert session._handler_calls == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url.endswith("porncomic/hell-village/"):
                raise ConnectionError("boom")
            return _MockResponse(self.CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = GedecomixScraper()
        meta = await scraper.scrape(
            "https://gedecomix.com/porncomic/hell-village/5-hell-village-ch-05/",
            session,
        )
        assert meta.artists == []
        assert meta.genres == []
        assert meta.status is None
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(self.SERIES_PAGE))
        scraper = GedecomixScraper()
        series = await scraper.scrape_series(
            "https://gedecomix.com/porncomic/hell-village/",
            session,
        )

        assert series.series_title == "Hell Village - My Sweet Seduction"
        assert series.description == "A 3D MILF mother incest porn comic by PigKing."
        assert series.cover_url == (
            "https://gedecomix.com/static/2026/06/Hell-Village-My-Sweet-Seduction.webp"
        )
        assert series.title_no == "hell-village"
        assert len(series.chapters) == 5
        assert series.chapters[0]["episode_no"] == "1"
        assert series.chapters[0]["title"] == "Hell Village Ch. 01 - [PigKing]"
        assert series.chapters[-1]["episode_no"] == "5"
        assert series.chapters[-1]["title"] == "Hell Village Ch. 05 - [PigKing]"

    @pytest.mark.asyncio
    async def test_scrape_series_rejects_archive_page(self):
        session = _MockSession(lambda url: _MockResponse(self.ARCHIVE_PAGE))
        scraper = GedecomixScraper()
        with pytest.raises(ValueError, match="category/tag listing"):
            await scraper.scrape_series(
                "https://gedecomix.com/comics-tag/feminization/", session,
            )

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        html = b"""
        <html><head><title>Some Series - Porn Comic</title></head>
        <body class="single single-wp-manga postid-1">
        <div class="listing-chapters_wrap"></div>
        </body></html>
        """
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = GedecomixScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(
                "https://gedecomix.com/porncomic/some-series/", session,
            )
