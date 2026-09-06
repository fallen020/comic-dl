from __future__ import annotations

import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

from comic_dl.archiver import create_archive
from comic_dl.models import ImageItem, PostMetadata
from comic_dl.utils import image_source_name


def _no_tmp_residue(dir_: Path, name: str) -> None:
    assert not any(p.name.endswith(".tmp") for p in dir_.iterdir())


def _src_name(page_number: int, url: str = "") -> str:
    return image_source_name(page_number, url or f"http://x.com/{page_number}")


def _make_image(dest: Path, page_number: int, fmt: str = "jpeg", content: bytes | None = None, url: str = "") -> Path:
    magic = {
        "jpeg": b'\xff\xd8\xff\xe0\x00\x10JFIF\x00',
        "png": b'\x89PNG\r\n\x1a\n',
        "webp": b'RIFF\x00\x00\x00\x00WEBP',
        "gif": b'GIF89a',
    }
    name = _src_name(page_number, url)
    p = dest / name
    data = magic.get(fmt, b'\xff\xd8\xff')
    if content is not None:
        data = data + content
    p.write_bytes(data)
    return p


class TestCreateCbz:
    def test_basic_creation(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, content=b'\x01')
            _make_image(src, 2, content=b'\x02')
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz)
            assert added == 2
            assert skipped == []

            with zipfile.ZipFile(cbz, 'r') as zf:
                names = zf.namelist()
                assert "ComicInfo.xml" not in names
                assert names == sorted(names)
                for n in names:
                    assert zf.getinfo(n).compress_type == zipfile.ZIP_STORED

    def test_on_packed_callback_reports_counts(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            for n in (1, 2, 3):
                _make_image(src, n, content=bytes([n]))
            images = [
                ImageItem(url=f"http://x.com/{n}", page_number=n, filename=_src_name(n))
                for n in (1, 2, 3)
            ]
            seen: list[tuple[int, int]] = []
            cbz = out / "test.cbz"
            added, _ = create_archive(images, src, cbz, on_packed=lambda n, t: seen.append((n, t)))
            assert added == 3
            assert seen == [(1, 3), (2, 3), (3, 3)]

    def test_on_packed_skipped_pages_not_counted(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, content=b'\x01')
            (src / _src_name(2)).write_bytes(b"not an image")
            _make_image(src, 3, content=b'\x03')
            images = [
                ImageItem(url=f"http://x.com/{n}", page_number=n, filename=_src_name(n))
                for n in (1, 2, 3)
            ]
            seen: list[tuple[int, int]] = []
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz, on_packed=lambda n, t: seen.append((n, t)))
            assert added == 2
            assert any("not a valid image" in s for s in skipped)
            assert seen == [(1, 3), (2, 3)]

    def test_comicinfo_embedded(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, url="http://x.com/img.jpg")
            images = [
                ImageItem(url="http://x.com/img.jpg", page_number=1, filename=_src_name(1, "http://x.com/img.jpg")),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(
                images, src, cbz,
                series_title="My Series",
                chapter_title="Chapter 1",
                source_url="https://example.com/g/1",
                chapter_number="5",
                series_meta=PostMetadata(
                    series_title="My Series",
                    chapter_title="Chapter 1",
                    description="A blurb",
                    authors=["Auth A"],
                    genres=["Action", "Romance"],
                    status="ongoing",
                ),
            )
            assert added == 1
            assert skipped == []
            with zipfile.ZipFile(cbz, 'r') as zf:
                assert "ComicInfo.xml" in zf.namelist()
                data = zf.read("ComicInfo.xml").decode("utf-8")
                assert "<Series>My Series</Series>" in data
                assert "<Number>5</Number>" in data
                assert "<PageCount>1</PageCount>" in data
                # Series metadata travels with every chapter archive, so the
                # embedded file and the sibling folder file stay in sync.
                assert "<Summary>A blurb</Summary>" in data
                assert "<Genre>Action, Romance</Genre>" in data
                assert "Auth A" in data
                assert "<Status>Ongoing</Status>" in data
                assert 'Type="FrontCover"' in data

    def test_comicinfo_without_series_meta(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, url="http://x.com/img.jpg")
            images = [
                ImageItem(url="http://x.com/img.jpg", page_number=1, filename=_src_name(1, "http://x.com/img.jpg")),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(
                images, src, cbz, series_title="S", chapter_title="C",
            )
            assert added == 1
            assert skipped == []
            with zipfile.ZipFile(cbz, 'r') as zf:
                data = zf.read("ComicInfo.xml").decode("utf-8")
                assert "Summary" not in data
                assert "Genre" not in data
                assert 'Type="FrontCover"' in data

    def test_cover_not_bundled(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            p = src / "cover.jpg"
            p.write_bytes(b"\xff\xd8\xff")
            _make_image(src, 1, url="http://x.com/img.jpg")
            images = [
                ImageItem(url="http://x.com/img.jpg", page_number=1, filename=_src_name(1, "http://x.com/img.jpg")),
            ]
            cbz = out / "test.cbz"
            added, _skipped = create_archive(
                images, src, cbz,
                series_title="S", chapter_title="C",
            )
            assert added == 1
            with zipfile.ZipFile(cbz, 'r') as zf:
                assert "cover.jpg" not in zf.namelist()
                assert not any(n.startswith("cover") for n in zf.namelist())

    def test_missing_file_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/99", page_number=99, filename=_src_name(99)),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz)
            assert added == 1
            assert len(skipped) == 1
            assert "missing" in skipped[0]

    def test_page_count_uses_embedded_pages(self):
        """ComicInfo PageCount must count the pages actually embedded, not the
        images requested — a partial chapter never claims pages it lacks."""
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            cbz = out / "test.cbz"
            added, _ = create_archive(
                images, src, cbz,
                series_title="S", chapter_title="C",
            )
            assert added == 1
            with zipfile.ZipFile(cbz, 'r') as zf:
                data = zf.read("ComicInfo.xml").decode("utf-8")
                assert "<PageCount>1</PageCount>" in data
                pages = [n for n in zf.namelist() if n.startswith("Page_")]
                assert len(pages) == 1

    def test_invalid_image_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            p = src / _src_name(1)
            p.write_bytes(b"not an image")
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbz = out / "test.cbz"
            with pytest.raises(ValueError, match="No pages could be packed"):
                create_archive(images, src, cbz)
            assert not p.exists()
            _no_tmp_residue(out, "test.cbz")

    def test_empty_archive_raises_and_leaves_existing(self):
        """A chapter whose pages all got skipped must not silently produce an
        archive that holds only ComicInfo.xml (or nothing)."""
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            for n in (1, 2):
                p = src / _src_name(n)
                p.write_bytes(b"not an image")
            images = [
                ImageItem(url=f"http://x.com/{n}", page_number=n, filename=_src_name(n))
                for n in (1, 2)
            ]
            cbz = out / "test.cbz"
            cbz.write_bytes(b"existing good archive")
            with pytest.raises(ValueError, match="No pages could be packed"):
                create_archive(images, src, cbz, series_title="S", chapter_title="C")
            assert cbz.read_bytes() == b"existing good archive"
            _no_tmp_residue(out, "test.cbz")

    def test_all_duplicates_keep_first(self):
        """Identical-size identical-content pages are deduplicated, but the
        first copy is archived, so this is not an empty-archive case."""
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            content = b'\xff\xd8\xff' + b'\x01' * 1000
            (src / _src_name(1)).write_bytes(content)
            (src / _src_name(2)).write_bytes(content)
            images = [
                ImageItem(url=f"http://x.com/{n}", page_number=n, filename=_src_name(n))
                for n in (1, 2)
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz)
            assert added == 1
            assert any("duplicate" in s for s in skipped)

    @pytest.mark.parametrize("bad_name", [
        "../evil.jpg",
        "../../etc/passwd",
        "/absolute/path.png",
        "sub/dir.png",
        "..",
        "",
    ])
    def test_traversal_filenames_never_touch_outside(self, bad_name):
        """Plugin-supplied filenames must stay inside source_dir: anything with
        a path separator, an '..' component, an absolute path, or an empty/'.'
        name is refused before any read or delete happens."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / "src"
            out = base / "out"
            src.mkdir()
            out.mkdir()

            outside = base / "evil.jpg"
            outside.write_bytes(b'\xff\xd8\xff' + b'secret')

            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=bad_name),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            (src / _src_name(2)).write_bytes(b'\xff\xd8\xff' + b'\x02')
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz)
            assert added == 1
            assert any("missing" in s for s in skipped)
            assert outside.exists()
            assert outside.read_bytes() == b'\xff\xd8\xff' + b'secret'
            with zipfile.ZipFile(cbz, 'r') as zf:
                names = zf.namelist()
                assert all("evil" not in n and "passwd" not in n for n in names)

    def test_verified_format_cache_is_just_a_fallback(self):
        """A stale download-run cache entry must not override the bytes really
        being packed: the magic read from the page decides the extension."""
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            p = src / _src_name(1)
            p.write_bytes(b'\xff\xd8\xff' + b'\x01')  # actually jpeg
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbz = out / "test.cbz"
            added, _skipped = create_archive(
                images, src, cbz, verified_formats={_src_name(1): "png"}
            )
            assert added == 1
            with zipfile.ZipFile(cbz, 'r') as zf:
                assert any("Page_0001.jpeg" in n for n in zf.namelist())

    def test_atomic_write(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbz = out / "test.cbz"
            added, _skipped = create_archive(images, src, cbz)
            assert added == 1
            _no_tmp_residue(out, "test.cbz")
            assert cbz.exists()

    def test_archive_corruption_detected(self, monkeypatch):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbz = out / "test.cbz"

            original_testzip = zipfile.ZipFile.testzip

            def broken_testzip(self):
                return next(iter(self.namelist()))

            monkeypatch.setattr(zipfile.ZipFile, "testzip", broken_testzip)

            with pytest.raises(ValueError, match="Archive corrupted"):
                create_archive(images, src, cbz)

            _no_tmp_residue(out, "test.cbz")
            assert not cbz.exists()

            monkeypatch.setattr(zipfile.ZipFile, "testzip", original_testzip)

    def test_verification_success_renames(self, monkeypatch):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbz = out / "test.cbz"

            monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: None)

            added, skipped = create_archive(images, src, cbz)
            assert added == 1
            assert skipped == []
            _no_tmp_residue(out, "test.cbz")
            assert cbz.exists()

    def test_verification_exception_cleans_up(self, monkeypatch):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbz = out / "test.cbz"

            def raising_testzip(self):
                raise zipfile.BadZipFile("boom")

            monkeypatch.setattr(zipfile.ZipFile, "testzip", raising_testzip)

            with pytest.raises(ValueError, match="Archive verification failed") as exc_info:
                create_archive(images, src, cbz)

            assert isinstance(exc_info.value.__cause__, zipfile.BadZipFile)
            _no_tmp_residue(out, "test.cbz")
            assert not cbz.exists()

    def test_page_ordering(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 3, content=b'\x03')
            _make_image(src, 1, content=b'\x01')
            _make_image(src, 2, content=b'\x02')
            images = [
                ImageItem(url="http://x.com/3", page_number=3, filename=_src_name(3)),
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            cbz = out / "test.cbz"
            added, _skipped = create_archive(images, src, cbz)
            assert added == 3
            with zipfile.ZipFile(cbz, 'r') as zf:
                names = zf.namelist()
                naming_order = [n for n in names if n.endswith(".jpg")]
                assert naming_order == sorted(naming_order)

    def test_extension_from_format(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, fmt="png", url="http://x.com/img.png")
            _make_image(src, 2, fmt="webp", url="http://x.com/img.webp")
            images = [
                ImageItem(url="http://x.com/img.png", page_number=1, filename=_src_name(1, "http://x.com/img.png")),
                ImageItem(url="http://x.com/img.webp", page_number=2, filename=_src_name(2, "http://x.com/img.webp")),
            ]
            cbz = out / "test.cbz"
            added, _ = create_archive(images, src, cbz)
            assert added == 2
            with zipfile.ZipFile(cbz, 'r') as zf:
                names = zf.namelist()
                assert any("Page_0001.png" in n for n in names)
                assert any("Page_0002.webp" in n for n in names)

    def test_backward_compat_no_meta(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz)
            assert added == 1
            assert skipped == []
            with zipfile.ZipFile(cbz, 'r') as zf:
                assert "ComicInfo.xml" not in zf.namelist()


class TestSizeFirstDedup:
    def test_unique_sizes_not_hashed(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, content=b'\x01')
            _make_image(src, 2, content=b'\x02' * 100)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz)
            assert added == 2
            assert skipped == []
            with zipfile.ZipFile(cbz, 'r') as zf:
                names = zf.namelist()
                assert any("Page_0001" in n for n in names)
                assert any("Page_0002" in n for n in names)

    def test_same_size_duplicate_content_removed(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            content = b'\xff\xd8\xff' + b'\x01' * 1000
            (src / _src_name(1)).write_bytes(content)
            (src / _src_name(2)).write_bytes(content)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz)
            assert added == 1
            assert len(skipped) == 1
            assert "duplicate" in skipped[0]
            assert not (src / _src_name(2)).exists()

    def test_same_size_different_content_both_kept(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            (src / _src_name(1)).write_bytes(b'\xff\xd8\xff' + b'\x01' * 1000)
            (src / _src_name(2)).write_bytes(b'\xff\xd8\xff' + b'\x02' * 1000)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz)
            assert added == 2
            assert skipped == []
            with zipfile.ZipFile(cbz, 'r') as zf:
                names = zf.namelist()
                assert any("Page_0001" in n for n in names)
                assert any("Page_0002" in n for n in names)


class TestParseCompression:
    def test_stored_default(self):
        from zipfile import ZIP_STORED

        from comic_dl.archiver import parse_compression

        assert parse_compression("stored") == (ZIP_STORED, 0)

    def test_deflate_default_level_6(self):
        from zipfile import ZIP_DEFLATED

        from comic_dl.archiver import parse_compression

        assert parse_compression("deflate") == (ZIP_DEFLATED, 6)

    def test_deflate_explicit_level(self):
        from zipfile import ZIP_DEFLATED

        from comic_dl.archiver import parse_compression

        assert parse_compression("deflate:0") == (ZIP_DEFLATED, 0)
        assert parse_compression("deflate:9") == (ZIP_DEFLATED, 9)

    def test_case_insensitive(self):
        from comic_dl.archiver import parse_compression

        assert parse_compression("DEFLATE") == parse_compression("deflate")
        assert parse_compression(" Stored ") == parse_compression("stored")

    @pytest.mark.parametrize("bad", [
        "deflate:10", "deflate:-1", "deflate:x", "gzip", "", "deflate:6:2",
    ])
    def test_invalid_raises(self, bad):
        from comic_dl.archiver import parse_compression

        with pytest.raises(ValueError):
            parse_compression(bad)


class TestCreateCbzCompression:
    def test_deflate_entries_compressed(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, content=b"\x00" * 4000)
            _make_image(src, 2, content=b"\x01" * 4000)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz, compression="deflate:6")
            assert added == 2
            assert skipped == []
            with zipfile.ZipFile(cbz, 'r') as zf:
                for n in zf.namelist():
                    if n != "ComicInfo.xml":
                        assert zf.getinfo(n).compress_type == zipfile.ZIP_DEFLATED

    def test_deflate_smaller_than_stored(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            content = b"\xff\xd8\xff" + b"\x00" * 20_000
            (src / _src_name(1)).write_bytes(content)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            stored_cbz = out / "stored.cbz"
            create_archive(images, src, stored_cbz, compression="stored")
            deflated_cbz = out / "deflated.cbz"
            create_archive(images, src, deflated_cbz, compression="deflate:6")
            assert deflated_cbz.stat().st_size < stored_cbz.stat().st_size

    def test_invalid_compression_raises_and_cleans_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbz = out / "test.cbz"
            with pytest.raises(ValueError):
                create_archive(images, src, cbz, compression="gzip")
            assert not cbz.exists()
            _no_tmp_residue(out, "test.cbz")

    def test_default_remains_stored(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbz = out / "test.cbz"
            create_archive(images, src, cbz)
            with zipfile.ZipFile(cbz, 'r') as zf:
                assert zf.getinfo(zf.namelist()[0]).compress_type == zipfile.ZIP_STORED

    def test_dedup_deterministic_order(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            content = b"\xff\xd8\xff" + b"\x07" * 500
            (src / _src_name(1)).write_bytes(content)
            (src / _src_name(2)).write_bytes(content)
            (src / _src_name(3)).write_bytes(content)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
                ImageItem(url="http://x.com/3", page_number=3, filename=_src_name(3)),
            ]
            cbz = out / "test.cbz"
            added, skipped = create_archive(images, src, cbz)
            assert added == 1  # first page claims the content, the rest are dupes
            assert len(skipped) == 2
            with zipfile.ZipFile(cbz, 'r') as zf:
                names = zf.namelist()
                assert any("Page_0001" in n for n in names)


class TestCreateArchive:
    """Format dispatch: ``.cbz``/``.zip`` are zip, ``.cbt`` is tar. The zip
    builders are covered by :class:`TestCreateCbz`; these focus on the tar
    path and the shared guarantees that must hold for every format."""

    def test_cbt_creation(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, content=b'\x01')
            _make_image(src, 2, content=b'\x02')
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            cbt = out / "test.cbt"
            added, skipped = create_archive(images, src, cbt)
            assert added == 2
            assert skipped == []
            with tarfile.open(cbt, "r") as tf:
                names = tf.getnames()
                assert names == sorted(names)
                assert any("Page_0001" in n for n in names)
                assert any("Page_0002" in n for n in names)

    def test_cbt_comicinfo_embedded(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, url="http://x.com/img.jpg")
            images = [
                ImageItem(url="http://x.com/img.jpg", page_number=1, filename=_src_name(1, "http://x.com/img.jpg")),
            ]
            cbt = out / "test.cbt"
            added, skipped = create_archive(
                images, src, cbt,
                series_title="My Series",
                chapter_title="Chapter 1",
                source_url="https://example.com/g/1",
                chapter_number="5",
            )
            assert added == 1
            assert skipped == []
            with tarfile.open(cbt, "r") as tf:
                member = tf.getmember("ComicInfo.xml")
                data = tf.extractfile(member).read().decode("utf-8")
                assert "<Series>My Series</Series>" in data
                assert "<Title>My Series</Title>" in data
                assert "example.com" in data
                assert "<Number>5</Number>" in data
                assert "<PageCount>1</PageCount>" in data

    def test_cbt_missing_and_invalid_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            (src / _src_name(2)).write_bytes(b"not an image")
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
                ImageItem(url="http://x.com/99", page_number=99, filename=_src_name(99)),
            ]
            cbt = out / "test.cbt"
            added, skipped = create_archive(images, src, cbt)
            assert added == 1
            assert any("missing" in s for s in skipped)
            assert any("not a valid image" in s for s in skipped)

    def test_cbt_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            content = b'\xff\xd8\xff' + b'\x01' * 1000
            (src / _src_name(1)).write_bytes(content)
            (src / _src_name(2)).write_bytes(content)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
                ImageItem(url="http://x.com/2", page_number=2, filename=_src_name(2)),
            ]
            cbt = out / "test.cbt"
            added, skipped = create_archive(images, src, cbt)
            assert added == 1
            assert len(skipped) == 1
            assert "duplicate" in skipped[0]
            with tarfile.open(cbt, "r") as tf:
                assert sum(1 for n in tf.getnames() if n.startswith("Page_")) == 1

    def test_cbt_atomic_no_tmp_left(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbt = out / "test.cbt"
            added, _skipped = create_archive(images, src, cbt)
            assert added == 1
            _no_tmp_residue(out, "test.cbt")
            assert cbt.exists()

    def test_cbt_ignores_compression(self):
        """Compression is a zip-family knob; a .cbt must still parse cleanly."""
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, content=b"\x00" * 4000)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbt = out / "test.cbt"
            added, skipped = create_archive(images, src, cbt, compression="deflate:9")
            assert added == 1
            assert skipped == []
            with tarfile.open(cbt, "r") as tf:
                assert any("Page_" in n for n in tf.getnames())

    def test_cbt_verification_failure_cleans_up(self, monkeypatch):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            cbt = out / "test.cbt"

            def broken_verify(path):
                raise ValueError("boom")

            monkeypatch.setattr("comic_dl.archiver._verify_tar", broken_verify)
            with pytest.raises(ValueError, match="boom"):
                create_archive(images, src, cbt)
            _no_tmp_residue(out, "test.cbt")
            assert not cbt.exists()

    def test_cbt_on_packed_callback(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            for n in (1, 2, 3):
                _make_image(src, n, content=bytes([n]))
            images = [
                ImageItem(url=f"http://x.com/{n}", page_number=n, filename=_src_name(n))
                for n in (1, 2, 3)
            ]
            seen: list[tuple[int, int]] = []
            cbt = out / "test.cbt"
            added, _ = create_archive(images, src, cbt, on_packed=lambda n, t: seen.append((n, t)))
            assert added == 3
            assert seen == [(1, 3), (2, 3), (3, 3)]

    def test_zip_suffix_produces_zip(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1, content=b'\x01')
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            zip_path = out / "test.zip"
            added, skipped = create_archive(
                images, src, zip_path,
                series_title="S", chapter_title="C", source_url="https://x/1",
            )
            assert added == 1
            assert skipped == []
            with zipfile.ZipFile(zip_path, 'r') as zf:
                names = zf.namelist()
                assert "ComicInfo.xml" in names
                assert any("Page_0001" in n for n in names)

    def test_unsupported_suffix_raises(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            out = Path(td) / "out"
            src.mkdir()
            out.mkdir()
            _make_image(src, 1)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename=_src_name(1)),
            ]
            target = out / "test.pdf"
            with pytest.raises(ValueError, match="Unsupported archive format"):
                create_archive(images, src, target)
            assert not target.exists()
            _no_tmp_residue(out, "test.pdf")
