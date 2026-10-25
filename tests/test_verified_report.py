"""One honest document: what the engine measured, how, and what it could not.

The spec's promise is that any section which could not measure says
``not verified: <the measurement that is missing>`` instead of guessing, that
every string passes ``secrets.redact``, that each section carries a digest of its
own inputs, and that identical inputs give identical bytes (no clock, no dict
order).  These tests hold exactly those four properties against real engine
objects -- a ``VerifyReport``, a ``WeeklyFullSyncReport``, ``pubconfig`` findings,
``urlcheck`` findings, live ``coverage`` rows -- rather than stand-ins of my own.
"""

from __future__ import annotations

import re
from pathlib import Path

from hundredways.adapters import Orphan, SurfaceDelta
from hundredways.analyzer import FileEntry, GapReport
from hundredways.coverage import WayCoverage, coverage
from hundredways.forklint import ForkLintIssue, ForkLintSummary
from hundredways.pubconfig import CredentialReference, Finding
from hundredways.receipts import sha256_file
from hundredways.urlcheck import URL_POLICY_PATH, UrlFinding, UrlPolicy, classify, extract_links
from hundredways.verified_report import SECTIONS, build
from hundredways.verify import FileResult, VerifyReport
from hundredways.ways import Way, build_registry
from hundredways.weekly_sync import AuditIssue, WeeklyFullSyncReport

HEADING = re.compile(r"^## (\w[\w '-]*)$", re.MULTILINE)
DIGEST = re.compile(r"^- digest: sha256 ([0-9a-f]{64})$", re.MULTILINE)
SECRET = "ghp_" + "a" * 24
BOT_TOKEN = "12345678:" + "b" * 32
# One upstream link, one of our own, and one scaffold that must never ship: the
# three verdicts the URLs section can make without a network.
URL_SAMPLE = (
    "Docs live at https://hermes-agent.nousresearch.com/docs/setup.\n"
    "Buy the appliance at https://nastechresearch.com/store\n"
    "Swap https://example.com/baseurl before shipping.\n"
)


def parity(passed: int = 2, total: int = 3) -> VerifyReport:
    report = VerifyReport(total=total, passed=passed)
    for index in range(total):
        result = FileResult(path=f"hermes_cli/f{index}.py", mapped_path=f"nastech_cli/f{index}.py",
                            pass_=index < passed, locked=False)
        report.results.append(result)
        if index >= passed:
            report.failed.append(result)
    return report


def sync(brand_code: str = "") -> WeeklyFullSyncReport:
    report = WeeklyFullSyncReport(
        upstream_sha="a" * 40,
        previous_sha="b" * 40,
        commits=4,
        files_changed=210,
        added_lines=1900,
        deleted_lines=330,
    )
    if brand_code:
        report.brand_issues.append(
            AuditIssue(code=brand_code, path="nastech_cli/app.py", detail="leftover upstream token")
        )
    return report


def urls(text: str = URL_SAMPLE) -> list[UrlFinding]:
    """Real offline findings, so the report's "no network probe" line is true."""

    policy = UrlPolicy.load(URL_POLICY_PATH)
    return [classify(link, policy) for link in extract_links(text, "README.md")]


def credentials() -> list[Finding]:
    reference = CredentialReference(
        name="NASTECH_SIGNING_KEY",
        kind="secrets",
        workflow="stage-pipeline.yml",
        job="sign",
        environment="release-signing",
        line=42,
    )
    return [
        Finding(
            name="NASTECH_SIGNING_KEY",
            kind="secret",
            verdict="missing",
            scopes=("release-signing",),
            references=(reference,),
            gate="sign",
            note=f"consumed by a step that also logs {SECRET}",
        )
    ]


def orphan(kind: str = "deleted-twin") -> Orphan:
    return Orphan(
        path="tests/nastech_cli/test_setup_menu_curses_migration.py",
        kind=kind,
        evidence="upstream deleted the twin of this path",
        suggestion="port upstream's replacement or delete this file",
    )


# -- structure --------------------------------------------------------------


def test_an_unmeasured_run_says_so_in_every_section():
    report = build()
    assert HEADING.findall(report.text) == list(SECTIONS)
    assert report.missing == SECTIONS
    assert report.text.count("not verified: ") == len(SECTIONS)
    assert "measured: " not in report.text


