from pathlib import Path

from hundredways.reference_audit import audit_references, reference_summary


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_allows_only_the_two_dependency_groups(tmp_path):
    _write(
        tmp_path,
        "package-lock.json",
        '"hermes-parser": "^0.25.1"\n"hermes-estree": "0.25.1"\n',
    )
    _write(
        tmp_path,
        "website/package-lock.json",
        '"name": "@nous-research/image-size"\n',
    )

    assert audit_references(tmp_path) == ()
    assert reference_summary(tmp_path)["gate"] == "PASS"


def test_blocks_non_dependency_path_and_content_references(tmp_path):
    _write(tmp_path, "apps/hermes-ui/main.ts", "export const brand = 'Hermes';\n")
    _write(tmp_path, "README.md", "Powered by Nous Research\n")

    findings = audit_references(tmp_path)

    assert {(finding.kind, finding.path) for finding in findings} == {
        ("path", "apps/hermes-ui/main.ts"),
        ("text", "apps/hermes-ui/main.ts"),
        ("text", "README.md"),
    }
    assert reference_summary(tmp_path)["gate"] == "FAIL"


def test_allows_only_exact_powered_by_attribution(tmp_path):
    _write(tmp_path, "README.md", "Powered by NousResearch\n")
    assert audit_references(tmp_path) == ()

    _write(tmp_path, "README.md", "Powered by NousResearch and Hermes\n")
    findings = audit_references(tmp_path)
    assert len(findings) == 1
    assert findings[0].token.lower() == "hermes"


def test_blocks_unapproved_reference_inside_lockfile(tmp_path):
    _write(tmp_path, "package-lock.json", '"name": "hermes-agent"\n')

    findings = audit_references(tmp_path)

    assert len(findings) == 1
    assert findings[0].path == "package-lock.json"
    assert findings[0].line == 1


def test_audit_references_ignores_pipeline_generated_reports(tmp_path):
    """UPDATE-REPORT.md and GATE-REPORT.md are pipeline-generated changelogs
    that always contain upstream brand names (they describe what Hermes changed).
    They must never produce findings."""
    from hundredways.reference_audit import audit_references

    # Create a file that would match the hermes/nous regex
    (tmp_path / "UPDATE-REPORT.md").write_text(
        "Changelog: upstream Hermes bumped to v1.2.3\n"
        "NousResearch/image-size@2.0.0\n"
        "hermes-estree v0.9.1\n"
    )
    findings = audit_references(tmp_path)
    assert findings == (), (
        f"UPDATE-REPORT.md should be ignored; got: {findings}"
    )


def test_audit_references_ignores_gate_report(tmp_path):
    from hundredways.reference_audit import audit_references

    (tmp_path / "GATE-REPORT.md").write_text(
        "Gate: PASS\nHermes Nous hermes nous Hermes Nous\n"
    )
    findings = audit_references(tmp_path)
    assert findings == (), f"GATE-REPORT.md should be ignored; got: {findings}"
