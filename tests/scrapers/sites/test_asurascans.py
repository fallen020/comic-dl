from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.asurascans import (
    DOMAIN,
    AsurascansScraper,
    _chapter_number_from_slug,
    _clean_image_url,
    _extract_chapter_title,
    _extract_images,
    _extract_lang,
    _extract_meta,
    _extract_series_title,
    _extract_status,
    _extract_year,
    _is_premium_page,
    _series_slug_from_url,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

COVER = (
    "https://cdn.asurascans.com/asura-images/covers/murim-psychopath.60ee5d.webp"
)

SERIES_JSONLD = """{
  "@context": "https://schema.org",
  "@type": "ComicSeries",
  "name": "Murim Psychopath",
  "description": "A once-in-an-era psychopath fell into the Murim.",
  "url": "https://asurascans.com/comics/murim-psychopath-00dcbf97",
  "image": "{COVER}",
  "genre": ["Action", "Adventure", "Crazy MC", "Fantasy", "Murim"],
  "author": {"@type": "Person", "name": "Gonbung"},
  "illustrator": {"@type": "Person", "name": "Hitbook"},
  "numberOfEpisodes": 4,
  "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": "9.6",
    "bestRating": "10",
    "ratingCount": 33081
  }
}""".replace("{COVER}", COVER)


class TestUrlPatterns:
    def test_valid_chapter_urls(self):
        assert is_chapter_url(
            "https://asurascans.com/comics/murim-psychopath-00dcbf97/chapter/1"
        )
        assert is_chapter_url(
            "https://www.asurascans.com/comics/nano-machine-00dcbf97/chapter/324"
        )
        assert is_chapter_url(
            "https://asurascans.com/comics/murim-psychopath-00dcbf97/chapter/0/"
        )

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://asurascans.com/")
        assert not is_chapter_url("https://asurascans.com/comics/")
        assert not is_chapter_url("https://asurascans.com/comics/series/")
        assert not is_chapter_url(
            "https://asurascans.com/comics/series/chapter/"
        )
        assert not is_chapter_url(
            "https://asurascans.com/comics/series/chapter/abc"
        )
        assert not is_chapter_url("https://other.com/comics/series/chapter/1")

    def test_valid_series_urls(self):
        assert is_series_url("https://asurascans.com/comics/murim-psychopath-00dcbf97")
        assert is_series_url(
            "https://www.asurascans.com/comics/nano-machine-00dcbf97/"
        )

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://asurascans.com/")
        assert not is_series_url("https://asurascans.com/comics/")
        assert not is_series_url(
            "https://asurascans.com/comics/series/chapter/1"
        )
        assert not is_series_url("https://other.com/comics/hello/")

    def test_error_urls_are_rejected(self):
        error_urls = [
            "https://asurascans.com/browse",
            "https://asurascans.com/register?redirectUrl=%2F",
            "https://asurascans.com/ebooks",
            "https://asurascans.com/series-ranking",
            "https://asurascans.com/leaderboard",
            "https://asurascans.com/privacy-policy",
            "https://asurascans.com/terms-of-service",
            "https://status.asurascans.com/",
            "https://asurascans.com/announcement/asura-novels-beta-is-now-available",
        ]
        for url in error_urls:
            assert not is_chapter_url(url)
            assert not is_series_url(url)

    def test_matches_url(self):
        scraper = AsurascansScraper()
        assert scraper.matches_url(
            "https://asurascans.com/comics/series/chapter/1"
        )
        assert scraper.matches_url("https://asurascans.com/comics/series")
        assert not scraper.matches_url("https://asurascans.com/browse")


class TestHelpers:
    def test_clean_image_url_strips_cache_buster(self):
        assert _clean_image_url(
            "https://cdn.asurascans.com/asura-images/chapters/s/1/001.webp?v=1748971234"
        ) == "https://cdn.asurascans.com/asura-images/chapters/s/1/001.webp"

    def test_chapter_number_from_slug(self):
        assert _chapter_number_from_slug(
            "https://asurascans.com/comics/s/chapter/37"
        ) == "37"
        assert _chapter_number_from_slug(
            "https://asurascans.com/comics/s/chapter/0"
        ) == "0"
        assert _chapter_number_from_slug("https://asurascans.com/comics/s") is None

    def test_series_slug_from_url(self):
        assert _series_slug_from_url(
            "https://asurascans.com/comics/murim-psychopath-00dcbf97"
        ) == "murim-psychopath-00dcbf97"
        assert _series_slug_from_url(
            "https://asurascans.com/comics/s/chapter/1"
        ) == "s"
        assert _series_slug_from_url("https://asurascans.com/") == ""


class TestMetaExtraction:
    def test_meta_from_comic_series(self):
        html = f"""
        <html><head>
            <script type="application/ld+json">{SERIES_JSONLD}</script>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        meta = _extract_meta(soup)
        assert meta["series_title"] == "Murim Psychopath"
        assert meta["description"].startswith("A once-in-an-era psychopath")
        assert meta["cover_url"] == COVER
        assert meta["genres"] == ["Action", "Adventure", "Crazy MC", "Fantasy", "Murim"]
        assert meta["authors"] == ["Gonbung"]
        assert meta["artists"] == ["Hitbook"]
        assert meta["community_rating"] == 9.6

    def test_meta_empty_when_missing(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_meta(soup) == {}

    def test_year_from_article_iso(self):
        html = """
        <html><head>
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Article",
             "datePublished":"2025-12-30T22:41:35Z"}
            </script>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _extract_year(soup) == 2025

    def test_year_none_when_missing(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_year(soup) is None

    def test_series_title_from_comic_series(self):
        html = f"""
        <html><head>
            <title>Murim Psychopath | Asura Scans</title>
            <script type="application/ld+json">{SERIES_JSONLD}</script>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _extract_series_title(soup, {}) == "Murim Psychopath"

    def test_chapter_title_from_article_headline(self):
        html = """
        <html><head>
            <title>Murim Psychopath Chapter 1 - Read Online | Asura Scans</title>
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Article",
             "headline":"Murim Psychopath Chapter 1",
             "isPartOf":{"@type":"ComicSeries","name":"Murim Psychopath"}}
            </script>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _extract_chapter_title(soup, {}, "Murim Psychopath") == "Chapter 1"

    def test_lang_from_html(self):
        soup = BeautifulSoup('<html lang="en-US"><body></body></html>', "lxml")
        assert _extract_lang(soup) == "en"

    def test_lang_empty_when_missing(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_lang(soup) == ""

    def test_status_from_label_card(self):
        html = """
        <html><body>
        <div class="flex gap-3">
            <div class="card"><div class="label">Status</div><div>ongoing</div></div>
            <div class="card"><div class="label">Type</div><div>manhwa</div></div>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _extract_status(soup) == "ongoing"

    def test_status_none_when_missing(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_status(soup) is None

    def test_premium_page_detected(self):
        html = """
        <html><head>
            <title>The Former Supreme Chapter 10 - Premium | Asura Scans</title>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert _is_premium_page(soup, {})

    def test_normal_page_not_premium(self):
        html = """
        <html><head>
            <title>Murim Psychopath Chapter 1 - Read Online | Asura Scans</title>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        assert not _is_premium_page(soup, {})


class TestImageExtraction:
    SAMPLE = """
    <html><body>
    <div class="reader">
        <img src="https://cdn.asurascans.com/asura-images/chapters/s/1/001.webp?v=111"/>
        <img src="https://cdn.asurascans.com/asura-images/chapters/s/1/002.webp?v=222"/>
        <img src="https://cdn.asurascans.com/asura-images/chapters/s/1/001.webp?v=333"/>
        <img src="https://cdn.asurascans.com/asura-images/covers/s-400.webp?v=1"/>
        <img src="https://asurascans.com/images/logo.webp"/>
        <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="/>
    </div>
    </body></html>
    """

    def test_extracts_only_chapter_cdn_images(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 2

    def test_strips_cache_buster(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        urls = " ".join(img.url for img in _extract_images(soup))
        assert "?v=" not in urls

    def test_excludes_cover_logo_and_data_uris(self):
        soup = BeautifulSoup(self.SAMPLE, "lxml")
        urls = " ".join(img.url for img in _extract_images(soup))
        assert "/covers/" not in urls
        assert "/images/logo" not in urls
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

    def test_accepts_chapters_restored_cdn_path(self):
        html = """
        <html><body><div class="reader">
            <img src="https://cdn.asurascans.com/asura-images/chapters-restored/s/1/001.webp?v=111"/>
            <img src="https://cdn.asurascans.com/asura-images/chapters-restored/s/1/002.webp?v=222"/>
        </div></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        images = _extract_images(soup)
        assert len(images) == 2
        assert all("/chapters-restored/" in img.url for img in images)


class TestAsurascansScraper:
    _SLUG = "murim-psychopath-00dcbf97"
    _CH = f"https://asurascans.com/comics/{_SLUG}/chapter/"
    _IMG = f"https://cdn.asurascans.com/asura-images/chapters/{_SLUG}/1/"
    _COVER_400 = (
        "https://cdn.asurascans.com/asura-images/covers/"
        f"{_SLUG}-400.webp?v=1"
    )

    SERIES_PAGE = f"""
    <html lang="en"><head>
        <title>Murim Psychopath | Asura Scans</title>
        <meta property="og:title" content="Murim Psychopath"/>
        <meta property="og:image" content="{COVER}"/>
        <meta property="og:description"
              content="A once-in-an-era psychopath fell into the Murim
              due to the Grim Reaper's mistake."/>
        <script type="application/ld+json">{SERIES_JSONLD}</script>
    </head><body>
    <div class="stats">
        <div class="card"><div class="label">Status</div><div>ongoing</div></div>
    </div>
    <div class="chapter-list">
        <a href="{_CH}3">Chapter 3<span>1 week ago</span></a>
        <a href="{_CH}2">Chapter 2<span>2 weeks ago</span></a>
        <a href="{_CH}1">Chapter 1<span>3 weeks ago</span></a>
        <a href="{_CH}0">First Chapter</a>
    </div>
    </body></html>
    """

    CHAPTER_PAGE = f"""
    <html lang="en"><head>
        <title>Murim Psychopath Chapter 1 - Read Online | Asura Scans</title>
        <meta property="og:title"
              content="Murim Psychopath Chapter 1 - Read Online | Asura Scans"/>
        <meta property="og:type" content="article"/>
        <meta property="og:image" content="{COVER}"/>
        <script type="application/ld+json">
        {{"@context":"https://schema.org","@type":"Article",
         "headline":"Murim Psychopath Chapter 1",
         "description":"Read Murim Psychopath Chapter 1 online for free at Asura Scans",
         "url":"https://asurascans.com/comics/{_SLUG}/chapter/1",
         "image":"{COVER}",
         "datePublished":"2025-12-30T22:41:35Z",
         "isPartOf":{{"@type":"ComicSeries","name":"Murim Psychopath",
                     "url":"https://asurascans.com/comics/{_SLUG}"}}
        }}
        </script>
    </head><body>
    <div class="reader">
        <img src="{_IMG}001.webp?v=1"/>
        <img src="{_IMG}002.webp?v=2"/>
        <img src="{_IMG}003.webp?v=3"/>
        <img src="{_COVER_400}"/>
    </div>
    </body></html>
    """

    PREMIUM_PAGE = """
    <html><head>
        <title>The Former Supreme Chapter 10 - Premium | Asura Scans</title>
        <meta property="og:title" content="The Former Supreme Chapter 10 - Premium | Asura Scans"/>
    </head><body></body></html>
    """

    def test_domain_attr(self):
        scraper = AsurascansScraper()
        assert scraper.domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_raises_on_premium_chapter(self):
        session = _MockSession(lambda url: _MockResponse(self.PREMIUM_PAGE))
        scraper = AsurascansScraper()
        with pytest.raises(ValueError, match="premium/locked"):
            await scraper.scrape(
                "https://asurascans.com/comics/the-former-supreme-00dcbf97/chapter/10",
                session,
            )

    @pytest.mark.asyncio
    async def test_scrape_raises_on_no_images(self):
        html = b"""<html><head>
            <title>Series Chapter 1 - Read Online | Asura Scans</title>
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Article",
             "headline":"Series Chapter 1",
             "isPartOf":{"@type":"ComicSeries","name":"Series"}}
            </script>
        </head><body></body></html>"""
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = AsurascansScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(
                "https://asurascans.com/comics/series/chapter/1", session,
            )

    @pytest.mark.asyncio
    async def test_scrape_chapter_with_enrichment(self):
        def handler(url):
            if url.endswith("/comics/murim-psychopath-00dcbf97"):
                return _MockResponse(self.SERIES_PAGE)
            return _MockResponse(self.CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = AsurascansScraper()
        meta = await scraper.scrape(
            "https://asurascans.com/comics/murim-psychopath-00dcbf97/chapter/1",
            session,
        )

        assert meta.series_title == "Murim Psychopath"
        assert meta.chapter_title == "Chapter 1"
        assert meta.chapter_number == "1"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.year == 2025
        assert meta.service == DOMAIN
        assert meta.cover_url == COVER
        assert meta.authors == ["Gonbung"]
        assert meta.artists == ["Hitbook"]
        assert meta.genres == ["Action", "Adventure", "Crazy MC", "Fantasy", "Murim"]
        assert meta.status == "ongoing"
        assert meta.community_rating == 9.6
        assert meta.description.startswith("A once-in-an-era psychopath")
        assert meta.total_pages == 3
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_cache_series_fetch(self):
        from comic_dl import config

        config.set_runtime_http(cache=False)
        try:
            def handler(url):
                if url.endswith("/comics/murim-psychopath-00dcbf97"):
                    return _MockResponse(self.SERIES_PAGE)
                return _MockResponse(self.CHAPTER_PAGE)

            session = _MockSession(handler)
            scraper = AsurascansScraper()
            url = "https://asurascans.com/comics/murim-psychopath-00dcbf97/chapter/1"
            await scraper.scrape(url, session)
            assert "murim-psychopath-00dcbf97" in scraper._series_cache
            await scraper.scrape(url, session)
            assert session._handler_calls == 3
        finally:
            config._RUNTIME_HTTP.pop("cache", None)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url.endswith("/comics/murim-psychopath-00dcbf97"):
                raise ConnectionError("boom")
            return _MockResponse(self.CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = AsurascansScraper()
        meta = await scraper.scrape(
            "https://asurascans.com/comics/murim-psychopath-00dcbf97/chapter/1",
            session,
        )
        assert meta.authors == []
        assert meta.genres == []
        assert meta.status is None
        assert meta.community_rating is None
        assert meta.cover_url == COVER
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(self.SERIES_PAGE))
        scraper = AsurascansScraper()
        series = await scraper.scrape_series(
            "https://asurascans.com/comics/murim-psychopath-00dcbf97",
            session,
        )

        assert series.series_title == "Murim Psychopath"
        assert series.description.startswith("A once-in-an-era psychopath")
        assert series.cover_url == COVER
        assert series.title_no == "murim-psychopath-00dcbf97"
        assert len(series.chapters) == 4
        assert series.chapters[0]["episode_no"] == "0"
        assert series.chapters[0]["title"] == "Chapter 0"
        assert series.chapters[1]["episode_no"] == "1"
        assert series.chapters[-1]["episode_no"] == "3"
        assert series.chapters[-1]["url"].endswith("/chapter/3")

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        html = (
            "<html><head>"
            "<title>Some Series | Asura Scans</title>"
            f'<script type="application/ld+json">{SERIES_JSONLD}</script>'
            "</head><body></body></html>"
        )
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = AsurascansScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(
                "https://asurascans.com/comics/some-series", session,
            )
