# Contributing to OpenMagpie

Thanks for helping out. This page covers the contribution flow, the branch
naming convention, and how to run the checks locally so your PR is green
the first time.

## Flow

1. **Fork** the repo (external contributors) or create a branch (maintainers).
2. Branch from `main` using the naming convention below.
3. Make your change; keep it focused (one concern per PR).
4. Run the checks locally (`make local-check`, plus `make hooks` once so the
   pre-commit hooks run on every commit).
5. Open a PR against `main`. CI (`lint` + `test`) must pass; `main` only
   takes changes through a PR.

## Branch naming

Branches are **`<type>/<kebab-slug>`**: a Conventional-Commits type, a slash,
and a short kebab-case description. `main` and release-please's own release-PR
branches (`release-please--…`) are exempt.

```
feat/leaf-only-action-cli     fix/poll-lock-lease     ci/branch-naming
```

| type       | when to use it                                          |
|------------|---------------------------------------------------------|
| `feat`     | a new user-facing capability                            |
| `fix`      | a bug fix                                                |
| `perf`     | a performance improvement (behavior unchanged)          |
| `refactor` | restructure code; no behavior or API change             |
| `docs`     | documentation only                                      |
| `test`     | add or correct tests only                               |
| `ci`       | CI / workflows / pipeline config                        |
| `build`    | build system, dependencies, packaging                   |
| `chore`    | maintenance / tooling that doesn't touch src behavior   |
| `style`    | formatting / whitespace only; no logic change           |
| `revert`   | revert a previous change                                |

The slug is lowercase letters, digits, and `- . _`, starting alphanumeric.
Append `!` to the type to mark a breaking change — e.g. `feat!/drop-legacy-api` —
mirroring the `feat!:` commit marker that bumps the version (see below).

This is enforced in two places by the same script,
[`scripts/check-branch-name.sh`](scripts/check-branch-name.sh):

- **pre-commit**: a commit on a misnamed branch is rejected locally.
- **CI**: the `branch-name` job validates a PR's source branch.

Rename a branch with `git branch -m <new-name>`.

## Commit messages

Use the same Conventional-Commits types for commit subjects, e.g.
`feat(watches): add the activity summary`. Not enforced, but it keeps the
history scannable and matches the branch types.

## Versioning and releases

Releases are cut by [release-please](https://github.com/googleapis/release-please)
from the Conventional-Commit history, on two independent tracks:

- **Product** (the server stack: core, web, email-render) is versioned together
  and tagged `v<x.y.z>`. The services deploy as a unit, so they share one version
  (no per-image compatibility matrix to reason about).
- **The `magpie` CLI** (`apps/cli`, published to PyPI as `openmagpie`) versions on
  its own track, tagged `cli-v<x.y.z>`. It talks to the server over a versioned API
  (`/v1/...`), so CLI/server compatibility is governed by the **API version, not the
  build number** — a CLI works with any server that still serves an API version it
  speaks.

Pre-1.0 SemVer: `feat:` → minor, `fix:` → patch, a breaking change
(`feat!:` / `BREAKING CHANGE:`) → minor (no major bump until 1.0). So the commit
type sets the next version — `feat(cli):` bumps the CLI, `feat(core):` bumps the
product. release-please keeps an open "release PR" per track with the pending
version + changelog; merging that PR cuts the release (tag + GitHub Release). You
don't tag by hand.

## Running the checks

```bash
make build            # build + start the stack (Django + Postgres + web)
make local-migrate      # migrate, create cache table, bootstrap the OAuth app
make hooks            # install the pre-commit hooks (once)

make local-check        # the full local gate: lint + types + tests
```

`make local-check` mirrors CI: `ruff` (lint + format), `ty` (types),
whitespace + file-length, `makemigrations --check`, and the Django test
suite. See the [README](README.md) for the full dev loop and `make help`
for every target.
