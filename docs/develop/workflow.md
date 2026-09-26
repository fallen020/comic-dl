# Contribution Workflow

This page walks a change from issue to merged pull request. Each section is
what the steps before it actually involve, with the commands and gates spelled
out. If a command fails or a gate rejects your change, the fix usually lives
one section earlier — run the gates before every push, not only at the end.

## 0. Branches and what goes where

Three branches live forever:

- **`dev`** — unstable. Every change lands here via a squash-merged PR.
- **`staging`** — cut from `dev` when release-ready; proves the release.
- **`main`** — production, releases only. Never committed to directly.

Feature branches fork from `dev`. Never base work on `main` or `staging`:
your code would miss everything merged since the last release.

## 1. Pick or file an issue

Start from an issue. Good candidates carry the `good first issue` or
`help wanted` labels. If the change is bigger than a one-file fix, open an
issue first and describe the approach; reviewers will not accept an
unrequested feature PR that contradicts active discussion.

Search [issues](https://github.com/fallen020/comic-dl/issues) for a duplicate
before filing. Report against the `.github/ISSUE_TEMPLATE/` forms — they
ask for the platform, install method, debug output, and reproduction steps.
See [reporting.md](reporting.md) for what makes a report actionable.

## 2. Branch

```bash
git checkout dev
git pull --rebase
git checkout -b fix/descriptive-name
```

A `fix/`, `feat/`, `docs/`, `refactor/`, or `chore/` prefix keeps PR titles
readable and matches the Conventional Commit that the squash merge will use.

## 3. Implement

Keep the change small enough to review in one sitting. Multiple themes or
unrelated fixes belong in separate PRs — one PR, one concern.

Signing:

- Commits are GPG-signed (`git commit -S`). Set `user.signingkey` once in
  `git config --global`. Unsigned commits are rewritten (`git commit --amend -S
  --no-edit`) or lost during the squash merge.
- Adoption of a fork means rebasing onto `dev` (`git pull --rebase upstream
  dev`) and re-signing — rebase drops signatures.

Commit messages use [Conventional Commits](https://www.conventionalcommits.org):

```text
docs: explain the SSRF guard's redirect re-validation
```

Subject lines are lowercase and imperative; a body explains *why* when the
subject cannot. One small commit per logical step is fine — the squash merge
collapses them into one anyway.

Never commit secrets, `.env` files, or packaging output. If you smell a
credential in history, stop and ask a maintainer.

## 4. Run the gates

The gates are the contract — CI runs exactly these scripts, so a green local
run means a green PR (modulo the OS/Python matrix).

| Gate | Command | What it checks |
| :--- | :------ | :------------- |
| Tests | `./scripts/test.sh` | Offline test suite, parallel |
| Lint | `./scripts/lint.sh` | ruff, mypy, bandit, ShellCheck |
| Docs | `./scripts/docs.sh` | Doc-table drift, mirror links, Markdown lint |
| Build | `./scripts/build.sh` | sdist + wheel build cleanly |

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/docs.sh
```

Test is the loop you will repeat; it is fast enough (the suite is offline and
mocked) to run after every meaningful edit.

If you changed a scraper or added a site:

```bash
uv run pytest tests/scrapers/sites/ -q
uv run python scripts/update-sites-docs.py
```

The second command regenerates the supported-sites tables in `docs/` and the
docs site — commit the result. `docs.sh` fails if you do not.

## 5. Open the pull request

Push the branch and open a PR **against `dev`** using the template. The
template asks for:

- **Type** — new scraper, site fix, bug fix, docs, refactor, dependency, or
  breaking change.
- **Site(s) affected** — helps reviewers triage; "none" is fine.
- **Checklist** — which gates you ran and whether docs changed.
- **Reviewer notes** — known limitations, edge cases, what deserves extra
  attention.
- **Related issue** — `Closes #NN` links the PR to the issue it fixes.

Run everything the checklist lists before ticking it. A checklist box ticked
without the run is the fastest way to annoy a reviewer.

PR title should use the same Conventional Commit prefix the squash merge will
keep (`fix:`, `feat:`, `docs:`...).

## 6. The review loop

A reviewer will add inline comments. Treat each one as a question, not a
judgment. The merge target is the checklist in
[code-review.md](code-review.md) — reviewers run it mechanically, so fix what
it flags and re-run the gates.

Push fixes as new commits; do not force-push history while review is open. A
fresh commit keeps the conversation readable (the squash merge flattens them
anyway).

## 7. Merge

Merge happens as a **squash merge** into `dev`: one commit per PR, with the
PR title as the subject. This is done by the reviewer or a maintainer — you do
not need to rebase your branch to a single commit first.

`dev` is deliberately unstable: what merged this week is not what ships next
week. That is a feature. See [releasing.md](releasing.md) for how a release is
cut from `dev`.

## Common failure modes

| Symptom | Cause | Fix |
| :------ | :---- | :-- |
| `uv run` misses a package | stale `uv.lock` | `uv sync --extra dev --locked` |
| `docs.sh` table drift | site added w/o regenerated docs | `uv run python scripts/update-sites-docs.py` |
| commit shows `unverified` | signature missing after rebase | `git commit --amend -S --no-edit` |
| tests fail only on Windows/macOS | path/encoding assumption | reread testing.md's console rule |
| `mypy` flags `Any` | an unchecked return | type the return; no casual `# type: ignore` |
