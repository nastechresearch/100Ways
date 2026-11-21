# nastech-agent Release Gate Known Fixes

Durable patterns from nastech-agent stable rc gate triage. Load `100ways` and `100wayskills` before acting. Do not weaken tests, receipt semantics, or gate checks without evidence.

## 1) PM worker post-result wait (TimeoutExpired)

- **Symptom**: PM operation succeeds but worker teardown is slow; old `process.wait(timeout=5)` raises `TimeoutExpired`, surfacing as update/test failure (e.g. pause_durable/worker timeout cascades).
- **Fix**: after result received, `process.wait(timeout=30)`; on timeout `terminate -> wait 5s -> kill -> wait 5s`; only raise if clean wait returns nonzero. Apply same guarded wait in finally cleanup.
- **Evidence**: `pm/client.py`, regression `tests/pm/test_client_worker_wait.py`.
- **Guardrail**: do not mask real worker failure; nonzero clean exit still raises.

## 2) Historical updater takeover silent hang (Mode A receipt hang)

- **Symptom**: desktop `nastech-desktop-app-update` (v2026.8.11 -> HEAD) fails with "no successful update completion receipt; leaving the install untouched". `desktop-update-handoff.log` ends at `-> Handing off to the new updater (package manager) for the rest of this update...`; orphan python/nastech processes killed at timeout.
- **Root cause area**: `nastech_cli/_old_updater.py` -> `nastech_cli/_update_takeover.py` prepare/sync_venv/ensure_tools_for_sync; takeover stdout piped through _run_child, silence means takeover hung before writing result.
- **Fix**: takeover startup marker + 25-min faulthandler watchdog dumping all thread stacks and `os._exit(2)`; PM wait hardening; Mode A driver timeout 1800000ms -> 2100000ms to match 35-min open-app-update window.
- **Guardrail**: never weaken receipt/completion semantics. Next gate run should produce stack dump if hang persists.

## 3) Windows open-app-update historical installer churn

- **Symptom**: post-update read-only install verification fails after handoff; installer-generated lockfile/CRLF churn autostashed/restored as conflict on large diff.
- **Fix**: `Clear-HistoricalInstallerChurn` at start of `Invoke-GuiUpdateDesktopRoute` in `tests/install/windows-e2e.ps1`. Safe contract: remove only installer-generated state, hard-fail on genuine local changes.
- **Guardrail**: do not auto-discard arbitrary user changes.

## 4) Windows crash_cells timeout/SIGKILL cascade

- **Symptom**: `NASTECH_TEST_FILE_TIMEOUT=3600` hit SIGKILL at ~3603s; leftover PowerShell/teardown causes cascading failures.
- **Fix**: raise to 5400s in `.github/workflows/windows-install-update-e2e.yml` (<120m job cap). Evidence required before raising.
- **Guardrail**: do not hide hangs by making timeout unbounded.

## 5) macOS installer-script quiescence contention

- **Symptom**: HEAD->NEXT contention flake; stale checkout lock/sidecar or in-flight update races served-ref advance/update/capture.
- **Fix**: git-common-dir-anchored `checkout_lock_held` + `wait_for_update_quiescence` with call sites before served-ref advance/update/capture in `tests/install/installer-script-e2e.sh`.
- **Guardrail**: treat as flake only with evidence; harden quiescence.

## 6) Release versioning test contract drift

- **Symptom**: `test_release_version_from_ref.py` mock drifts from `published_release_identity` (`gh api --paginate .../releases?per_page=100`, raw decode, no `--slurp`).
- **Fix**: mock accepts `--paginate`, returns `json.dumps(releases)`. Update contract tests/mocks with behavior changes; never weaken assertions.

## RC gate triage commands

```bash
gh run view <run> -R nastechresearch/nastech-agent --json status,conclusion,jobs
gh api repos/nastechresearch/nastech-agent/actions/runs/<run>/attempts/<n>/jobs?per_page=100&page=<p>
gh api repos/nastechresearch/nastech-agent/actions/jobs/<id>/logs
# 502 workaround: per_page=50&page=3/4 for original page 2
```

## Classification rubric

- **Deterministic/contract drift**: fix code/test/mock together.
- **Harness contention/flake**: reproduce evidence, harden, do not paper over.
- **Product/runtime hang or missing receipt**: add diagnostics, fix root cause, never weaken completion semantics.
- **Timeout budget**: raise only with evidence and within job cap.
- **Cascade**: Source matrix gate fails when legs fail; fix underlying legs.

## Release safety

- Review-only handoff; no automatic merge/tag/release/deploy.
- No attribution hints in code/comments/commits/PR/release notes.
- Verify publish by API/check-runs/state only: non-draft/non-prerelease, builds table, R2 assets 200, check-runs green.
