"""Rate limiter contract: hostname-keyed, case-insensitive, no throttle for unknown hosts."""

from __future__ import annotations

import comic_dl.config as cfgmodule
from comic_dl.rate import RateLimiter


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(cfgmodule, "config_path", lambda: tmp_path / "config.toml")


class TestLimitFor:
    def test_known_host_returns_rate(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        limiter = RateLimiter(rates={"example.com": 2.0})
        assert limiter.limit_for("example.com") == 2.0

    def test_case_insensitive(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        limiter = RateLimiter(rates={"Example.COM": 2.0})
        assert limiter.limit_for("example.com") == 2.0

    def test_full_url_does_not_match(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        limiter = RateLimiter(rates={"example.com": 2.0})
        assert limiter.limit_for("https://example.com/page/1") is None

    def test_unknown_host_unlimited(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        limiter = RateLimiter(rates={"example.com": 2.0})
        assert limiter.limit_for("other.test") is None


class TestAcquireKeysByHost:
    async def test_reserves_hostname_slot(self, monkeypatch, tmp_path):
        _isolated(monkeypatch, tmp_path)
        limiter = RateLimiter(rates={"example.com": 1000.0})
        await limiter.acquire("example.com")
        assert "example.com" in limiter._next_available
        assert limiter._next_available.get("https://example.com/page") is None


class TestAcquireConcurrency:
    async def test_concurrent_acquires_keep_slot_spacing(self, monkeypatch, tmp_path):
        """Racers must queue onto successive slots, not share one."""
        import asyncio
        import time
        from itertools import pairwise

        _isolated(monkeypatch, tmp_path)
        limit = 10.0
        limiter = RateLimiter(rates={"example.com": limit})
        stamps: list[float] = []

        async def worker() -> None:
            await limiter.acquire("example.com")
            stamps.append(time.monotonic())

        await asyncio.gather(*(worker() for _ in range(8)))
        stamps.sort()
        gaps = [b - a for a, b in pairwise(stamps)]
        assert min(gaps) >= (1.0 / limit) * 0.9
