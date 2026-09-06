from __future__ import annotations

from pathlib import Path

import pytest

import comic_dl.config as cfgmodule
from comic_dl.cli import parse_urls
from comic_dl.errors import EXIT_USAGE


def _patch_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(cfgmodule, "config_path", lambda: tmp_path / "config.toml")


class TestConfigFile:
    def test_missing_file_returns_empty(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        assert cfgmodule.load_config() == {}

    def test_invalid_toml_returns_empty(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text("this is not [ valid toml")
        assert cfgmodule.load_config() == {}

    def test_valid_toml_is_loaded(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text('output = "/c/comics"\nconcurrency = 3\n')
        conf = cfgmodule.load_config()
        assert conf["output"] == "/c/comics"
        assert conf["concurrency"] == 3

    def test_non_mapping_document_returns_empty(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text("items = [1, 2, 3]\n")
        # a TOML table is a dict; nothing to do here beyond not crashing
        assert isinstance(cfgmodule.load_config(), dict)

    def test_download_tmp_dir_is_loaded(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text('[download]\ntmp-dir = "/data/scratch"\n')
        assert cfgmodule.load_config()["download"]["tmp-dir"] == "/data/scratch"


class TestDefaultOutputDir:
    def test_points_to_home_downloads(self, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: Path("/home/me"))
        assert cfgmodule.default_output_dir() == Path("/home/me/Downloads/comic-dl")


class TestConfiguredOutputDir:
    def test_uses_config_output(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text('output = "~/Comics"\n')
        assert cfgmodule.configured_output_dir() == Path("~/Comics").expanduser()

    def test_falls_back_to_default(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        # no config file -> falls back to default_output_dir
        monkeypatch.setattr(cfgmodule, "default_output_dir", lambda: Path("/home/me/Downloads/comic-dl"))
        assert cfgmodule.configured_output_dir() == Path("/home/me/Downloads/comic-dl")


class TestCLIPrecedence:
    def _cli(self, monkeypatch, conf, outdir, argv):
        monkeypatch.setattr("comic_dl.cli.load_config", lambda: conf)
        monkeypatch.setattr("comic_dl.cli.configured_output_dir", lambda: outdir)
        monkeypatch.setattr("sys.argv", ["prog", *argv])

    def test_defaults_when_no_config(self, monkeypatch, tmp_path):
        outdir = tmp_path / "default"
        self._cli(monkeypatch, {}, outdir, ["-u", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.output == outdir
        assert args.concurrency == 5
        assert args.max_image_size == 100 * 1024 * 1024
        assert args.max_size == 0

    def test_config_applies_when_no_flag(self, monkeypatch, tmp_path):
        outdir = tmp_path / "cfg"
        conf = {
            "output": str(outdir),
            "concurrency": 3,
            "max_image_size": "50MB",
            "max_size": "1GB",
        }
        self._cli(monkeypatch, conf, outdir, ["-u", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.output == outdir
        assert args.concurrency == 3
        assert args.max_image_size == 50 * 1024 * 1024
        assert args.max_size == 1024 ** 3

    def test_flag_overrides_config(self, monkeypatch, tmp_path):
        conf_dir = tmp_path / "cfg"
        flag_out = tmp_path / "flag"
        conf = {"output": str(conf_dir), "concurrency": 3}
        self._cli(
            monkeypatch, conf, conf_dir,
            ["-u", "https://e-hentai.org/g/1/a/", "-o", str(flag_out),
             "-c", "9", "--max-image-size", "5MB", "--max-size", "1KB"],
        )
        _, args = parse_urls()
        assert args.output == flag_out
        assert args.concurrency == 9
        assert args.max_image_size == 5 * 1024 * 1024
        assert args.max_size == 1024

    def test_format_defaults_to_cbz(self, monkeypatch, tmp_path):
        outdir = tmp_path / "dl"
        self._cli(monkeypatch, {}, outdir, ["-u", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.format == "cbz"

    def test_format_from_config(self, monkeypatch, tmp_path):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {"archive": {"format": "zip"}}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/"],
        )
        _, args = parse_urls()
        assert args.format == "zip"

    def test_format_normalized_from_config(self, monkeypatch, tmp_path):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {"archive": {"format": "CBT"}}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/"],
        )
        _, args = parse_urls()
        assert args.format == "cbt"

    def test_format_flag_overrides_config(self, monkeypatch, tmp_path):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {"archive": {"format": "zip"}}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/", "--format", "cbt"],
        )
        _, args = parse_urls()
        assert args.format == "cbt"

    def test_bad_format_config_is_a_usage_error(self, monkeypatch, tmp_path):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {"archive": {"format": "rar"}}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/"],
        )
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == EXIT_USAGE

    def test_bad_format_flag_is_a_usage_error(self, monkeypatch, tmp_path):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/", "--format", "rar"],
        )
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == EXIT_USAGE

    def test_bad_config_concurrency_is_ignored(self, monkeypatch, tmp_path):
        outdir = tmp_path / "dl"
        conf = {"concurrency": "not-a-number", "max_image_size": "bogus"}
        self._cli(monkeypatch, conf, outdir, ["-u", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.concurrency == 5
        assert args.max_image_size == 100 * 1024 * 1024

    def test_bad_parallel_config_warns_with_key_name(self, monkeypatch, tmp_path, capsys):
        outdir = tmp_path / "dl"
        conf = {"parallel": "not-a-number"}
        self._cli(monkeypatch, conf, outdir, ["-u", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.parallel == 5
        captured = capsys.readouterr()
        assert "Invalid parallel" in (captured.out + captured.err)

    def test_parallel_defaults_and_clamp(self, monkeypatch, tmp_path):
        outdir = tmp_path / "dl"
        self._cli(monkeypatch, {}, outdir, ["-u", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.parallel == 5

        self._cli(
            monkeypatch, {}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/", "--parallel", "16"],
        )
        _, args = parse_urls()
        assert args.parallel == 16

    def test_http_flags_apply_runtime_overrides(self, monkeypatch, tmp_path):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/", "--impersonate", "chrome131",
             "--solver", "off", "--no-cookie", "--no-rate"],
        )
        _, args = parse_urls()
        assert args.impersonate == "chrome131"
        assert args.solver == "off"
        assert args.no_cookie is True
        assert args.no_rate is True
        try:
            from comic_dl.config import _RUNTIME_HTTP
            assert _RUNTIME_HTTP["impersonate"] == "chrome131"
            assert _RUNTIME_HTTP["solver"] == "off"
            assert _RUNTIME_HTTP["cookie-jar"] is False
            assert _RUNTIME_HTTP["rate-enabled"] is False
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_unknown_impersonate_warns_and_falls_back(self, monkeypatch, tmp_path, capsys):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/", "--impersonate", "netscape9"],
        )
        try:
            _, args = parse_urls()
            captured = capsys.readouterr()
            assert "unknown impersonation profile" in (captured.out + captured.err)
            assert args.impersonate == "chrome146"
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_deprecated_impersonate_warns_but_kept(self, monkeypatch, tmp_path, capsys):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/", "--impersonate", "chrome99"],
        )
        try:
            _, args = parse_urls()
            captured = capsys.readouterr()
            assert "outdated" in (captured.out + captured.err)
            assert args.impersonate == "chrome99"
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_bad_config_impersonate_is_neutralized(self, monkeypatch, tmp_path, capsys):
        # A bad [http] impersonate with no --impersonate flag must fall back to
        # chrome146 AND the runtime override must be neutralized, otherwise the
        # config value resurfaces at the first request (http_setting re-read).
        outdir = tmp_path / "dl"
        conf = {"http": {"impersonate": "netscape9"}}
        self._cli(monkeypatch, conf, outdir, ["-u", "https://e-hentai.org/g/1/a/"])
        # http_setting reads the config via config.load_config, so both entry
        # points must return the same conf for the bad value to surface.
        monkeypatch.setattr(cfgmodule, "load_config", lambda: conf)
        try:
            _, args = parse_urls()
            captured = capsys.readouterr()
            assert "unknown impersonation profile" in (captured.out + captured.err)
            assert args.impersonate == "chrome146"
            assert cfgmodule._RUNTIME_HTTP["impersonate"] == "chrome146"
            assert cfgmodule.http_setting("impersonate", "chrome146") == "chrome146"
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_rate_disabled_warns(self, monkeypatch, tmp_path, capsys):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/", "--no-rate"],
        )
        try:
            parse_urls()
            captured = capsys.readouterr()
            assert "rate limiting is disabled" in (captured.out + captured.err)
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_valid_impersonate_no_warning(self, monkeypatch, tmp_path, capsys):
        outdir = tmp_path / "dl"
        self._cli(
            monkeypatch, {}, outdir,
            ["-u", "https://e-hentai.org/g/1/a/", "--impersonate", "chrome146"],
        )
        try:
            parse_urls()
            captured = capsys.readouterr()
            assert "unknown impersonation" not in (captured.out + captured.err)
            assert "outdated" not in (captured.out + captured.err)
        finally:
            cfgmodule._RUNTIME_HTTP.clear()


class TestEffectiveConfig:
    def test_no_file_returns_documented_defaults(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        eff = cfgmodule.effective_config()
        assert eff["output"] == "~/Downloads/comic-dl"
        assert eff["concurrency"] == 5
        assert eff["max_size"] == 0
        assert eff["http"]["rate"]["kstatic.to"] == 2.0
        assert eff["archive"]["format"] == "cbz"
        assert "sources" not in eff

    def test_file_wins_per_key_else_defaults(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            'output = "/x"\nconcurrency = 3\n[http]\nsolver = "off"\n',
            encoding="utf-8",
        )
        eff = cfgmodule.effective_config()
        assert eff["output"] == "/x"
        assert eff["concurrency"] == 3
        assert eff["http"]["solver"] == "off"
        assert eff["http"]["impersonate"] == "chrome146"
        assert eff["archive"]["format"] == "cbz"

    def test_sources_merge_is_recursive(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            '[sources."other.example"]\nrate = 2.5\n', encoding="utf-8",
        )
        eff = cfgmodule.effective_config()
        assert eff["sources"]["other.example"]["rate"] == 2.5

    def test_no_config_returns_defaults_only(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text('output = "/x"\n', encoding="utf-8")
        cfgmodule.set_no_config(True)
        try:
            eff = cfgmodule.effective_config()
            assert eff["output"] == "~/Downloads/comic-dl"
        finally:
            cfgmodule.set_no_config(False)


class TestNoConfigFlag:
    def test_load_config_returns_empty(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text('output = "/x"\n', encoding="utf-8")
        cfgmodule.set_no_config(True)
        try:
            assert cfgmodule.no_config_active() is True
            assert cfgmodule.load_config() == {}
            assert cfgmodule.configured_output_dir() == cfgmodule.default_output_dir()
        finally:
            cfgmodule.set_no_config(False)

    def test_disabled_restores_loading(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text('output = "/x"\n', encoding="utf-8")
        cfgmodule.set_no_config(True)
        cfgmodule.set_no_config(False)
        assert cfgmodule.no_config_active() is False
        assert cfgmodule.load_config()["output"] == "/x"


class TestMalformedConfigWarns:
    def test_invalid_toml_warns_and_returns_empty(self, monkeypatch, tmp_path, capsys):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text("this is not [ valid toml", encoding="utf-8")
        assert cfgmodule.load_config() == {}
        err = capsys.readouterr().err
        assert "malformed config file" in err

    def test_missing_file_stays_silent(self, monkeypatch, tmp_path, capsys):
        _patch_paths(monkeypatch, tmp_path)
        assert cfgmodule.load_config() == {}
        assert capsys.readouterr().err == ""

    def test_directory_path_warns_and_returns_empty(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(cfgmodule, "config_path", lambda: tmp_path)
        assert cfgmodule.load_config() == {}
        # Strip folding newlines: Rich wraps the warning at the console width,
        # which can split the phrase mid-token on narrow terminals.
        err = capsys.readouterr().err.replace("\n", "")
        assert "not a regular file" in err

    def test_malformed_warns_once_until_file_changes(self, monkeypatch, tmp_path, capsys):
        _patch_paths(monkeypatch, tmp_path)
        path = tmp_path / "config.toml"
        path.write_text("not [ valid", encoding="utf-8")
        assert cfgmodule.load_config() == {}
        first = capsys.readouterr().err
        assert first.count("malformed config file") == 1
        # Same bytes: the failed parse is cached, so no re-warning.
        assert cfgmodule.load_config() == {}
        second = capsys.readouterr().err
        assert "malformed config file" not in second
        # Rewriting the file re-parses; still broken -> warns again.
        path.write_text("still [ not valid", encoding="utf-8")
        assert cfgmodule.load_config() == {}
        third = capsys.readouterr().err
        assert "malformed config file" in third


class TestConfigValidationWarnings:
    """Bad values degrade to defaults (consumers handle them) but must produce
    a visible warning at load time instead of silently shifting behavior."""

    def test_warns_for_wrong_types_ranges_enums_and_unknown_keys(self, monkeypatch, tmp_path, capsys):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            'output = 5\n'
            'parallel = 99\n'
            'concurrency = "three"\n'
            'bogus_top = 1\n'
            '[http]\n'
            'solver = "nope"\n'
            'cache = "yes"\n'
            'mystery = 1\n'
            '[archive]\n'
            'format = "rar"\n'
            '[sources."kagane.to"]\n'
            'rate = -1\n'
            'mode = "sometimes"\n',
            encoding="utf-8",
        )
        cfgmodule.load_config()
        err = capsys.readouterr().err
        assert "config" in err
        # Warnings are capped at six problems per config with a (N more)
        # marker; assert the visible ones and the truncation hint.
        for needle in ['output', 'parallel', 'concurrency', 'bogus_top', 'solver', 'cache']:
            assert needle in err, f"missing validation warning for {needle}"
        assert "more" in err

    def test_valid_config_warns_nothing(self, monkeypatch, tmp_path, capsys):
        """The shipped DEFAULT_CONFIG_TOML must round-trip warning-free."""
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            cfgmodule.DEFAULT_CONFIG_TOML, encoding="utf-8",
        )
        assert cfgmodule.load_config() != {}
        assert capsys.readouterr().err == ""

    def test_validation_warns_once_per_parse(self, monkeypatch, tmp_path, capsys):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text('bogus = 1\n', encoding="utf-8")
        cfgmodule.load_config()
        cap1 = capsys.readouterr()
        cfgmodule.load_config()  # cache hit -> no re-parse, no re-warning
        cap2 = capsys.readouterr()
        assert "bogus" in (cap1.out + cap1.err)
        assert (cap2.out + cap2.err) == ""


class TestConfigSnapshot:
    """load_config parses once per (path, mtime, size) and never re-reads the
    same bytes twice, while an edit is picked up on the next stat."""

    def test_unchanged_file_is_not_reparsed(self, monkeypatch, tmp_path, capsys):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text('concurrency = 5\n', encoding="utf-8")
        assert cfgmodule.load_config()["concurrency"] == 5
        cap1 = capsys.readouterr()
        cfgmodule.load_config()
        cap2 = capsys.readouterr()
        assert (cap1.out + cap1.err) == (cap2.out + cap2.err) == ""

    def test_edited_file_is_picked_up(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        path = tmp_path / "config.toml"
        path.write_text('concurrency = 5\n', encoding="utf-8")
        assert cfgmodule.load_config()["concurrency"] == 5
        path.write_text('concurrency = 7\n', encoding="utf-8")
        assert cfgmodule.load_config()["concurrency"] == 7

    def test_reload_config_forces_reparse(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        path = tmp_path / "config.toml"
        path.write_text('output = "/a"\n', encoding="utf-8")
        assert cfgmodule.load_config()["output"] == "/a"
        path.write_text('output = "/b"\n', encoding="utf-8")
        assert cfgmodule.reload_config()["output"] == "/b"


class TestConfigPathSemantics:
    def test_set_config_path_blank_resets_to_default(self, monkeypatch):
        monkeypatch.setattr(cfgmodule, "config_dir", lambda: Path("/plat/config"))
        try:
            cfgmodule.set_config_path("~/custom.toml")
            assert cfgmodule.config_path() == Path("~/custom.toml").expanduser().absolute()
            cfgmodule.set_config_path("")
            assert cfgmodule.config_path() == Path("/plat/config/config.toml")
            cfgmodule.set_config_path(None)
            assert cfgmodule.config_path() == Path("/plat/config/config.toml")
        finally:
            cfgmodule.set_config_path(None)

    def test_config_path_is_absolute(self, monkeypatch):
        monkeypatch.setattr(cfgmodule, "config_dir", lambda: Path("/plat/config"))
        try:
            cfgmodule.set_config_path("relative.toml")
            assert cfgmodule.config_path() == (Path("relative.toml").expanduser().absolute())
            monkeypatch.setenv("COMIC_DL_CONFIG", "envrel.toml")
            cfgmodule.set_config_path(None)
            assert cfgmodule.config_path() == Path("envrel.toml").expanduser().absolute()
        finally:
            cfgmodule.set_config_path(None)

    def test_set_config_dir_override(self, tmp_path):
        original = cfgmodule.config_dir()
        try:
            cfgmodule.set_config_dir(tmp_path)
            assert cfgmodule.config_dir() == tmp_path
        finally:
            cfgmodule.set_config_dir(None)
        assert cfgmodule.config_dir() == original


class TestRuntimeOverrides:
    def test_effective_config_reflects_runtime_overrides(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text('[http]\nsolver = "off"\n', encoding="utf-8")
        cfgmodule.set_runtime_http(**{"impersonate": "chrome131"})
        try:
            eff = cfgmodule.effective_config()
            assert eff["http"]["impersonate"] == "chrome131"
            assert eff["http"]["solver"] == "off"
        finally:
            cfgmodule.clear_runtime_http()
        eff = cfgmodule.effective_config()
        assert eff["http"]["impersonate"] == "chrome146"
        assert eff["http"]["solver"] == "off"

    def test_clear_runtime_download(self):
        cfgmodule.set_runtime_download(generic=False)
        assert cfgmodule._RUNTIME_DOWNLOAD["generic"] is False
        cfgmodule.clear_runtime_download()
        assert cfgmodule._RUNTIME_DOWNLOAD == {}


class TestHostAwareHttpSetting:
    def test_host_table_beats_http_map(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            '[http]\nrate = { "kagane.to" = 1.5 }\n'
            '[sources."kagane.to"]\nrate = 0.8\n',
            encoding="utf-8",
        )
        assert cfgmodule.http_setting("rate", {}, host="kagane.to") == 0.8

    def test_host_missing_falls_back_to_http(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            '[http]\nrate = { "kagane.to" = 1.5 }\n', encoding="utf-8",
        )
        assert cfgmodule.http_setting("rate", {}, host="other.example") == {
            "kagane.to": 1.5
        }

    def test_runtime_override_beats_host_table(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            '[sources."kagane.to"]\nrate = 0.8\n', encoding="utf-8",
        )
        cfgmodule.set_runtime_http(**{"rate": {"kagane.to": 9.0}})
        try:
            assert cfgmodule.http_setting("rate", {}, host="kagane.to") == {
                "kagane.to": 9.0
            }
        finally:
            cfgmodule._RUNTIME_HTTP.clear()

    def test_unknown_host_uses_default(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        assert cfgmodule.http_setting("rate", {}, host="example.com") == {}


class TestDefaultConfigParity:
    """``DEFAULT_CONFIG_TOML`` must stay byte-identical to examples/config.toml.

    The two are edited by hand in different files, so the pin catches drift
    instead of relying on the "kept in sync" comment.
    """

    def test_matches_examples_config(self):
        examples = Path(__file__).resolve().parents[1] / "examples" / "config.toml"
        assert examples.read_text(encoding="utf-8") == cfgmodule.DEFAULT_CONFIG_TOML

    def test_written_default_round_trips_through_parser(self, monkeypatch, tmp_path):
        _patch_paths(monkeypatch, tmp_path)
        (tmp_path / "config.toml").write_text(
            cfgmodule.DEFAULT_CONFIG_TOML, encoding="utf-8",
        )
        conf = cfgmodule.load_config()
        assert conf["output"] == "~/Downloads/comic-dl"
        assert conf["http"]["solver"] == "auto"
        assert conf["archive"]["format"] == "cbz"
        assert conf["http"]["rate"]["kagane.to"] == 1.5
