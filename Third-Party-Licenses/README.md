# Third-Party Licenses

License texts for every package in the resolved runtime closure
(`uv.lock`, all supported platforms). Each subdirectory mirrors the
`LICENSE`/`licenses` files shipped inside that package's PyPI wheel,
including any extra attribution files the wheel carries. The PyInstaller
bundle ships this directory as `third_party_licenses`; the sdist includes
it as-is.

After any dependency change, refresh the affected subdirectory from the
new wheel and update its version below.

| Package | Version | License | Files |
|---|---|---|---|
| [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/bs4/) | 4.15.0 | MIT | [LICENSE](beautifulsoup4/LICENSE), [AUTHORS](beautifulsoup4/AUTHORS) |
| [bottle](https://github.com/bottlepy/bottle) | 0.13.4 | MIT | [LICENSE](bottle/LICENSE) |
| [certifi](https://github.com/certifi/python-certifi) | 2026.7.22 | MPL-2.0 | [LICENSE](certifi/LICENSE) |
| [cffi](https://github.com/python-cffi/cffi) | 2.1.0 | MIT-0 | [LICENSE](cffi/LICENSE) |
| [clr-loader](https://pypi.org/project/clr-loader/) | 0.3.1 | MIT | [LICENSE](clr-loader/LICENSE) |
| [curl-cffi](https://pypi.org/project/curl-cffi/) | 0.16.3 | MIT | [LICENSE](curl-cffi/LICENSE) |
| [defusedxml](https://github.com/tiran/defusedxml) | 0.7.1 | PSF-2 | [LICENSE](defusedxml/LICENSE) |
| [lxml](https://github.com/lxml/lxml) | 6.1.3 | BSD-3-Clause | [LICENSE](lxml/LICENSE), [LICENSES.txt](lxml/LICENSES.txt) |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | 4.2.0 | MIT | [LICENSE](markdown-it-py/LICENSE), [LICENSE.markdown-it](markdown-it-py/LICENSE.markdown-it) |
| [mdurl](https://github.com/executablebooks/mdurl) | 0.1.2 | MIT | [LICENSE](mdurl/LICENSE) |
| [packaging](https://github.com/pypa/packaging) | 26.2 | Apache-2.0 OR BSD-3-Clause | [LICENSE](packaging/LICENSE), [LICENSE.APACHE](packaging/LICENSE.APACHE), [LICENSE.BSD](packaging/LICENSE.BSD) |
| [platformdirs](https://github.com/tox-dev/platformdirs) | 4.11.8 | MIT | [LICENSE](platformdirs/LICENSE) |
| [proxy-tools](https://github.com/jtushman/proxy_tools) | 0.1.0 | MIT | [LICENSE](proxy-tools/LICENSE) |
| [pycparser](https://github.com/eliben/pycparser) | 3.0 | BSD-3-Clause | [LICENSE](pycparser/LICENSE) |
| [pygments](https://github.com/pygments/pygments) | 2.20.0 | BSD-3-Clause | [LICENSE](pygments/LICENSE), [AUTHORS](pygments/AUTHORS) |
| [pyobjc-core](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | MIT | [LICENSE](pyobjc-core/LICENSE) |
| [pyobjc-framework-cocoa](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | MIT | [LICENSE](pyobjc-framework-cocoa/LICENSE) |
| [pyobjc-framework-quartz](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | MIT | [LICENSE](pyobjc-framework-quartz/LICENSE) |
| [pyobjc-framework-security](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | MIT | [LICENSE](pyobjc-framework-security/LICENSE) |
| [pyobjc-framework-uniformtypeidentifiers](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | MIT | [LICENSE](pyobjc-framework-uniformtypeidentifiers/LICENSE) |
| [pyobjc-framework-webkit](https://github.com/ronaldoussoren/pyobjc) | 12.2.2 | MIT | [LICENSE](pyobjc-framework-webkit/LICENSE) |
| [pythonnet](https://pythonnet.github.io/) | 3.1.0 | MIT | [LICENSE](pythonnet/LICENSE) |
| [pywebview](https://github.com/r0x0r/pywebview) | 6.2.1 | BSD-3-Clause | [LICENSE](pywebview/LICENSE) |
| [qtpy](https://github.com/spyder-ide/qtpy) | 2.4.3 | MIT | [LICENSE](qtpy/LICENSE) |
| [rich](https://github.com/Textualize/rich) | 15.0.0 | MIT | [LICENSE](rich/LICENSE) |
| [soupsieve](https://github.com/facelessuser/soupsieve) | 2.9.1 | MIT | [LICENSE](soupsieve/LICENSE) |
| [typing-extensions](https://github.com/python/typing_extensions) | 4.16.0 | PSF-2 | [LICENSE](typing-extensions/LICENSE) |

Notes:

- `packaging` is dual-licensed; `LICENSE` is the upstream notice pointing
  at the `LICENSE.APACHE` / `LICENSE.BSD` texts.
- `pyobjc-*` ship on macOS only, `pythonnet` and `clr-loader` on Windows
  only, `qtpy` on OpenBSD only. They are listed because the closure is
  resolved for every supported platform, not just the one you built on.
- `cffi` 2.x is MIT-0 ("MIT No Attribution"), not classic MIT. The text
  is kept anyway.
- Dev, docs, and build tooling (pytest, ruff, mypy, hatchling,
  pymarkdownlnt, the node toolchain) is deliberately absent: none of it
  ships to users.

## Website assets

The built docs site carries its own third-party code, separate from the
Python closure above. `website/public/NOTICES` (deployed with the site)
holds the full texts; the rows below say what and where.

| Asset | Version | License | Shipped as |
|---|---|---|---|
| [Lucide](https://lucide.dev) icons | 1.46.0 (`@lucide/astro`) | ISC + MIT (Feather-derived icons) | Inline SVG in built pages |
| [Simple Icons](https://simple-icons.org) brand marks | 16.31.0 | CC0-1.0 | Paths vendored in `website/src/components/icons/BrandIcon.astro` |
| [Pagefind](https://pagefind.app) | 1.5.2 (`pagefind`) | MIT | `pagefind/` bundle, lazy-loaded on first search |
| [Shiki](https://shiki.style) | 4.4.3 | MIT | Highlighted HTML emitted at build time |
| [Astro](https://astro.build) + `@astrojs/mdx` | 7.3.2 / 8.0.1 | MIT | Build tooling; the static output carries no Astro code |
