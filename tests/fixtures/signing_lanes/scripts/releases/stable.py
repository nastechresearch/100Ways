"""Reduced fork module: the gate, the smoke policy, and the reconcile anchors.

The gate helpers are the fork's own, because the point of the change is that
the gate must accept a lane the signing credentials left out and still block a
genuine failure. The regions the reconcile rewrites (``admit``, its emit and
its summary line, ``candidate_manifest``, ``require_smokes_match_claim`` and
the ``gate`` branch of ``main``) are copied verbatim from the fork so the
anchors are the real anchors.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from nastech_cli.update_channel import STABLE_TAG_RE  # noqa: F401
from scripts.releases import optional_platforms
from scripts.releases.draft_warning import strip_draft_warning  # noqa: F401
from scripts.releases.optional_platforms import require_targets_match_claim
from scripts.releases.versioning import tag_record  # noqa: F401

DESKTOP_TARGETS = ("windows/x64", "windows/arm64", "macos/x64", "macos/arm64")
OPTIONAL_TARGETS = optional_platforms.TARGETS  # built only when the claim opts in
REQUIRED_DESKTOP_TARGETS = tuple(t for t in DESKTOP_TARGETS if t not in OPTIONAL_TARGETS)
SMOKE_JOBS = {
    "smoke-darwin-arm64": "macOS DMG + ZIP (arm64)",
    "smoke-darwin-x64": "macOS DMG + ZIP (x64)",
    "smoke-win32-arm64": "Windows MSIX (arm64)",
    "smoke-win32-x64": "Windows MSIX (x64)",
}
SMOKE_TARGETS = {"smoke-darwin-arm64": "macos/arm64", "smoke-darwin-x64": "macos/x64",
                 "smoke-win32-arm64": "windows/arm64", "smoke-win32-x64": "windows/x64"}
CANDIDATE_HANDOFFS = ("win32-x64", "win32-arm64", "darwin-x64", "darwin-arm64",
                      "termux", "windows-universal")
CALL_SMOKE_JOBS = {
    "candidates-darwin-arm64": "smoke-darwin-arm64",
    "candidates-darwin-x64": "smoke-darwin-x64",
    "candidates-win32-arm64": "smoke-win32-arm64",
    "candidates-win32-x64": "smoke-win32-x64",
}


def require_success(needs: dict, required: list[str]) -> None:
    if not required or len(set(required)) != len(required):
        raise ValueError("Invalid required-job list")
    failures = [f"{name}={needs.get(name, {}).get('result', 'missing')}"
                for name in required if needs.get(name, {}).get("result") != "success"]
    if failures:
        raise ValueError("Release blocked: " + ", ".join(failures))


# The claim flags that remove stable-release.yml jobs, per job. A job one of
# the claim's active removals removes must report `skipped`. Every other gated
# job must report `success`. Jobs not listed here never skip.
CLAIM_FLAGS = ("autopublish", "skipBundles", "skipTests")
_TESTS, _BUNDLES = frozenset({"skipTests"}), frozenset({"skipBundles"})
SKIPPED_BY = {
    **{job: _TESTS for job in ("ci", "nix", "termux-checks", "windows-live", "install-e2e",
                               "bootstrap-version")},
    **{job: _BUNDLES for job in ("candidates-darwin-arm64", "candidates-darwin-x64",
                                 "candidates-win32-arm64", "candidates-win32-x64",
                                 "candidates-win32-bundle", "candidates-termux",
                                 "candidate-manifest", "publish-bundles")},
    # Bundle acceptance is a test of bundles, so either flag removes it.
    **{job: _TESTS | _BUNDLES for job in ("pm-bundle", "transitions-darwin-arm64",
                                          "transitions-darwin-x64", "transitions-win32",
                                          "windows-packaged", "macos-packaged-arm64",
                                          "macos-packaged-x64")},
}
for _platform in optional_platforms.PLATFORMS:
    for _job in _platform.jobs:
        SKIPPED_BY[_job] = SKIPPED_BY[_job] | {_platform.exclusion}


# Each signed-package upgrade arm and the transitions job that plans it.
PACKAGED_BY = {"macos-packaged-arm64": "transitions-darwin-arm64",
               "macos-packaged-x64": "transitions-darwin-x64",
               "windows-packaged": "transitions-win32"}


def gate_expectations(required: list[str], *, skip_bundles: bool, skip_tests: bool,
                      **platforms: bool) -> dict:
    """Each gated job's required result under the claim's flags (``platforms``:
    the optional_platforms keys, each false when omitted)."""
    active = {flag for flag, on in (("skipBundles", skip_bundles), ("skipTests", skip_tests)) if on}
    if set(platforms) - {p.key for p in optional_platforms.PLATFORMS}:
        raise TypeError(f"Unknown platform flags: {sorted(platforms)}")
    active |= {p.exclusion for p in optional_platforms.excluded(platforms)}
    return {name: "skipped" if SKIPPED_BY.get(name, frozenset()) & active else "success"
            for name in required}


def require_gate(needs: dict, required: list[str], *, skip_bundles: bool, skip_tests: bool,
                 **platforms: bool) -> None:
    """``require_success`` that also demands a flag-removed job really was skipped."""
    if not required or len(set(required)) != len(required):
        raise ValueError("Invalid required-job list")
    expected = gate_expectations(required, skip_bundles=skip_bundles, skip_tests=skip_tests, **platforms)
    for packaged, planner in PACKAGED_BY.items():
        # Without a published baseline there is no OLD package, so the planner
        # skips the upgrade arm. Only that planner's own successful output can excuse it.
        planned = needs.get(planner, {})
        if (expected.get(packaged) == "success" and planned.get("result") == "success"
                and (planned.get("outputs") or {}).get("baseline") == "none"):
            expected[packaged] = "skipped"
    failures = [f"{name}={needs.get(name, {}).get('result', 'missing')} (expected {want})"
                for name, want in expected.items() if needs.get(name, {}).get("result") != want]
    if failures:
        raise ValueError("Release blocked: " + ", ".join(failures))


def accepted_smoke_results(needs: object, *, absent: tuple[str, ...] | frozenset = ()) -> dict:
    """Persist only observed native groups, never infer them from artifacts."""
    if not isinstance(needs, dict):
        raise ValueError("Candidate smoke results must be a job-result object")
    results = {}
    for job in SMOKE_JOBS:
        row = needs.get(job)
        results[job] = {"result": row.get("result") if isinstance(row, dict) else None}
    if {row["result"] for row in results.values()} != {"skipped"}:
        built = [job for job in SMOKE_JOBS if SMOKE_TARGETS[job] not in absent]
        require_success(results, built)
        unbuilt = [f"{job}={results[job]['result']}" for job in SMOKE_JOBS
                   if job not in built and results[job]["result"] != "skipped"]
        if unbuilt:
            raise ValueError("Smoke results name targets this release did not build: "
                             + ", ".join(unbuilt))
    return results


def smokes_skipped(manifest: dict) -> bool:
    results = manifest.get("smoke_results") or {}
    return all((results.get(job) or {}).get("result") == "skipped" for job in SMOKE_JOBS)


def require_smokes_match_claim(manifest: dict, *, skip_tests: bool) -> None:
    if smokes_skipped(manifest) != skip_tests:
        raise ValueError("Candidate smoke results differ from the claim's test policy")


def _flag(env: dict, name: str) -> bool:
    value = env.get(name)
    if value not in ("true", "false"):
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def check_claim(env: dict) -> dict:
    """The fixture does not validate a claim; a test supplies the admitted dict."""
    raise NotImplementedError("the fixture does not validate a claim")


def emit(fields: dict, env: dict) -> None:
    """Write ``GITHUB_OUTPUT``, the way the fork's own admit step does."""
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        for name, value in fields.items():
            file.write(f"{name}={value}\n")


