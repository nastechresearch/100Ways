from pathlib import Path

from hundredways.updates import _reconcile_domains, _reconcile_github_pages_release_plane


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_domains_does_not_report_a_mailbox_rewritten_back_to_its_original_bytes(tmp_path):
    path = _write(
        tmp_path,
        "scripts/releases/authors_legacy.py",
        'AUTHORS = {"nastech@nastechresearch.com": "NastechResearch"}\n',
    )
    before = path.read_bytes()

    assert _reconcile_domains(str(tmp_path)) == []
    assert path.read_bytes() == before


def test_pages_workflow_with_handoff_extended_store_env_is_already_reconciled(tmp_path):
    rel = ".github/workflows/desktop-bundle-smoke.yml"
    text = """\
permissions:
  contents: read

env:
  RELEASE_CHANNEL_STORE: github
  # Handoff extends this same top-level env block.
  RELEASE_HANDOFF_STORE: github

jobs: {}
"""
    path = _write(tmp_path, rel, text)
    before = path.read_bytes()

    assert _reconcile_github_pages_release_plane(str(tmp_path)) == []
    assert path.read_bytes() == before
