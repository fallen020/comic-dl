# Development Guide

This is the how-to for working on comic-dl itself, in the order a contributor
meets the parts of it. The pages here cover the whole lifecycle of a change —
from the first bug report to the release that ships it.

## Read these in order

| Page | You need it when |
| :--- | :--------------- |
| [Reporting](reporting.md) | Found a bug, a feature idea, or a security concern. |
| [Workflow](workflow.md) | About to branch, implement, and open a PR. Gates included. |
| [Setup](setup.md) | Checkout doesn't run, or gates need explaining. |
| [Architecture](architecture.md) | Need to know what each module does. |
| [Testing](testing.md) | Writing or fixing tests. Offline-only rules. |
| [Security Testing](security-testing.md) | Your change touches fetching, files, or the DB. |
| [Error Style Guide](error-style-guide.md) | Writing or editing user-visible messages. |
| [Code Review](code-review.md) | A PR of yours gets reviewed — or you review one. |
| [Releasing](releasing.md) | Cutting a version. Maintainer-only in practice. |

## The life of a change

1. **Report or pick** — an issue exists or you file one
   ([reporting.md](reporting.md)). The triage labels
   (`good first issue`, `help wanted`) mark ones new contributors can take.
2. **Branch** — a feature branch forks from `dev`, never from `main`.
3. **Implement** — small, signed commits, each one a Conventional Commit that
   stands on its own.
4. **Gate** — run `test.sh`, `lint.sh`, and `docs.sh` locally until they pass.
5. **Review** — open a pull request against `dev`; reviewers check it against
   the checklist in [code-review.md](code-review.md).
6. **Merge** — the PR is squash-merged into `dev`, one commit per PR.
7. **Validate** — release-ready work is cut to `staging`, proven there, then
   released to `main` by the Monday cadence in [releasing.md](releasing.md).

Each page above turns one of those steps into concrete commands and choices.
[workflow.md](workflow.md) has the branch and gate rules in full;
[releasing.md](releasing.md) covers how a tag actually gets cut.
