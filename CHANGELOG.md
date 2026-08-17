# Changelog

All notable changes to 100Ways are recorded here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

### Fixed
- Pages workflow 429 cascade: shallow clone + ancestry recovery triggered
  `git fetch --unshallow` that github.com rate-limited. Added a `--pages`
  flag to `commit_stream.inspect_commit_stream` that skips the unshallow.
  Pages now also emits a `DEGRADED` payload when the source fetch fails
  instead of dying.
- `httpx` moved from optional extras to `[project.dependencies]`. The
  Telegram bot and Pages builder import it unconditionally; a fresh
  `pip install -e .` (no extras) was `ImportError`-ing at runtime.
- Fine-grained GitHub PATs (`github_pat_`, `gho_`, `ghs_`, `ghr_`,
  `glpat-`) added to the redaction patterns. They would otherwise leak
  through any log that landed in a published Pages payload.
- Operator precedence bug in `actions_analyzer.py`'s 429 `elif`: wrapped
  in explicit parens. Worked by accident; fragile to future refactors.
- `site/status.json` is generated on every workflow run; removed from
  the repo and added to `.gitignore`.
- The hardened weekly gate failed on a fresh project with a 1000+ commit
  backlog because `freshness_ok` required the snapshot's recorded SHA to
  match the live upstream HEAD. Added a `bootstrap=True` flag that
  accepts one jump with `review_required=True`. Strict mode resumes
  after the bootstrap is merged.
- `actions/cache` bumped to v6 by dependabot (#187) which broke the
  Telegram memory cache restore. Pinned all four `actions/cache`
  references to v4.2.0 (SHA-locked) and updated `.github/dependabot.yml`
  to ignore future v5/v6 bumps.
- `int(pending_commits)` in the Pages builder crashed on non-numeric
  input. Added `_safe_int()` fallback.
- Default paths in `hundredways/cli.py` were hard-coded to
  `/home/nascode/Documents/A1/...`. Now uses XDG defaults
  (`~/.local/share/100ways/<name>`); env vars still win.

### Added
- `count_rate_limit_signals()` and `rate_limit_budget_exceeded()` in
  `actions_analyzer.py`. Default budget = 3.
- Bounded exponential backoff (3 attempts, 10s/40s) inside
  `_recover_complete_history` for the main pipeline.
- New `workflow_dispatch` inputs on `ci.yml`: `bootstrap`,
  `threshold_override`.
- New `.github/workflows/weekly-sync.yml` cron at 03:00 UTC Sunday.
  Plan-only; never publishes a PR.
- `ruff check` and `ruff format --check` in `stage-lint.yml`.
- `pytest-cov` with `--cov-fail-under=70` in `stage-tests.yml`.
- `Makefile` with `install`, `test`, `lint`, `format`, `coverage`,
  `update`, `forkcheck`, `weekly`, `clean` targets.
- `docs/architecture.md` with mermaid diagrams of the 19-stage pipeline
  and the surfaces.
- `docs/operations.md` covering manual runs, alert interpretation,
  common failure modes, rollback.
- `docs/security-model.md` documenting threat model, trust boundaries,
  redaction chokepoint, publication boundary, Telegram boundary,
  gate boundary.
- 24 test cases in `tests/test_pages_payload.py`.
- 8 test cases in `tests/test_analyzer_redaction.py`.
- 3 test cases in `tests/test_weekly_sync.py` (bootstrap mode).
- 2 test cases in `tests/test_commit_stream.py` (backoff).

## [0.1.0] - 2026-08-13

Initial public release.
