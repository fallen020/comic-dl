from __future__ import annotations

import xml.etree.ElementTree as ET

from comic_dl.comicinfo import (
    generate_comicinfo_xml,
    generate_series_comicinfo_xml,
    normalize_status,
)

_TACHIHOMI_NS = "http://www.w3.org/2001/XMLSchema"


class TestComicInfo:
    def test_basic_xml_structure(self):
        xml = generate_comicinfo_xml("Series", "The First Battle", 30, "https://x.com/g/1")
        root = ET.fromstring(xml)
        assert root.tag == "ComicInfo"
        assert root.find("Series").text == "Series"
        assert root.find("Title").text == "The First Battle"
        assert root.find("PageCount").text == "30"
        assert root.find("Web").text == "https://x.com/g/1"

    def test_number_extracted(self):
        xml = generate_comicinfo_xml("Series", "Chapter 5", 30)
        root = ET.fromstring(xml)
        assert root.find("Number").text == "5"

    def test_number_explicit(self):
        xml = generate_comicinfo_xml("Series", "Prologue", 30, chapter_number="1")
        root = ET.fromstring(xml)
        assert root.find("Number").text == "1"

    def test_number_missing(self):
        xml = generate_comicinfo_xml("Series", "Prologue", 30)
        root = ET.fromstring(xml)
        assert root.find("Number") is None

    def test_no_source_url(self):
        xml = generate_comicinfo_xml("Series", "Chapter 1", 10)
        root = ET.fromstring(xml)
        assert root.find("Web") is None

    def test_empty_title(self):
        xml = generate_comicinfo_xml("", "", 0)
        root = ET.fromstring(xml)
        assert root.find("Series") is not None
        assert root.find("Title") is not None

    def test_xml_declaration(self):
        xml = generate_comicinfo_xml("S", "C", 1)
        assert xml.startswith('<?xml version="1.0" encoding="utf-8"?>')

    def test_namespaces(self):
        xml = generate_comicinfo_xml("S", "C", 1)
        assert 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' in xml
        assert 'xmlns:xsd="http://www.w3.org/2001/XMLSchema"' in xml

    def test_description(self):
        xml = generate_comicinfo_xml(
            "S", "C", 1, description="A great series"
        )
        root = ET.fromstring(xml)
        assert root.find("Summary").text == "A great series"

    def test_description_omitted(self):
        xml = generate_comicinfo_xml("S", "C", 1)
        root = ET.fromstring(xml)
        assert root.find("Summary") is None

    def test_description_empty(self):
        xml = generate_comicinfo_xml("S", "C", 1, description="  ")
        root = ET.fromstring(xml)
        assert root.find("Summary") is None

    def test_volume(self):
        xml = generate_comicinfo_xml("S", "C", 1, volume_number="2")
        root = ET.fromstring(xml)
        assert root.find("Volume").text == "2"

    def test_genre(self):
        xml = generate_comicinfo_xml("S", "C", 1, genres=["Action", "Romance"])
        root = ET.fromstring(xml)
        assert root.find("Genre").text == "Action, Romance"

    def test_authors_single_element_comma_joined(self):
        xml = generate_comicinfo_xml("S", "C", 1, authors=["Author A", "Author B"])
        root = ET.fromstring(xml)
        writers = root.findall("Writer")
        assert len(writers) == 1
        assert writers[0].text == "Author A, Author B"

    def test_artists_written_as_artist_element(self):
        xml = generate_comicinfo_xml("S", "C", 1, artists=["Artist X", "Artist Y"])
        root = ET.fromstring(xml)
        assert root.find("Penciller") is None
        artists = root.findall("Artist")
        assert len(artists) == 1
        assert artists[0].text == "Artist X, Artist Y"

    def test_artists_omitted_when_absent(self):
        xml = generate_comicinfo_xml("S", "C", 1)
        root = ET.fromstring(xml)
        assert root.find("Artist") is None
        assert root.find("Writer") is None

    def test_reading_direction_rtl(self):
        xml = generate_comicinfo_xml("S", "C", 1, reading_direction="rtl")
        root = ET.fromstring(xml)
        assert root.find("Manga").text == "YesAndRightToLeft"

    def test_reading_direction_ltr(self):
        xml = generate_comicinfo_xml("S", "C", 1, reading_direction="ltr")
        root = ET.fromstring(xml)
        assert root.find("Manga").text == "No"

    def test_reading_direction_omitted_when_unknown(self):
        xml = generate_comicinfo_xml("S", "C", 1, reading_direction=None)
        root = ET.fromstring(xml)
        assert root.find("Manga") is None

    def test_community_rating(self):
        xml = generate_comicinfo_xml("S", "C", 1, community_rating=9.2)
        root = ET.fromstring(xml)
        assert root.find("CommunityRating").text == "9.2"

    def test_community_rating_omitted_when_none(self):
        xml = generate_comicinfo_xml("S", "C", 1)
        root = ET.fromstring(xml)
        assert root.find("CommunityRating") is None

    def test_year(self):
        xml = generate_comicinfo_xml("S", "C", 1, year=2025)
        root = ET.fromstring(xml)
        assert root.find("Year").text == "2025"

    def test_year_omitted_when_none(self):
        xml = generate_comicinfo_xml("S", "C", 1)
        root = ET.fromstring(xml)
        assert root.find("Year") is None

    def test_language(self):
        xml = generate_comicinfo_xml("S", "C", 1, language="en")
        root = ET.fromstring(xml)
        assert root.find("LanguageISO").text == "en"

    def test_publisher(self):
        xml = generate_comicinfo_xml("S", "C", 1, publisher="Super Melons")
        root = ET.fromstring(xml)
        assert root.find("Publisher").text == "Super Melons"

    def test_no_publisher_when_absent(self):
        xml = generate_comicinfo_xml("S", "C", 1)
        root = ET.fromstring(xml)
        assert root.find("Publisher") is None

    def test_colorist(self):
        xml = generate_comicinfo_xml(
            "S", "C", 1, colorists=["Red", "Blue"]
        )
        root = ET.fromstring(xml)
        assert root.find("Colorist").text == "Red, Blue"

    def test_no_colorist_when_absent(self):
        xml = generate_comicinfo_xml("S", "C", 1)
        root = ET.fromstring(xml)
        assert root.find("Colorist") is None

    def test_status_normalized(self):
        xml = generate_comicinfo_xml("S", "C", 1, status="Hiatus")
        root = ET.fromstring(xml)
        assert root.find("Status").text == "On hiatus"

    def test_status_tachiyomi_element(self):
        xml = generate_comicinfo_xml("S", "C", 1, status="ongoing")
        root = ET.fromstring(xml)
        tag = "{" + _TACHIHOMI_NS + "}PublishingStatusTachiyomi"
        status_el = root.find(tag)
        assert status_el is not None
        assert status_el.text == "Ongoing"
        assert "xmlns:ty=" in xml

    def test_status_unknown_omitted(self):
        xml = generate_comicinfo_xml("S", "C", 1, status="Seasons")
        root = ET.fromstring(xml)
        assert root.find("Status") is None
        assert root.find("{" + _TACHIHOMI_NS + "}PublishingStatusTachiyomi") is None

    def test_no_status_when_absent(self):
        xml = generate_comicinfo_xml("S", "C", 1)
        root = ET.fromstring(xml)
        assert root.find("Status") is None
        assert root.find("{" + _TACHIHOMI_NS + "}PublishingStatusTachiyomi") is None

    def test_title_placeholder_falls_back_to_series(self):
        xml = generate_comicinfo_xml("My Series", "Chapter 3", 30)
        root = ET.fromstring(xml)
        assert root.find("Title").text == "My Series"
        assert root.find("Number").text == "3"

    def test_title_kept_when_real(self):
        xml = generate_comicinfo_xml("My Series", "The Betrayal", 30)
        root = ET.fromstring(xml)
        assert root.find("Title").text == "The Betrayal"

    def test_cover_page(self):
        xml = generate_comicinfo_xml("S", "C", 1, has_cover=True)
        root = ET.fromstring(xml)
        pages = root.find("Pages")
        assert pages is not None
        page = pages.find("Page")
        assert page is not None
        assert page.get("Image") == "0"
        assert page.get("Type") == "FrontCover"

    def test_no_cover_page(self):
        xml = generate_comicinfo_xml("S", "C", 1, has_cover=False)
        root = ET.fromstring(xml)
        pages = root.find("Pages")
        assert pages is not None
        page = pages.find("Page")
        assert page.get("Image") == "0"
        assert page.get("Type") is None

    def test_pages_enumeration(self):
        xml = generate_comicinfo_xml("S", "C", 3, has_cover=True)
        root = ET.fromstring(xml)
        pages = root.find("Pages")
        assert len(pages.findall("Page")) == 3
        types = [p.get("Type") for p in pages.findall("Page")]
        assert types == ["FrontCover", None, None]

    def test_no_pages_when_zero(self):
        xml = generate_comicinfo_xml("S", "C", 0, has_cover=True)
        root = ET.fromstring(xml)
        assert root.find("Pages") is None

    def test_special_chars_escaped(self):
        xml = generate_comicinfo_xml("A&B <test>", 'Say "hello"', 1)
        assert "&amp;" in xml
        assert "&lt;" in xml
        root = ET.fromstring(xml)
        assert root.find("Series").text == "A&B <test>"

    def test_control_chars_removed(self):
        xml = generate_comicinfo_xml("Test\x00Series", "Chapter\x01", 1)
        assert "\x00" not in xml
        assert "\x01" not in xml
        root = ET.fromstring(xml)
        assert root.find("Series").text == "TestSeries"


