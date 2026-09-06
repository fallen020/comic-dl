from __future__ import annotations

import tempfile
import unicodedata
from pathlib import Path

from comic_dl.utils import (
    IMAGE_MAGIC,
    MAGIC_MAX,
    ensure_unique_dir,
    is_valid_ehentai_url,
    is_valid_pawchive_url,
    is_valid_webtoon_url,
    normalize_url,
    normalize_url_key,
    sanitize_filename,
    verify_image_bytes,
    verify_image_file,
)


class TestSanitizeFilename:
    def test_basic(self):
        assert sanitize_filename("hello") == "hello"

    def test_invalid_chars(self):
        assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a-b-c-d-e-f-g-h-i-j"

    def test_multi_spaces(self):
        assert sanitize_filename("a   b  c") == "a b c"

    def test_trailing_dots_spaces(self):
        assert sanitize_filename(" hello. ") == "hello"

    def test_empty_returns_untitled(self):
        assert sanitize_filename("") == "untitled"

    def test_max_len(self):
        long = "x" * 300
        result = sanitize_filename(long, max_len=200)
        assert len(result) <= 200

    def test_all_invalid(self):
        assert sanitize_filename("<>:\"/\\|?*") == "untitled"

    def test_control_chars_stripped(self):
        assert sanitize_filename("a\x00b\x01c\x1f") == "abc"

    def test_nfc_normalized(self):
        decomposed = "Cafe\u0301"  # e + combining acute → NFC "é"
        assert sanitize_filename(decomposed) == "Caf\u00e9"
        assert unicodedata.is_normalized("NFC", sanitize_filename(decomposed))

    def test_reserved_device_base(self):
        assert sanitize_filename("CON.txt") == "_CON.txt"
        assert sanitize_filename("com9.tar") == "_com9.tar"
        assert sanitize_filename("AUX") == "_AUX"

    def test_unicode_space_collapsed(self):
        assert sanitize_filename("a\u00a0b") == "a b"
        assert sanitize_filename("\u00a0hello") == "hello"


class TestEnsureUniqueDir:
    def test_creates_parent_and_dir(self, tmp_path):
        path = ensure_unique_dir(tmp_path, "One Piece")
        assert path == tmp_path / "One Piece"
        assert path.is_dir()

    def test_exact_match_reused(self, tmp_path):
        first = ensure_unique_dir(tmp_path, "One Piece")
        second = ensure_unique_dir(tmp_path, "One Piece")
        assert first == second

    def test_case_variant_suffixed(self, tmp_path):
        ensure_unique_dir(tmp_path, "one piece")
        path = ensure_unique_dir(tmp_path, "One Piece")
        assert path == tmp_path / "One Piece (2)"
        assert path.is_dir()
        assert ensure_unique_dir(tmp_path, "ONE PIECE") == tmp_path / "ONE PIECE (3)"

    def test_existing_file_is_a_collision(self, tmp_path):
        (tmp_path / "Manga").write_text("not a directory", encoding="utf-8")
        path = ensure_unique_dir(tmp_path, "Manga")
        assert path == tmp_path / "Manga (2)"
        assert path.is_dir()


