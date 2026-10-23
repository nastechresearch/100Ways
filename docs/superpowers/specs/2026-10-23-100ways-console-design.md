# 100Ways evidence surface and admin console — design

Date: 2026-10-23 · Status: approved by the operator in chat (topology: engine-hosted + Vercel mirror)
Decomposes into eight stacked PRs: **A** evidence, **B** delivery, **C** assets + self-check,
**D** console contracts, **E** console shell, **F** console writes, **G** repo intelligence,
**H** mirror + mobile.

## 1. Problem

The engine already decides a great deal — parity, brand, gaps, credentials, reds — but every answer
leaves as stdout, so an operator has to re-read log lines to learn anything. Three specific holes the
operator named:

1. Nothing validates the **URLs** the engine ships: a dead link, a placeholder (`example.com`), or a
   link that still points at the upstream brand goes out unseen.
2. Nothing assembles the run's facts into one **verified** document, so "what is the state of this
   fork" has no single honest answer.
3. Nothing watches **upstream's own surface** for the things the engine has never heard of, so every
   new upstream CLI verb, workflow, env read or entry point is discovered by accident — the measured
   case: upstream added `headings` to `curses_radiolist()` and **deleted** `tests/hermes_cli/test_setup_menu_curses_migration.py`, leaving the fork with an
   orphan test whose stale mock broke PR #166 (`TypeError: _fake() got an unexpected keyword argument
   'headings'`). Branding cannot re-derive a file upstream no longer has, so orphans are permanent
   until something names them.

Then the console: a real admin surface with live animation, AMOLED black, mobile and desktop, Vercel
support, tables and cards the operator can edit, and tools that work on **any** repository — not only
Hermes.

## 2. Existing ground (verified 2026-10-23 at main `81bc536`)

- `hundredways/dashboard.py` — stdlib `ThreadingHTTPServer`, routes `/`, `/api/state`, `/api/gaps`,
  `/api/rules`, `/api/achievements`, `/api/logs`, `/api/events` (SSE), and one write route
  `POST /api/rules` behind `security.verify_token` (sha-256 + `secrets.compare_digest`), a 16 KiB body
  cap, per-field length validation, and additive-only rule overrides. `_HTML` is an inline
  placeholder page; `site/app.js` is 55 lines.
- `site/status.json` — the published evidence contract (`schema`, `generatedAt`, `state`, `gates`,
  `history`, `publication`, `privacy`), written by CI and read by the Pages site.
- `secrets.redact` + `SECRET_PATTERNS`/`EXTENDED_SECRET_PATTERNS`, `operation_safety`, `red_classifier`
  (refuses to name a class without a measurement), `pubconfig` (names only, never values),
  `forklint`, `brandscan.STRONG_BRAND_RE`, `rules.BrandingRules`, `receipts.sha256_file`,
  `coverage.resolve_symbol`, `readme.render_readme`.
- Dependencies: core is `pillow>=10,<12` only; `httpx` is an extra (`ai`, `telegram`); `pyyaml` is
  **dev-only** and absent from the runtime interpreter. Workflow YAML is parsed with regex elsewhere.

## 3. Decisions

- **D1 One app, two modes, zero new build toolchain.** `web/console/` holds hand-written ES modules +
  one stylesheet. The engine server serves it in *live mode*; the same directory deploys to Vercel in
  *mirror mode*. Engine CI is Python-only; a Node build would create a second home for analysis logic.
- **D2 The console renders engine output; it never recomputes it.** Every view is a payload a Python
  builder produces. This is what keeps PRs D–H from drifting from A–C.
- **D3 Writes are PRs, never merges.** The console can open issues/discussions/PRs and edit engine-owned
  config; it cannot merge, tag, release or deploy (publication receipts forbid it, and the operator
  merges).
- **D4 Policy is YAML, read by a tested subset reader.** `hundredways/policy.py` parses only the shape
  our own policy files use (comments, flat keys, `key:`, string lists, quoted scalars) and raises on
  anything outside it. `pyyaml` stays dev-only, and `tests/test_policy.py` cross-checks the reader
  against real `yaml.safe_load` — an oracle, not a self-assertion. A base install gains no dependency.
- **D5 No URL check pretends to have hit the network.** Offline classification (syntax + policy) is
  always available; a live probe is opt-in per run, uses stdlib `urllib.request` with a wall budget,
  and a probe it could not make is reported as `UNREACHABLE`/"not verified", never as a pass.

## 4. PR A — evidence (`feat/engine-evidence-surface`)

- `hundredways/policy.py` — subset reader + `PolicyError`; schema validation for policy files.
- `config/url-policy.yml` — `allowed_hosts`, `upstream_hosts`, `placeholder_hosts`, `brand_hosts`,
  `ignore_paths`, `probe` (`timeout_seconds`, `max_urls`).
- `hundredways/urlcheck.py` — extract URLs from markdown/HTML/plain text with path:line and the
  surrounding span; classify `OK` / `BROKEN` / `PLACEHOLDER` / `BRAND_VIOLATION` / `UNREACHABLE`;
  render suggestions (the concrete replacement the brand map implies). Deterministic ordering.
- `hundredways/verified_report.py` — one markdown document: Summary, Delta, Gates, Credentials,
  URLs, Lint, Ways, Orphans, Receipts. Every section states what it measured and how; anything it could
  not measure prints `not verified: <the measurement that is missing>` instead of a guess. All strings
  pass `secrets.redact`; each section carries a `sha256` of its own inputs. Byte-identical for
  identical inputs (no clock, no dict order).
- `hundredways/adapters.py` — discover upstream's surface from a checkout: CLI verbs (AST over
  `add_parser`/click decorators), workflow names + triggers, env-var reads, entry points, package
  metadata; public-signature inventory for modules the engine patches; diff against a stored baseline
  snapshot; classify each delta `ADOPT` / `BRAND` / `RULE` / `IGNORE`; **report fork-only orphans**
  (a fork path whose upstream twin no longer exists) because those are permanent divergence.
- CLI verbs `urls`, `verify-report`, `surface`.
- Tests: real `http.server` (stdlib) serving 200/301/404 for the broken/redirect cases; real policy
  YAML files; real synthetic upstream trees parsed by AST; the orphan case built as a fork-only file
  against a git fixture where upstream deleted the twin.

## 5. PR B — delivery

`delivery.py` (issue + discussion drafts, GraphQL category lookup, idempotent by title hash, dry-run by
default), `errorfeed.py` (poll new failures since a cursor, classify with `red_classifier`, persist
dedupe state, `--once`/`--interval`), `telegram_tables.py` (width-capped monospace tables for the plain
text surface). Verbs: `issues`, `discuss`, `suggest`, `errors`, `table`.

## 6. PR C — assets and self-check

`assetforge.py` (deterministic brand SVG icons, picture-replacement plan, validation: well-formed XML,
no external references, no upstream brand marks, idempotent by content digest + receipt),
`config/asset-policy.yml`, `.github/workflows/stage-selfcheck.yml` wired into `ci.yml` and
`all-checks-pass` (ways = 200, every workflow parses and keeps `workflow_call`, URL policy resolves
offline, engine lint simulation, README coverage), and `readme.py` gaining a real coverage metric:
`readme --check-coverage --min 0.7` counts documented capabilities against the live registry and the
CLI surface, so 70% is measured, not asserted.

## 7. PR D — console contracts

- Payload envelope: `{"schema": "100ways.console.<view>/v1", "generatedAt", "source": {...},
  "mode": "live"|"mirror", "verified": bool, "data": ..., "limits": {"truncated": bool, "max": int}}`.
  `truncated: true` is explicit; a silent cut is a defect.
- Views: `state`, `gates`, `gaps`, `diffs`, `urls`, `brand`, `assets`, `surface`, `orphans`, `errors`,
  `suggestions`, `lint`, `readme`, `repo-intel`, `receipts`.
- `hundredways/console.py` builds them from engine objects; `100ways console export --out DIR` writes
  the same views as static JSON for the mirror; each is capped so a Pages artifact stays small.
- Event envelope `100ways.event/v1` on the existing SSE route.

## 8. PR E–F — the console

Layout: AMOLED `#000` base, one accent, real type scale, `prefers-reduced-motion` honored. Sections:
Overview (live gates), Gaps/Diffs, Brand, URLs, Assets, Surface + Orphans, Errors, Suggestions, Repo
Intel, Receipts. Two interaction primitives everywhere: **suggestion cards** (engine proposes with its
evidence and names the command it maps to; operator accepts, edits or rejects) and **editable tables**
(inline edit only where the engine accepts that verb). Mobile: bottom tab bar, stacked cards, 44 px
targets. Desktop: sidebar + dense tables. Animations are driven by SSE events, not timers.

