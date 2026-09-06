# Plugins

comic-dl sources are plugins. The built-in scrapers ship with the package, but
any third party can add a site as a separate Python package.

## How plugins work

A plugin registers a `Source` class through the `comic_dl.sources` entry-point
group. After installation, the source appears in `comic-dl --list-sources` and
handles URLs matching its domain.

## Finding plugins

```bash
comic-dl --list-sources
```

Lists every registered source, marking each as built-in or plugin and showing
its capabilities (chapter / series).

## Installing a plugin

Install the plugin package into the same environment as comic-dl:

```bash
pip install <plugin-package>
```

Then restart the CLI — the new source is discovered automatically.

## Plugin priority

A domain can be owned by only one source. Built-ins register at priority `0`.
A plugin with `priority > 0` overrides a built-in for the same domain.

## Security

Plugins are arbitrary code. A scraper plugin runs with your user account's
privileges on every scrape. Only install plugins you trust and can audit.

comic-dl enforces outbound-fetch invariants (redirect validation, no
loopback/private targets) on its own helpers, but a deliberately malicious
plugin is not constrained by those helpers.

## Writing a plugin

See [Writing a Plugin](write-plugin.md) for the full guide with code examples.
