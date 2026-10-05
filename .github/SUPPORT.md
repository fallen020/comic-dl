# Getting help with comic-dl

> [!CAUTION]
> Found a security vulnerability? Report it privately via a
> [security advisory](https://github.com/fallen020/comic-dl/security/advisories/new)
> (needs a GitHub account). Never open a public issue for a vulnerability —
> that puts users at risk before a fix exists.

## Docs

- [README](https://github.com/fallen020/comic-dl/blob/main/README.md) — install, quick start, common commands
- [Downloading](https://github.com/fallen020/comic-dl/blob/main/docs/usage/download.md) — download options
- [Supported sites](https://github.com/fallen020/comic-dl/blob/main/docs/reference/supported-sites.md) — which domains work and their URL patterns

If the docs don't answer your question, open a [feature request](https://github.com/fallen020/comic-dl/issues/new?template=feature_request.yml)
describing what's missing — that is how gaps get found.

## Bugs

Search [open issues](https://github.com/fallen020/comic-dl/issues) first —
duplicates get closed.

If nothing matches, open a [bug report](https://github.com/fallen020/comic-dl/issues/new?template=bug_report.yml).
The template asks for version (`comic-dl self version`), platform, install
method, command, URL, and logs. Re-run with `-vvv` for a traceback, and
prefer `--debug-file PATH` over pasting terminal output.

`-vvv` prints request/response headers, so redact before posting: cookies,
tokens, auth headers, usernames, and local paths. In URLs keep the domain
and path (needed to reproduce) and strip query tokens and session IDs.

A domain outside [Supported sites](https://github.com/fallen020/comic-dl/blob/main/docs/reference/supported-sites.md)
is a feature request, not a bug — open a [site request](https://github.com/fallen020/comic-dl/issues/new?template=site_request.yml)
instead.

## Response time

This is a volunteer-run project: issues are answered on a
best-effort basis with no guaranteed response time.
