from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _stub_unresolvable_test_dns(monkeypatch):
    """Resolve every hostname to a public documentation IP — no real DNS.

    The suite is offline-safe and uses mock HTTP clients, but SSRF
    validation resolves every hostname it checks. Delegating unknown hosts
    to the real resolver stalls restricted networks for seconds per test
    (the verdict cache is cleared between tests, so nothing is reused).
    Real resolution is never load-bearing here: IP literals bypass the
    resolver, and every test that asserts resolver behavior
    (slow/failing/hostile resolution) patches ``getaddrinfo`` itself.

    Two names keep real resolution: ``localhost`` (hosts-file fast, and the
    SSRF tests require it to stay blocked) and ``unresolvable.test``
    (fail-closed intent; nothing in the suite queries it today).
    """
    real_getaddrinfo = socket.getaddrinfo
    real_names = frozenset({"localhost", "unresolvable.test"})
    fake_result = [(2, 1, 6, "", ("93.184.216.34", 0))]

    def _fake_getaddrinfo(host, *args, **kwargs):
        name = host.lower().rstrip(".") if isinstance(host, str) else ""
        if name in real_names:
            return real_getaddrinfo(host, *args, **kwargs)
        return fake_result

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    yield


@pytest.fixture(autouse=True)
def _assume_online(monkeypatch):
    """Pin the connectivity pre-flight to "online" so the suite stays offline.

    ``_run_urls`` probes the network before scraping. Left real, every CLI
    test would open a socket to 1.1.1.1 and hit pypi.org, which is both slow
    and non-deterministic on CI. Tests that exercise the offline branch patch
    ``comic_dl.cli.check_connectivity`` themselves.
    """

    async def _online(*, force: bool = False) -> bool:
        return True

    monkeypatch.setattr("comic_dl.cli.check_connectivity", _online)


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
    from comic_dl import cache, config, cookies, downloader, http, rate, utils
    from comic_dl import ui as ui_module

    cache.set_cache_dir(tmp_path / "http-cache")
    config.set_config_dir(tmp_path / "config-dir")
    config.set_data_dir(tmp_path / "data-dir")
    downloader.reset_host_breaker()
    rate._limiter = None
    utils.clear_dns_cache()
    consoles = (ui_module.console, ui_module.err_console)
    before = [(c.no_color, c._force_terminal, c._color_system) for c in consoles]
    # Full "never" pin (not just no_color): macOS CI resolves the captured
    # pipe as a terminal, and bare no_color still leaves bold/underline
    # codes spliced inside asserted phrases. Tests asserting color override
    # explicitly via apply_color_mode.
    ui_module.set_no_color(True)
    ui_module.set_verbosity(0)
    config._RUNTIME_DOWNLOAD.clear()
    cookies._warned_plaintext = False
    yield
    ui_module.set_verbosity(0)
    ui_module.set_debug_file(None)
    config.set_config_path(None)
    config.set_no_config(False)
    config.set_config_dir(None)
    config.set_data_dir(None)
    config._RUNTIME_HTTP.clear()
    config._RUNTIME_DOWNLOAD.clear()
    config._WARNED_BAD_CONFIG = False
    ui_module.set_json_mode(False)
    for c, (no_color, force_terminal, color_system) in zip(consoles, before, strict=True):
        c.no_color = no_color
        c._force_terminal = force_terminal
        c._color_system = color_system
    cache.set_cache_dir(None)
    # Without the reset, the jar outlives the test (frozen tmp path):
    # leaked sqlite conn plus cookies bleeding into later tests.
    if http._JAR is not None:
        http._JAR.close()
        http._JAR = None
