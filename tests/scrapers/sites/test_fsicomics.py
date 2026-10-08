from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.base import BaseScraper, meta_index
from comic_dl.scrapers.sites.fsicomics._base import (
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


class _HtmlResp:
    """Minimal _timeout_get response stub (HTML text, always 200)."""

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


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

    def test_valid_mirror_chapter_urls(self):
        for host in ("es", "de", "fr", "it"):
            assert is_chapter_url(f"https://{host}.fsicomics.com/a-comic-slug/")

    def test_invalid_mirror_chapter_urls(self):
        # Category roots, localized legal pages, and WordPress machinery on a
        # mirror are listings, never a comic permalink.
        for url in (
            "https://es.fsicomics.com/comics-porno-3d/",
            "https://de.fsicomics.com/3d-porno-comics/",
            "https://fr.fsicomics.com/comics-porno-3d/",
            "https://it.fsicomics.com/fumetti-porno-3d/",
            "https://es.fsicomics.com/contactenos/",
            "https://de.fsicomics.com/datenschutzerklarung/",
            "https://fr.fsicomics.com/contactez-nous/",
            "https://it.fsicomics.com/termini-di-servizio/",
            "https://es.fsicomics.com/wp-json/",
            "https://es.fsicomics.com/dmca/",
            "https://sub.es.fsicomics.com/comic/",
        ):
            assert not is_chapter_url(url), url

    def test_valid_series_urls(self):
        assert is_series_url("https://fsicomics.com/all-porn-comics/3d-porn-comics/tlameteotl/")
        assert is_series_url(
            "https://fsicomics.com/all-porn-comics/indian-porn-comics/savita-bhabhi-english/"
        )

    def test_valid_mirror_series_urls(self):
        # Mirrors expose their taxonomy at /{category}/{artist-or-group}/.
        assert is_series_url("https://es.fsicomics.com/comics-porno-3d/kizaru3d/")
        assert is_series_url("https://de.fsicomics.com/3d-porno-comics/antalore42/")
        assert is_series_url("https://it.fsicomics.com/manga-hentai/vari-manga-hentai/")

    def test_mirror_category_root_is_not_a_series(self):
        # A single segment is a category root, not the artist/group listing.
        for url in (
            "https://es.fsicomics.com/comics-porno-3d/",
            "https://es.fsicomics.com/ultimos-comics-porno/",
        ):
            assert not is_series_url(url), url

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


class TestLocalizedTitleFormats:
    """Live title formats on the four mirrors and the apex.

    The site tail is localized ("FSI Comics ES", "FSI Comics Italiano", ...) and
    the chapter/artist separator is an ASCII hyphen on some hosts and an en dash
    on others, so both the tail match and the split have to be shape-based.
    ``EN`` spells out the en dash because the repo's lint flags a literal one.
    """

    EN = "\u2013"

    LIVE_TITLES = {
        "https://es.fsicomics.com/a-night-with-loona-capitulo-3-jizoku/": (
            "A Night With Loona Cap\u00edtulo\u00a03 " + EN + " Jizoku - FSI Comics ES",
            "A Night With Loona - Jizoku",
            "3",
        ),
        "https://de.fsicomics.com/emmas-corruption-kapitel-10-antalore42/": (
            "Emma's Corruption Kapitel 10 - Antalore42 - FSI Comics",
            "Emmas Corruption - Antalore42",
            "10",
        ),
        "https://fr.fsicomics.com/epouse-pervertie-chapitre-13-historikito/": (
            "\u00c9pouse Pervertie Chapitre 13 - Historikito - FSI Comics Fran\u00e7ais",
            "Epouse Pervertie - Historikito",
            "13",
        ),
        "https://it.fsicomics.com/oba-to-haha-zenpen-zia-e-madre-capitolo-2-nishikawa-kou/": (
            "Oba to Haha Zenpen - Zia e Madre Capitolo 2 " + EN + " Nishikawa Kou"
            " - FSI Comics Italiano",
            "Oba To Haha Zenpen Zia E Madre - Nishikawa Kou",
            "2",
        ),
        "https://fsicomics.com/taming-the-beast-chapter-5-kizaru3d/": (
            "Taming The Beast Chapter 5 - Kizaru3D - FSIComics",
            "Taming The Beast - Kizaru3D",
            "5",
        ),
    }

    @pytest.mark.parametrize("url", sorted(LIVE_TITLES))
    def test_series_and_chapter_from_live_title(self, url):
        title, expected_series, expected_number = self.LIVE_TITLES[url]
        html = (
            "<html><head><title>" + title + "</title></head><body>"
            '<div class="entry-content"><figure><img src="https://fsicomics.com'
            '/wp-content/uploads/2026/07/comic-001.webp"/></figure></div>'
            "</body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        series, _chapter = _extract_meta(soup, meta_index(soup))
        assert series != title, "the localized site tail was not stripped"
        assert "FSI Comics" not in series and "FSIComics" not in series
        assert _derive_series_title(url, title) == expected_series
        assert _extract_chapter_number(title) == expected_number


class TestLocalizedChapterNumbers:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("My Comic Chapter 5", "5"),
            ("Comic Ch. 05", "05"),
            ("A Night With Loona Cap\u00edtulo 3\u2013 Jizoku", "3"),
            ("Emma's Corruption Kapitel 10", "10"),
            ("Épouse Pervertie Chapitre 13", "13"),
            ("Zia e Madre Capitolo 2", "2"),
        ],
    )
    def test_matches_every_language(self, title, expected):
        assert _extract_chapter_number(title) == expected

    def test_non_breaking_space_before_number(self):
        # The mirrors use U+00A0 between the chapter word and the number.
        assert _extract_chapter_number("A Night With Loona Capítulo\u00a03") == "3"

    def test_epilogue_has_no_number(self):
        assert _extract_chapter_number("Valentina's Choice Epílogo") is None


