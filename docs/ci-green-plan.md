# CI-green plan: making the branded sync (`100WAYS` → nastech-agent) pass

Status: working document for branch `fix/ci-green` of `nastechresearch/100Ways`.

## How the fork stays fixed (the durable loop)

1. This branch changes the **engine** (`hundredways/*` + `tests/`), never the
   generated candidate. The engine is the single source of truth for every
   transform and reconcile.
2. When this branch is reviewed and merged to `100Ways` `main`, the next
   scheduled/manual sync regenerates the branded tree from the latest upstream
   `NousResearch/hermes-agent` and force-pushes it to
   `nastechresearch/nastech-agent` branch `100WAYS` (PR #128).
3. Because the fixes live in the engine, every future sync carries them; a
   hand-edit on `100WAYS` would be overwritten (this is why prior branch-only
   fixes regressed).

Rule kept throughout: **no guard or scanner is weakened**. Each fix either
corrects the transform, corrects engine-owned data, or stops for human review.

## Fixed on this branch (unit-verified)

| Failing check | Root cause | Fix (engine) | Verification |
|---|---|---|---|
| `Docs Site / docs-site-checks` | Branding renames change skill slugs, so inherited upstream-sorted catalogs/sidebar drift out of order; a fork-only skill (`blender-mcp`) is missing from the sidebar; the trimmed `nastech-agent` description is not propagated to its catalog row/page. | `_reconcile_skill_docs_order`, `_reconcile_fork_skill_sidebars`, `_reconcile_skill_description_hardline` (now returns every generated artifact it touches). | Reproduced `generate-skill-docs.py` output for all 4 files byte-for-byte; running the generator again is a no-op. |
| `Python tests / Run tests (1/2, 2/2)` | Upstream raised `TEST_WORKERS` from `96` to `32`; the fork keeps `32` on a standard 4-vCPU `ubuntu-latest`, so 32 per-file pytest subprocesses starve the CPU and timing tests hit the wall-clock guard. | Generalize `_normalize_test_workers`: cap any bare integer above `_TEST_WORKERS_CAP = 8` (`96→8`, `32→8`); quoted/expression values untouched. | Transform check on the real upstream workflows; `hundredways/rules.py` tests. CI is the final judge (fallback: cap `4`). |
| `Vercel – nastech-agent`, `Vercel – nastech-agent-bootstrap-installer` | Root `prepare` runs `lefthook install` whenever `.git` exists. Vercel clones with `.git` but skips devDependencies, so `lefthook` is absent and the install exits `127` before the build. | `_reconcile_vercel_prepare`: probe `lefthook --version` and exit 0 only when it is genuinely absent; a present-but-failing install still propagates. Local hooks unchanged. | Structural + `node -e` behaviour test (`.git` present, no `lefthook` → exit 0). |
| `Icon assets freshness` | The engine's `config/owned-assets/manifest.json` overrides 17 paths that `scripts/generate_icons.py` also regenerates, so committed bytes never equal regeneration. | Removed the 17 generator-owned keys from the registry; kept the 17 genuine brand assets. The generator becomes the authoritative producer for those paths. | Subagent repro: `generate-icons.mjs --check` passes once the registry no longer overrides; candidate regen equals upstream bytes for all 17. |
| `Python lints / Windows footguns (blocking)` | A file upstream deleted (`apps/desktop/electron/update-relaunch.ts`) is retained by the sync and carries an embedded `#!/bin/bash` payload; the shebang checker flags it. | `_reconcile_portable_shebang`: rewrite a fixed `#!/bin/bash` payload to `#!/usr/bin/env bash` (corrective, matches the guard). | Real candidate: checker `1 violation → 0`; idempotent unit test. |

## Remaining work (needs verification or a human decision)

| Failing check | Root cause | Plan |
|---|---|---|
| `pinned-source-validate` | `plugin-catalog-ci.yml` data-only admission guard trips on a full-sync PR; there is no upstream escape hatch. | Add `_reconcile_plugin_catalog_admission` that exempts only same-repo heads carrying the maintainer `full-sync` label. **Verify** whether the `pull_request` event evaluates the merge-commit workflow file (which would apply before this branch lands). |
| `Python lints / code health ratchet (blocking)` | The candidate imports real upstream growth vs the fork's stale `main`; the gate is PR-only and compares against `main`. No engine reconcile causes it; both engine reconciles add zero lines. | **Human review / upstream** — the growth is genuine. Non-weakening options: run the ratchet on pushes to `main`, bump the required-check version, or an upstream commit that brings the files back under cap. Do **not** touch enforcement/caps/excludes. |
| `JS & TS checks` | (a) skin test `expected 'nastech' to be 'classic'`; (b) `_keys.desktop.json` / `_keys.tui.json` unsorted (i18n:keys:check); (c) `src/typed.ts(1,14) TS2322`; (d) `update-ui.test.mjs` / `bundle-smoke-metadata.test.mjs` timeouts. | (a)/(b) engine transforms (skin alias reconcile, i18n key sort) once root-caused with the failing logs; (c) fix the source type; (d) likely the same CPU-starvation class as the Python tests — recheck after the worker cap. |
| `OS-specific tests / Windows-only tests` | Likely the same worker oversubscription (windows job carries `NASTECH_TEST_WORKERS` 16 via an expression) plus host-sensitive tests that also fail on upstream locally. | Re-run after the worker cap; if still red, decide whether to cap the expression value too. |
| `Prebuilt TUI checks` | `hud-modifier-monitor-x11.c` needs X11 headers on the runner; `TS2322`; `npm … only-if-cached` (ENOTCACHED). | Mostly runner/toolchain and the same `TS2322` source fix; re-scope after the above. |
| `Vercel` docs/landing pages | The landing/docs deploy only needs to build the `web`/`website` workspace; root Node tooling should not run at deploy time. | Add `vercel.json` (or Dashboard install command `npm install --ignore-scripts`) so the deploy builds the right workspace explicitly. Docs remain on the Pages workflow. |

## Verification steps for the next sync

1. `PYTHONPATH=. python3 -m pytest -q` in the engine → green.
2. Apply the engine to the latest upstream in an isolated worktree and confirm:
   - `NASTECH_TEST_WORKERS` reads `8` in `tests.yml`;
   - `generate-skill-docs.py` produces no diff;
   - `scripts/check_bash_shebangs.py` → 0 violations;
   - `scripts/generate-icons.mjs --check` → 0 diff;
   - `node -e` on the `prepare` script exits 0 without `lefthook`.
3. Let CI judge the timing-sensitive suites; if they still flake, reduce the
   worker cap to `4` (strict 1:1 with 4 vCPU) rather than loosening any test.
