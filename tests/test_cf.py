"""Tests for cf.py challenge escalation (handle_challenge)."""

from __future__ import annotations

import asyncio

import pytest

from comic_dl import cf
from comic_dl import config as cfgmodule


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _clean_jar(monkeypatch):
    monkeypatch.setattr(cf, "get_jar", lambda: None)


class TestEscalationLadder:
    def test_off_mode_never_retries(self, monkeypatch):
        monkeypatch.setattr(
            cf, "solver_mode", lambda host=None: "off"
        )
        assert _run(cf.handle_challenge("https://kagane.to/x")) is False

    def test_impersonation_mode_stops_before_webview(self, monkeypatch):
        monkeypatch.setattr(
            cf, "solver_mode", lambda host=None: "impersonation"
        )
        opened = []

        class _FakeWebview:
            @staticmethod
            def available():
                return True

            @staticmethod
            async def solve_challenge(url):
                opened.append(url)
                return True

        monkeypatch.setitem(
            __import__("sys").modules, "comic_dl.webview", _FakeWebview
        )
        assert _run(cf.handle_challenge("https://kagane.to/x")) is True
        assert opened == []  # impersonation retry signal only; no webview

    def test_auto_mode_escalates_to_webview(self, monkeypatch):
        """auto must reach the webview rung — a cookie wipe alone never
        passes a managed challenge (regression: auto returned early)."""
        monkeypatch.setattr(cf, "solver_mode", lambda host=None: "auto")
        opened = []

        class _FakeWebview:
            @staticmethod
            def available():
                return True

            @staticmethod
            async def solve_challenge(url):
                opened.append(url)
                return True

        monkeypatch.setitem(
            __import__("sys").modules, "comic_dl.webview", _FakeWebview
        )
        assert _run(cf.handle_challenge("https://kagane.to/x")) is True
        assert opened == ["https://kagane.to/x"]

    def test_auto_falls_back_to_retry_when_webview_unavailable(
        self, monkeypatch
    ):
        monkeypatch.setattr(cf, "solver_mode", lambda host=None: "auto")

        class _FakeWebview:
            @staticmethod
            def available():
                return False

        monkeypatch.setitem(
            __import__("sys").modules, "comic_dl.webview", _FakeWebview
        )
        assert _run(cf.handle_challenge("https://kagane.to/x")) is True

    def test_auto_survives_solver_exception(self, monkeypatch):
        monkeypatch.setattr(cf, "solver_mode", lambda host=None: "webview")

        class _FakeWebview:
            @staticmethod
            def available():
                return True

            @staticmethod
            async def solve_challenge(url):
                raise RuntimeError("gtk exploded")

        monkeypatch.setitem(
            __import__("sys").modules, "comic_dl.webview", _FakeWebview
        )
        assert _run(cf.handle_challenge("https://kagane.to/x")) is True

    def test_webview_success_returns_true_without_impersonation_signal(
        self, monkeypatch
    ):
        monkeypatch.setattr(cf, "solver_mode", lambda host=None: "webview")

        class _FakeWebview:
            @staticmethod
            def available():
                return True

            @staticmethod
            async def solve_challenge(url):
                return True

        monkeypatch.setitem(
            __import__("sys").modules, "comic_dl.webview", _FakeWebview
        )
        assert _run(cf.handle_challenge("https://kagane.to/x")) is True


class TestSolverModePrecedence:
    """Pin the solver precedence of the *real* cf.solver_mode.

    Exercises the genuine resolution path (config file + runtime overrides),
    not a stubbed solver_mode. Documented precedence: CLI ``--solver`` (via
    the runtime HTTP override) > ``[sources."<host>"] mode`` > ``[http]
    solver`` > ``auto``.
    """

    def _config(self, monkeypatch, tmp_path, text):
        monkeypatch.setattr(
            cfgmodule, "config_path", lambda: tmp_path / "config.toml"
        )
        (tmp_path / "config.toml").write_text(text, encoding="utf-8")

    def test_defaults_to_auto(self, monkeypatch, tmp_path):
        self._config(monkeypatch, tmp_path, "")
        assert cf.solver_mode("example.com") == "auto"

    def test_global_http_setting_when_no_host_mode(self, monkeypatch, tmp_path):
        self._config(monkeypatch, tmp_path, '[http]\nsolver = "impersonation"\n')
        assert cf.solver_mode("example.com") == "impersonation"

    def test_per_host_mode_beats_global(self, monkeypatch, tmp_path):
        self._config(
            monkeypatch,
            tmp_path,
            '[sources."other.example"]\nmode = "webview"\n'
            '[http]\nsolver = "off"\n',
        )
        assert cf.solver_mode("other.example") == "webview"

    def test_runtime_solver_flag_beats_global(self, monkeypatch, tmp_path):
        self._config(monkeypatch, tmp_path, '[http]\nsolver = "webview"\n')
        cfgmodule.set_runtime_http(solver="off")
        try:
            assert cf.solver_mode("example.com") == "off"
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_cli_flag_beats_per_host_mode(self, monkeypatch, tmp_path):
        self._config(
            monkeypatch,
            tmp_path,
            '[sources."other.example"]\nmode = "webview"\n',
        )
        cfgmodule.set_runtime_http(solver="off")
        try:
            assert cf.solver_mode("other.example") == "off"
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_bogus_sources_mode_falls_through(self, monkeypatch, tmp_path):
        self._config(
            monkeypatch, tmp_path, '[sources."other.example"]\nmode = "turbo"\n'
        )
        assert cf.solver_mode("other.example") == "auto"