class TestLocalizedSeriesDerivation:
    @pytest.mark.parametrize(
        "url,title,expected",
        [
            (
                "https://es.fsicomics.com/valentinas-choice-epilogo-jl78/",
                "Valentina's Choice Ep\u00edlogo \u2013 JL78 - FSI Comics ES",
                "Valentinas Choice - JL78",
            ),
            (
                "https://de.fsicomics.com/emmas-corruption-kapitel-10-antalore42/",
                "Emma's Corruption Kapitel 10 - Antalore42 - FSI Comics",
                "Emmas Corruption - Antalore42",
            ),
        ],
    )
    def test_marker_words(self, url, title, expected):
        assert _derive_series_title(url, title) == expected


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


class TestMirrorHosts:
    """Each mirror registers its own host, because the CLI resolves by exact host."""

    @pytest.mark.parametrize(
        "module,domain,language,chapter_url,series_url",
        [
            (
                "apex",
                "fsicomics.com",
                "en",
                "https://fsicomics.com/x-chapter-1-a/",
                "https://fsicomics.com/all-porn-comics/cat/artist/",
            ),
            (
                "es",
                "es.fsicomics.com",
                "es",
                "https://es.fsicomics.com/x-capitulo-1-a/",
                "https://es.fsicomics.com/comics-porno-3d/artist/",
            ),
            (
                "de",
                "de.fsicomics.com",
                "de",
                "https://de.fsicomics.com/x-kapitel-1-a/",
                "https://de.fsicomics.com/3d-porno-comics/artist/",
            ),
            (
                "fr",
                "fr.fsicomics.com",
                "fr",
                "https://fr.fsicomics.com/x-chapitre-1-a/",
                "https://fr.fsicomics.com/comics-porno-3d/artist/",
            ),
            (
                "it",
                "it.fsicomics.com",
                "it",
                "https://it.fsicomics.com/x-capitolo-1-a/",
                "https://it.fsicomics.com/fumetti-porno-3d/artist/",
            ),
        ],
    )
    def test_host_is_owned_and_language_comes_from_the_host(
        self, module, domain, language, chapter_url, series_url
    ):
        import importlib

        cls = importlib.import_module(f"comic_dl.scrapers.sites.fsicomics.{module}")
        scraper = next(
            v for k, v in vars(cls).items() if isinstance(v, type) and v.__module__ == cls.__name__
        )()
        assert scraper.domain == domain
        assert scraper.language == language

        other = "de.fsicomics.com" if domain != "de.fsicomics.com" else "es.fsicomics.com"
        assert scraper.matches_url(f"https://{domain}/a-slug/")
        assert not scraper.matches_url(f"https://{other}/a-slug/")
        assert scraper.matches_series_url(series_url)

    def test_every_fsi_host_is_registered_exactly_once(self):
        from comic_dl.scrapers import list_sources

        entries = [e for e in list_sources() if e.builtin and "fsicomics" in e.domain]
        assert len(entries) == 5
        assert len({e.site_id for e in entries}) == 5
        assert {e.domain for e in entries} == {
            "fsicomics.com",
            "es.fsicomics.com",
            "de.fsicomics.com",
            "fr.fsicomics.com",
            "it.fsicomics.com",
        }
        for entry in entries:
            assert entry.has_chapter and entry.has_series
            assert entry.display_name.strip()
            assert entry.chapter_url_pattern.strip()
            assert entry.series_url_pattern.strip()

    @pytest.mark.asyncio
    async def test_mirror_chapter_end_to_end(self):
        # Real live title/artist separator for the Spanish mirror.
        html = (
            "<html><head>"
            "<title>A Night With Loona Cap\u00edtulo 3 \u2013 Jizoku - FSI Comics ES</title>"
            '<meta property="og:image" content="https://es.fsicomics.com'
            '/wp-content/uploads/2026/06/cover.webp"/>'
            "</head><body>"
            '<div class="entry-content"><figure><img src="https://es.fsicomics.com'
            '/wp-content/uploads/2026/06/a-night-with-loona-capitulo-3-jizoku-001.webp"/>'
            "</figure></div></body></html>"
        ).encode()

        from comic_dl.scrapers.sites.fsicomics.es import FsicomicsEsScraper

        session = _MockSession(lambda url: _MockResponse(html))
        chapter = await FsicomicsEsScraper()._scrape_chapter(
            "https://es.fsicomics.com/a-night-with-loona-capitulo-3-jizoku/", session
        )
        assert chapter.info.series_title == "A Night With Loona - Jizoku"
        assert chapter.info.chapter_title == "Chapter 3"
        assert chapter.info.chapter_number == "3"
        assert chapter.info.artists == ["Jizoku"]
        assert chapter.info.language == "es"
        assert chapter.source.service == "es.fsicomics.com"


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
    async def test_pagination_probes_past_single_dead_page(self, monkeypatch):
        """One dead page must not truncate the listing; later pages still land."""
        from comic_dl.scrapers.sites.fsicomics._base import _collect_series_pages

        def page(next_on):
            nxt = '<a class="next page-numbers" href="/page/9/">Next</a>' if next_on else ""
            return f"<html><body>{nxt}</body></html>"

        fetched = []

        async def fake_timeout_get(u, client):
            fetched.append(u)
            if u.endswith("/page/2/"):
                raise RuntimeError("blip")
            if u.endswith("/page/3/"):
                return _HtmlResp(page(True))
            return _HtmlResp(page(False))

        monkeypatch.setattr("comic_dl.scrapers.base.BaseScraper._timeout_get", fake_timeout_get)
        base = "https://fsicomics.com/all-porn-comics/x/"
        pages = await _collect_series_pages(
            base,
            BeautifulSoup(page(True), "lxml"),
            object(),  # type: ignore
        )
        assert [u for u, _ in pages] == [
            base,
            base + "page/2/",
            base + "page/3/",
            base + "page/4/",
        ]
        assert [ps is not None for _, ps in pages] == [True, False, True, True]
        assert fetched == [base + "page/2/", base + "page/3/", base + "page/4/"]

    @pytest.mark.asyncio
    async def test_pagination_stops_after_consecutive_dead_pages(self, monkeypatch):
        """A genuinely finished series still terminates after two misses."""
        from comic_dl.scrapers.sites.fsicomics._base import _collect_series_pages

        fetched = []

        async def fake_timeout_get(u, client):
            fetched.append(u)
            raise RuntimeError("gone")

        monkeypatch.setattr("comic_dl.scrapers.base.BaseScraper._timeout_get", fake_timeout_get)
        base = "https://fsicomics.com/all-porn-comics/x/"
        pages = await _collect_series_pages(
            base,
            BeautifulSoup('<html><body><a class="next page-numbers">N</a></body></html>', "lxml"),
            object(),  # type: ignore
        )
        assert [u for u, _ in pages] == [base, base + "page/2/", base + "page/3/"]
        assert fetched == [base + "page/2/", base + "page/3/"]

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

        assert chapter.info.series_title == "Family Debt - TRAPLust"
        assert chapter.info.chapter_title == "Chapter 1"
