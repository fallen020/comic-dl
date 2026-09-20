"""Offline tests for ``comic-dl self site`` (per-site support versioning).

Manifest fetches, live checks, and core-update delegation are all mocked;
no network, release asset, or package manager is ever touched.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from comic_dl import __version__ as _CORE_VERSION
from comic_dl.errors import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    SiteRegistryError,
)
from comic_dl.scrapers.registry import register_builtin, register_scraper
from comic_dl.site_update import (
    LiveResult,
    SiteManifest,
    SiteRelease,
    local_sites,
    run_site_check_command,
    run_site_list_command,
    run_site_update_command,
    site_update_hint,
    status_for,
)


def _text(capsys) -> str:
    cap = capsys.readouterr()
    return (cap.out + cap.err).replace("\n", "")


def _bump_patch(v: str) -> str:
    parts = [int(p) for p in v.split(".")]
    parts[-1] += 1
    return ".".join(str(p) for p in parts)


# A mocked future manifest must outrank the installed core version.
_FUTURE = _bump_patch(_CORE_VERSION)


# -------------------------------------------------------------------------
# Construction fixtures

class _Scraper:
    """Bare scraper stand-in: the registry only touches declared attrs."""

    def __init__(self, **attrs):
        for key, value in attrs.items():
            setattr(self, key, value)


def _manifest(sites: dict[str, tuple[str, str]], core: str = "0.0.3") -> SiteManifest:
    return SiteManifest(
        schema_version=1,
        core_version=core,
        sites={
            sid: SiteRelease(version=v, minimum_core_version=mc)
            for sid, (v, mc) in sites.items()
        },
    )


def _empty_registry(monkeypatch) -> None:
    monkeypatch.setattr("comic_dl.scrapers.registry._sourcemap", {})
    monkeypatch.setattr("comic_dl.scrapers.registry._builtin_ids", {})


@pytest.fixture
def real_sites():
    return local_sites()


# -------------------------------------------------------------------------
# Registry metadata

class TestRegistryMetadata:
    def test_all_builtins_declare_metadata(self, real_sites):
        assert len(real_sites) == 21

    def test_site_ids_unique(self, real_sites):
        ids = [s.site_id for s in real_sites]
        assert len(ids) == len(set(ids))
        assert all(ids)

    def test_webtoon_id_is_stable(self, real_sites):
        webtoon = next(s for s in real_sites if s.domain == "webtoons.com")
        assert webtoon.site_id == "webtoon"
        assert webtoon.version == "1.0.0"

    def test_duplicate_site_id_rejected(self, monkeypatch):
        _empty_registry(monkeypatch)
        register_builtin(
            _Scraper(),
            domain="a.example",
            capabilities={"chapter"},
            name="A",
            version="1.0.0",
            site_id="dup-test",
            minimum_core_version="0.0.1",
        )
        with pytest.raises(SiteRegistryError):
            register_builtin(
                _Scraper(),
                domain="b.example",
                capabilities={"chapter"},
                name="B",
                version="1.0.0",
                site_id="dup-test",
                minimum_core_version="0.0.1",
            )

    def test_duplicate_site_id_same_domain_is_idempotent(self, monkeypatch):
        _empty_registry(monkeypatch)
        register_builtin(
            _Scraper(), domain="a.example", capabilities={"chapter"},
            name="A", version="1.0.0", site_id="dup-same", minimum_core_version="0.0.1",
        )
        register_builtin(
            _Scraper(), domain="a.example", capabilities={"chapter"},
            name="A", version="1.0.0", site_id="dup-same", minimum_core_version="0.0.1",
        )

    def test_invalid_site_version_rejected(self, monkeypatch):
        _empty_registry(monkeypatch)
        with pytest.raises(ValueError):

            @register_scraper(domain="bad-ver.example")
            class Broken(_Scraper):  # type: ignore[misc]
                version = "1.2"
                site_id = "broken"
                minimum_core_version = "0.0.1"

    def test_invalid_min_core_rejected(self, monkeypatch):
        _empty_registry(monkeypatch)
        with pytest.raises(ValueError):

            @register_scraper(domain="bad-min.example")
            class BrokenMin(_Scraper):  # type: ignore[misc]
                version = "1.0.0"
                site_id = "broken-min"
                minimum_core_version = "nope"


# -------------------------------------------------------------------------
# Manifest parsing

class TestManifestParsing:
    def test_valid(self):
        m = SiteManifest.from_dict(
            {
                "schema_version": 1,
                "core_version": "0.0.3",
                "sites": {
                    "manga-example": {
                        "version": "1.3.0",
                        "minimum_core_version": "0.0.2",
                        "status": "supported",
                        "test_url": "https://x/pkg",
                    }
                },
            }
        )
        assert m is not None
        rel = m.sites["manga-example"]
        assert rel.version == "1.3.0" and rel.test_url == "https://x/pkg"

    def test_malformed_site_entry(self):
        assert SiteManifest.from_dict(
            {"schema_version": 1, "core_version": "0.0.3", "sites": {"x": "yes"}}
        ) is None

    def test_missing_core_version(self):
        assert SiteManifest.from_dict(
            {"schema_version": 1, "sites": {}}
        ) is None

    def test_wrong_schema_version(self):
        assert SiteManifest.from_dict(
            {"schema_version": 2, "core_version": "0.0.3", "sites": {}}
        ) is None

    def test_bad_version_format(self):
        assert SiteManifest.from_dict(
            {
                "schema_version": 1,
                "core_version": "0.0.3",
                "sites": {"x": {"version": "1", "minimum_core_version": "0.0.2"}},
            }
        ) is None

    def test_non_dict(self):
        assert SiteManifest.from_dict([]) is None


# -------------------------------------------------------------------------
# Status comparison

class TestStatusFor:
    def _site(self, **kw):

        return replace(
            local_sites()[0],
            **{"test_url": None, **kw},
        )

    def test_update_available(self):
        s = self._site(version="1.0.0", minimum_core_version="0.0.2")
        assert status_for(s, SiteRelease("1.1.0", "0.0.2"), "0.0.2") == "update available"

    def test_up_to_date(self):
        s = self._site(version="1.0.0", minimum_core_version="0.0.2")
        assert status_for(s, SiteRelease("1.0.0", "0.0.2"), "0.0.2") == "up to date"

    def test_incompatible_core(self):
        s = self._site(version="1.0.0", minimum_core_version="0.0.9")
        assert status_for(s, SiteRelease("1.0.0", "0.0.9"), "0.0.2") == "incompatible"

    def test_unable_to_check(self):
        assert status_for(local_sites()[0], None, "0.0.2") == "unable to check"

    def test_unpublished(self):
        s = self._site(version="1.2.0", minimum_core_version="0.0.2")
        assert status_for(s, SiteRelease("1.0.0", "0.0.2"), "0.0.2") == "unpublished"


# -------------------------------------------------------------------------
# Failed-download hint

class TestUpdateHint:
    def test_empty_without_cache(self, monkeypatch):
        monkeypatch.setattr("comic_dl.site_update._read_manifest_cache", lambda: None)
        assert site_update_hint("e-hentai.org") == ""

    def test_hint_when_newer_available(self, monkeypatch):
        m = _manifest({"e-hentai": ("1.2.0", "0.0.2")})
        monkeypatch.setattr("comic_dl.site_update._read_manifest_cache", lambda: m)
        hint = site_update_hint("e-hentai.org")
        assert "self site update e-hentai" in hint
        assert "1.2.0" in hint

    def test_no_hint_when_current(self, monkeypatch):
        m = _manifest({"e-hentai": ("1.0.0", "0.0.2")})
        monkeypatch.setattr("comic_dl.site_update._read_manifest_cache", lambda: m)
        assert site_update_hint("e-hentai.org") == ""

    def test_no_hint_for_unknown_domain(self, monkeypatch):
        m = _manifest({"e-hentai": ("1.2.0", "0.0.2")})
        monkeypatch.setattr("comic_dl.site_update._read_manifest_cache", lambda: m)
        assert site_update_hint("nope.example") == ""


# -------------------------------------------------------------------------
# Commands

class TestSiteList:
    async def test_renders_ids_and_versions(self, monkeypatch, capsys):
        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", _noop_fetch)
        monkeypatch.setattr("comic_dl.site_update._read_manifest_cache", lambda: None)
        rc = await run_site_list_command(json_mode=False)
        assert rc == EXIT_OK
        out = _text(capsys)
        assert "e-hentai" in out and "1.0.0" in out
        assert "unable to check" in out

    async def test_json_shape(self, monkeypatch, capsys):
        monkeypatch.setattr("comic_dl.site_update._read_manifest_cache", lambda: None)
        rc = await run_site_list_command(json_mode=True)
        assert rc == EXIT_OK
        out = _text(capsys)
        assert '"schema_version": 1' in out
        assert '"site_id": "webtoon"' in out


async def _noop_fetch():
    return None


class TestSiteCheck:
    async def test_offline_is_unable_to_check(self, monkeypatch, capsys):
        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", _noop_fetch)
        rc = await run_site_check_command(target=None, live=False, json_mode=False)
        assert rc == EXIT_ERROR
        assert "unable to check" in _text(capsys)

    async def test_unknown_site_is_usage_error(self, monkeypatch):
        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", _noop_fetch)
        rc = await run_site_check_command(target="nope", live=False, json_mode=False)
        assert rc == EXIT_USAGE

    async def test_live_requires_target(self, monkeypatch):
        rc = await run_site_check_command(target=None, live=True, json_mode=False)
        assert rc == EXIT_USAGE

    async def test_live_skipped_without_test_url(self, monkeypatch, capsys):
        async def healthy(site):
            return LiveResult("healthy")

        monkeypatch.setattr("comic_dl.site_update._live_check", healthy)
        rc = await run_site_check_command(target="webtoon", live=True, json_mode=False)
        assert rc == EXIT_OK
        assert "live: healthy" in _text(capsys)

    async def test_manifest_shows_up_to_date(self, monkeypatch, capsys):
        m = _manifest({"webtoon": ("1.0.0", "0.0.2")})

        async def fetch():
            return m

        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", fetch)
        rc = await run_site_check_command(target="webtoon", live=False, json_mode=False)
        assert rc == EXIT_OK
        assert "up to date" in _text(capsys)

    async def test_manifest_shows_update_available(self, monkeypatch, capsys):
        m = _manifest({"webtoon": ("1.1.0", "0.0.2")})

        async def fetch():
            return m

        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", fetch)
        rc = await run_site_check_command(target="webtoon", live=False, json_mode=False)
        assert rc == EXIT_OK
        assert "update available" in _text(capsys)


class TestSiteUpdate:
    async def test_neither_target_nor_all(self, monkeypatch):
        rc = await run_site_update_command(target=None, all_sites=False, yes=False)
        assert rc == EXIT_USAGE

    async def test_target_and_all_conflict(self, monkeypatch):
        rc = await run_site_update_command(target="webtoon", all_sites=True, yes=False)
        assert rc == EXIT_USAGE

    async def test_offline(self, monkeypatch, capsys):
        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", _noop_fetch)
        rc = await run_site_update_command(target="webtoon", all_sites=False, yes=False)
        assert rc == EXIT_ERROR
        assert "Unable to check" in _text(capsys)

    async def test_unknown_site(self, monkeypatch):
        async def fetch():
            return _manifest({"webtoon": ("1.1.0", "0.0.2")})

        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", fetch)
        rc = await run_site_update_command(target="nope", all_sites=False, yes=False)
        assert rc == EXIT_USAGE

    async def test_up_to_date_single(self, monkeypatch, capsys):
        m = _manifest({"webtoon": ("1.0.0", "0.0.2")})

        async def fetch():
            return m

        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", fetch)
        rc = await run_site_update_command(target="webtoon", all_sites=False, yes=False)
        assert rc == EXIT_OK
        assert "up to date" in _text(capsys)

    async def test_delegates_core_update(self, monkeypatch, capsys):
        m = _manifest({"webtoon": ("1.1.0", "0.0.3")}, core=_FUTURE)
        calls: list[dict] = []

        async def fake_fetch():
            return m

        async def fake_core_update(*, check, yes) -> int:
            calls.append({"check": check, "yes": yes})
            return EXIT_OK

        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", fake_fetch)
        monkeypatch.setattr("comic_dl.site_update.run_update_command", fake_core_update)
        rc = await run_site_update_command(target="webtoon", all_sites=False, yes=True)
        assert rc == EXIT_OK
        assert calls == [{"check": False, "yes": True}]
        out = _text(capsys)
        assert "Required core update" in out
        assert "1.1.0" in out

    async def test_all_aggregates_outdated(self, monkeypatch, capsys):
        m = _manifest({"webtoon": ("1.1.0", "0.0.3"), "e-hentai": ("1.2.0", "0.0.3")}, core=_FUTURE)

        async def fake_fetch():
            return m

        async def fake_core_update(**kw) -> int:
            return EXIT_OK

        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", fake_fetch)
        monkeypatch.setattr("comic_dl.site_update.run_update_command", fake_core_update)
        rc = await run_site_update_command(target=None, all_sites=True, yes=False)
        assert rc == EXIT_OK
        out = _text(capsys)
        assert "webtoon" in out and "e-hentai" in out

    async def test_inconsistent_manifest_refused(self, monkeypatch, capsys):
        m = _manifest({"webtoon": ("1.1.0", "0.0.2")}, core="0.0.2")

        async def fake_fetch():
            return m

        monkeypatch.setattr("comic_dl.site_update.fetch_site_manifest", fake_fetch)
        rc = await run_site_update_command(target="webtoon", all_sites=False, yes=False)
        assert rc == EXIT_ERROR
        assert "reinstall" in _text(capsys)