class TestSeriesComicInfo:
    def test_snapshot_chapter_xml(self):
        """Exact bytes for a fully-populated chapter archive: the canonical
        tag order is a contract, so writer changes must be deliberate."""
        xml = generate_comicinfo_xml(
            "My Series", "Chapter 5", 30, "https://example.com/g/1",
            description="A great series", chapter_number="5", volume_number="1",
            authors=["A", "B"], artists=["X"], colorists=["C"],
            genres=["Action", "Romance"], language="en", publisher="Pub",
            status="Ongoing", reading_direction="rtl", community_rating=9.2,
            year=2024, has_cover=True,
        )
        pages = "".join(
            f'<Page Image="{i}" />' if i else '<Page Image="0" Type="FrontCover" />'
            for i in range(30)
        )
        assert xml == (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ComicInfo xmlns:ty="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            "<Series>My Series</Series><Title>My Series</Title>"
            "<Number>5</Number><Volume>1</Volume><PageCount>30</PageCount>"
            "<Summary>A great series</Summary><Web>https://example.com/g/1</Web>"
            "<Genre>Action, Romance</Genre><Writer>A, B</Writer><Artist>X</Artist>"
            "<Colorist>C</Colorist><Publisher>Pub</Publisher><Status>Ongoing</Status>"
            "<ty:PublishingStatusTachiyomi>Ongoing</ty:PublishingStatusTachiyomi>"
            "<LanguageISO>en</LanguageISO><Manga>YesAndRightToLeft</Manga>"
            "<CommunityRating>9.2</CommunityRating><Year>2024</Year>"
            f"<Pages>{pages}</Pages>"
            "</ComicInfo>"
        )

    def test_snapshot_series_xml(self):
        """Exact bytes for a fully-populated series folder file."""
        xml = generate_series_comicinfo_xml(
            "My Series", "https://example.com/s", "A blurb", ["Auth A"],
            ["Art B"], ["Col C"], ["Action", "Romance"], "Pub", "Ongoing",
            "en", "rtl", 9.2, 2024,
        )
        assert xml == (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ComicInfo xmlns:ty="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            "<Series>My Series</Series><Title>My Series</Title>"
            "<Summary>A blurb</Summary><Web>https://example.com/s</Web>"
            "<Genre>Action, Romance</Genre><Writer>Auth A</Writer>"
            "<Artist>Art B</Artist><Colorist>Col C</Colorist>"
            "<Publisher>Pub</Publisher><Status>Ongoing</Status>"
            "<ty:PublishingStatusTachiyomi>Ongoing</ty:PublishingStatusTachiyomi>"
            "<LanguageISO>en</LanguageISO><Manga>YesAndRightToLeft</Manga>"
            "<CommunityRating>9.2</CommunityRating><Year>2024</Year>"
            "</ComicInfo>"
        )

    def test_series_fields_present(self):
        xml = generate_series_comicinfo_xml(
            "My Series",
            source_url="https://example.com/s",
            description="A blurb",
            authors=["Auth A"],
            artists=["Art B"],
            colorists=["Col C"],
            genres=["Action", "Romance"],
            publisher="Pub",
            status="Ongoing",
            language="en",
            reading_direction="rtl",
            community_rating=9.2,
            year=2024,
        )
        root = ET.fromstring(xml)
        assert root.tag == "ComicInfo"
        assert root.find("Series").text == "My Series"
        assert root.find("Title").text == "My Series"
        assert root.find("Summary").text == "A blurb"
        assert root.find("Web").text == "https://example.com/s"
        assert root.find("Writer").text == "Auth A"
        assert root.find("Artist").text == "Art B"
        assert root.find("Colorist").text == "Col C"
        assert root.find("Genre").text == "Action, Romance"
        assert root.find("Publisher").text == "Pub"
        assert root.find("Status").text == "Ongoing"
        assert root.find("LanguageISO").text == "en"
        assert root.find("Manga").text == "YesAndRightToLeft"
        assert root.find("CommunityRating").text == "9.2"
        assert root.find("Year").text == "2024"

    def test_no_chapter_fields(self):
        xml = generate_series_comicinfo_xml("My Series")
        root = ET.fromstring(xml)
        assert root.find("Number") is None
        assert root.find("Volume") is None
        assert root.find("PageCount") is None
        assert root.find("Pages") is None

    def test_minimal(self):
        xml = generate_series_comicinfo_xml("S")
        root = ET.fromstring(xml)
        assert root.find("Series").text == "S"
        assert root.find("Summary") is None
        assert root.find("Web") is None

    def test_omits_absent_fields(self):
        xml = generate_series_comicinfo_xml(
            "S",
            source_url="https://example.com/s",
            description="",
            authors=[],
            reading_direction="ltr",
        )
        root = ET.fromstring(xml)
        assert root.find("Summary") is None
        assert root.find("Writer") is None
        assert root.find("Manga").text == "No"

    def test_special_chars_escaped(self):
        xml = generate_series_comicinfo_xml("A&B <test>")
        assert "&amp;" in xml
        assert "&lt;" in xml

    def test_shared_fields_do_not_diverge(self):
        """The chapter and series documents must agree on every shared field:
        they originate from the same metadata model, so a drift here is a
        regression against that architectural invariant."""
        kwargs = {
            "description": "A blurb",
            "genres": ["Action", "Comedy"],
            "authors": ["Auth A"],
            "artists": ["Art B"],
            "colorists": ["Col C"],
            "publisher": "Pub",
            "status": "ongoing",
            "language": "en",
            "reading_direction": "ltr",
            "community_rating": 9.2,
            "year": 2024,
        }
        shared = [
            "Summary", "Genre", "Writer", "Artist", "Colorist", "Publisher",
            "Status", "LanguageISO", "Manga", "CommunityRating", "Year",
        ]
        chapter = ET.fromstring(generate_comicinfo_xml(
            "S", "C", 3, "https://x.com/g/1", **kwargs,
        ))
        series = ET.fromstring(generate_series_comicinfo_xml(
            "S", "https://x.com/s", **kwargs,
        ))
        for tag in shared:
            assert chapter.find(tag).text == series.find(tag).text, tag
        assert chapter.find("Web").text != series.find("Web").text


class TestNormalizeStatus:
    def test_ongoing_variants(self):
        for raw in ("ongoing", "Ongoing", "in progress", "currently publishing"):
            assert normalize_status(raw) == "Ongoing", raw

    def test_completed_variants(self):
        for raw in ("completed", "Completed", "complete", "finished", "ended"):
            assert normalize_status(raw) == "Completed", raw

    def test_hiatus_variants(self):
        for raw in ("hiatus", "on hiatus", "on-hiatus", "paused"):
            assert normalize_status(raw) == "On hiatus", raw

    def test_cancelled_variants(self):
        for raw in ("cancelled", "canceled"):
            assert normalize_status(raw) == "Cancelled", raw

    def test_less_common_tokens(self):
        assert normalize_status("licensed") == "Licensed"
        assert normalize_status("publishing finished") == "Publishing finished"

    def test_bare_canonical_tokens_pass_through(self):
        assert normalize_status("Ongoing") == "Ongoing"
        assert normalize_status("On hiAtus") == "On hiatus"

    def test_missing_and_unknown(self):
        assert normalize_status(None) is None
        assert normalize_status("") is None
        assert normalize_status("   ") is None
        assert normalize_status("Seasons") is None
        assert normalize_status("dropped") is None