def test_a_measurement_replaces_only_the_sections_it_belongs_to():
    report = build(parity=parity(), orphans=[orphan()])
    assert "not verified: " not in report.section("Gates")
    assert "not verified: " not in report.section("Orphans")
    # Summary is measured by derivation: once anything has run it has something
    # honest to say, so it leaves the missing list with the sections it names.
    assert report.missing == tuple(
        name for name in SECTIONS if name not in {"Summary", "Gates", "Orphans"}
    )
    assert report.measured == ("Summary", "Gates", "Orphans")


# -- determinism ------------------------------------------------------------


def test_identical_inputs_give_identical_bytes():
    first = build(parity=parity(), urls=urls(), orphans=[orphan()])
    second = build(parity=parity(), urls=urls(), orphans=[orphan()])
    assert first.text == second.text
    assert first.digests == second.digests


def test_the_document_carries_no_clock_of_its_own():
    report = build(
        parity=parity(),
        sync=sync(),
        gaps=GapReport(upstream_commit="c" * 40, nastech_commit="d" * 40),
        urls=urls(),
        orphans=[orphan()],
    )
    assert not re.search(r"\b20\d\d-\d\d-\d\d", report.text)


def test_order_of_input_does_not_move_a_digest():
    first = Orphan(path="docs/a.md", kind="unclassified", evidence="no twin", suggestion="own it")
    second = Orphan(path="docs/b.md", kind="unclassified", evidence="no twin", suggestion="own it")
    assert build(orphans=[first, second]).digests["Orphans"] == build(
        orphans=[second, first]
    ).digests["Orphans"]


# -- digests ----------------------------------------------------------------


def test_every_section_carries_a_digest_of_its_own_inputs():
    report = build(parity=parity(), urls=urls())
    assert len(DIGEST.findall(report.text)) == len(SECTIONS)
    assert set(report.digests) == set(SECTIONS)
    assert len(set(report.digests.values())) == len(SECTIONS)


def test_a_change_moves_only_the_section_that_measured_it():
    before = build(parity=parity(), urls=urls())
    after = build(parity=parity(), urls=urls("one link now: https://nastechresearch.com/store\n"))
    assert before.digests["Gates"] == after.digests["Gates"]
    assert before.digests["URLs"] != after.digests["URLs"]
    assert before.digests["Summary"] != after.digests["Summary"]


# -- redaction --------------------------------------------------------------


def test_a_credential_in_the_inputs_never_reaches_the_document():
    report = build(
        parity=parity(),
        credentials=credentials(),
        urls=urls(),
        orphans=[orphan()],
    )
    assert SECRET not in report.text
    assert "[REDACTED]" in report.text
    assert "NASTECH_SIGNING_KEY" in report.section("Credentials")


def test_a_bot_token_shape_is_redacted_too():
    report = build(orphans=[Orphan(path="x.md", kind="unclassified",
                                   evidence=f"webhook {BOT_TOKEN} configured here",
                                   suggestion="rotate it")])
    assert BOT_TOKEN not in report.text


def test_credential_names_are_kept_because_names_are_the_evidence():
    report = build(credentials=credentials())
    assert "NASTECH_SIGNING_KEY" in report.section("Credentials")
    assert "release-signing" in report.section("Credentials")


# -- section content, measured from the objects given -----------------------


def test_the_gate_section_reports_the_parity_it_was_handed():
    section = build(parity=parity()).section("Gates")
    assert "2/3" in section
    assert "FAIL" in section  # a failed file exists, so the gate cannot pass
    assert "threshold 0.99" in section


def test_the_gate_section_names_sync_blockers_with_their_codes():
    section = build(sync=sync(brand_code="brand-leftover-token")).section("Gates")
    assert "brand-leftover-token" in section
    assert "nastech_cli/app.py" in section


def test_the_delta_section_adds_up_from_the_sync_and_the_gaps():
    gaps = GapReport(
        upstream_commit="c" * 40,
        nastech_commit="d" * 40,
        entries=[
            FileEntry(path="hermes_cli/new.py", status="upstream-only"),
            FileEntry(path="nastech_cli/only.py", status="nastech-only", explanation="fork skill"),
        ],
    )
    section = build(sync=sync(), gaps=gaps).section("Delta")
    assert "4 commits" in section
    assert "210 files" in section
    assert "+1900/-330" in section
    assert "1 upstream-only" in section
    assert "1 nastech-only" in section


