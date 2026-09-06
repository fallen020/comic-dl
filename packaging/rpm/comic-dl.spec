%global curl_cffi_version 0.16.1

# The vendored curl_cffi .so is a prebuilt library with no debug source;
# skip the find-debuginfo/eu-strip step entirely.
%define debug_package %{nil}

Name:           comic-dl
Version:        0.0.1
Release:        1%{?dist}
Summary:        Download comic and manga galleries as CBZ archives
License:        MIT
URL:            https://github.com/fallen020/comic-dl
Source0:        %{name}-%{version}.tar.gz
BuildRequires:  python3 >= 3.11
BuildRequires:  python3-pip
BuildRequires:  unzip

Requires:       python3 >= 3.11
Requires:       python3-beautifulsoup4
Requires:       python3-lxml
Requires:       python3-rich
Requires:       python3-platformdirs
Requires:       python3-defusedxml
Requires:       python3-certifi
Requires:       python3-cffi

%description
comic-dl downloads comic and manga galleries from supported websites and
compiles them into CBZ, ZIP, and CBT archives. It automates scraping,
downloading, verifying, and packaging - one command per gallery or series.

The package is architecture-specific: the vendored curl-cffi wheel bundles
native libcurl-impersonate shared objects for the build architecture (x86_64
or aarch64). Build the RPM on the matching architecture (amd64 -> x86_64,
arm64 -> aarch64).

%prep
%setup -q -n src

%build
python3 -m pip wheel --no-deps --no-cache-dir --wheel-dir .wheel .
python3 -m pip wheel --no-deps --no-cache-dir --wheel-dir .wheel "curl-cffi==%{curl_cffi_version}"

%install
mkdir -p %{buildroot}/usr/lib/comic-dl/site-packages %{buildroot}/usr/bin
(cd .wheel && for w in comic_dl-*.whl curl_cffi-*.whl; do unzip -qo "$w" -d %{buildroot}/usr/lib/comic-dl/site-packages; done)
cat > %{buildroot}/usr/bin/comic-dl <<'EOF'
#!/bin/sh
PYTHONPATH=/usr/lib/comic-dl/site-packages${PYTHONPATH:+:$PYTHONPATH} \
    exec /usr/bin/python3 -m comic_dl "$@"
EOF
chmod 0755 %{buildroot}/usr/bin/comic-dl

%files
%license LICENSE
/usr/bin/comic-dl
/usr/lib/comic-dl/site-packages/*

%changelog
* Thu Sep 04 2026 Comic Downloader contributors <maintainers@users.noreply.github.com> - 0.0.1-1
- Initial RPM packaging.
