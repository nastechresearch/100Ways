"""The review narrative is built from the manifest, so it cannot flatter the run.

These tests pin the properties that make the headline and evidence trustworthy:
the title names the delta the manifest actually carries, evidence states a FAIL
as a FAIL, model prose is absent rather than guessed, and every string the
narrative emits is already a branding fixed point.
"""
from pathlib import Path

from hundredways.ai import AIConfig, AIEngine
from hundredways.narrative import (
    MAX_GARNISH_CHARS,
    MAX_TITLE_CHARS,
    body_from_manifest,
    evidence_from_manifest,
    garnish,
    title_from_manifest,
    write_narrative,
)
from hundredways.rules import BrandingRules, transform_strict_metadata_text


def manifest(**overrides):
    base = {
        "upstream_sha": "b" * 40,
        "gate": True,
        "commit_subjects": [
            "feat: add scheduled workspace sync",
            "feat: expose tool permissions in settings",
            "feat: add terminal theme picker",
            "fix: stop retry loop on expired session",
            "fix: correct clipboard paste on windows",
        ],
        "verify": {"total": 4200, "passed": 4200, "locked": 12, "failed": [], "pass_ratio": 1.0},
        "brand": {"total": 4212, "renamed": 80, "rewritten": 140, "errors": 0},
        "fork": {"violations": 0, "preserved_paths": ["skills/nastech-only/pdf-tools/SKILL.md"]},
        "source_delta": {"counts": {"added": 3, "modified": 9, "deleted": 1, "renamed": 2}},
        "source_provenance": {
            "acquisition": "fresh-direct-clone",
            "fetched_at": "2026-10-21T06:00:00+00:00",
        },
    }
    base.update(overrides)
    return base


class StubEngine:
    """Stands in for Ollama Cloud so the garnish contract is testable offline."""

    def __init__(self, *, available=True, reply="This snapshot makes sync routine.", error=None):
        self.available = available
        self.reply = reply
        self.error = error
        self.prompts = []

    def narrative(self, context):
        self.prompts.append(context)
        if self.error:
            raise self.error
        return self.reply


def test_title_names_the_delta_instead_of_a_bare_sha():
    title = title_from_manifest(manifest())

    assert title == f"Sync NasTech-Agent with upstream {'b' * 8}: 5 new capabilities"


def test_title_reports_alignment_when_no_commit_was_pending():
    assert title_from_manifest(manifest(commit_subjects=[])) == f"Align NasTech-Agent with upstream {'b' * 8}"


def test_title_uses_the_singular_for_one_commit():
    title = title_from_manifest(manifest(commit_subjects=["fix: stop retry loop on expired session"]))

    assert title.endswith(": 1 fix")


def test_a_mixed_delta_is_not_headlined_as_one_bucket():
    subjects = [
        "feat: add scheduled workspace sync",
        "feat: expose tool permissions",
        "fix: stop retry loop on expired session",
        "fix: correct clipboard paste on windows",
        "docs: refresh the settings guide",
    ]
    title = title_from_manifest(manifest(commit_subjects=subjects))

    assert title.endswith(": 5 verified updates")


def test_a_clearly_dominant_bucket_does_headline_the_delta():
    subjects = ["fix: harden " + str(index) for index in range(4)] + ["feat: add scheduled workspace sync"]

    assert title_from_manifest(manifest(commit_subjects=subjects)).endswith(": 5 fixes")


def test_the_title_never_carries_the_upstream_brand():
    title = title_from_manifest(
        manifest(commit_subjects=["feat: hermes agent loads hermes skills", "feat: hermes dashboard refresh"])
    )

    assert "hermes" not in title.casefold()
    assert "nastech" in title.casefold()


def test_the_title_fits_a_review_headline():
    title = title_from_manifest(
        manifest(commit_subjects=["feat: " + "capability ".join(str(index) * 90) for index in range(12)])
    )

    assert len(title) <= MAX_TITLE_CHARS