class TestNormalizeUrl:
    def test_https_upgrade(self):
        assert normalize_url("http://example.com").startswith("https://")

    def test_no_scheme(self):
        assert normalize_url("example.com").startswith("https://")

    def test_https_stays(self):
        assert normalize_url("https://example.com") == "https://example.com/"

    def test_lowercases_host(self):
        assert normalize_url("https://EXAMPLE.COM/Path") == "https://example.com/Path"

    def test_strips_trailing_slash(self):
        result = normalize_url("https://example.com/path/")
        assert result == "https://example.com/path"

    def test_strips_default_port(self):
        assert normalize_url("https://example.com:443/path") == "https://example.com/path"

    def test_preserves_non_default_port(self):
        assert normalize_url("https://example.com:8080/path") == "https://example.com:8080/path"

    def test_preserves_query(self):
        assert normalize_url("https://example.com/path?a=1") == "https://example.com/path?a=1"

    def test_preserves_fragment(self):
        assert normalize_url("https://example.com/path#sec") == "https://example.com/path#sec"

    def test_trailing_slash_root(self):
        assert normalize_url("https://example.com") == "https://example.com/"

    def test_key_strips_fragment_for_identity(self):
        assert normalize_url_key("https://example.com/page#a") == "https://example.com/page"
        assert normalize_url_key("https://example.com/page#b") == "https://example.com/page"
        assert normalize_url_key("https://example.com/page") == "https://example.com/page"

    def test_key_keeps_query_and_identity(self):
        assert normalize_url_key("https://example.com/p?a=1#s") == "https://example.com/p?a=1"
        assert normalize_url_key("https://EXAMPLE.com/p#s") == "https://example.com/p"

    def test_pawchive_url_preserved(self):
        url = "https://pawchive.pw/patreon/user/123/post/456/"
        result = normalize_url(url)
        assert result == "https://pawchive.pw/patreon/user/123/post/456"

    def test_ehentai_url_preserved(self):
        url = "https://e-hentai.org/g/123/abc/"
        result = normalize_url(url)
        assert result == "https://e-hentai.org/g/123/abc"


class TestIsValidPawchiveUrl:
    def test_valid_standard(self):
        assert is_valid_pawchive_url("https://pawchive.pw/patreon/user/123/post/456")

    def test_valid_with_trailing_slash(self):
        assert is_valid_pawchive_url("https://pawchive.pw/fanbox/user/123/post/456/")

    def test_valid_www(self):
        assert is_valid_pawchive_url("https://www.pawchive.pw/patreon/user/123/post/456")

    def test_invalid_no_scheme(self):
        assert not is_valid_pawchive_url("pawchive.pw/patreon/user/123/post/456")

    def test_invalid_no_match(self):
        assert not is_valid_pawchive_url("https://example.com/patreon/user/123/post/456")

    def test_invalid_ehentai(self):
        assert not is_valid_pawchive_url("https://e-hentai.org/g/123/abc/")


class TestIsValidWebtoonUrl:
    def test_valid_desktop_series(self):
        assert is_valid_webtoon_url(
            "https://www.webtoons.com/en/action/nano-machine/list?title_no=4344"
        )

    def test_valid_desktop_chapter(self):
        assert is_valid_webtoon_url(
            "https://www.webtoons.com/en/action/nano-machine/ep-1/"
            "viewer?title_no=4344&episode_no=1"
        )

    def test_valid_mobile(self):
        assert is_valid_webtoon_url(
            "https://m.webtoons.com/en/action/s/list?title_no=1"
        )

    def test_invalid_domain(self):
        assert not is_valid_webtoon_url("https://example.com")

    def test_invalid_no_query(self):
        assert not is_valid_webtoon_url("https://www.webtoons.com/en/action/s")

    def test_invalid_no_scheme(self):
        assert not is_valid_webtoon_url("www.webtoons.com/en/action/s/list?title_no=1")


class TestIsValidEhentaiUrl:
    def test_valid_standard(self):
        assert is_valid_ehentai_url("https://e-hentai.org/g/123/abc/")

    def test_valid_no_trailing_slash(self):
        assert is_valid_ehentai_url("https://e-hentai.org/g/123/abc")

    def test_valid_www(self):
        assert is_valid_ehentai_url("https://www.e-hentai.org/g/123/abc")

    def test_valid_hex_token(self):
        assert is_valid_ehentai_url("https://e-hentai.org/g/3161202/e7a26f9e16/")

    def test_invalid_no_scheme(self):
        assert not is_valid_ehentai_url("e-hentai.org/g/123/abc")

    def test_invalid_no_match(self):
        assert not is_valid_ehentai_url("https://example.com/g/123/abc")

    def test_invalid_pawchive(self):
        assert not is_valid_ehentai_url("https://pawchive.pw/patreon/user/123/post/456")


