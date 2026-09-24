# Third-Party Licenses

License texts for every package in the resolved runtime closure (`uv.lock`,
all supported platforms). Each subdirectory mirrors the license files shipped
in that package's wheel, including extra attribution files. The PyInstaller
bundle ships this directory as `third_party_licenses`; the sdist includes it
as-is. After a dependency change, refresh the affected subdirectory from the
new wheel and update its version below.

## Runtime closure

| Package | Version | License |
|---|---|---|
| [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/bs4/) | 4.15.0 | [MIT](beautifulsoup4/LICENSE), [AUTHORS](beautifulsoup4/AUTHORS) |
| [bottle](https://github.com/bottlepy/bottle) | 0.13.4 | [MIT](bottle/LICENSE) |
| [certifi](https://github.com/certifi/python-certifi) | 2026.7.22 | [MPL-2.0](certifi/LICENSE) |
| [cffi](https://github.com/python-cffi/cffi) | 2.1.0 | [MIT-0](cffi/LICENSE) |
| [clr-loader](https://pypi.org/project/clr-loader/) | 0.3.1 | [MIT](clr-loader/LICENSE) |
| [curl-cffi](https://pypi.org/project/curl-cffi/) | 0.16.3 | [MIT](curl-cffi/LICENSE) |
| [cryptography](https://github.com/pyca/cryptography) | 50.0.1 | [Apache-2.0](cryptography/LICENSE.APACHE) or [BSD-3-Clause](cryptography/LICENSE.BSD), [notice](cryptography/LICENSE) |
| [defusedxml](https://github.com/tiran/defusedxml) | 0.7.1 | [PSF-2](defusedxml/LICENSE) |
| [keyring](https://github.com/jaraco/keyring) | 25.7.0 | [MIT](keyring/LICENSE) |
| [lxml](https://github.com/lxml/lxml) | 6.1.3 | [BSD-3-Clause](lxml/LICENSE), [LICENSES.txt](lxml/LICENSES.txt) |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | 4.2.0 | [MIT](markdown-it-py/LICENSE), [LICENSE.markdown-it](markdown-it-py/LICENSE.markdown-it) |
| [mdurl](https://github.com/executablebooks/mdurl) | 0.1.2 | [MIT](mdurl/LICENSE) |
| [packaging](https://github.com/pypa/packaging) | 26.2 | [Apache-2.0](packaging/LICENSE.APACHE) or [BSD-3-Clause](packaging/LICENSE.BSD), [notice](packaging/LICENSE) |
| [platformdirs](https://github.com/tox-dev/platformdirs) | 4.11.12 | [MIT](platformdirs/LICENSE) |
| [proxy-tools](https://github.com/jtushman/proxy_tools) | 0.1.0 | [MIT](proxy-tools/LICENSE) |
| [pycparser](https://github.com/eliben/pycparser) | 3.0 | [BSD-3-Clause](pycparser/LICENSE) |
| [pygments](https://github.com/pygments/pygments) | 2.20.0 | [BSD-3-Clause](pygments/LICENSE), [AUTHORS](pygments/AUTHORS) |
| [pyobjc-core](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | [MIT](pyobjc-core/LICENSE) |
| [pyobjc-framework-cocoa](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | [MIT](pyobjc-framework-cocoa/LICENSE) |
| [pyobjc-framework-quartz](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | [MIT](pyobjc-framework-quartz/LICENSE) |
| [pyobjc-framework-security](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | [MIT](pyobjc-framework-security/LICENSE) |
| [pyobjc-framework-uniformtypeidentifiers](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | [MIT](pyobjc-framework-uniformtypeidentifiers/LICENSE) |
| [pyobjc-framework-webkit](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | [MIT](pyobjc-framework-webkit/LICENSE) |
| [pythonnet](https://pythonnet.github.io/) | 3.1.0 | [MIT](pythonnet/LICENSE) |
| [pywebview](https://github.com/r0x0r/pywebview) | 6.2.1 | [BSD-3-Clause](pywebview/LICENSE) |
| [qtpy](https://github.com/spyder-ide/qtpy) | 2.4.3 | [MIT](qtpy/LICENSE) |
| [rich](https://github.com/Textualize/rich) | 15.0.0 | [MIT](rich/LICENSE) |
| [soupsieve](https://github.com/facelessuser/soupsieve) | 2.9.1 | [MIT](soupsieve/LICENSE) |
| [typing-extensions](https://github.com/python/typing_extensions) | 4.16.0 | [PSF-2](typing-extensions/LICENSE) |

Platform notes:

- `pyobjc-*` ship on macOS only, `pythonnet` and `clr-loader` on Windows
  only, `qtpy` on OpenBSD only. They appear because the closure is resolved
  for every supported platform.
- `packaging` is dual-licensed; `LICENSE` points at the `LICENSE.APACHE` and
  `LICENSE.BSD` texts.
- `cffi` 2.x is MIT-0 ("MIT No Attribution"), not classic MIT.

## Website assets

The docs site carries other third-party code, separate from the Python
closure. `website/public/NOTICES` (deployed with the site) holds the full
texts; the links below point to it.

| Asset | Version | License | Shipped as |
|---|---|---|---|
| [Lucide](https://lucide.dev) icons | 1.46.0 (`@lucide/astro`) | [ISC + MIT](../website/public/NOTICES) | Inline SVG in built pages |
| [Simple Icons](https://simple-icons.org) brand marks | 16.31.0 | [CC0-1.0](../website/public/NOTICES) | Paths vendored in `website/src/components/icons/BrandIcon.astro` |
| [Pagefind](https://pagefind.app) | 1.5.2 (`pagefind`) | [MIT](../website/public/NOTICES) | `pagefind/` bundle, lazy-loaded on first search |
| [Shiki](https://shiki.style) | 4.4.3 | [MIT](../website/public/NOTICES) | Highlighted HTML emitted at build time |
| [Astro](https://astro.build) + `@astrojs/mdx` | 7.3.3 / 8.0.1 | [MIT](../website/public/NOTICES) | Build tooling; the static output carries no Astro code |

The website toolchain (`@astrojs/sitemap`, Tailwind, Vite, and the node
tooling under `website/`) builds on the Astro packages above.

## Development tooling

The Python dev, docs, and build tooling does not ship to users, so no license
texts are vendored here. Versions are informational.

| Tool | Version | License |
|---|---|---|
| [hatchling](https://hatch.pypa.io/) | 1.32.4 | MIT |
| [pymarkdownlnt](https://github.com/jackdewinter/pymarkdown) | 0.9.40 | MIT |
| [pytest](https://pytest.org) | 9.1.1 | MIT |
| [pytest-xdist](https://github.com/pytest-dev/pytest-xdist) | 3.8.0 | MIT |
| [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) | 1.4.0 | Apache-2.0 |
| [pytest-cov](https://github.com/pytest-dev/pytest-cov) | 7.1.0 | MIT |
| [ruff](https://docs.astral.sh/ruff/) | 0.16.8 | MIT |
| [mypy](https://www.mypy-lang.org) | 2.3.1 | MIT |
| [bandit](https://bandit.readthedocs.io/) | 1.9.4 | Apache-2.0 |
| [pyinstaller](https://pyinstaller.org) | 6.22.3 | GPL-2.0-or-later with bootloader exception |
| [types-defusedxml](https://pypi.org/project/types-defusedxml/) | 0.7.0.20260504 | Apache-2.0 |