def test_the_narrative_is_already_a_brand_fixed_point():
    rules = BrandingRules()
    produced = title_from_manifest(manifest()) + "\n" + "\n".join(
        f"{name}: {outcome}" for name, outcome in evidence_from_manifest(manifest())
    )

    assert transform_strict_metadata_text(produced, rules) == produced


def test_evidence_states_a_failed_gate_as_a_failure():
    outcomes = dict(evidence_from_manifest(manifest(gate=False)))

    assert "FAIL" in outcomes["Publication gate"]
    assert "Passed" not in " ".join(outcomes.values())


def test_evidence_counts_the_parity_differences_that_remain():
    outcomes = dict(evidence_from_manifest(
        manifest(verify={"total": 4200, "passed": 4198, "locked": 12, "failed": ["agents/nastech/loop.py", "apps/desktop/app.ts"]})
    ))

    assert outcomes["Tree parity"] == "4198/4200 files verified byte-faithful after branding; 2 still differ"
    assert outcomes["Fork consistency"] == "0 inconsistency violations, 1 fork-owned files carried forward"


def test_evidence_accounts_for_upstream_changes_and_provenance():
    outcomes = dict(evidence_from_manifest(manifest()))

    assert outcomes["Upstream change evidence"] == "added 3, deleted 1, modified 9, renamed 2"
    assert "fresh-direct-clone" in outcomes["Direct source provenance"]
    assert "b" * 12 in outcomes["Direct source provenance"]


def test_evidence_says_so_when_no_fork_checkout_was_supplied():
    outcomes = dict(evidence_from_manifest(manifest(fork={})))

    assert outcomes["Fork consistency"] == "No fork checkout supplied"


def test_garnish_is_absent_without_a_key():
    assert garnish(manifest(), engine=StubEngine(available=False)) == ""


def test_garnish_is_absent_when_the_model_cannot_answer():
    assert garnish(manifest(), engine=StubEngine(error=RuntimeError("upstream 503"))) == ""


def test_garnish_bounds_and_brands_what_the_model_returns():
    reply = "feat: hermes operators get routine sync. " + "detail " * MAX_GARNISH_CHARS

    text = garnish(manifest(), engine=StubEngine(reply=reply))

    assert len(text) <= MAX_GARNISH_CHARS
    assert "hermes" not in text.casefold()


def test_garnish_only_ever_sees_the_factual_digest():
    engine = StubEngine()
    garnish(manifest(), engine=engine)

    assert len(engine.prompts) == 1
    assert "gate PASS" in engine.prompts[0]
    assert "commit_subjects" not in engine.prompts[0]


def test_body_keeps_the_summary_first_and_labels_advisory_text_last():
    summary = "# NasTech-Agent Update Summary\n\nverified content\n"
    body = body_from_manifest(manifest(), summary, paragraph="Operators get routine sync.")

    assert body.index("# NasTech-Agent Update Summary") < body.index("## Editorial note")
    assert "Operators get routine sync." in body
    assert "not verification evidence" in body


def test_body_is_unchanged_when_no_paragraph_was_produced():
    summary = "# NasTech-Agent Update Summary\n\n"
    body = body_from_manifest(manifest(), summary)

    assert body == summary.rstrip() + "\n"
    assert "Editorial note" not in body


def test_write_narrative_persists_both_files(tmp_path):
    title_file = Path(tmp_path) / "SYNC-TITLE.md"
    body_file = Path(tmp_path) / "SYNC-PR-BODY.md"

    title = write_narrative(
        str(title_file),
        str(body_file),
        manifest=manifest(),
        summary_markdown="# NasTech-Agent Update Summary\n",
    )

    assert title_file.read_text(encoding="utf-8").strip() == title
    assert "# NasTech-Agent Update Summary" in body_file.read_text(encoding="utf-8")


def test_the_engine_assembles_a_narrative_only_for_a_configured_key():
    engine = AIEngine(AIConfig(base_url="https://ollama.com/v1", provider="ollama-cloud", api_key=""))

    assert engine.narrative("gate PASS") == ""
