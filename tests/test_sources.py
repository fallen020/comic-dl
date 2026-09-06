from __future__ import annotations

from typing import ClassVar

from comic_dl.scrapers import (
    get_source_for_url,
    list_sources,
    register,
    url_in_domain,
)


class _ChapterSource:
    domain = "example.org"
    name = "ExampleSource"
    version = "0.1.0"
    capabilities: ClassVar[set[str]] = {"chapter"}


class TestUrlInDomain:
    def test_exact_host(self):
        assert url_in_domain("https://example.org/x", "example.org")

    def test_subdomain(self):
        assert url_in_domain("https://a.example.org/x", "example.org")

    def test_www(self):
        assert url_in_domain("https://www.example.org/x", "example.org")

    def test_other_host(self):
        assert not url_in_domain("https://example.net/x", "example.org")

    def test_dotted_prefix_not_subdomain(self):
        assert not url_in_domain("https://notexample.org/x", "example.org")


class TestRegistration:
    def test_list_sources_includes_registered(self):
        entry = register(
            _ChapterSource(),
            domain="example.org",
            capabilities={"chapter"},
            name="ExampleSource",
            version="0.1.0",
            builtin=True,
        )
        assert entry.has_chapter
        assert not entry.has_series
        assert "example.org" in {e.domain for e in list_sources()}

    def test_duplicate_lower_priority_kept_first(self):
        register(
            _ChapterSource(),
            domain="dup.org",
            capabilities={"chapter"},
            name="A",
            version="1",
            builtin=True,
            priority=0,
        )
        first = get_source_for_url("https://dup.org/x").instance
        register(
            _ChapterSource(),
            domain="dup.org",
            capabilities={"chapter"},
            name="B",
            version="1",
            builtin=True,
            priority=0,
        )
        assert "dup.org" in {e.domain for e in list_sources()}
        assert get_source_for_url("https://dup.org/x").instance is first

    def test_higher_priority_overrides(self):
        orig = get_source_for_url("https://dup.org/y")
        register(
            _ChapterSource(),
            domain="dup.org",
            capabilities={"chapter"},
            name="C",
            version="9",
            builtin=True,
            priority=5,
        )
        new = get_source_for_url("https://dup.org/y")
        assert new is not orig
        assert new.name == "C"


class TestBuiltinRegistration:
    def test_builtin_sources_registered_with_names(self):
        import comic_dl.scrapers  # noqa: F401  (triggers built-in registration)

        by_domain = {e.domain: e for e in list_sources()}
        for domain, name in {
            "pawchive.pw": "pawchive",
            "e-hentai.org": "e-hentai",
            "webtoons.com": "webtoons",
            "flamecomics.xyz": "flamecomics",
            "fsicomics.com": "fsicomics",
            "mangadex.org": "mangadex",
            "kodokustudio.com": "kodokustudio",
            "toonily.com": "toonily",
            "manhwaz.com": "manhwaz",
        }.items():
            assert domain in by_domain, f"{domain} not registered"
            assert by_domain[domain].name == name
            assert by_domain[domain].builtin is True

    def test_series_capable(self):
        import comic_dl.scrapers  # noqa: F401

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain["webtoons.com"].has_series
        assert by_domain["flamecomics.xyz"].has_series
        assert by_domain["mangadex.org"].has_series
        assert by_domain["toonily.com"].has_series
        assert by_domain["manhwaz.com"].has_series
        assert not by_domain["pawchive.pw"].has_series


class TestResolution:
    def test_get_source_for_url_matches_host(self):
        register(
            _ChapterSource(),
            domain="resolve.org",
            capabilities={"chapter"},
            name="R",
            version="1",
            builtin=True,
        )
        entry = get_source_for_url("https://resolve.org/a")
        assert entry is not None
        assert entry.domain == "resolve.org"

    def test_get_source_for_url_none_when_no_match(self):
        assert get_source_for_url("https://nothing.matches/x") is None


