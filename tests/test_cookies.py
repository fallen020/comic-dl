"""Cookie jar round-trip (offline, encryption off)."""

from __future__ import annotations

import time

from comic_dl.cookies import CookieJar


class TestCookieJar:
    def test_set_and_get_round_trip(self, tmp_path):
        jar = CookieJar(path=tmp_path / "cookies.db", encryption="off")
        jar.set("example.com", "session", "abc123")
        assert jar.cookies_for("example.com") == {"session": "abc123"}

    def test_expired_set_deletes(self, tmp_path):
        jar = CookieJar(path=tmp_path / "cookies.db", encryption="off")
        jar.set("example.com", "gone", "x", expires=int(time.time()) - 10)
        assert jar.cookies_for("example.com") == {}

    def test_public_suffix_refused(self, tmp_path):
        jar = CookieJar(path=tmp_path / "cookies.db", encryption="off")
        jar.set("github.io", "evil", "1")
        assert jar.cookies_for("github.io") == {}
