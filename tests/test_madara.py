"""Shared Madara plumbing: summary box, reader images, archive detection."""

from __future__ import annotations

from bs4 import BeautifulSoup

from comic_dl.scrapers.madara import (
    clean_image_url,
    extract_lang,
    extract_meta_rows,
    extract_post_id,
    genres_from_rows,
    is_archive_page,
    reader_images,
    rows_first_prefixed,
    rows_get,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class TestIsArchivePage:
    def test_archive_body_class(self):
        html = '<body class="archive category category-x"><h1>t</h1></body>'
        assert is_archive_page(_soup(html)) is True

    def test_single_post_is_not_archive(self):
        html = (
            '<body class="single post postid-123 format-standard">'
            "<h1>t</h1></body>"
        )
        assert is_archive_page(_soup(html)) is False

    def test_prefixed_class_does_not_match(self):
        # `taxonomy-category` contains "category" as a substring but is not
        # the exact token the archive check keys on.
        html = '<body class="taxonomy-category term-x">t</body>'
        assert is_archive_page(_soup(html)) is False


class TestExtractPostId:
    def test_from_body_class(self):
        html = '<body class="single postid-456 dark">t</body>'
        assert extract_post_id(_soup(html)) == "456"

    def test_missing(self):
        assert extract_post_id(_soup("<body>x</body>")) == ""


class TestExtractLang:
    def test_primary_subtag(self):
        assert extract_lang(_soup('<html lang="en-US">')) == "en"

    def test_missing(self):
        assert extract_lang(_soup("<html><body></body></html>")) == ""


class TestCleanImageUrl:
    def test_strips_query_and_fragment(self):
        assert clean_image_url("https://c/x.jpg?v=2#f") == "https://c/x.jpg"

    def test_strip_resize_suffix(self):
        assert clean_image_url(
            "https://c/img-300x450.jpg", strip_resize=True,
        ) == "https://c/img.jpg"

    def test_keep_resize_by_default(self):
        assert clean_image_url("https://c/img-300x450.jpg") == (
            "https://c/img-300x450.jpg"
        )


class TestMetaRows:
    def _rows(self):
        html = """
        <div class="post-content_item">
          <h5>Genre(s)</h5>
          <div class="summary-content">
            <a>Action</a> <a>Comedy</a>
          </div>
        </div>
        <div class="post-content_item">
          <h5>Status:</h5>
          <div class="summary-content">Ongoing</div>
        </div>
        """
        return extract_meta_rows(_soup(html))

    def test_labels_lowercased_and_colon_stripped(self):
        rows = self._rows()
        assert set(rows) == {"genre(s)", "status"}

    def test_link_rows_split_per_anchor(self):
        assert rows_get(self._rows(), "genre(s)") == ["Action", "Comedy"]

    def test_scalar_row_comma_fallback(self):
        assert rows_get(self._rows(), "status") == ["Ongoing"]

    def test_rows_get_case_insensitive(self):
        assert rows_get(self._rows(), "GENRE(S)") == ["Action", "Comedy"]

    def test_genres_merge_tags_dedup(self):
        rows = {"genre(s)": ["Action"], "tag(s)": ["Action", "School"]}
        assert genres_from_rows(rows) == ["Action", "School"]

    def test_prefixed_lookup_absorbs_label_variants(self):
        assert rows_first_prefixed({"status (raw)": ["Hiatus"]}, "status") == (
            "Hiatus"
        )


class TestReaderImages:
    HTML = """
    <html><body>
      <nav><img src="https://cdn/logo.png"/></nav>
      <div class="reading-content">
        <img data-src="https://cdn/p/1.jpg?v=9"/>
        <img src="data:image/gif;base64,AAAA"/>
        <img src="https://other/p/2.jpg"/>
        <img data-lazy-src="https://cdn/p/3.jpg"/>
        <img src="https://cdn/p/1.jpg"/>
      </div>
    </body></html>
    """

    def test_container_scope_cdn_filter_dedup_order(self):
        imgs = reader_images(
            _soup(self.HTML), (".reading-content",),
            lambda u: u.startswith("https://cdn/"),
        )
        urls = [i.url for i in imgs]
        assert urls == [
            "https://cdn/p/1.jpg",
            "https://cdn/p/3.jpg",
        ]
        assert [i.page_number for i in imgs] == [1, 2]

    def test_fallback_selector_then_document(self):
        soup = _soup('<div class="read-container">'
                      '<img src="https://cdn/a.jpg"/>'
                      "</div>")
        imgs = reader_images(
            soup, (".reading-content", ".read-container"),
            lambda u: True,
        )
        assert [i.url for i in imgs] == ["https://cdn/a.jpg"]