def summary(text: str, env: dict) -> None:
    with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
        file.write(text + "\n")


def admit(env: dict) -> None:
    """The reconcile folds the signing credentials into the claim right here."""
    admitted = check_claim(env)
    repository = env["GITHUB_REPOSITORY"]
    emit({
        "claim-tag": admitted["claim_tag"],
        "skip-bundles": "true" if admitted["skip_bundles"] else "false",
        "skip-tests": "true" if admitted["skip_tests"] else "false",
        **{p.key.replace("_", "-"): "true" if admitted[p.key] else "false"
           for p in optional_platforms.PLATFORMS},
    }, env)
    skipped = [name for name, on in (("bundles", admitted["skip_bundles"]),
                                     ("tests", admitted["skip_tests"])) if on]
    opted = [p.label for p in optional_platforms.PLATFORMS if admitted[p.key]]
    summary(
        f"## Stable candidate {admitted['claim_tag']}\nCommit: {admitted['commit']}\n"
        f"Version: {admitted['version']}\nPayload tag: {admitted['tag']}\n"
        f"Skipped: {', '.join(skipped) or 'nothing'}\n"
        f"Opt-in packages: {', '.join(opted) or 'none (macOS Intel and Termux are not built)'}\n",
        env,
    )


def candidate_manifest(env: dict) -> dict:
    """The reconcile drops the lanes the credentials left out right here.

    Returns the three values the real step persists, so a test can read them.
    """
    claim = env["RELEASE_CLAIM"]
    handoffs = optional_platforms.claim_handoffs(CANDIDATE_HANDOFFS, claim)
    # A call the claim did not opt into never ran, so it has no smoke to record.
    skipped_calls = {p.call for p in optional_platforms.excluded(claim)}
    calls = {call: job for call, job in CALL_SMOKE_JOBS.items() if call not in skipped_calls}
    return {"handoffs": handoffs, "skipped_calls": skipped_calls, "calls": calls}


def complete(env: dict) -> None:
    """The reconcile keeps reading the folded flags the gate published right here."""
    claim = env["RELEASE_CLAIM"]
    candidate: dict = {}
    require_targets_match_claim(candidate, include_macos_intel=claim["include_macos_intel"],
                                include_termux=claim["include_termux"])


def main(argv: list[str] | None = None, env: dict | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ if env is None else env)
    needs = json.loads(env.get("RELEASE_NEEDS", "{}"))
    if argv and argv[0] == "gate":
        summary("\n".join(f"- {name}: {needs.get(name, {}).get('result', 'missing')}" for name in argv[1:]), env)
        require_gate(needs, argv[1:], skip_bundles=_flag(env, "SKIP_BUNDLES"),
                     skip_tests=_flag(env, "SKIP_TESTS"),
                     **{p.key: _flag(env, p.key.upper()) for p in optional_platforms.PLATFORMS})
        return
    raise ValueError(f"Unknown command: {argv[0]}")
