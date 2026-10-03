from pathlib import Path

from hundredways.conformance import verify_final_candidate
from hundredways.rules import BrandingRules
from hundredways.updates import UpdateManager, brand_tree


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


def _git_repo(root: Path, files: dict[str, str]) -> Path:
    """Seed a mini git repo (the conformance pipeline needs real git evidence)."""
    _tree(root, files)
    import subprocess

    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "seed"], check=True)
    return root


def test_final_conformance_replays_fork_preservation_and_post_preserve_reconciles(tmp_path):
    """The final-conformance rebuild must reproduce post-preserve reconcile bytes.

    The pipeline preserves fork-local files after reconcile, then re-syncs the
    skill catalogs and normalizes generated-docs separators; the final
    candidate carries those bytes.  Conformance rebuilds its own expected tree,
    so it must replay the same fork-preservation + post-preserve reconciles
    from the fork -- never trust the candidate -- or a reconciled fork file is
    reported as a source-parity mismatch (the real runner failure this gates).
    """
    catalog = (
        "# Optional Skills Catalog\n"
        "\n"
        "## creative\n"
        "\n"
        "| Skill | Description |\n"
        "|-------|-------------|\n"
        "| [**canvas-design**](../user-guide/skills/optional/creative/creative-canvas-design.md) | Design art. |\n"
    )
    source = _git_repo(
        tmp_path / "source",
        {
            "hermes_cli/main.py": "def use_hermes():\n    return 'Nous Research'\n",
            "README.md": "# Hermes Agent\n",
            "website/docs/reference/optional-skills-catalog.md": catalog,
        },
    )
    import subprocess

    source_head = (
        subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True)
        .strip()
    )
    fork = _tree(
        tmp_path / "fork",
        {
            "manifest.json": '{"upstream_sha": "%s"}\n' % source_head,
            "optional-skills/creative/blender-mcp/SKILL.md": (
                "---\n"
                "name: blender-mcp\n"
                "description: Drive Blender via the catalog blender MCP.\n"
                "---\n"
            ),
            # A generated-docs page committed from a Windows host and inherited
            # by the fork: the exact drift the POSIX reconcile exists for.
            "website/docs/user-guide/skills/optional/creative/creative-blender-mcp.md": (
                "| Path | `skills/optional/creative\\blender-mcp` |\n"
            ),
        },
    )
    page_rel = "website/docs/user-guide/skills/optional/creative/creative-blender-mcp.md"
    skill_rel = "optional-skills/creative/blender-mcp/SKILL.md"
    catalog_rel = "website/docs/reference/optional-skills-catalog.md"

    updates_dir = tmp_path / "Updates-Commits"
    res = UpdateManager(str(updates_dir), hermes_url=str(source), fork_root=str(fork)).run()
    assert res.gate, [s for s in res.stages if s.status == "fail"]
    candidate = Path(res.dir)

    # The candidate carries the post-preserve reconciles: catalog row + a
    # POSIX-clean page, while every other fork byte survives verbatim.
    candidate_catalog = (candidate / catalog_rel).read_text(encoding="utf-8")
    assert "blender-mcp" in candidate_catalog
    candidate_page = (candidate / page_rel).read_text(encoding="utf-8")
    assert "\\" not in candidate_page
    assert "skills/optional/creative/blender-mcp" in candidate_page
    assert (candidate / skill_rel).is_file()

    # Without a fork root the rebuilt expected tree lacks the post-preserve
    # reconciles: the catalog source-parity issue the runner hit.
    without_fork = verify_final_candidate(
        source, candidate, allowed_extra_paths=[page_rel, skill_rel]
    )
    assert not without_fork.passed
    assert ("source-parity", catalog_rel) in [
        (issue.code, issue.path) for issue in without_fork.issues
    ]

    # With the fork root, preservation + post-preserve reconciles are replayed
    # from the fork and the exact candidate verifies clean.
    with_fork = verify_final_candidate(source, candidate, fork_root=str(fork))
    assert with_fork.passed, [
        f"{issue.code} {issue.path}: {issue.detail}" for issue in with_fork.issues
    ]
    assert catalog_rel in with_fork.reconciled_paths
    assert page_rel in with_fork.reconciled_paths
