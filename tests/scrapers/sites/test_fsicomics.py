from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.base import BaseScraper, meta_index
from comic_dl.scrapers.sites.fsicomics import (
    DOMAIN,
    FsicomixScraper,
    _clean_image_url,
    _derive_series_title,
    _extract_artists,
    _extract_chapter_number,
    _extract_description,
    _extract_genres,
    _extract_images,
    _extract_meta,
    _extract_post_id,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession


class TestUrlPatterns:
    def test_valid_chapter_urls(self):
        assert is_chapter_url("https://fsicomics.com/elixer-tlameteotl/")
        assert is_chapter_url("https://fsicomics.com/some-comic-name/")
        assert is_chapter_url("https://www.fsicomics.com/another-one/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://fsicomics.com/")
        assert not is_chapter_url("https://fsicomics.com/all-porn-comics/")
        assert not is_chapter_url("https://fsicomics.com/wp-content/uploads/image.jpg")
        assert not is_chapter_url("https://fsicomics.com/feed/")
        assert not is_chapter_url("https://fsicomics.com/porn-comics-video/")
        assert not is_chapter_url("https://fsicomics.com/ai-generated/")
        assert not is_chapter_url("https://fsicomics.com/search/")
        assert not is_chapter_url("https://other.com/comic/")

    def test_valid_series_urls(self):
        assert is_series_url("https://fsicomics.com/all-porn-comics/3d-porn-comics/tlameteotl/")
        assert is_series_url(
            "https://fsicomics.com/all-porn-comics/indian-porn-comics/savita-bhabhi-english/"
        )

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://fsicomics.com/elixer-tlameteotl/")
        assert not is_series_url("https://other.com/all-porn-comics/artist/")


class TestImageUrlCleaning:
    def test_no_resize_suffix(self):
        assert (
            _clean_image_url("https://fsicomics.com/wp-content/uploads/2026/07/img-001.webp")
            == "https://fsicomics.com/wp-content/uploads/2026/07/img-001.webp"
        )

    def test_strips_resize_suffix(self):
        assert (
            _clean_image_url(
                "https://fsicomics.com/wp-content/uploads/2026/07/img-001-768x768.webp"
            )
            == "https://fsicomics.com/wp-content/uploads/2026/07/img-001.webp"
        )

    def test_strips_large_resize(self):
        assert (
            _clean_image_url("https://fsicomics.com/wp-content/uploads/2026/07/img-001-150x96.webp")
            == "https://fsicomics.com/wp-content/uploads/2026/07/img-001.webp"
        )

    def test_strips_query_string(self):
        assert (
            _clean_image_url("https://fsicomics.com/wp-content/uploads/2026/07/img-001.webp?w=800")
            == "https://fsicomics.com/wp-content/uploads/2026/07/img-001.webp"
        )


class TestGetImageExt:
    def test_valid_extensions(self):
        assert BaseScraper.image_ext("https://example.com/img.jpg") == "jpg"
        assert BaseScraper.image_ext("https://example.com/img.jpeg") == "jpeg"
        assert BaseScraper.image_ext("https://example.com/img.png") == "png"
        assert BaseScraper.image_ext("https://example.com/img.webp") == "webp"
        assert BaseScraper.image_ext("https://example.com/img.gif") == "gif"

    def test_fallback_extension(self):
        assert BaseScraper.image_ext("https://example.com/img") == "jpg"
        assert BaseScraper.image_ext("https://example.com/img.unknown") == "jpg"


class TestMetaExtraction:
    def test_extracts_from_title(self):
        html = (
            "<html><head><title>Comic Name - Artist - FSIComics</title></head><body></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        series, chapter = _extract_meta(soup)
        assert series == "Artist"
        assert chapter == "Comic Name"

    def test_extracts_from_og_section(self):
        html = """
        <html><head>
            <title>Chapter Title - Artist Name - FSIComics</title>
            <meta property="article:section" content="Artist Name"/>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        series, chapter = _extract_meta(soup)
        assert series == "Artist Name"
        assert chapter == "Chapter Title"

    def test_fallback_untitled(self):
        html = "<html><head><title>Just A Title</title></head><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        series, chapter = _extract_meta(soup)
        assert series != ""
        assert chapter == "Just A Title"

    def test_short_title(self):
        html = "<html><head><title>Comic - FSIComics</title></head><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        series, chapter = _extract_meta(soup)
        assert series == chapter
        assert chapter == "Comic"

    def test_og_title_fallback(self):
        html = """
        <html><head>
            <meta property="og:title" content="My Comic - Cool Artist"/>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        series, chapter = _extract_meta(soup)
        assert series == "Cool Artist"
        assert chapter == "My Comic"

    def test_title_preferred_over_og_title(self):
        html = """
        <html><head>
            <title>Explicit Title - Artist - FSIComics</title>
            <meta property="og:title" content="Og Title - Og Artist"/>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        series, chapter = _extract_meta(soup)
        assert series == "Artist"
        assert chapter == "Explicit Title"


class TestDeriveSeriesTitle:
    def test_groups_chapter_by_series_and_artist(self):
        assert (
            _derive_series_title(
                "https://fsicomics.com/family-debt-chapter-1-traplust/",
                "Family Debt Chapter 1 \u2013 TRAPLust",
            )
            == "Family Debt - TRAPLust"
        )

    def test_artist_casing_from_title(self):
        assert (
            _derive_series_title(
                "https://fsicomics.com/the-elven-prince-chapter-2-traplust",
                "The Elven Prince Chapter 2 \u2013 TRAPLust",
            )
            == "The Elven Prince - TRAPLust"
        )

    def test_artist_from_slug_when_title_has_no_dash(self):
        assert (
            _derive_series_title(
                "https://fsicomics.com/deal-with-devil-chapter-3-traplust/",
                "Deal With Devil Chapter 3",
            )
            == "Deal With Devil - Traplust"
        )

    def test_artist_from_title_when_slug_has_no_number_tail(self):
        assert (
            _derive_series_title(
                "https://fsicomics.com/friends-with-benefits-chapter-1-traplust/",
                "Friends With Benefits \u2013 TRAPLust",
            )
            == "Friends With Benefits - TRAPLust"
        )

    def test_no_marker_returns_empty(self):
        assert (
            _derive_series_title(
                "https://fsicomics.com/my-comic/",
                "My Comic",
            )
            == ""
        )

    def test_marker_is_case_insensitive(self):
        assert (
            _derive_series_title(
                "https://fsicomics.com/family-debt-CHAPTER-2-traplust/",
                "Family Debt Chapter 2",
            )
            == "Family Debt - Traplust"
        )

    def test_multi_digit_chapter_number(self):
        assert (
            _derive_series_title(
                "https://fsicomics.com/family-debt-chapter-10-traplust/",
                "Family Debt Chapter 10 \u2013 TRAPLust",
            )
            == "Family Debt - TRAPLust"
        )

    def test_series_only_when_no_artist(self):
        assert (
            _derive_series_title(
                "https://fsicomics.com/family-debt-chapter-1/",
                "Family Debt Chapter 1",
            )
            == "Family Debt"
        )


class TestChapterNumber:
    def test_chapter_word(self):
        assert _extract_chapter_number("My Comic Chapter 5") == "5"

    def test_chapter_dot(self):
        assert _extract_chapter_number("Comic Ch. 05") == "05"

    def test_chapter_no_dot(self):
        assert _extract_chapter_number("Comic Ch 5") == "5"

    def test_no_chapter_number(self):
        assert _extract_chapter_number("My Comic") is None

    def test_empty_title(self):
        assert _extract_chapter_number("") is None


class TestArtistExtraction:
    def test_title_contains_artist(self):
        html = "<html><head><title>Comic Name - Artist Name - FSIComics</title></head><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        artists = _extract_artists(soup)
        assert artists == ["Artist Name"]

    def test_no_title(self):
        soup = BeautifulSoup("<html><head></head><body></body></html>", "lxml")
        artists = _extract_artists(soup)
        assert artists == []

    def test_short_title_no_artist(self):
        html = "<html><head><title>Just A Title</title></head><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        artists = _extract_artists(soup)
        assert artists == []


class TestGenreExtraction:
    def test_article_tag(self):
        html = """
        <html><head>
            <meta property="article:tag" content="3D"/>
            <meta property="article:tag" content="Parody"/>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        genres = _extract_genres(soup)
        assert genres == ["3D", "Parody"]

    def test_duplicate_tags_removed(self):
        html = """
        <html><head>
            <meta property="article:tag" content="3D"/>
            <meta property="article:tag" content="3D"/>
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        genres = _extract_genres(soup)
        assert genres == ["3D"]

    def test_no_tags(self):
        html = "<html><head></head><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        genres = _extract_genres(soup)
        assert genres == []


class TestImageExtraction:
    SAMPLE_HTML = """
    <html><body>
    <div class="entry-content">
        <figure class="wp-block-image size-large">
            <img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp" alt="Page 1"/>
        </figure>
        <figure class="wp-block-image">
            <img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-002-768x768.webp" alt="Page 2"/>
        </figure>
        <figure class="wp-block-image">
            <img data-src="https://fsicomics.com/wp-content/uploads/2026/07/comic-003.webp" alt="Lazy"/>
        </figure>
    </div>
    <div class="sidebar">
        <img src="https://fsicomics.com/wp-content/uploads/2026/07/sidebar-ad.webp" alt="ad"/>
    </div>
    <img src="https://other.com/image.jpg" alt="external"/>
    </body></html>
    """

    def test_extracts_all_valid_images(self):
        soup = BeautifulSoup(self.SAMPLE_HTML, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3

    def test_image_urls_cleaned(self):
        soup = BeautifulSoup(self.SAMPLE_HTML, "lxml")
        images = _extract_images(soup)
        urls = [img.url for img in images]
        assert "https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp" in urls
        assert "https://fsicomics.com/wp-content/uploads/2026/07/comic-002.webp" in urls
        assert "https://fsicomics.com/wp-content/uploads/2026/07/comic-003.webp" in urls

    def test_excludes_external_and_sidebar(self):
        soup = BeautifulSoup(self.SAMPLE_HTML, "lxml")
        images = _extract_images(soup)
        urls = [img.url for img in images]
        assert "https://other.com/image.jpg" not in urls
        assert "sidebar-ad" not in " ".join(urls)

    def test_sequential_numbering(self):
        soup = BeautifulSoup(self.SAMPLE_HTML, "lxml")
        images = _extract_images(soup)
        for i, img in enumerate(images, start=1):
            assert img.page_number == i

    def test_empty_content(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        images = _extract_images(soup)
        assert images == []

    def test_lazy_images_via_data_src(self):
        html = """
        <html><body>
        <div class="entry-content">
            <figure class="wp-block-image">
                <img data-src="https://fsicomics.com/wp-content/uploads/2026/07/lazy-001.webp"/>
            </figure>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        images = _extract_images(soup)
        assert len(images) == 1
        assert "lazy-001.webp" in images[0].url
        assert images[0].page_number == 1


class TestFsicomixScraper:
    def test_domain_attr(self):
        scraper = FsicomixScraper()
        assert scraper.domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_raises_on_no_images(self):
        html = b"<html><head><title>Test</title></head><body></body></html>"

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape("https://fsicomics.com/test/", session)

    @pytest.mark.asyncio
    async def test_scrape_rejects_category_archive_page(self):
        html = b"""
        <html><head><title>Porn Comics Video - FSIComics</title></head>
        <body class="archive category category-porn-comics-video">
        <div class="entry-content"><figure class="wp-block-image">
            <img src="https://fsicomics.com/wp-content/uploads/2026/07/thumb-001.webp"/>
        </figure></div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        with pytest.raises(ValueError, match="category/tag listing"):
            await scraper.scrape("https://fsicomics.com/porn-comics-video/", session)

    @pytest.mark.asyncio
    async def test_scrape_allows_single_post_category_classes(self):
        # WordPress prefixes category classes on real single posts (category-<slug>);
        # the bare "category"/"archive" tokens must be absent for it to be a comic.
        html = b"""
        <html><head><title>My Comic - Artist - FSIComics</title></head>
        <body class="wp-singular single postid-123 single-format-standard category-3d-porn-comics">
        <div class="entry-content">
            <figure><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
        </div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        chapter = await scraper._scrape_chapter(
            "https://fsicomics.com/my-comic/",
            session,
        )
        assert len(chapter.images) == 1

    @pytest.mark.asyncio
    async def test_scrape_extracts_publisher_from_jsonld(self):
        html = b"""
        <html><head>
            <title>My Comic - Artist - FSIComics</title>
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Article",
             "publisher":{"@type":"Organization","name":"Super Melons"}}
            </script>
        </head><body>
        <div class="entry-content">
            <figure><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
        </div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        chapter = await scraper._scrape_chapter(
            "https://fsicomics.com/my-comic/",
            session,
        )
        assert chapter.info.publisher == "Super Melons"

    @pytest.mark.asyncio
    async def test_scrape_extracts_publisher_from_article_section_first(self):
        html = b"""
        <html><head>
            <title>My Comic - Artist - FSIComics</title>
            <meta property="article:section" content="Super Melons"/>
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Article",
             "publisher":{"@type":"Organization","name":"FSI Comics"}}
            </script>
        </head><body>
        <div class="entry-content">
            <figure><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
        </div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        chapter = await scraper._scrape_chapter(
            "https://fsicomics.com/my-comic/",
            session,
        )
        assert chapter.info.publisher == "Super Melons"

    @pytest.mark.asyncio
    async def test_scrape_series_reads_foxiz_taxonomy_grid(self):
        html = b"""
        <html><head><title>Kizaru3D - FSIComics</title>
            <meta property="og:description" content="Kizaru3D comics."/>
        </head>
        <body class="archive category category-kizaru3d category-2391">
        <div class="block-wrap" id="uid_c2391"><div class="block-inner">
            <div class="p-wrap p-grid p-grid-1"><div class="p-content">
                <h4 class="entry-title"><a href="https://fsicomics.com/taming-chapter-5-x/">
                Taming Chapter 5 - X</a></h4>
            </div></div>
            <div class="p-wrap p-grid p-grid-1"><div class="p-content">
                <h4 class="entry-title"><a href="https://fsicomics.com/taming-chapter-4-x/">
                Taming Chapter 4 - X</a></h4>
            </div></div>
        </div></div>
        </body></html>
        """

        async def run():
            session = _MockSession(lambda url: _MockResponse(html))
            scraper = FsicomixScraper()
            return await scraper.scrape_series(
                "https://fsicomics.com/all-porn-comics/3d-porn-comics/kizaru3d/",
                session,
            )

        series = await run()
        assert series.series_title == "Kizaru3D"
        assert [c["url"] for c in series.chapters] == [
            "https://fsicomics.com/taming-chapter-5-x/",
            "https://fsicomics.com/taming-chapter-4-x/",
        ]
        assert [c["episode_no"] for c in series.chapters] == ["5", "4"]
        assert series.description == "Kizaru3D comics."

    @pytest.mark.asyncio
    async def test_scrape_series_skips_cross_promo_carousel(self):
        html = b"""
        <html><head><title>Kizaru3D - FSIComics</title></head>
        <body class="archive category category-kizaru3d">
        <div class="block-wrap" id="uid_c2391"><div class="block-inner">
            <div class="p-wrap p-grid p-grid-1"><div class="p-content">
                <h4 class="entry-title"><a href="https://fsicomics.com/mine-chapter-1-x/">
                Mine Chapter 1 - X</a></h4>
            </div></div>
        </div></div>
        <div class="block-wrap"><div class="block-inner">
            <div class="post-carousel swiper-container"><div class="swiper-wrapper">
                <div class="p-wrap p-grid p-box"><div class="grid-box">
                    <h1 class="entry-title"><a href="https://fsicomics.com/theirs-chapter-9-y/">
                    Theirs Chapter 9 - Y</a></h1>
                </div></div>
            </div></div>
        </div></div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        series = await scraper.scrape_series(
            "https://fsicomics.com/all-porn-comics/3d-porn-comics/kizaru3d/",
            session,
        )
        assert [c["url"] for c in series.chapters] == ["https://fsicomics.com/mine-chapter-1-x/"]

    @pytest.mark.asyncio
    async def test_scrape_series_follows_pagination(self):
        page2 = b"""
        <html><head><title>Kizaru3D - FSIComics</title></head>
        <body class="archive category category-kizaru3d">
        <div class="block-inner">
            <div class="p-wrap p-grid p-grid-1"><div class="p-content">
                <h4 class="entry-title"><a href="https://fsicomics.com/mine-chapter-1-x/">
                Mine Chapter 1 - X</a></h4>
            </div></div>
        </div>
        </body></html>
        """
        page1 = (
            page2.replace(
                b"https://fsicomics.com/mine-chapter-1-x/",
                b"https://fsicomics.com/mine-chapter-2-x/",
            )
            .replace(
                b"Mine Chapter 1 - X",
                b"Mine Chapter 2 - X",
            )
            # The pager anchor must live inside <body>: markup trailing
            # </html> is dropped by some libxml2 builds (Windows CI).
            .replace(
                b"</body>",
                b'<a class="next page-numbers" href="/page/2/">Next</a></body>',
            )
        )

        session = _MockSession(lambda url: _MockResponse(page2 if "/page/2/" in url else page1))
        scraper = FsicomixScraper()
        series = await scraper.scrape_series(
            "https://fsicomics.com/all-porn-comics/3d-porn-comics/kizaru3d/",
            session,
        )
        assert [c["episode_no"] for c in series.chapters] == ["2", "1"]

    @pytest.mark.asyncio
    async def test_scrape_series_empty_listing_raises(self):
        html = b"""
        <html><head><title>Empty - FSIComics</title></head>
        <body class="archive category category-empty">
        <div class="block-inner"></div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(
                "https://fsicomics.com/all-porn-comics/empty/",
                session,
            )

    @pytest.mark.asyncio
    async def test_scrape_chapter_still_rejects_archive_page(self):
        html = b"""
        <html><head><title>Indian Porn Comics - FSIComics</title></head>
        <body class="archive category category-indian-porn-comics">
        <div class="entry-content"><figure class="wp-block-image">
            <img src="https://fsicomics.com/wp-content/uploads/2026/07/thumb-001.webp"/>
        </figure></div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        with pytest.raises(ValueError, match="category/tag listing"):
            await scraper.scrape("https://fsicomics.com/thumb/", session)

    @pytest.mark.asyncio
    async def test_scrape_with_images_success(self):
        html = b"""
        <html><head>
            <title>My Comic - Artist Name - FSIComics</title>
            <meta property="og:description" content="A great comic"/>
            <meta property="og:image" content="https://fsicomics.com/wp-content/uploads/2026/07/cover.webp"/>
        </head><body>
        <div class="entry-content">
            <figure class="wp-block-image"><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
            <figure class="wp-block-image"><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-002-768x768.webp"/></figure>
        </div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        meta = await scraper.scrape("https://fsicomics.com/my-comic/", session)

        assert meta.series_title == "Artist Name"
        assert meta.chapter_title == "My Comic"
        assert len(meta.images) == 2
        assert meta.description == "A great comic"
        assert meta.service == DOMAIN
        assert meta.total_pages == 2

    @pytest.mark.asyncio
    async def test_scrape_with_tags_and_metadata(self):
        html = b"""
        <html><head>
            <title>My Comic Chapter 3 - Cool Artist - FSIComics</title>
            <meta property="og:description" content="A great comic"/>
            <meta property="og:image" content="https://fsicomics.com/wp-content/uploads/2026/07/cover.webp"/>
            <meta property="article:tag" content="3D"/>
            <meta property="article:tag" content="Parody"/>
        </head><body>
        <div class="entry-content">
            <figure class="wp-block-image"><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
        </div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        chapter = await scraper._scrape_chapter(
            "https://fsicomics.com/my-comic/",
            session,
        )

        assert chapter.info.series_title == "Cool Artist"
        assert chapter.info.chapter_title == "Chapter 3"
        assert chapter.info.chapter_number == "3"
        assert chapter.info.artists == ["Cool Artist"]
        assert chapter.info.genres == ["3D", "Parody"]
        assert len(chapter.images) == 1
        assert chapter.cover_url == "https://fsicomics.com/wp-content/uploads/2026/07/cover.webp"

    @pytest.mark.asyncio
    async def test_unnumbered_single_chapter_gets_no_number(self):
        # Sites like FSIComics don't number their comics; a directly
        # downloaded single chapter must stay unnumbered, never "0".
        html = b"""
        <html><head>
            <title>My Comic - Cool Artist - FSIComics</title>
        </head><body>
        <div class="entry-content">
            <figure class="wp-block-image"><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
        </div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        chapter = await scraper._scrape_chapter(
            "https://fsicomics.com/my-comic/",
            session,
        )

        assert chapter.info.chapter_title == "My Comic"
        assert chapter.info.chapter_number is None
        assert chapter.info.chapter_number != "0"

    @pytest.mark.asyncio
    async def test_scrape_extracts_post_id_from_body_class(self):
        html = b"""
        <html><head><title>My Comic - Artist - FSIComics</title></head>
        <body class="wp-singular post-template-default single postid-828652 single-format-standard">
        <div class="entry-content">
            <figure><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
        </div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        chapter = await scraper._scrape_chapter(
            "https://fsicomics.com/my-comic/",
            session,
        )

        assert chapter.source.post_id == "828652"

    @pytest.mark.asyncio
    async def test_scrape_extracts_post_id_from_element_id(self):
        html = b"""
        <html><head><title>My Comic - Artist - FSIComics</title></head>
        <body>
        <article id="post-987654" class="post">
        <div class="entry-content">
            <figure><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
        </div>
        </article>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        chapter = await scraper._scrape_chapter(
            "https://fsicomics.com/my-comic/",
            session,
        )

        assert chapter.source.post_id == "987654"

    @pytest.mark.asyncio
    async def test_scrape_post_id_empty_when_missing(self):
        html = b"""
        <html><head><title>My Comic - Artist - FSIComics</title></head>
        <body>
        <div class="entry-content">
            <figure><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
        </div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        chapter = await scraper._scrape_chapter(
            "https://fsicomics.com/my-comic/",
            session,
        )

        assert chapter.source.post_id == ""

    def test_extract_post_id_from_body_class(self):
        soup = BeautifulSoup('<body class="single single-post postid-828652 x">', "lxml")
        assert _extract_post_id(soup) == "828652"

    def test_extract_post_id_from_element_id(self):
        soup = BeautifulSoup('<div id="post-987654"></div>', "lxml")
        assert _extract_post_id(soup) == "987654"

    def test_extract_post_id_returns_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_post_id(soup) == ""

    def test_description_prefers_full_body_over_truncated_meta(self):
        soup = BeautifulSoup(
            """
            <html><head>
                <meta property="og:description" content="Left alone, a"/>
            </head><body>
            <div class="entry-content">
                <h2>Episode Description</h2>
                <p>Left alone, a dark-skinned guy seduced his new acquaintance.</p>
                <p>Kizaru3D Is The Publisher of This Comic Book Episode. Watch comics
                feature genres such as Anal, Blowjob, and more. Support the artist
                from here.</p>
                <div><p>Join the new channel for latest comics and manga updates:
                Join our Telegram channel.</p></div>
                <p><em><strong>Note: The original comic is a single chapter.</strong></em></p>
            </div>
            </body></html>
            """,
            "lxml",
        )
        desc = _extract_description(soup, meta_index(soup))
        assert "dark-skinned guy seduced" in desc
        assert "Publisher of This Comic Book" not in desc
        assert "Telegram" not in desc
        assert "single chapter" in desc

    def test_description_falls_back_to_meta_without_body_paragraphs(self):
        soup = BeautifulSoup(
            """
            <html><head>
                <meta property="og:description" content="Meta only summary"/>
            </head><body>
            <div class="entry-content">
                <figure><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
            </div>
            </body></html>
            """,
            "lxml",
        )
        assert _extract_description(soup, meta_index(soup)) == "Meta only summary"

    @pytest.mark.asyncio
    async def test_scrape_groups_single_part_title_by_series_and_artist(self):
        html = b"""
        <html><head>
            <title>Family Debt Chapter 1 \xe2\x80\x93 TRAPLust</title>
            <meta property="og:description" content="A great comic"/>
            <meta property="og:image" content="https://fsicomics.com/wp-content/uploads/2026/07/cover.webp"/>
        </head><body>
        <div class="entry-content">
            <figure class="wp-block-image"><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-001.webp"/></figure>
            <figure class="wp-block-image"><img src="https://fsicomics.com/wp-content/uploads/2026/07/comic-002-768x768.webp"/></figure>
        </div>
        </body></html>
        """

        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FsicomixScraper()
        chapter = await scraper._scrape_chapter(
            "https://fsicomics.com/family-debt-chapter-1-traplust/",
            session,
        )

        assert chapter.info.series_title == "Family Debt-TRAPLust"
        assert chapter.info.chapter_title == "Chapter 1"
