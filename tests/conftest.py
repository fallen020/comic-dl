from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _stub_unresolvable_test_dns(monkeypatch):
    """Resolve fake test-only hosts to a public documentation IP.

    The suite is offline-safe and uses mock HTTP clients with fake
    domains (``*.example``, ``*.hath.network``, ``manhwaz.com``, ...).
    SSRF validation is fail-closed on DNS errors, so those hosts would
    otherwise be blocked before the mock client is reached. Stub only
    hosts that fail real resolution and look like test fixtures; real
    DNS failures (e.g. ``unresolvable.test``) still propagate so the
    fail-closed behavior stays covered.
    """
    real_getaddrinfo = socket.getaddrinfo
    fake_hosts = frozenset(
        {
            "x.com",
            "example.com",
            "www.site",
            "cdn.site",
            "manhwaz.com",
            "cdn.manhwaz.com",
            "www.webtoons.com",
        }
    )
    fake_suffixes = (".example", ".hath.network", ".invalid")
    test_suffixes = (".test",)
    passthrough_failures = frozenset({"unresolvable.test"})
    fake_result = [(2, 1, 6, "", ("93.184.216.34", 0))]

    def _fake_getaddrinfo(host, *args, **kwargs):
        name = host.lower().rstrip(".") if isinstance(host, str) else ""
        if name in passthrough_failures:
            return real_getaddrinfo(host, *args, **kwargs)
        if name in fake_hosts or name.endswith(fake_suffixes) or name.endswith(test_suffixes):
            return fake_result
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    yield


@pytest.fixture(autouse=True)
def _reset_cli_globals(tmp_path):
    """Point the scrape cache at a throwaway dir and reset process-wide
    UI/config state between tests.

    The cache is on by default and persists across runs, so without isolation
    network-mocking tests that reuse the same URL would serve each other's
    cached bodies (and pollute the real user cache dir). ``main()`` (and a few
    direct calls) set global state — JSON routing, forced no-color, a custom
    config path, runtime [http] overrides — that otherwise leaks across tests
    and reorders rendering/config assertions.
    """
    from comic_dl import cache, config, downloader, utils
    from comic_dl import ui as ui_module

    cache.set_cache_dir(tmp_path / "http-cache")
    downloader.reset_host_breaker()
    utils.clear_dns_cache()
    consoles = (ui_module.console, ui_module.err_console)
    before = [(c.no_color, c._force_terminal, c._color_system) for c in consoles]
    # Full "never" pin (not just no_color): macOS CI resolves the captured
    # pipe as a terminal, and bare no_color still leaves bold/underline
    # codes spliced inside asserted phrases. Tests asserting color override
    # explicitly via apply_color_mode.
    ui_module.set_no_color(True)
    config._RUNTIME_DOWNLOAD.clear()
    yield
    config.set_config_path(None)
    config.set_no_config(False)
    config._RUNTIME_HTTP.clear()
    config._RUNTIME_DOWNLOAD.clear()
    config._WARNED_BAD_CONFIG = False
    ui_module.set_json_mode(False)
    for c, (no_color, force_terminal, color_system) in zip(consoles, before, strict=True):
        c.no_color = no_color
        c._force_terminal = force_terminal
        c._color_system = color_system
    cache.set_cache_dir(None)
