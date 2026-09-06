# Third-Party Licenses

| Name  | License   | License File  |
|:-----:|:---------:|:-------------:|
[beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/bs4/) | MIT | [LICENSE](beautifulsoup4/LICENSE) (+ [AUTHORS](beautifulsoup4/AUTHORS))
[bottle](https://github.com/bottlepy/bottle) | MIT | [LICENSE](bottle/LICENSE)
[certifi](https://github.com/certifi/python-certifi) | MPL-2.0 | [LICENSE](certifi/LICENSE)
[cffi](https://github.com/python-cffi/cffi) | MIT-0 | [LICENSE](cffi/LICENSE)
[clr-loader](https://pypi.org/project/clr-loader/) | MIT | [LICENSE](clr-loader/LICENSE)
[curl-cffi](https://pypi.org/project/curl-cffi/) | MIT | [LICENSE](curl-cffi/LICENSE)
[defusedxml](https://github.com/tiran/defusedxml) | PSFL | [LICENSE](defusedxml/LICENSE)
[lxml](https://github.com/lxml/lxml) | BSD-3-Clause | [LICENSE](lxml/LICENSE) (+ [LICENSES.txt](lxml/LICENSES.txt))
[markdown-it-py](https://github.com/executablebooks/markdown-it-py) | MIT | [LICENSE](markdown-it-py/LICENSE) (+ [LICENSE.markdown-it](markdown-it-py/LICENSE.markdown-it))
[mdurl](https://github.com/executablebooks/mdurl) | MIT | [LICENSE](mdurl/LICENSE)
[packaging](https://github.com/pypa/packaging) | Apache-2.0 OR BSD-3-Clause | [LICENSE](packaging/LICENSE), [LICENSE.APACHE](packaging/LICENSE.APACHE), [LICENSE.BSD](packaging/LICENSE.BSD)
[platformdirs](https://github.com/tox-dev/platformdirs) | MIT | [LICENSE](platformdirs/LICENSE)
[proxy-tools](https://github.com/jtushman/proxy_tools) | MIT | [LICENSE](proxy-tools/LICENSE)
[pycparser](https://github.com/eliben/pycparser) | BSD-3-Clause | [LICENSE](pycparser/LICENSE)
[pygments](https://github.com/pygments/pygments) | BSD-3-Clause | [LICENSE](pygments/LICENSE) (+ [AUTHORS](pygments/AUTHORS))
[pyobjc-core](https://github.com/ronaldoussoren/pyobjc) | MIT | [LICENSE](pyobjc-core/LICENSE)
[pyobjc-framework-cocoa](https://github.com/ronaldoussoren/pyobjc) | MIT | [LICENSE](pyobjc-framework-cocoa/LICENSE)
[pyobjc-framework-quartz](https://github.com/ronaldoussoren/pyobjc) | MIT | [LICENSE](pyobjc-framework-quartz/LICENSE)
[pyobjc-framework-security](https://github.com/ronaldoussoren/pyobjc) | MIT | [LICENSE](pyobjc-framework-security/LICENSE)
[pyobjc-framework-uniformtypeidentifiers](https://github.com/ronaldoussoren/pyobjc) | MIT | [LICENSE](pyobjc-framework-uniformtypeidentifiers/LICENSE)
[pyobjc-framework-webkit](https://github.com/ronaldoussoren/pyobjc) | MIT | [LICENSE](pyobjc-framework-webkit/LICENSE)
[pythonnet](https://pythonnet.github.io/) | MIT | [LICENSE](pythonnet/LICENSE)
[pywebview](https://github.com/r0x0r/pywebview) | BSD-3-Clause | [LICENSE](pywebview/LICENSE)
[qtpy](https://github.com/spyder-ide/qtpy) | MIT | [LICENSE](qtpy/LICENSE)
[rich](https://github.com/Textualize/rich) | MIT | [LICENSE](rich/LICENSE)
[soupsieve](https://github.com/facelessuser/soupsieve) | MIT | [LICENSE](soupsieve/LICENSE)
[typing-extensions](https://github.com/python/typing_extensions) | PSF License | [LICENSE](typing-extensions/LICENSE)

## Summary

Each subdirectory holds the license text for one package in the resolved
runtime dependency closure (`uv.lock`, all supported platforms). The text
mirrors the `LICENSE`/`licenses` files shipped inside the corresponding
PyPI wheel. Additional attribution or notice files that the wheels ship
are included alongside the license where present.

`packaging` is dual licensed: `LICENSE.APACHE` (Apache-2.0) and
`LICENSE.BSD` (BSD-3-Clause); `LICENSE` is the upstream dual-license notice.

Platform-specific runtime dependencies of `pywebview` are included even
though they are not installed on every platform: `pyobjc-*` (macOS),
`pythonnet` and `clr-loader` (Windows), and `qtpy` (OpenBSD).