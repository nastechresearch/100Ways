from pathlib import Path

from hundredways.reference_audit import EXACT_ATTRIBUTION, audit_references


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_strict_audit_allows_only_exact_dependency_and_summary_exceptions(tmp_path):
    _write(
        tmp_path,
        "package-lock.json",
        '"hermes-parser": "1", "hermes-estree": "1"\n',
    )
    _write(
        tmp_path,
        "website/package-lock.json",
        '"name": "@nous-research/image-size"\n',
    )
    _write(
        tmp_path,
        "website/.npmrc",
        "min-release-age-exclude[]=@nous-research/image-size\n",
    )
    _write(tmp_path, "SYNC-SUMMARY.md", f"> {EXACT_ATTRIBUTION}\n")

    report = audit_references(tmp_path)

    assert report.passes
    assert report.blocking_occurrences == {"hermes": 0, "nous": 0}
    assert report.approved_occurrences == {"hermes": 2, "nous": 3}


def test_strict_audit_rejects_unapproved_references_and_contributor_email_paths(tmp_path):
    _write(tmp_path, "contributors/emails/agent@hermes.dev", "nous-login\n")
    _write(tmp_path, "README.md", "NousResearch reference\n")
    _write(tmp_path, "plugins/bridge.py", "hermesbot\n")

    report = audit_references(tmp_path)

    assert not report.passes
    assert report.blocking_occurrences == {"hermes": 2, "nous": 2}
    assert {finding.path for finding in report.findings} >= {
        "contributors/emails/agent@hermes.dev",
        "README.md",
        "plugins/bridge.py",
    }


def test_strict_audit_allows_contributor_name_identity_only(tmp_path):
    _write(tmp_path, "contributors/names/NadiaNous.txt", "Nous Researcher\n")

    report = audit_references(tmp_path)

    assert report.passes
    assert report.approved_occurrences == {"hermes": 0, "nous": 2}


def test_strict_audit_does_not_misclassify_ordinary_words(tmp_path):
    _write(tmp_path, "docs/terms.md", "asynchronous anonymous autonomous luminous\n")

    report = audit_references(tmp_path)

    assert report.passes
    assert report.lexical_occurrences["nous"] == 2


def test_attribution_is_rejected_outside_summary_files(tmp_path):
    _write(tmp_path, "README.md", f"> {EXACT_ATTRIBUTION}\n")

    report = audit_references(tmp_path)

    assert not report.passes
    assert report.blocking_occurrences == {"hermes": 0, "nous": 1}
