"""Tests for search-engine referrer pool and humane request layer."""

from __future__ import annotations

from urllib.parse import urlsplit

import comic_dl.config as cfgmodule
from comic_dl.downloader import _humane_backoff_delay
from comic_dl.utils import (
    _SEARCH_ORIGINS,
    _search_referer_for_host,
    http_client_args,
    search_referer,
)


def _patch_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(cfgmodule, "config_path", lambda: tmp_path / "config.toml")


class TestSearchReferer:
    """Tests for search-engine referrer helpers."""

    def test_search_origins_has_5_engines(self):
        assert len(_SEARCH_ORIGINS) == 5

    def test_search_origins_are_valid_urls(self):
        for origin in _SEARCH_ORIGINS:
            parsed = urlsplit(origin)
            assert parsed.scheme in ("http", "https")
            assert parsed.netloc

    def test_search_referer_for_host_returns_string(self):
        result = _search_referer_for_host("example.com")
        assert isinstance(result, str)
        assert result in _SEARCH_ORIGINS

    def test_search_referer_deterministic_per_host(self):
        r1 = _search_referer_for_host("kagane.to")
        r2 = _search_referer_for_host("kagane.to")
        assert r1 == r2

    def test_search_referer_different_hosts_distribute(self):
        results = set()
        for i in range(100):
            host = f"host-{i}.example.com"
            results.add(_search_referer_for_host(host))
        # With 5 origins and 100 hosts, we should hit at least 3 different ones
        assert len(results) >= 3

    def test_search_referer_empty_host_returns_first(self):
        result = _search_referer_for_host("")
        assert result == _SEARCH_ORIGINS[0]

    def test_search_referer_public_helper(self):
        result = search_referer("mangadex.org")
        assert result in _SEARCH_ORIGINS

    def test_search_referer_none_host_returns_default(self):
        result = search_referer(None)
        assert result == _SEARCH_ORIGINS[0]


class TestHttpClientArgsReferrer:
    """Tests for http_client_args with host parameter."""

    def test_default_no_referer(self):
        args = http_client_args()
        assert "Referer" not in args.get("headers", {})

    def test_explicit_referer_wins(self):
        args = http_client_args(referer_url="https://example.com/page")
        assert args["headers"]["Referer"] == "https://example.com/page"

    def test_host_adds_search_referrer(self):
        args = http_client_args(host="kagane.to")
        assert "Referer" in args["headers"]
        assert args["headers"]["Referer"] in _SEARCH_ORIGINS

    def test_explicit_referer_beats_host(self):
        args = http_client_args(
            referer_url="https://example.com/page",
            host="kagane.to",
        )
        assert args["headers"]["Referer"] == "https://example.com/page"

    def test_host_referrer_is_deterministic(self):
        args1 = http_client_args(host="kagane.to")
        args2 = http_client_args(host="kagane.to")
        assert args1["headers"]["Referer"] == args2["headers"]["Referer"]

    def test_origin_derived_from_referer(self):
        args = http_client_args(referer_url="https://example.com/page")
        assert args["headers"]["Origin"] == "https://example.com"


class TestHumaneBackoffDelay:
    """Tests for _humane_backoff_delay()."""

    def test_first_attempt_base_delay(self):
        delay = _humane_backoff_delay(0)
        # Base is 1.0, with ±20% jitter, should be in [0.8, 1.2]
        assert 0.8 <= delay <= 1.2

    def test_second_attempt_doubles(self):
        delays = [_humane_backoff_delay(1) for _ in range(100)]
        avg = sum(delays) / len(delays)
        # Base is 2.0, with ±20% jitter, avg should be ~2.0
        assert 1.8 <= avg <= 2.2

    def test_third_attempt_caps_at_max(self):
        delays = [_humane_backoff_delay(2) for _ in range(100)]
        avg = sum(delays) / len(delays)
        # Base is 4.0 (capped), with ±20% jitter, avg should be ~4.0
        assert 3.8 <= avg <= 4.2

    def test_higher_attempts_stay_capped(self):
        delay_10 = _humane_backoff_delay(10)
        delay_100 = _humane_backoff_delay(100)
        # Both should be capped at ~4.0 (with jitter)
        assert delay_10 < 5.0
        assert delay_100 < 5.0
