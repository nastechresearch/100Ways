import json
from pathlib import Path

from hundredways.assets import OwnedAssets
from hundredways.conformance import verify_final_candidate
from hundredways.integrity import tree_digest
from hundredways.rules import BrandingRules
from hundredways.updates import UpdateManager, apply_owned_assets, brand_tree


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


def test_final_conformance_rejects_declared_fork_owned_path(tmp_path):
    source = _tree(tmp_path / "source", {"README.md": "# Hermes Agent\n"})
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())
    local_path = "config/nastech-local-note.md"
    note = candidate / local_path
    note.parent.mkdir(parents=True)
    note.write_text("NasTech-owned note\n", encoding="utf-8")

    report = verify_final_candidate(source, candidate, allowed_extra_paths=[local_path])

    assert not report.passed
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("unexpected-candidate-path", local_path)
    ]


def test_final_conformance_allows_registered_brand_visuals_but_not_fork_code(tmp_path):
    source = _tree(tmp_path / "source", {"README.md": "# Hermes Agent\n"})
    assets = tmp_path / "config" / "owned-assets"
    assets.mkdir(parents=True)
    (assets / "banner.png").write_bytes(b"Nastech banner")
    (assets / "generate_icons.py").write_text("print('generator')\n", encoding="utf-8")
    (assets / "manifest.json").write_text(
        json.dumps(
            {
                "assets/nastech-banner.png": "banner.png",
                "scripts/generate_icons.py": "generate_icons.py",
            }
        ),
        encoding="utf-8",
    )
    owned = OwnedAssets(root=str(assets)).brand_visuals()
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules(), owned)
    apply_owned_assets(str(candidate), owned)

    report = verify_final_candidate(source, candidate, owned_assets_root=assets)

    assert report.passed
    assert (candidate / "assets" / "nastech-banner.png").read_bytes() == b"Nastech banner"
    (candidate / "scripts").mkdir()
    (candidate / "scripts" / "generate_icons.py").write_text("fork code\n", encoding="utf-8")
    report_with_code = verify_final_candidate(source, candidate, owned_assets_root=assets)
    assert not report_with_code.passed
    assert [(issue.code, issue.path) for issue in report_with_code.issues] == [
        ("unexpected-candidate-path", "scripts/generate_icons.py")
    ]


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


def test_final_conformance_excludes_fork_files_and_reconciliation(tmp_path):
    """The candidate is exactly branded upstream, regardless of fork state."""
    catalog = (
        "# Optional Skills Catalog\n"
        "\n"
        "## creative\n"
        "\n"
        "| Skill | Description |\n"
        "|-------|-------------|\n"
        "| [**canvas-design**](../user-guide/skills/optional/creative/"
        "creative-canvas-design.md) | Design art. |\n"
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

    # Fork-only skills, docs, and catalog rows are not overlaid.
    candidate_catalog = (candidate / catalog_rel).read_text(encoding="utf-8")
    assert candidate_catalog == catalog
    assert not (candidate / page_rel).exists()
    assert not (candidate / skill_rel).exists()

    with_fork = verify_final_candidate(source, candidate, fork_root=str(fork))
    assert with_fork.passed, [
        f"{issue.code} {issue.path}: {issue.detail}" for issue in with_fork.issues
    ]
    assert with_fork.reconciled_paths == ()


def test_final_conformance_excludes_fork_release_publication_layer(tmp_path):
    """Fork-only release behavior is not carried into an upstream snapshot."""
    rel = "scripts/releases/channel_releases.py"
    source = _git_repo(
        tmp_path / "source",
        {
            rel: (
                "import os\n\n\n"
                "def publish_release(policy, env, root):\n"
                '    if policy == "canary-release":\n'
                '        stable.output(["gh", "release", "edit", env["TAG"], "--draft=false"])\n'
                "    def release_gate(request):\n"
                "        return True\n"
            ),
        },
    )
    fork = _tree(
        tmp_path / "fork",
        {
            rel: (
                "def verify_canary_outputs():\n"
                "    return None\n\n\n"
                '# Assets the release carries.\n'
                '_ASSET_SUFFIXES = (".zip",)\n\n\n'
                "def release_assets(root):\n"
                "    return []\n\n\n"
                "def publish_release(policy, env, root):\n"
                '    if policy == "canary-release":\n'
                '        upload_assets(env["TAG"], env["REPO"], root)\n'
                "    def release_gate(request):\n"
                "        return True\n"
            ),
        },
    )
    updates_dir = tmp_path / "Updates-Commits"
    res = UpdateManager(str(updates_dir), hermes_url=str(source), fork_root=str(fork)).run()
    assert res.gate, [s for s in res.stages if s.status == "fail"]
    candidate = Path(res.dir)
    assert "_ASSET_SUFFIXES" not in (candidate / rel).read_text(encoding="utf-8")
    assert (candidate / rel).read_text(encoding="utf-8") == (source / rel).read_text(
        encoding="utf-8"
    )

    report = verify_final_candidate(source, candidate, fork_root=str(fork))

    assert report.passed, [
        f"{issue.code} {issue.path}: {issue.detail}" for issue in report.issues
    ]
    assert report.reconciled_paths == ()


def test_final_candidate_reports_every_issue_kind_in_sequence(tmp_path):
    source = _tree(
        tmp_path / "source",
        {
            "README.md": "# Hermes Agent\n",
            "hermes_cli/main.py": "def use_hermes():\n    return 'Nous Research'\n",
        },
    )
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())
    (candidate / "nastech_cli" / "main.py").write_text("unsafe post-brand edit\n", encoding="utf-8")
    (candidate / "zeta.md").write_text("extra\n", encoding="utf-8")
    (candidate / "alpha.md").write_text("extra\n", encoding="utf-8")
    unsafe = candidate / "unsafe.txt"
    unsafe.write_text("unsafe\n", encoding="utf-8")
    unsafe.chmod(0o666)

    report = verify_final_candidate(source, candidate)

    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("source-parity", "nastech_cli/main.py"),
        ("unexpected-candidate-path", "alpha.md"),
        ("unexpected-candidate-path", "unsafe.txt"),
        ("unexpected-candidate-path", "zeta.md"),
        ("candidate-world-writable", "unsafe.txt"),
    ]
    assert (report.source_files, report.verified_files) == (2, 1)
    assert report.candidate_digest == tree_digest(candidate)


def test_final_candidate_path_checks_are_not_pruned_like_the_brand_audits(tmp_path):
    """A candidate ``.git`` is a finding, so conformance must see every path."""
    source = _tree(tmp_path / "source", {"README.md": "# Hermes Agent\n"})
    candidate = tmp_path / "candidate"
    brand_tree(str(source), str(candidate), BrandingRules())
    metadata = candidate / ".git"
    metadata.mkdir()
    (metadata / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    report = verify_final_candidate(source, candidate)

    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("unexpected-candidate-path", ".git/HEAD"),
        ("candidate-candidate-git-metadata", ".git"),
        ("candidate-candidate-git-metadata", ".git/HEAD"),
    ]
    assert report.candidate_digest == tree_digest(candidate)
    assert report.candidate_digest
