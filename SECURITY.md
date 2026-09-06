# Security Policy

## Reporting a Vulnerability

Security issues are handled privately. Please report vulnerabilities by
opening a private security advisory rather than a public issue:

**[Report a vulnerability](https://github.com/fallen020/comic-dl/security/advisories/new)**
(GitHub → Security → Report a vulnerability)

No PGP key is used; all reports go through the private advisory.

When you file an advisory, please include:

- The version or commit you found the issue in (from `--version` or `uv lock`)
- The steps to reproduce, including the affected site / URL if applicable
- The impact you believe the issue has

Reports are handled under **coordinated disclosure**: no public disclosure is
made until a fix is available. Once a fix ships you will be credited in the
release notes and, if you wish, as a co-author of the fix.

### Response timeline

| Stage | Target |
| :---- | :----- |
| Acknowledgement | within **3 business days** |
| Initial severity / impact assessment | within **5 business days** |
| Fix for Critical / High severity | dedicated patch release within **7 days** of confirmation |
| Fix for Medium / Low severity | next planned release |

## Supported Versions

| Version | Supported |
| :------ | :-------- |
| Latest release (`0.x`) | Yes |
| Older releases | No — please upgrade |

Security fixes are released against the latest tagged version. Prebuilt
binaries attached to the GitHub Release and the Python package are covered by
the same release.

## Scope

This project downloads public galleries from third-party sites. The following
are **not** treated as in-scope vulnerabilities:

- Availability / rate-limiting behaviour caused by batched or high-concurrency
  use on a source site.
- Absence of authentication, credentials, or secrets — none are stored.
- Behaviour of the third-party sites themselves.

## Recommended practices

- Only run binaries or wheels you obtained from the official GitHub Release
  (or the PyPI distribution, once publishing is enabled) — never executables
  from unofficial mirrors, gists, or chat channels.
- Verify release integrity against the `SHA256SUMS` file attached to every
  GitHub Release.
- Pin releases in your scripts rather than using floating tags on
  non-released heads.