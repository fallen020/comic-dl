"""Offline unit tests for the generic fallback scraper (static extraction)."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers import generic as gen
from comic_dl.scrapers.generic import (
    GenericScraper,
    _candidate_urls,
    _deep_links,
    _episode_no,
    _extract_gallery_images,
    _is_image_url,
    _largest_srcset,
    _looks_like_direct_image,
    _page_title,
    _path_segment,
    _series_title_for,
    _structured_image_urls,
)
from comic_dl.utils import RequestBlockedError

GALLERY_BASE = "https://cdn.example.com/chapter/12/"
SERIES_BASE = "https://manga.example.com/series/foo/"


@pytest.fixture(autouse=True)
def _permissive_validation(monkeypatch):
    """Keep unit tests offline: permissive URL validation (no DNS), which is
    where the fetch path would otherwise resolve hosts."""

    async def _permissive_async(url):
        return url

    monkeypatch.setattr(
        "comic_dl.scrapers.base.validate_request_url_async", _permissive_async
    )
    monkeypatch.setattr(
        "comic_dl.scrapers.generic.validate_request_url", lambda url: url
    )


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# Path / srcset helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_is_image_url_extensions(self):
        for ext in ("jpg", "jpeg", "png", "webp", "gif", "bmp", "avif"):
            assert _is_image_url(f"https://cdn.example.com/p/1.{ext}")

    def test_is_image_url_rejects_non_image(self):
        assert not _is_image_url("https://cdn.example.com/p/1.css")
        assert not _is_image_url("https://cdn.example.com/p/1")
        assert not _is_image_url("https://cdn.example.com/p/1.JS")

    def test_looks_like_direct_image_with_query(self):
        assert _looks_like_direct_image("https://cdn.example.com/1.jpg?token=abc")

    def test_looks_like_direct_image_rejects_no_ext(self):
        assert not _looks_like_direct_image("https://cdn.example.com/image/1234")

    def test_path_segment_strips_extension_and_kebab(self):
        assert _path_segment("https://x.example/a/b/chapter-12/") == "chapter 12"
        assert _path_segment("https://x.example/a/01.jpg") == "01"


class TestLargestSrcset:
    def test_picks_widest_w(self):
        srcset = (
            "https://cdn.example.com/small.jpg 480w, "
            "https://cdn.example.com/large.jpg 1920w, "
            "https://cdn.example.com/mid.jpg 960w"
        )
        assert _largest_srcset(srcset) == "https://cdn.example.com/large.jpg"

    def test_picks_highest_density_when_no_width(self):
        srcset = (
            "https://cdn.example.com/1x.jpg 1x, "
            "https://cdn.example.com/3x.jpg 3x, "
            "https://cdn.example.com/2x.jpg 2x"
        )
        assert _largest_srcset(srcset) == "https://cdn.example.com/3x.jpg"

    def test_skips_data_uri_blur_up(self):
        srcset = (
            "data:image/gif;base64,R0lGODl, "
            "https://cdn.example.com/real.jpg 800w"
        )
        assert _largest_srcset(srcset) == "https://cdn.example.com/real.jpg"

    def test_single_url_without_descriptor(self):
        assert _largest_srcset("https://cdn.example.com/only.jpg") == (
            "https://cdn.example.com/only.jpg"
        )

    def test_empty(self):
        assert _largest_srcset("") == ""


# ---------------------------------------------------------------------------
# Candidate URL extraction
# ---------------------------------------------------------------------------


class TestCandidateUrls:
    def test_src_in_document_order(self):
        soup = _soup("""
        <html><body>
          <img src="https://cdn.example.com/a/1.jpg"/>
          <img src="https://cdn.example.com/a/2.png"/>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/1.jpg",
            "https://cdn.example.com/a/2.png",
        ]

    def test_lazy_attrs_used_when_src_is_placeholder(self):
        soup = _soup("""
        <html><body>
          <img src="https://cdn.example.com/placeholder/1.gif"
               data-src="https://cdn.example.com/a/1.jpg"/>
          <img data-original="https://cdn.example.com/a/2.jpg"/>
          <img data-lazy-src="https://cdn.example.com/a/3.jpg"/>
          <img data-image="https://cdn.example.com/a/4.jpg"/>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/1.jpg",
            "https://cdn.example.com/a/2.jpg",
            "https://cdn.example.com/a/3.jpg",
            "https://cdn.example.com/a/4.jpg",
        ]

    def test_srcset_uses_largest_w(self):
        soup = _soup("""
        <html><body>
          <img srcset="https://cdn.example.com/a/small.jpg 480w,
                       https://cdn.example.com/a/big.jpg 1920w"/>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/big.jpg",
        ]

    def test_picture_source_preferred_over_fallback_img(self):
        soup = _soup("""
        <html><body>
          <picture>
            <source srcset="https://cdn.example.com/a/hi.webp 2x"/>
            <img src="https://cdn.example.com/a/lo.jpg"/>
          </picture>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/hi.webp",
        ]

    def test_noscript_fallback_collected(self):
        soup = _soup("""
        <html><body>
          <noscript><img src="https://cdn.example.com/a/js-off.jpg"/></noscript>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/js-off.jpg",
        ]

    def test_css_background_in_style_attr_and_block(self):
        soup = _soup("""
        <html><body>
          <div style="background-image: url('https://cdn.example.com/a/bg1.jpg')"></div>
          <style>.hero { background: url(https://cdn.example.com/a/bg2.jpg); }</style>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/bg1.jpg",
            "https://cdn.example.com/a/bg2.jpg",
        ]

    def test_placeholder_icon_spacer_filtered(self):
        soup = _soup("""
        <html><body>
          <img src="https://cdn.example.com/a/real.jpg"/>
          <img src="https://cdn.example.com/placeholder/1.gif"/>
          <img src="https://cdn.example.com/icon/logo.png"/>
          <img src="https://cdn.example.com/avatar/u.png"/>
          <img src="https://cdn.example.com/spacer.gif"/>
          <img src="https://cdn.example.com/loader.gif"/>
          <img src="https://cdn.example.com/favicon.ico"/>
          <img src="https://cdn.example.com/preview/thumb.jpg"/>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/real.jpg",
        ]

    def test_data_uri_src_dropped(self):
        soup = _soup("""
        <html><body>
          <img src="data:image/gif;base64,R0lGODlhAQABAIAAAAUEBAAAACwAAAAAAQABAAACAkQBADs="/>
          <img src="https://cdn.example.com/a/real.jpg"/>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/real.jpg",
        ]

    def test_non_image_asset_extensions_dropped(self):
        soup = _soup("""
        <html><body>
          <img src="https://cdn.example.com/app.css"/>
          <img src="https://cdn.example.com/main.js"/>
          <img src="https://cdn.example.com/font.woff2"/>
          <img src="https://cdn.example.com/a/real.jpg"/>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/real.jpg",
        ]

    def test_relative_urls_resolved_against_page(self):
        soup = _soup("""
        <html><body>
          <img src="pics/1.jpg"/>
          <img src="/static/2.jpg"/>
          <img src="https://cdn.example.com/a/3.jpg"/>
        </body></html>
        """)
        urls = _candidate_urls(soup, "https://manga.example.com/read/foo")
        assert urls == [
            "https://manga.example.com/read/pics/1.jpg",
            "https://manga.example.com/static/2.jpg",
            "https://cdn.example.com/a/3.jpg",
        ]

    def test_dedupe_drops_repeated_url(self):
        soup = _soup("""
        <html><body>
          <img src="https://cdn.example.com/a/1.jpg"/>
          <img data-src="https://cdn.example.com/a/1.jpg"/>
          <img src="https://cdn.example.com/a/1.jpg#frag"/>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/1.jpg",
        ]

    def test_validate_request_url_rejects_crafted_url(self, monkeypatch):
        def _block(url: str) -> str:
            if "evil.example" in url:
                raise RequestBlockedError(f"blocked {url!r}")
            return url

        monkeypatch.setattr(
            "comic_dl.scrapers.generic.validate_request_url", _block
        )
        soup = _soup("""
        <html><body>
          <img src="https://evil.example/a/1.jpg"/>
          <img src="https://cdn.example.com/a/good.jpg"/>
        </body></html>
        """)
        assert _candidate_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/a/good.jpg",
        ]


class TestStructuredImageUrls:
    def test_json_ld_string(self):
        soup = _soup("""
        <html><head>
          <script type="application/ld+json">
            {"@type": "Article", "image": "https://cdn.example.com/ld/1.jpg"}
          </script>
        </head></html>
        """)
        assert _structured_image_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/ld/1.jpg",
        ]

    def test_json_ld_list_and_nested_image_object(self):
        soup = _soup("""
        <html><head>
          <script type="application/ld+json">
            {"@type": "Article", "image": [
              "https://cdn.example.com/ld/1.jpg",
              {"@type": "ImageObject", "url": "https://cdn.example.com/ld/2.jpg"}
            ]}
          </script>
        </head></html>
        """)
        assert _structured_image_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/ld/1.jpg",
            "https://cdn.example.com/ld/2.jpg",
        ]

    def test_next_data_blob(self):
        soup = _soup("""
        <html><head>
          <script id="__NEXT_DATA__" type="application/json">
            {"props": {"pageProps": {"images": ["https://cdn.example.com/nd/1.jpg"]}}}
          </script>
        </head></html>
        """)
        assert _structured_image_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/nd/1.jpg",
        ]

    def test_placeholder_filtered(self):
        soup = _soup("""
        <html><head>
          <script type="application/ld+json">
            {"image": "https://cdn.example.com/preview/1.jpg"}
          </script>
        </head></html>
        """)
        assert _structured_image_urls(soup, GALLERY_BASE) == []

    def test_duplicates_deduped_first_seen_order(self):
        """The same URL (also with a fragment) must appear only once, in
        first-seen order — it commonly lives in both JSON-LD and next data,
        and the downloader has no URL dedup of its own."""
        soup = _soup("""
        <html><head>
          <script type="application/ld+json">
            {"@type": "Article", "image": [
              "https://cdn.example.com/ld/1.jpg",
              "https://cdn.example.com/ld/1.jpg#frag"
            ]}
          </script>
          <script id="__NEXT_DATA__" type="application/json">
            {"props": {"pageProps": {"images": [
              "https://cdn.example.com/ld/1.jpg",
              "https://cdn.example.com/nd/2.jpg"
            ]}}}
          </script>
        </head></html>
        """)
        assert _structured_image_urls(soup, GALLERY_BASE) == [
            "https://cdn.example.com/ld/1.jpg",
            "https://cdn.example.com/nd/2.jpg",
        ]


class TestExtractGalleryImages:
    def test_numbering_and_filenames_in_document_order(self):
        soup = _soup("""
        <html><body>
          <img src="https://cdn.example.com/a/0001.jpg"/>
          <img src="https://cdn.example.com/a/0002.jpg"/>
          <img src="https://cdn.example.com/a/0003.jpg"/>
        </body></html>
        """)
        images = _extract_gallery_images(soup, GALLERY_BASE)
        assert [i.page_number for i in images] == [1, 2, 3]
        assert [i.filename for i in images] == [
            "0001.jpg", "0002.jpg", "0003.jpg",
        ]

    def test_json_ld_used_only_when_no_img_candidates(self):
        soup = _soup("""
        <html><head>
          <script type="application/ld+json">
            {"image": "https://cdn.example.com/ld/1.jpg"}
          </script>
        </head><body>
          <img src="https://cdn.example.com/a/real.jpg"/>
        </body></html>
        """)
        images = _extract_gallery_images(soup, GALLERY_BASE)
        assert len(images) == 1
        assert images[0].url == "https://cdn.example.com/a/real.jpg"

        empty_soup = _soup("""
        <html><head>
          <script type="application/ld+json">
            {"image": "https://cdn.example.com/ld/1.jpg"}
          </script>
        </head></html>
        """)
        images = _extract_gallery_images(empty_soup, GALLERY_BASE)
        assert len(images) == 1
        assert images[0].url == "https://cdn.example.com/ld/1.jpg"

    def test_empty_page(self):
        assert _extract_gallery_images(_soup("<html><body></body></html>"), GALLERY_BASE) == []


# ---------------------------------------------------------------------------
# Series / chapter-list detection
# ---------------------------------------------------------------------------


class TestDeepLinks:
    def test_chapter_links_collected(self):
        soup = _soup("""
        <html><body>
          <a href="/series/foo/chapter-1">Chapter 1</a>
          <a href="/series/foo/chapter-2">Chapter 2</a>
          <a href="/series/foo/chapter-3">Chapter 3</a>
        </body></html>
        """)
        links = _deep_links(soup, SERIES_BASE)
        assert [u for u, _ in links] == [
            "https://manga.example.com/series/foo/chapter-1",
            "https://manga.example.com/series/foo/chapter-2",
            "https://manga.example.com/series/foo/chapter-3",
        ]
        assert [t for _, t in links] == ["Chapter 1", "Chapter 2", "Chapter 3"]

    def test_skips_self_page_and_shallow_links(self):
        soup = _soup("""
        <html><body>
          <a href="/series/foo/">Self</a>
          <a href="/">Home</a>
          <a href="#frag">Fragment</a>
          <a href="mailto:a@b.c">Mail</a>
          <a href="javascript:void(0)">JS</a>
          <a href="/series/foo/chapter-1">Chapter 1</a>
          <a href="/series/foo/chapter-2">Chapter 2</a>
          <a href="/series/foo/chapter-3">Chapter 3</a>
        </body></html>
        """)
        links = _deep_links(soup, SERIES_BASE)
        assert len(links) == 3

    def test_skips_asset_links(self):
        soup = _soup("""
        <html><body>
          <a href="/assets/cover.jpg">Cover</a>
          <a href="/assets/app.js">JS</a>
          <a href="/series/foo/chapter-1">Chapter 1</a>
          <a href="/series/foo/chapter-2">Chapter 2</a>
          <a href="/series/foo/chapter-3">Chapter 3</a>
        </body></html>
        """)
        links = _deep_links(soup, SERIES_BASE)
        assert len(links) == 3

    def test_skips_cross_host_links(self):
        soup = _soup("""
        <html><body>
          <a href="https://other.example.com/series/foo/chapter-1">Other</a>
          <a href="https://manga.example.com/series/foo/chapter-1">Chapter 1</a>
          <a href="https://manga.example.com/series/foo/chapter-2">Chapter 2</a>
          <a href="https://manga.example.com/series/foo/chapter-3">Chapter 3</a>
        </body></html>
        """)
        links = _deep_links(soup, SERIES_BASE)
        assert len(links) == 3

    def test_dedupe(self):
        soup = _soup("""
        <html><body>
          <a href="/series/foo/chapter-1">Chapter 1</a>
          <a href="/series/foo/chapter-1">Chapter 1 again</a>
          <a href="/series/foo/chapter-2">Chapter 2</a>
          <a href="/series/foo/chapter-3">Chapter 3</a>
        </body></html>
        """)
        assert len(_deep_links(soup, SERIES_BASE)) == 3


class TestSeriesHelpers:
    def test_episode_no_trailing_number(self):
        assert _episode_no("Chapter 12", 1) == "12"
        assert _episode_no("Episode 3.5", 1) == "3.5"

    def test_episode_no_falls_back_to_index(self):
        assert _episode_no("Prologue", 1) == "1"
        assert _episode_no("", 4) == "4"

    def test_series_title_first_segment(self):
        assert _series_title_for(SERIES_BASE, "Manga - Chapter 1 | Site") == "Manga"
        assert _series_title_for(SERIES_BASE, "Manga | Site") == "Manga"

    def test_series_title_falls_back_to_path(self):
        assert _series_title_for(SERIES_BASE, "") == "foo"

    def test_page_title_chain(self):
        soup = _soup("""
        <html><head><title>Page Title</title>
          <meta property="og:title" content="OG Title"/>
        </head></html>
        """)
        idx = gen.meta_index(soup)
        assert _page_title(soup, idx, SERIES_BASE) == "Page Title"

        soup2 = _soup("""
        <html><head>
          <meta property="og:title" content="OG Title"/>
        </head></html>
        """)
        assert _page_title(soup2, gen.meta_index(soup2), SERIES_BASE) == "OG Title"

        soup3 = _soup("""
        <html><head>
          <script type="application/ld+json">{"name": "LD Name"}</script>
        </head></html>
        """)
        assert _page_title(soup3, gen.meta_index(soup3), SERIES_BASE) == "LD Name"

        soup4 = _soup("<html><head></head><body></body></html>")
        assert _page_title(soup4, gen.meta_index(soup4), SERIES_BASE) == "foo"


# ---------------------------------------------------------------------------
# Fake transport + scraper-level behaviour
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, content: bytes | str, *, content_type="text/html", status=200):
        self.content = content if isinstance(content, bytes) else content.encode()
        self.text = content.decode(errors="ignore") if isinstance(content, bytes) else content
        self.status_code = status
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

        if self.status_code >= 400:
            raise CurlHTTPError(f"HTTP {self.status_code}", response=self)


class _FakeClient:
    def __init__(self, pages: dict[str, _FakeResp]):
        self._pages = pages
        self.calls: list[tuple[str, str]] = []

    async def get(self, url: str, **kwargs):
        self.calls.append(("get", url))
        return self._pages.get(url) or _FakeResp("", status=404)

    async def head(self, url: str, **kwargs):
        self.calls.append(("head", url))
        return self._pages.get(url) or _FakeResp("", status=404)


GALLERY_HTML = """\
<html><head><title>Manga - Chapter 12 | Read Site</title></head><body>
<div><img src="https://cdn.example.com/ch/001.jpg"/></div>
<div><img src="https://cdn.example.com/ch/002.jpg"/></div>
<div><img src="https://cdn.example.com/ch/003.jpg"/></div>
</body></html>
"""

SERIES_HTML = """\
<html><head><title>Manga - All Chapters | Read Site</title>
<meta property="og:image" content="https://cdn.example.com/cover.jpg"/></head><body>
<ul>
  <li><a href="/series/foo/chapter-1">Chapter 1</a></li>
  <li><a href="/series/foo/chapter-2">Chapter 2</a></li>
  <li><a href="/series/foo/chapter-3">Chapter 3</a></li>
  <li><a href="/series/foo/chapter-4">Chapter 4</a></li>
</ul>
</body></html>
"""

EMPTY_HTML = "<html><head><title>Nothing here</title></head><body></body></html>"


class TestGenericScraperScrape:
    @pytest.mark.asyncio
    async def test_direct_image_by_extension_no_fetch(self):
        scraper = GenericScraper()
        client = _FakeClient({})
        meta = await scraper.scrape("https://cdn.example.com/page-01.jpg", client)
        assert client.calls == []
        assert len(meta.images) == 1
        assert meta.images[0].page_number == 1
        assert meta.images[0].filename == "page_0001.jpg"
        assert meta.total_pages == 1
        assert meta.chapter_title == "page 01"
        assert meta.series_title == "cdn.example.com"

    @pytest.mark.asyncio
    async def test_direct_image_by_content_type_head(self):
        scraper = GenericScraper()
        url = "https://cdn.example.com/signed/image?id=abc"
        client = _FakeClient({url: _FakeResp(b"\xff\xd8\xff", content_type="image/jpeg")})
        meta = await scraper.scrape(url, client)
        assert client.calls == [("head", url)]
        assert len(meta.images) == 1
        assert meta.images[0].url == url
        assert meta.images[0].filename == "page_0001.jpg"

    @pytest.mark.asyncio
    async def test_gallery_page(self):
        scraper = GenericScraper()
        url = "https://manga.example.com/read/foo/12"
        client = _FakeClient({url: _FakeResp(GALLERY_HTML)})
        meta = await scraper.scrape(url, client)
        assert [i.filename for i in meta.images] == [
            "page_0001.jpg", "page_0002.jpg", "page_0003.jpg",
        ]
        assert meta.total_pages == 3
        assert meta.series_title == "Manga"
        assert meta.chapter_title == "Chapter 12 | Read Site"
        assert ("head", url) in client.calls
        assert ("get", url) in client.calls

    @pytest.mark.asyncio
    async def test_gallery_raises_on_no_images(self):
        scraper = GenericScraper()
        url = "https://manga.example.com/read/foo/12"
        client = _FakeClient({url: _FakeResp(EMPTY_HTML)})
        with pytest.raises(ValueError):
            await scraper.scrape(url, client)


class TestGenericScraperDetect:
    @pytest.mark.asyncio
    async def test_series_page(self):
        scraper = GenericScraper()
        url = SERIES_BASE.rstrip("/")
        client = _FakeClient({url: _FakeResp(SERIES_HTML)})
        assert await scraper.detect(url, client) == "series"

    @pytest.mark.asyncio
    async def test_gallery_page(self):
        scraper = GenericScraper()
        url = "https://manga.example.com/read/foo/12"
        client = _FakeClient({url: _FakeResp(GALLERY_HTML)})
        assert await scraper.detect(url, client) == "gallery"

    @pytest.mark.asyncio
    async def test_image_dominated_page_is_gallery_even_with_links(self):
        scraper = GenericScraper()
        html = """
        <html><body>
          <img src="https://cdn.example.com/ch/001.jpg"/>
          <img src="https://cdn.example.com/ch/002.jpg"/>
          <img src="https://cdn.example.com/ch/003.jpg"/>
          <a href="/read/foo/13">Next</a>
        </body></html>
        """
        url = "https://manga.example.com/read/foo/12"
        client = _FakeClient({url: _FakeResp(html)})
        assert await scraper.detect(url, client) == "gallery"

    @pytest.mark.asyncio
    async def test_not_scrapable(self):
        scraper = GenericScraper()
        url = "https://example.com/page"
        client = _FakeClient({url: _FakeResp(EMPTY_HTML)})
        assert await scraper.detect(url, client) is None

    @pytest.mark.asyncio
    async def test_direct_image_extension(self):
        scraper = GenericScraper()
        client = _FakeClient({})
        assert await scraper.detect("https://cdn.example.com/page-01.jpg", client) == "gallery"
        assert client.calls == []


class TestGenericScraperSeries:
    @pytest.mark.asyncio
    async def test_series_metadata(self):
        scraper = GenericScraper()
        url = SERIES_BASE.rstrip("/")
        client = _FakeClient({url: _FakeResp(SERIES_HTML)})
        series = await scraper.scrape_series(url, client)
        assert series.series_title == "Manga"
        assert series.cover_url == "https://cdn.example.com/cover.jpg"
        assert [c["url"] for c in series.chapters] == [
            "https://manga.example.com/series/foo/chapter-1",
            "https://manga.example.com/series/foo/chapter-2",
            "https://manga.example.com/series/foo/chapter-3",
            "https://manga.example.com/series/foo/chapter-4",
        ]
        assert [c["title"] for c in series.chapters] == [
            "Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4",
        ]
        assert [c["episode_no"] for c in series.chapters] == ["1", "2", "3", "4"]

    @pytest.mark.asyncio
    async def test_series_raises_when_no_chapters(self):
        scraper = GenericScraper()
        url = "https://manga.example.com/read/foo/12"
        client = _FakeClient({url: _FakeResp(EMPTY_HTML)})
        with pytest.raises(ValueError):
            await scraper.scrape_series(url, client)


class TestSingleImageMeta:
    def test_direct_image_meta(self):
        meta = gen._single_image_meta("https://cdn.example.com/a/hello.jpg")
        assert meta.series_title == "cdn.example.com"
        assert meta.chapter_title == "hello"
        assert meta.total_pages == 1
        assert meta.images[0].filename == "page_0001.jpg"