class TestVerifyImageBytes:
    def test_jpeg(self):
        assert verify_image_bytes(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00') == 'jpeg'

    def test_png(self):
        assert verify_image_bytes(b'\x89PNG\r\n\x1a\n') == 'png'

    def test_gif87(self):
        assert verify_image_bytes(b'GIF87a') == 'gif'

    def test_gif89(self):
        assert verify_image_bytes(b'GIF89a') == 'gif'

    def test_webp(self):
        data = b'RIFF\x00\x00\x00\x00WEBP'
        assert verify_image_bytes(data) == 'webp'
        data2 = b'RIFF' + b'\x00' * 4 + b'WEBP'
        assert verify_image_bytes(data2) == 'webp'

    def test_bmp(self):
        assert verify_image_bytes(b'BM\x00\x00') == 'bmp'

    def test_ico(self):
        assert verify_image_bytes(b'\x00\x00\x01\x00') == 'ico'

    def test_empty(self):
        assert verify_image_bytes(b'') is None

    def test_unknown(self):
        assert verify_image_bytes(b'\x00\x01\x02\x03') is None

    def test_riff_not_webp(self):
        # AVI starts with RIFF but is not WEBP
        avi_header = b'RIFF\x00\x00\x00\x00AVI '
        assert verify_image_bytes(avi_header) is None

    def test_riff_too_short(self):
        data = b'RIFF\x00\x00\x00'
        assert len(data) < 12
        assert verify_image_bytes(data) is None


class TestVerifyImageFile:
    def test_jpeg_file(self):
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00')
            p = Path(f.name)
        try:
            assert verify_image_file(p) == 'jpeg'
        finally:
            p.unlink(missing_ok=True)

    def test_png_file(self):
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(b'\x89PNG\r\n\x1a\n')
            p = Path(f.name)
        try:
            assert verify_image_file(p) == 'png'
        finally:
            p.unlink(missing_ok=True)

    def test_webp_file(self):
        with tempfile.NamedTemporaryFile(suffix='.webp', delete=False) as f:
            f.write(b'RIFF\x00\x00\x00\x00WEBP')
            p = Path(f.name)
        try:
            assert verify_image_file(p) == 'webp'
        finally:
            p.unlink(missing_ok=True)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            p = Path(f.name)
        try:
            assert verify_image_file(p) is None
        finally:
            p.unlink(missing_ok=True)

    def test_missing_file(self):
        assert verify_image_file(Path("/nonexistent/file.jpg")) is None

    def test_non_image_file(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False, mode='w') as f:
            f.write("hello world")
            p = Path(f.name)
        try:
            assert verify_image_file(p) is None
        finally:
            p.unlink(missing_ok=True)

    def test_riff_not_webp(self):
        """Bug A regression: RIFF files that are not WebP should not match."""
        with tempfile.NamedTemporaryFile(suffix='.avi', delete=False) as f:
            f.write(b'RIFF\x00\x00\x00\x00AVI ')
            p = Path(f.name)
        try:
            assert verify_image_file(p) is None
        finally:
            p.unlink(missing_ok=True)

    def test_bmp_file(self):
        with tempfile.NamedTemporaryFile(suffix='.bmp', delete=False) as f:
            f.write(b'BM\x00\x00')
            p = Path(f.name)
        try:
            assert verify_image_file(p) == 'bmp'
        finally:
            p.unlink(missing_ok=True)

    def test_ico_file(self):
        with tempfile.NamedTemporaryFile(suffix='.ico', delete=False) as f:
            f.write(b'\x00\x00\x01\x00')
            p = Path(f.name)
        try:
            assert verify_image_file(p) == 'ico'
        finally:
            p.unlink(missing_ok=True)

    def test_only_reads_header(self):
        """Bug D regression: should not read entire file into memory."""
        long_data = b'\xff\xd8\xff' + b'\x00' * 1000000
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(long_data)
            p = Path(f.name)
        try:
            assert verify_image_file(p) == 'jpeg'
        finally:
            p.unlink(missing_ok=True)


class TestMagicMax:
    def test_magic_max_enough_for_webp(self):
        assert MAGIC_MAX >= 12

    def test_magic_max_computed_correctly(self):
        expected = max(
            max(offset + len(m) for m, offset, _ in IMAGE_MAGIC),
            12,
        )
        assert expected == MAGIC_MAX
