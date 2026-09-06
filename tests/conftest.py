from __future__ import annotations

import pytest


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
    before = (ui_module.console.no_color, ui_module.err_console.no_color)
    config._RUNTIME_DOWNLOAD.clear()
    yield
    config.set_config_path(None)
    config.set_no_config(False)
    config._RUNTIME_HTTP.clear()
    config._RUNTIME_DOWNLOAD.clear()
    config._WARNED_BAD_CONFIG = False
    ui_module.set_json_mode(False)
    ui_module.console.no_color, ui_module.err_console.no_color = before
    cache.set_cache_dir(None)
