from __future__ import annotations

from comic_dl.models import ImageItem, PostMetadata


class TestImageItem:
    def test_from_url_valid(self):
        url = "https://pawchive.pw/data/...?f=Page%201.jpg"
        item = ImageItem.from_url(url)
        assert item is not None
        assert item.page_number == 1
        assert item.filename == "Page 1.jpg"
        assert item.url == url

    def test_from_url_no_f_param(self):
        url = "https://pawchive.pw/data/..."
        item = ImageItem.from_url(url)
        assert item is None

    def test_from_url_no_number_in_filename(self):
        url = "https://example.com/?f=image.jpg"
        item = ImageItem.from_url(url)
        assert item is None

    def test_from_url_multiple_digits(self):
        url = "https://example.com/?f=Page%2042.jpg"
        item = ImageItem.from_url(url)
        assert item is not None
        assert item.page_number == 42
        assert item.filename == "Page 42.jpg"

    def test_from_url_with_path(self):
        url = "https://pawchive.pw/data/abc?f=Page%203.jpg&other=1"
        item = ImageItem.from_url(url)
        assert item is not None
        assert item.page_number == 3

    def test_from_url_path_traversal_dots(self):
        url = "https://example.com/?f=../../Page%201.jpg"
        item = ImageItem.from_url(url)
        assert item is not None
        assert "/" not in item.filename
        assert ".." not in item.filename
        assert item.page_number == 1

    def test_from_url_path_traversal_encoded(self):
        url = "https://example.com/?f=..%2F..%2Fimg%201.jpg"
        item = ImageItem.from_url(url)
        assert item is not None
        assert "/" not in item.filename
        assert ".." not in item.filename
        assert item.page_number == 1

    def test_from_url_absolute_path(self):
        url = "https://example.com/?f=/absolute/path/pic%201.jpg"
        item = ImageItem.from_url(url)
        assert item is not None
        assert item.filename.startswith("/") is False
        assert item.page_number == 1

    def test_equality(self):
        a = ImageItem(url="http://x.com", page_number=1, filename="a.jpg")
        b = ImageItem(url="http://x.com", page_number=1, filename="a.jpg")
        c = ImageItem(url="http://x.com", page_number=2, filename="b.jpg")
        assert a == b
        assert a != c


class TestPostMetadata:
    def test_minimal(self):
        m = PostMetadata(series_title="S", chapter_title="C")
        assert m.series_title == "S"
        assert m.chapter_title == "C"
        assert m.images == []
        assert m.total_pages is None

    def test_with_images(self):
        imgs = [ImageItem(url="http://x.com/1", page_number=1, filename="1.jpg")]
        m = PostMetadata(series_title="S", chapter_title="C", images=imgs)
        assert len(m.images) == 1

    def test_total_pages(self):
        m = PostMetadata(series_title="S", chapter_title="C", total_pages=30)
        assert m.total_pages == 30

    def test_service_fields(self):
        m = PostMetadata(
            series_title="S", chapter_title="C",
            service="e-hentai", user_id="123", post_id="abc",
        )
        assert m.service == "e-hentai"
        assert m.user_id == "123"
        assert m.post_id == "abc"
