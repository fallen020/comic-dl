"""Cookie-at-rest encryption: key resolution, AES-256-GCM envelopes, jar wiring.

Hermetic by construction: these tests never touch a real OS keyring. Crypto
cases use a key injected through ``COMIC_DL_COOKIE_KEY`` (or ``off`` for the
plaintext fallbacks); backend-discovery functions are monkeypatched where a
keyring branch is exercised, so ``resolve_key`` here can only ever consult the
environment or a fake. Where a raw footer/row must be inspected at rest, it is
read through plain ``sqlite3`` — never a ``CookieJar`` — so no keyring can be
reached by accident.
"""

from __future__ import annotations

import base64
import sqlite3
from unittest.mock import patch

import pytest

from comic_dl.cookiecrypt import (
    decrypt_value,
    encrypt_value,
    is_encrypted,
    resolve_key,
)
from comic_dl.cookies import CookieJar


def _env_key(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode("ascii")


def _env_key_env(key: bytes) -> str:  # COMIC_DL_COOKIE_KEY is urlsafe-b64
    return _env_key(key)


@pytest.fixture
def jar_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    key = bytes(range(32))
    monkeypatch.setenv("COMIC_DL_COOKIE_KEY", _env_key(key))
    return key


# -- resolve_key ----------------------------------------------------------


def test_resolve_key_off_is_none() -> None:
    assert resolve_key("off") is None


def test_resolve_key_auto_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    key = bytes(range(32))
    monkeypatch.setenv("COMIC_DL_COOKIE_KEY", _env_key(key))
    assert resolve_key("auto") == key


def test_resolve_key_auto_empty_env_falls_through_to_keyring_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COMIC_DL_COOKIE_KEY", raising=False)
    # No real keyring on this (offline/CI) box: discovery is mocked to prove
    # the fall-through is *keyring-shaped*, not env-shaped.
    with patch("comic_dl.cookiecrypt.keyring_available", return_value=False):
        assert resolve_key("auto") is None


def test_resolve_key_keyring_never_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMIC_DL_COOKIE_KEY", _env_key(bytes(range(32))))
    # "keyring" mode must skip the env entirely and go straight to keyring
    # discovery (here: none).
    with patch("comic_dl.cookiecrypt.keyring_available", return_value=False):
        assert resolve_key("keyring") is None


def test_resolve_key_malformed_env_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMIC_DL_COOKIE_KEY", "not-valid-base64!!")
    # Must not raise, and must not fall through to a real keyring.
    with patch("comic_dl.cookiecrypt.keyring_available", return_value=False):
        assert resolve_key("auto") is None


def test_resolve_key_wrong_length_env_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMIC_DL_COOKIE_KEY", _env_key(bytes(range(16))))
    with patch("comic_dl.cookiecrypt.keyring_available", return_value=False):
        assert resolve_key("auto") is None


# -- encrypt_value / decrypt_value ----------------------------------------


def test_round_trip(jar_key: bytes) -> None:
    stored = encrypt_value(jar_key, "abc")
    assert stored.startswith("enc1.")
    assert decrypt_value(jar_key, stored) == "abc"


def test_encrypt_is_nondeterministic(jar_key: bytes) -> None:
    a = encrypt_value(jar_key, "same")
    b = encrypt_value(jar_key, "same")
    assert a != b


def test_decrypt_wrong_key_is_none(jar_key: bytes) -> None:
    wrong = bytes(reversed(jar_key))
    stored = encrypt_value(jar_key, "secret")
    assert decrypt_value(wrong, stored) is None


def test_decrypt_corrupt_is_none(jar_key: bytes) -> None:
    stored = encrypt_value(jar_key, "value")
    corrupted = stored[:-4] + ("AAAA" if not stored.endswith("AAAA") else "BBBB")
    assert decrypt_value(jar_key, corrupted) is None


def test_decrypt_truncated_is_none(jar_key: bytes) -> None:
    stored = encrypt_value(jar_key, "value")
    assert decrypt_value(jar_key, stored[:10]) is None


def test_is_encrypted_legacy_plaintext() -> None:
    assert is_encrypted("sk=abc") is False


def test_is_encrypted_envelope(jar_key: bytes) -> None:
    stored = encrypt_value(jar_key, "v")
    assert is_encrypted(stored) is True


# -- CookieJar wiring -----------------------------------------------------


def test_jar_encrypts_at_rest(tmp_path, jar_key: bytes, monkeypatch) -> None:
    monkeypatch.setenv("COMIC_DL_COOKIE_KEY", _env_key_env(jar_key))
    jar = CookieJar(tmp_path / "cookies.db", encryption="auto")
    jar.set("e-hentai.org", "sk", "plaintext-secret")
    jar.close()

    # Raw rows (byte-for-byte through plain sqlite3, never allowed to see a
    # CookieJar) must be encrypted envelopes, never the plaintext secret.
    with sqlite3.connect(tmp_path / "cookies.db") as conn:
        rows = conn.execute("SELECT name, value FROM cookies").fetchall()
    assert rows and rows[0][0] == "sk"
    assert is_encrypted(rows[0][1])
    assert "plaintext-secret" not in rows[0][1]


def test_jar_decrypts_on_read(tmp_path, jar_key: bytes, monkeypatch) -> None:
    monkeypatch.setenv("COMIC_DL_COOKIE_KEY", _env_key_env(jar_key))
    with CookieJar(tmp_path / "cookies.db", encryption="auto") as jar:
        jar.set("e-hentai.org", "sk", "secret-value")
    with CookieJar(tmp_path / "cookies.db", encryption="auto") as jar:
        got = jar.cookies_for("e-hentai.org")
    assert got == {"sk": "secret-value"}


def test_jar_reads_legacy_plaintext(tmp_path, jar_key: bytes, monkeypatch) -> None:
    monkeypatch.setenv("COMIC_DL_COOKIE_KEY", _env_key_env(jar_key))
    with CookieJar(tmp_path / "cookies.db", encryption="off") as jar:
        jar.set("e-hentai.org", "sk", "legacy-plain")

    with CookieJar(tmp_path / "cookies.db", encryption="auto") as jar:
        got = jar.cookies_for("e-hentai.org")
    assert got == {"sk": "legacy-plain"}


def test_jar_drops_corrupt_encrypted_row(tmp_path, jar_key: bytes, monkeypatch) -> None:
    monkeypatch.setenv("COMIC_DL_COOKIE_KEY", _env_key_env(jar_key))
    with CookieJar(tmp_path / "cookies.db", encryption="auto") as jar:
        jar.set("e-hentai.org", "sk", "good-value")

    # Corrupt that row's envelope directly (plain sqlite3, no CookieJar) so
    # decryption must fail on read.
    with sqlite3.connect(tmp_path / "cookies.db") as conn:
        conn.execute("UPDATE cookies SET value = 'enc1.garbage'")

    with CookieJar(tmp_path / "cookies.db", encryption="auto") as jar:
        got = jar.cookies_for("e-hentai.org")
    assert "sk" not in got


def test_jar_plaintext_mode_unchanged(tmp_path) -> None:
    with CookieJar(tmp_path / "cookies.db", encryption="off") as jar:
        jar.set("e-hentai.org", "sk", "plain")
    with CookieJar(tmp_path / "cookies.db", encryption="off") as jar:
        got = jar.cookies_for("e-hentai.org")
    assert got == {"sk": "plain"}


def test_warning_for_plaintext_env_missing(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("COMIC_DL_COOKIE_KEY", raising=False)
    # Force "auto" but block any real keyring: no key is found, so the jar
    # must degrade to plaintext and emit the once-per-run warning (to stderr).
    with (
        patch("comic_dl.cookiecrypt.keyring_available", return_value=False),
        CookieJar(tmp_path / "cookies.db", encryption="auto") as jar,
    ):
        jar.set("e-hentai.org", "sk", "v")
    err = capsys.readouterr().err
    assert "not encrypted" in err.lower() or "plaintext" in err.lower()
