# API reference

Manually maintained index of `comic_dl`'s public modules. Keep docstrings
in `src/comic_dl/` current — they are the ground truth for each module's
public surface. For a tour of how the pieces fit together, see
[Architecture](develop/architecture.md) first.

## Data contracts

`comic_dl.models` — gallery, chapter, and image data models.

## Download engine

`comic_dl.downloader` — asynchronous download engine.

## Archive creation

`comic_dl.archiver` — CBZ/ZIP/CBT packing.

## Scraper contract and registry

The built-in scrapers and the plugin registry. Plugin authors should start
with [Writing a plugin](usage/write-plugin.md) — this section documents the
helpers a plugin builds on.

- `comic_dl.scrapers.base` — `BaseScraper` contract.
- `comic_dl.scrapers.registry` — plugin loader.
- `comic_dl.scrapers.generic` — fallback HTML scraper.

## URL validation and sanitization

`comic_dl.utils` — URL validation and sanitization helpers.

## Configuration loading

`comic_dl.config` — config-file discovery, precedence (`CLI flag >
config.toml > built-in default`), per-source overrides, and the
resolved-effective-config view used by `comic-dl config show|list`. Includes
the download-toggles (`[download] generic`) and runtime overrides
(`set_runtime_download` / `download_setting` / `generic_enabled`).

## Error codes and exit status

`comic_dl.errors` — exit codes and error hierarchy.
