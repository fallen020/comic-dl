from __future__ import annotations

import contextlib
from unittest.mock import Mock

import pytest

import comic_dl.config as cfgmodule
import comic_dl.http as httpmodule
import comic_dl.rate as ratemodule
from comic_dl.cf import looks_like_challenge, solver_mode
from comic_dl.cookies import CookieJar
from comic_dl.utils import http_client_args


def _patch_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(cfgmodule, "config_path", lambda: tmp_path / "config.toml")


class TestRuntimeHttpOverrides:
    def test_defaults(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        assert httpmodule.cookie_jar_enabled() is True
        assert solver_mode() == "auto"
        assert ratemodule.rate_limiting_enabled() is True
        assert http_client_args()["impersonate"] == "chrome146"

    def test_config_values_apply(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            '[http]\nsolver = "webview"\ncookie-jar = false\n'
            'rate-enabled = false\nimpersonate = "chrome131"\n'
        )
        assert httpmodule.cookie_jar_enabled() is False
        assert solver_mode() == "webview"
        assert ratemodule.rate_limiting_enabled() is False
        assert http_client_args()["impersonate"] == "chrome131"

    def test_runtime_override_beats_config(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            '[http]\nsolver = "off"\ncookie-jar = true\nrate-enabled = true\n'
        )
        cfgmodule.set_runtime_http(**{"solver": "auto"})
        cfgmodule.set_runtime_http(**{"cookie-jar": False, "rate-enabled": False})
        try:
            assert solver_mode() == "auto"
            assert httpmodule.cookie_jar_enabled() is False
            assert ratemodule.rate_limiting_enabled() is False
        finally:
            cfgmodule.set_runtime_http(**{"solver": "auto", "cookie-jar": None})
            cfgmodule._RUNTIME_HTTP.clear()


class TestCookieJarList:
    def test_list_and_clear(self, tmp_path):
        jar = CookieJar(tmp_path / "cookies.db")
        jar.set("e-hentai.org", "sk", "v1")
        jar.set("kagane.to", "sk", "v2")
        rows = jar.list()
        assert {r["name"] for r in rows} == {"sk"}
        assert {r["host"] for r in rows} == {"e-hentai.org", "kagane.to"}

        rows = jar.list("e-hentai.org")
        assert len(rows) == 1
        assert rows[0]["host"] == "e-hentai.org"

        jar.clear("e-hentai.org")
        assert len(jar.list()) == 1
        jar.clear()
        assert len(jar.list()) == 0

    def test_store_created_owner_only(self, tmp_path):
        import stat
        db = tmp_path / "cookies.db"
        jar = CookieJar(db)
        jar.set("kagane.to", "cf_clearance", "secret")
        # DB and WAL sidecars must be user-readable only.
        mode = stat.S_IMODE(db.stat().st_mode)
        assert mode == 0o600

    def test_restrict_perms_repairs_loose_file(self, tmp_path):
        import os
        import stat
        db = tmp_path / "cookies.db"
        db.write_bytes(b"")  # pre-create with default umask perms
        os.chmod(db, 0o644)
        jar = CookieJar(db)
        jar.set("kagane.to", "sk", "v")
        mode = stat.S_IMODE(db.stat().st_mode)
        assert mode == 0o600


class TestCookieShortCircuit:
    """The hot-path fast-lanes: avoid jar work unless a response changes it."""

    def test_absorb_skips_jar_without_set_cookie(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            httpmodule, "get_jar", lambda: (called.append(1), object())[1]
        )
        client = Mock()
        client.cookies.jar = object()
        httpmodule.absorb_response_cookies(client, {"Content-Type": "text/html"})
        # No Set-Cookie, so no jar read path (and no SQLite round-trip) at all.
        assert called == []

    def test_absorb_persists_on_set_cookie(self, monkeypatch, tmp_path):
        jar = CookieJar(tmp_path / "cookies.db")
        monkeypatch.setattr(httpmodule, "get_jar", lambda: jar)
        spy = Mock()
        monkeypatch.setattr(CookieJar, "store_cookiejar", spy)
        client = Mock()
        client.cookies.jar = Mock()
        httpmodule.absorb_response_cookies(client, {"set-cookie": "sk=v"})
        spy.assert_called_once_with(client.cookies.jar)

    def test_jar_kwargs_empty_when_disabled(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        cfgmodule.set_runtime_http(**{"cookie-jar": False})
        try:
            assert httpmodule.jar_cookies_kwargs("https://kagane.to/x") == {}
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_jar_kwargs_carries_host_cookies(self, monkeypatch, tmp_path):
        jar = CookieJar(tmp_path / "cookies.db")
        jar.set("kagane.to", "sk", "v1")
        monkeypatch.setattr(httpmodule, "get_jar", lambda: jar)
        assert httpmodule.jar_cookies_kwargs("https://kagane.to/a") == {
            "cookies": {"sk": "v1"}
        }
        assert httpmodule.jar_cookies_kwargs("http://other.test/") == {}


class TestChallengeDetection:
    def test_cloudflare_server_header(self):
        assert looks_like_challenge(403, {"Server": "cloudflare"})
        assert looks_like_challenge(503, {"server": "Cloudflare-nginx"})

    def test_cf_mitigated_header(self):
        assert looks_like_challenge(403, {"Cf-Mitigated": "challenge"})

    def test_body_marker(self):
        assert looks_like_challenge(
            403, {}, "something challenge-error-text something"
        )

    def test_plain_error_not_challenge(self):
        assert not looks_like_challenge(403, {"Server": "nginx"})
        assert not looks_like_challenge(500, {"Server": "cloudflare"})
        assert not looks_like_challenge(200, {"Server": "cloudflare"})


class TestRateLimiterDisable:
    def test_rate_disabled_means_unlimited(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        cfgmodule.set_runtime_http(**{"rate-enabled": False})
        try:
            limiter = ratemodule.RateLimiter({"kagane.to": 1.5})
            assert limiter.limit_for("kagane.to") is None
            assert limiter.limit_for("example.com") is None
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_rate_enabled_keeps_defaults(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        limiter = ratemodule.RateLimiter({})
        assert limiter.limit_for("kagane.to") == 1.5
        assert limiter.limit_for("example.com") is None


class TestRateLimiterSourceOverride:
    def test_sources_rate_beats_defaults_and_map(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            '[http]\nrate = { "kagane.to" = 1.5 }\n'
            '[sources."kagane.to"]\nrate = 0.8\n'
            '[sources."newhost.example"]\nrate = 3.0\n',
            encoding="utf-8",
        )
        limiter = ratemodule.RateLimiter({})
        assert limiter.limit_for("kagane.to") == 0.8
        assert limiter.limit_for("newhost.example") == 3.0
        assert limiter.limit_for("kstatic.to") == 2.0
        assert limiter.limit_for("example.com") is None


class TestRateLimiterPacing:
    async def _pacing(self, monkeypatch, tmp_path, rate, n):
        import asyncio
        from unittest.mock import patch

        from comic_dl import rate as ratemodule

        _patch_paths(monkeypatch, tmp_path)
        limiter = ratemodule.RateLimiter({})
        waits = []
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds):
            waits.append(seconds)
            await real_sleep(0)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            for _ in range(n):
                await limiter.acquire("kagane.to", rate=rate)
        return waits

    def test_fractional_rate_spaces_requests(self, monkeypatch, tmp_path):
        # 1.5 req/s -> first request is immediate, then each request is paced
        # ~0.67s behind the previous slot (1/1.5s), never a back-to-back burst.
        import asyncio

        waits = asyncio.run(self._pacing(monkeypatch, tmp_path, 1.5, 3))
        assert len(waits) == 2
        assert 0.5 < waits[0] <= 0.75
        assert 1.0 < waits[1] <= 1.5

    def test_integer_rate_paces_evenly(self, monkeypatch, tmp_path):
        import asyncio

        waits = asyncio.run(self._pacing(monkeypatch, tmp_path, 2.0, 3))
        assert len(waits) == 2
        assert 0.4 < waits[0] <= 0.6
        assert 0.8 < waits[1] <= 1.2


class TestRunCookie:
    def _run(self, argv, tmp_path, monkeypatch, tty=False):
        from unittest.mock import patch

        from comic_dl.cli import _run_cookie

        monkeypatch.setattr("comic_dl.cli._is_interactive_output", lambda: tty)
        with patch("comic_dl.cookies.config_dir") as cd:
            cd.return_value = tmp_path
            return _run_cookie(argv)

    def _out(self, capsys) -> str:
        captured = capsys.readouterr()
        return captured.out + captured.err

    def test_cookie_ls_empty(self, capsys, tmp_path, monkeypatch):
        assert self._run(["ls"], tmp_path, monkeypatch) == 0
        assert "No cookies stored" in self._out(capsys)

    def test_cookie_ls_and_clear(self, capsys, tmp_path, monkeypatch):
        from unittest.mock import patch

        from comic_dl.cookies import CookieJar

        with patch("comic_dl.cookies.config_dir") as cd:
            cd.return_value = tmp_path
            jar = CookieJar()
            jar.set("e-hentai.org", "sk", "v1")
            jar.set("kagane.to", "sk", "v2")

        assert self._run(["ls"], tmp_path, monkeypatch) == 0
        out = self._out(capsys)
        assert "e-hentai.org" in out
        assert "kagane.to" in out

        assert self._run(["clear", "-y"], tmp_path, monkeypatch) == 0
        assert "Cleared cookies" in self._out(capsys)
        assert self._run(["ls"], tmp_path, monkeypatch) == 0
        assert "No cookies stored" in self._out(capsys)

    def test_cookie_clear_requires_confirmation_noninteractive(
        self, capsys, tmp_path, monkeypatch
    ):
        """Non-TTY `cookie clear` without -y refuses (exit 130) and clears
        nothing, instead of silently wiping the jar."""
        from unittest.mock import patch

        from comic_dl.cookies import CookieJar

        with patch("comic_dl.cookies.config_dir") as cd:
            cd.return_value = tmp_path
            jar = CookieJar()
            jar.set("e-hentai.org", "sk", "v1")

        assert self._run(["clear"], tmp_path, monkeypatch, tty=False) == 130
        out = self._out(capsys)
        assert "requires confirmation" in out
        assert "-y" in out
        with patch("comic_dl.cookies.config_dir") as cd:
            cd.return_value = tmp_path
            assert len(CookieJar().list()) == 1

    def test_cookie_clear_single_host(self, capsys, tmp_path, monkeypatch):
        from unittest.mock import patch

        from comic_dl.cookies import CookieJar

        with patch("comic_dl.cookies.config_dir") as cd:
            cd.return_value = tmp_path
            jar = CookieJar()
            jar.set("e-hentai.org", "sk", "v1")
            jar.set("kagane.to", "sk", "v2")

        assert self._run(["clear", "-y", "e-hentai.org"], tmp_path, monkeypatch) == 0
        assert self._run(["ls"], tmp_path, monkeypatch) == 0
        out = self._out(capsys)
        lines = [
            ln
            for ln in out.splitlines()
            if "cookie(s) stored" in ln
            or ln.strip().startswith("kagane")
            or ln.strip().startswith("e-hentai")
        ]
        listing = "\n".join(lines)
        assert "e-hentai.org  sk" not in listing
        assert "kagane.to  sk" in listing

    def test_cookie_ls_json(self, capsys, tmp_path, monkeypatch):
        import json as _json
        from unittest.mock import patch

        from comic_dl.cookies import CookieJar

        with patch("comic_dl.cookies.config_dir") as cd:
            cd.return_value = tmp_path
            jar = CookieJar()
            jar.set("e-hentai.org", "sk", "v1")

        assert self._run(["ls", "--json"], tmp_path, monkeypatch) == 0
        out = self._out(capsys)
        payload = _json.loads(out)
        assert payload["schema_version"] == 1
        assert any(
            c["host"] == "e-hentai.org" and c["name"] == "sk"
            for c in payload["cookies"]
        )

    def test_cookie_set_rejects_bad_host(self, capsys, tmp_path, monkeypatch):
        assert self._run(
            ["set", "https://e-hentai.org/path", "sk", "v1"],
            tmp_path, monkeypatch,
        ) == 2
        assert "Invalid cookie host" in self._out(capsys)

        assert self._run(
            ["set", "example.com:8080", "sk", "v1"],
            tmp_path, monkeypatch,
        ) == 2

        assert self._run(
            ["set", "ok.example.com", "sk", "v1"],
            tmp_path, monkeypatch,
        ) == 0
        assert "Stored cookie" in self._out(capsys)

    def test_cookie_ls_host_filter_json(self, capsys, tmp_path, monkeypatch):
        from unittest.mock import patch

        from comic_dl.cookies import CookieJar

        with patch("comic_dl.cookies.config_dir") as cd:
            cd.return_value = tmp_path
            jar = CookieJar()
            jar.set("e-hentai.org", "sk", "v1")
            jar.set("kagane.to", "sk", "v2")

        assert self._run(["ls", "--json", "kagane.to"], tmp_path, monkeypatch) == 0
        out = self._out(capsys)
        assert '"kagane.to"' in out
        assert '"e-hentai.org"' not in out


class TestLowSpeedWatchdog:
    """Dead-peer abort is explicit and post-connect only (C)."""

    def test_curl_options_carry_low_speed(self):
        from curl_cffi.const import CurlOpt

        opts = http_client_args()["curl_options"]
        assert opts[CurlOpt.LOW_SPEED_LIMIT] == 1
        assert opts[CurlOpt.LOW_SPEED_TIME] == 20

    def test_curl_options_dict_is_per_call(self):
        from curl_cffi.const import CurlOpt

        a = http_client_args()
        b = http_client_args()
        assert a["curl_options"] is not b["curl_options"]
        a["curl_options"][CurlOpt.LOW_SPEED_LIMIT] = 99  # type: ignore[index]
        assert b["curl_options"][CurlOpt.LOW_SPEED_LIMIT] != 99  # type: ignore[index]


class TestAsyncDnsValidation:
    """Resolver latency must not freeze the event loop (B)."""

    pytestmark = pytest.mark.asyncio

    def setup_method(self):
        from comic_dl.utils import clear_dns_cache

        clear_dns_cache()

    def teardown_method(self):
        from comic_dl.utils import clear_dns_cache

        clear_dns_cache()

    async def test_slow_resolver_does_not_block_loop(self, monkeypatch):
        import asyncio
        import socket as socket_module
        import time

        from comic_dl.utils import validate_request_url_async

        def slow_getaddrinfo(host, *args, **kwargs):
            time.sleep(0.3)  # real blocking, like a stalled resolver
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(socket_module, "getaddrinfo", slow_getaddrinfo)

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        t = asyncio.create_task(ticker())
        try:
            await asyncio.wait_for(
                validate_request_url_async("https://slow-dns.test/page"), timeout=5
            )
        finally:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        # A blocked loop would yield ~0 ticks during the 300 ms resolution.
        assert ticks >= 10

    async def test_verdict_cached_within_ttl(self, monkeypatch):
        import socket as socket_module

        import comic_dl.utils as u

        calls = []

        def counting(host, *args, **kwargs):
            calls.append(host)
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(socket_module, "getaddrinfo", counting)
        await u.validate_request_url_async("https://cached.test/a")
        await u.validate_request_url_async("https://cached.test/b")
        assert len(calls) == 1

    async def test_literal_private_ip_needs_no_resolver(self, monkeypatch):
        import socket as socket_module

        import comic_dl.utils as u
        from comic_dl.utils import RequestBlockedError

        def boom(*args, **kwargs):
            raise AssertionError("resolver must not be consulted for literals")

        monkeypatch.setattr(socket_module, "getaddrinfo", boom)
        with pytest.raises(RequestBlockedError):
            await u.validate_request_url_async("http://127.0.0.1/x")

    async def test_private_resolution_blocked_async(self, monkeypatch):
        import socket as socket_module

        import comic_dl.utils as u
        from comic_dl.utils import RequestBlockedError

        monkeypatch.setattr(
            socket_module,
            "getaddrinfo",
            lambda h, *a, **k: [(2, 1, 6, "", ("10.0.0.5", 0))],
        )
        with pytest.raises(RequestBlockedError):
            await u.validate_request_url_async("https://evil.test/x")