Writes (`POST`, bearer-guarded by `HUNDREDWAYS_ADMIN_TOKEN`, 16 KiB caps per route, allowlisted actions):
rule/token overrides (exists), ignore globs and locked-path additions, asset upload/replace (base64
JSON body, magic-byte sniffing, extension whitelist, hard byte cap, sha256 receipt, idempotent by
content digest, validated by `assetforge`), url-policy edits, errorfeed cursor reset, issue/discussion/
PR drafts through `gh`. **A write that would weaken a guard is refused**: removing a birth-commit-required
token, lowering `threshold`, dropping a CI cap or a scanner budget. Every accepted write emits an audit
line plus the diff it produced. Mirror mode hides every mutation and offers the command instead.

## 9. PR G — repo intelligence

`hundredways/repo_intel.py`: identity (npm `name`/`bin`/entry points, `pyproject`, lockfiles), import
and env names, secret **names** only, URL inventory, workflows + runner labels, docker images, brand
candidate runs (`brandscan`), and a rename plan ordered by risk then measured count. Works on any
checkout, not only the Hermes fork; read-only; feeds the `repo-intel` view.

## 10. PR H — mirror and Vercel

Static bundle + `vercel.json`; the CI job that exports it; a live-mode note that the browser may point
at an engine URL the operator types in, with the token held in the browser only. Publishing/deploying
is an operator action — the build ships deployable and stops at the publish button.

## 11. Testing (no fake tests)

- Real `http.server` for URL classification; real YAML files cross-checked against `yaml.safe_load`;
  real git fixtures for `adapters`/`repo_intel` (synthetic `package.json` projects), so inventories are
  measured from disk.
- Route tests over a live server on an ephemeral port with a real token: 400/403/413 and the success
  path writing real files into `tmp_path` and returning a verifiable digest.
- Cross-language contract test `tests/test_console_static.py`: parses the shipped JS and proves every
  `/api/...` it calls is a registered route and every `payload.field` it reads exists in the Python
  builder's output; plus CSP/no-inline-script, a byte budget, and no upstream brand token in the bundle.
- Real browser verification against the running server (Qoder browser connector) for the golden path
  and the error paths, with mobile and desktop screenshots before anything is called working.
- Invariants held by every PR: ways registry stays 200, `ruff` baseline does not grow, the full suite
  passes, README coverage stays ≥ 0.7.

## 12. Out of scope

No automatic merge, tag, release or deploy. No new core runtime dependency. No timeout, cap, budget or
scanner raised to make a lane green. No secret values in any payload, receipt or report. No second
implementation of analysis in JavaScript.