def test_the_delta_section_names_surface_deltas_with_the_class_they_need():
    # The reader of this section decides who acts: an adopted verb needs nothing,
    # a branded one needs the transform, a widened signature needs an engine rule.
    deltas = [
        SurfaceDelta("added", "cli-verb", "", "doctor"),
        SurfaceDelta("added", "env-var", "HERMES_TOKEN", "NASTECH_TOKEN"),
        SurfaceDelta("changed", "signature", "go(x, y)", "go(x, y, headings)"),
    ]
    section = build(surface=deltas).section("Delta")
    assert "surface: 3 movement(s)" in section
    assert "[adopt]" in section
    assert "[brand]" in section
    assert "[rule]" in section
    assert "cli-verb added: doctor [adopt]" in section
    assert "signature changed: go(x, y) -> go(x, y, headings) [rule]" in section
    assert "headings" in section


def test_the_urls_section_says_when_nothing_was_probed():
    report = build(urls=urls())
    section = report.section("URLs")
    assert "no network probe" in section
    assert "BRAND_VIOLATION" in section
    assert "nastech-agent.nastechresearch.com" in section  # the suggestion it names
    assert "0 answered by the network, 3 not looked at" in section
    assert "verified" not in section.lower()
    # A link nobody looked at is neither a verdict nor a pass, and the headline says so.
    assert "3 link(s): 2 verdict(s)" in report.section("Summary")


def test_the_lint_section_repeats_the_verdict_the_summary_made():
    summary = ForkLintSummary(
        (ForkLintIssue(script="ruff check .", path="nastech_cli/app.py",
                       detail="E501", blocking=True),)
    )
    section = build(lint=summary).section("Lint")
    assert "fork-lint FAIL" in section
    assert "ruff check ." in section


def test_the_lint_section_reports_an_unavailable_checker_honestly():
    summary = ForkLintSummary(
        (ForkLintIssue(script="mypy", path="", detail="mypy absent", blocking=False,
                       status="unavailable"),)
    )
    section = build(lint=summary).section("Lint")
    assert "NOT SIMULATED" in section
    assert "advisory" in section  # a checker that could not run is not a blocking finding


def test_the_ways_section_counts_the_live_registry():
    rows = coverage()
    section = build(ways=rows).section("Ways")
    built = sum(1 for row in rows if row.status == "built")
    catalog_only = sum(1 for row in rows if row.status == "catalog-only")
    assert len(rows) == build_registry().count  # the report may not blur the registry size
    assert f"total={len(rows)}" in section
    assert f"built={built}" in section
    assert f"catalog-only={catalog_only}" in section


def test_a_dangling_way_is_named_by_id_and_by_the_symbol_that_is_missing():
    rows = [
        WayCoverage(
            way=Way(
                "verify.made-up", "Made up", "verify", "no engine symbol", uses="adapters.nothing"
            ),
            status="dangling",
        )
    ]
    section = build(ways=rows).section("Ways")
    assert "verify.made-up" in section
    assert "adapters.nothing" in section


def test_the_ways_section_is_measured_not_asserted():
    rows = [WayCoverage(way=Way("gate.x", "X", "gate", "nothing"), status="catalog-only")]
    section = build(ways=rows).section("Ways")
    assert "total=1" in section
    assert "catalog-only=1" in section
    assert "built=0" in section


def test_the_orphans_section_carries_evidence_and_the_action():
    section = build(orphans=[orphan()]).section("Orphans")
    assert "tests/nastech_cli/test_setup_menu_curses_migration.py" in section
    assert "deleted-twin" in section
    assert "port upstream's replacement or delete this file" in section


def test_the_receipts_section_digests_the_bytes_on_disk(tmp_path: Path):
    receipt = tmp_path / "gate-decision.json"
    receipt.write_text('{"gate": "PASS"}\n', encoding="utf-8")
    section = build(receipts=[receipt]).section("Receipts")
    assert sha256_file(receipt) in section
    assert "gate-decision.json" in section


def test_an_unreadable_receipt_is_named_instead_of_guessed(tmp_path: Path):
    missing = tmp_path / "gone.json"
    section = build(receipts=[missing]).section("Receipts")
    assert "not verified: " in section
    assert "gone.json" in section


def test_the_summary_names_every_measurement_that_ran():
    report = build(parity=parity(), sync=sync(), ways=coverage()[:3])
    section = report.section("Summary")
    assert "parity" in section
    assert "sync" in section
    assert "ways" in section
    assert "not verified: " not in section


def test_the_missing_measurements_are_specific_enough_to_act_on():
    section = build(parity=parity()).section("URLs")
    assert "no url scan was run" in section
    section = build().section("Ways")
    assert "no ways coverage rows" in section