class TestRunListSources:
    def _run(self, argv, monkeypatch=None, tty=False):
        import asyncio

        import comic_dl.scrapers  # noqa: F401
        from comic_dl.cli import _run_list_sources

        if monkeypatch is not None:
            monkeypatch.setattr("comic_dl.cli._is_interactive_output", lambda: tty)
        return asyncio.run(_run_list_sources(argv))

    def test_lists_builtin_sources(self, capsys):
        assert self._run([]) == 0
        out = capsys.readouterr().out + capsys.readouterr().err
        assert "e-hentai.org" in out
        assert "flamecomics.xyz" in out
        assert "Supported sources" in out

    def test_table_has_columns(self, capsys):
        self._run([])
        out = capsys.readouterr().out
        assert "SOURCE" in out
        assert "ORIGIN" in out
        assert "e-hentai.org" in out
        assert "webtoons.com" in out
        assert "built-in" in out
        assert "CAPABILITIES" not in out
        assert "series" not in out

    def test_query_filters_table(self, capsys):
        self._run(["webtoons"])
        out = capsys.readouterr().out
        assert "webtoons.com" in out
        assert "e-hentai.org" not in out

    def test_query_matches_name(self, capsys):
        self._run(["hentai"])
        out = capsys.readouterr().out
        assert "e-hentai.org" in out

    def test_json_output(self, capsys):
        import json

        self._run(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        entries = payload["sources"]
        assert entries, "expected at least one source"
        assert all(set(e) == {"domain", "origin"} for e in entries)
        domains = {e["domain"] for e in entries}
        assert "e-hentai.org" in domains
        assert "webtoons.com" in domains

    def test_json_respects_query(self, capsys):
        import json

        self._run(["--json", "webtoons"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert [e["domain"] for e in payload["sources"]] == ["webtoons.com"]

    def test_plugin_flag_shows_only_third_party(self, capsys):
        register(
            _ChapterSource(),
            domain="plugin.example",
            capabilities={"series", "chapter"},
            name="TestPlugin",
            version="2.0",
            builtin=False,
            priority=3,
        )
        self._run(["--plugin"])
        out = capsys.readouterr().out
        assert "plugin.example" in out
        assert "e-hentai.org" not in out

    def test_piped_routes_to_table(self, monkeypatch, capsys):
        self._run([], monkeypatch=monkeypatch, tty=False)
        out = capsys.readouterr().out
        assert "Supported sources" in out
        assert "SOURCE" in out

    def test_tty_routes_to_search(self, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr(
            "comic_dl.cli.source_search",
            lambda rows: calls.append(rows) or 0,
        )
        self._run([], monkeypatch=monkeypatch, tty=True)
        assert calls, "expected the interactive search view on a TTY"
        assert any(r.domain == "webtoons.com" for r in calls[0])

    def test_tty_with_no_rows_falls_back_to_table(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "comic_dl.cli.source_search",
            lambda rows: (_ for _ in ()).throw(AssertionError("must not search")),
        )
        self._run(["--plugin", "nonexistent-match"], monkeypatch=monkeypatch, tty=True)
        out = capsys.readouterr().out
        assert "Supported sources (0)" in out


class _PluginSource:
    domain = "plugin.example"
    name = "TestPlugin"
    version = "2.0"
    capabilities: ClassVar[set[str]] = {"chapter", "series"}
    priority = 3

    def __init__(self):
        pass

    def matches_url(self, url: str) -> bool:
        return "plugin.example" in url


class TestEntryPointDiscovery:
    def test_load_plugins_registers_and_overrides(self, monkeypatch):
        from dataclasses import dataclass

        from comic_dl.scrapers import registry

        @dataclass
        class _Ep:
            def load(self):
                return _PluginSource

        monkeypatch.setattr(registry, "entry_points", lambda: type(
            "EPs", (), {"select": lambda self, group: [_Ep()]}
        )())

        before = registry._loaded_plugins.copy()
        registry._loaded_plugins.clear()
        try:
            found = registry.load_plugins()
            names = {e.domain for e in found}
            assert "plugin.example" in names

            entry = registry.get_entry("plugin.example")
            assert entry is not None
            assert entry.builtin is False
            assert entry.name == "TestPlugin"
            assert entry.has_series

            src = registry.get_source_for_url("https://plugin.example/g/1")
            assert src is not None
            assert src.domain == "plugin.example"
        finally:
            registry._loaded_plugins.clear()
            registry._loaded_plugins.update(before)
