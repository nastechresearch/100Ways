from pathlib import Path

from hundredways.conformance import verify_final_candidate
from hundredways.rules import BrandingRules
from hundredways.updates import brand_tree


def _tree(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_final_conformance_accepts_exact_branded_candidate(tmp_path):
    source = _tree(
        tmp_path / "source",
        {
            "hermes_cli/main.py": "def use_hermes():\n    return 'Nous Research'\n",
            "README.md": "# Hermes Agent\n",
        },
    )
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())

    report = verify_final_candidate(source, candidate)

    assert report.passed
    assert report.source_files == 2
    assert report.verified_files == 2
    assert report.issues == ()


def test_final_conformance_blocks_post_brand_source_mismatch(tmp_path):
    source = _tree(
        tmp_path / "source",
        {"hermes_cli/main.py": "def use_hermes():\n    return 'Nous Research'\n"},
    )
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())
    (candidate / "nastech_cli" / "main.py").write_text("unsafe post-brand edit\n")

    report = verify_final_candidate(source, candidate)

    assert not report.passed
    assert report.source_files == 1
    assert report.verified_files == 0
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("source-parity", "nastech_cli/main.py")
    ]


def test_final_conformance_blocks_unsafe_candidate_tree_entry(tmp_path):
    source = _tree(tmp_path / "source", {"README.md": "# Hermes Agent\n"})
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())
    unsafe = candidate / "unsafe.txt"
    unsafe.write_text("unsafe", encoding="utf-8")
    unsafe.chmod(0o666)

    report = verify_final_candidate(source, candidate)

    assert not report.passed
    assert any(issue.code == "candidate-world-writable" for issue in report.issues)


def test_final_conformance_blocks_unexpected_candidate_path(tmp_path):
    source = _tree(tmp_path / "source", {"README.md": "# Hermes Agent\n"})
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())
    (candidate / "stale-source.ts").write_text("legacy\n", encoding="utf-8")

    report = verify_final_candidate(source, candidate)

    assert not report.passed
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("unexpected-candidate-path", "stale-source.ts")
    ]


def test_final_conformance_allows_declared_fork_owned_path(tmp_path):
    source = _tree(tmp_path / "source", {"README.md": "# Hermes Agent\n"})
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())
    local_path = "config/nastech-local-note.md"
    note = candidate / local_path
    note.parent.mkdir(parents=True)
    note.write_text("NasTech-owned note\n", encoding="utf-8")

    report = verify_final_candidate(source, candidate, allowed_extra_paths=[local_path])

    assert report.passed


def test_final_conformance_allows_exact_inherited_immutable_case_collision(tmp_path):
    source = _tree(
        tmp_path / "source",
        {
            "contributors/emails/agent@Agents-Mac-mini.local": "first\n",
            "contributors/emails/agent@agents-Mac-mini.local": "second\n",
        },
    )
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())

    report = verify_final_candidate(source, candidate)

    assert report.passed
    assert report.issues == ()


def test_final_conformance_blocks_changed_inherited_case_collision(tmp_path):
    source = _tree(
        tmp_path / "source",
        {
            "contributors/emails/agent@Agents-Mac-mini.local": "first\n",
            "contributors/emails/agent@agents-Mac-mini.local": "second\n",
        },
    )
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())
    (candidate / "contributors" / "emails" / "agent@Agents-Mac-mini.local").write_text(
        "changed\n",
        encoding="utf-8",
    )

    report = verify_final_candidate(source, candidate)

    assert not report.passed
    assert {issue.code for issue in report.issues} == {"candidate-case-collision"}
