# Reporting

How problems reach the project, what makes a report actionable, and where each
kind of report goes. The rule that runs through everything: **read the
troubleshooting guide before filing.** Many "bugs" are config, rate limits, or
a site that blocked the scrape.

## Routing: who gets what

| Topic | Where |
| :---- | :---- |
| "How do I..." setup/usage questions | [Discussions][discussions-link] |
| Ideas / feature requests | Discussions first, issue after discussion |
| Bugs | [Bug report template][bug-template-link] |
| Security vulnerabilities | Private [security advisory][security-advisory-link] — never public |

[discussions-link]: https://github.com/fallen020/comic-dl/discussions
[bug-template-link]: https://github.com/fallen020/comic-dl/issues/new?template=bug_report.yml
[security-advisory-link]:
  https://github.com/fallen020/comic-dl/security/advisories/new

`blank_issues_enabled` is false: issues can only be filed with the templates.
Bug reports are pre-labeled `bug needs-triage`; every issue starts as
`needs-triage` until a maintainer reproduces it.

## Filing a bug report

The [bug report issue template](https://github.com/fallen020/comic-dl/issues/new?template=bug_report.yml) asks for:

1. **Description** — what went wrong, what you expected instead.
2. **Steps to reproduce** — the *exact* command, the *exact* output. If you
   cannot paste both, the report is not actionable.
3. **Affected URL** — the gallery/series URL, with personal data scrubbed.
4. **Platform** — OS + architecture (`Linux x86_64`, `macOS arm64`).
5. **Install method** — prebuilt binary, `pip`/`uvx`, source checkout.
6. **Debug output** — rerun with `-vvv` and paste the tail including the
   traceback. Conventions: run `comic-dl self version` first so the report has
   a version, not "latest".

How to produce the debug run:

```bash
comic-dl self version
comic-dl -vvv -u "<URL>" 2>&1 | tail -50
```

Two pieces of advice that make every report land:

- **Sanitize before pasting.** URLs can contain cookies or tokens. The
  template says this; the maintainer means it.
- **Do not include a real work's identifier** if it can be avoided — use
  synthetic slugs. This is a legal constraint from
  [CONTRIBUTING.md](../../CONTRIBUTING.md), not a style preference.

## When not to file

- A single site is down or slow → check Discussions first; it is usually the
  site, not comic-dl (rate-limit behaviour on a source site is explicitly
  [out of scope for security reports](security-testing.md)).
- A site changed layout → a bug report with the URL and `-vvv` output is
  welcome, but note it in the title ("sudden 404") so triage sees the trend.
- 403 on a gallery → the [troubleshooting guide](../troubleshooting.md) has a
  decision tree; report it only if the guide's steps did not help.

## Feature requests

Start a Discussion, not an issue. A feature that survives discussion gets an
issue with the concrete design; a feature that starts as an issue without
discussion usually gets closed as a discussion pointer. That is intentional —
the scraper set is curated, and a new generic "download this site too" without
a plan is not actionable.

## Security reports

Security issues go through the **private advisory flow**
([`SECURITY.md`](https://github.com/fallen020/comic-dl/blob/main/SECURITY.md)):

- Report privately via [Security → Report a vulnerability](https://github.com/fallen020/comic-dl/security/advisories/new).
- Include version/commit, reproduction steps, and the impact you believe the
  issue has.
- Handling is **coordinated disclosure**: nothing public until a fix lands.
- Timelines (from `SECURITY.md`): acknowledgement within **3 business days**,
  initial assessment within **5**, Critical/High fix as a dedicated patch
  release within **7** days of confirmation; Medium/Low ship in the next
  planned release.

What is **not** in scope (from `SECURITY.md`): availability/rate-limiting
behaviour from batched downloads, absence of credentials (the project stores
none), and the behaviour of third-party sites themselves.

## What happens after you file

- Every issue lands with `needs-triage`; a maintainer reproduces it, then
  triages to `bug`/`enhancement` + labels from the fixed vocabulary
  (`bug`, `enhancement`, `documentation`, `good first issue`, `help wanted`,
  `duplicate`, `invalid`, `question`, `wontfix`, `accessibility`).
- A `duplicate` close is not a dismissal — the older issue gets the new
  information merged into it.
- A `wontfix` close means deliberately out of scope; reopen only if new facts
  change the scope.
