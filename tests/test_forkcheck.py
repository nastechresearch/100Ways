"""Tests for fork-consistency: the branded snapshot must stay byte-faithful to
the nastech-agent fork (no feature loss, no whole-tree churn, no brand
violations on newly-added upstream lines), and fork-local files must be
preserved into the snapshot so the pushed PR never deletes them."""

import os
import stat
import subprocess
import sys

import pytest

from hundredways.forkcheck import (
    ForkCheckReport,
    fork_consistency,
    preserve_fork_files,
    scan_brand_violations,
    _walk,
)
from hundredways.rules import BrandingRules
from hundredways.updates import STAGES, UpdateManager, brand_tree


def _tree(root, files):
    for rel, content in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
    return root


def _hermes_repo(tmp_path):
    hermes = tmp_path / "hermes-agent"
    hermes.mkdir()
    subprocess.run(["git", "init", "-q", str(hermes)], check=True)
    subprocess.run(["git", "-C", str(hermes), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(hermes), "config", "user.name", "t"], check=True)
    cli = hermes / "hermes_cli"
    cli.mkdir()
    (cli / "hermes_runner.py").write_text("def run_hermes():\n    return 'hermes-agent'\n")
    (hermes / "README.md").write_text("# Hermes Agent\nPowered by Nous Research.\n")
    subprocess.run(["git", "-C", str(hermes), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(hermes), "commit", "-q", "-m", "fake hermes"], check=True)
    return str(hermes)


def test_fork_consistency_classifies_identical_updated_added_missing(tmp_path):
    rules = BrandingRules()
    fork = _tree(tmp_path / "fork", {
        "README.md": "same\n",
        "changed.txt": "old line\n",
        "local-only.md": "fork content\n",
    })
    upstream = _tree(tmp_path / "upstream", {
        "README.md": "same\n",
        "changed.txt": "old line\n",
        "new-file.txt": "brand new\n",
    })
    # branded snapshot: README identical, changed.txt has an upstream update,
    # new-file.txt added, local-only.md present (preserved)
    branded = _tree(tmp_path / "branded", {
        "README.md": "same\n",
        "changed.txt": "new line\n",
        "new-file.txt": "brand new\n",
        "local-only.md": "fork content\n",
    })
    report = fork_consistency(str(fork), str(branded), str(upstream), rules)
    by_status = {e.path: e.status for e in report.entries}
    assert by_status["README.md"] == "identical"
    assert by_status["changed.txt"] == "updated"
    assert by_status["new-file.txt"] == "added"
    assert by_status["local-only.md"] == "identical"  # preserved verbatim
    assert report.gate_passes()
    assert report.preserved == ["local-only.md"]


def test_fork_consistency_flags_missing_upstream_file(tmp_path):
    rules = BrandingRules()
    upstream = _tree(tmp_path / "upstream", {"dropped.py": "x\n"})
    fork = _tree(tmp_path / "fork", {"dropped.py": "x\n"})
    branded = _tree(tmp_path / "branded", {})  # branding dropped it
    report = fork_consistency(str(fork), str(branded), str(upstream), rules)
    entry = next(e for e in report.entries if e.path == "dropped.py")
    assert entry.status == "missing"
    assert not report.gate_passes()


def test_fork_consistency_flags_unpreserved_fork_local_file(tmp_path):
    rules = BrandingRules()
    upstream = _tree(tmp_path / "upstream", {})
    fork = _tree(tmp_path / "fork", {"local.md": "fork only\n"})
    branded = _tree(tmp_path / "branded", {})  # preserve step not run
    report = fork_consistency(str(fork), str(branded), str(upstream), rules)
    entry = next(e for e in report.entries if e.path == "local.md")
    assert entry.status == "local_only"
    assert not report.gate_passes()


def test_updated_file_added_line_brand_violation_is_reported(tmp_path):
    rules = BrandingRules()
    upstream = _tree(tmp_path / "upstream", {
        "app.py": "def hermes_brand():\n    return 'hermes'\n",
    })
    # the fork had the OLD upstream state (hermes already branded at birth)
    fork = _tree(tmp_path / "fork", {
        "app.py": "def nastech_brand():\n    return 'nastech'\n",
    })
    # upstream added a line with a NEW un-branded token; the branding pass
    # missed it -> violation with file:line
    branded = _tree(tmp_path / "branded", {
        "app.py": "def nastech_brand():\n    return 'nastech'\n# TODO Nous Research\n",
    })
    report = fork_consistency(str(fork), str(branded), str(upstream), rules)
    entry = next(e for e in report.entries if e.path == "app.py")
    assert entry.status == "updated"
    assert any(v.path == "app.py" and v.line == 3 for v in entry.violations)
    assert report.violation_count == 1
    assert not report.gate_passes()


def test_scan_brand_violations_sweeps_whole_tree(tmp_path):
    rules = BrandingRules()
    tree = _tree(tmp_path / "tree", {
        "clean.py": "return 'nastech'\n",
        "dirty.py": "return 'hermes'\n",
        "doc.md": "by Nous Research\n",
        "package-lock.json": "name = hermes-agent\n",  # locked: skipped
    })
    hits = scan_brand_violations(str(tree), rules)
    by_path = {}
    for h in hits:
        by_path.setdefault(h.path, []).append(h.line)
    assert by_path["dirty.py"] == [1]
    assert by_path["doc.md"] == [1]
    assert "package-lock.json" not in by_path


def test_preserve_fork_files_copies_fork_local_content(tmp_path):
    rules = BrandingRules()
    upstream = _tree(tmp_path / "upstream", {
        "hermes_cli/main.py": "x\n",
    })
    fork = _tree(tmp_path / "fork", {
        "nastech_cli/main.py": "x\n",             # upstream twin (branded): NOT preserved
        "config/owned-assets/manifest.json": "{}\n",  # fork-local: preserved
        "local.md": "fork\n",
    })
    branded = _tree(tmp_path / "branded", {})
    preserved = preserve_fork_files(str(fork), str(branded), str(upstream), rules)
    assert "config/owned-assets/manifest.json" in preserved
    assert "local.md" in preserved
    assert "nastech_cli/main.py" not in preserved  # upstream provides it
    assert (tmp_path / "branded" / "local.md").read_text() == "fork\n"


def test_preserve_fork_files_keeps_existing_engine_owned_registry(tmp_path):
    rules = BrandingRules()
    upstream = _tree(tmp_path / "upstream", {})
    fork = _tree(tmp_path / "fork", {
        "config/owned-assets/manifest.json": '{"assets/logo.png": "fork-logo.png"}\n',
        "config/owned-assets/fork-logo.png": "fork asset\n",
    })
    branded = _tree(tmp_path / "branded", {
        "config/owned-assets/manifest.json": '{"assets/logo.png": "engine-logo.png"}\n',
        "config/owned-assets/engine-logo.png": "engine asset\n",
    })

    preserved = preserve_fork_files(str(fork), str(branded), str(upstream), rules)

    assert preserved == []
    manifest = tmp_path / "branded" / "config/owned-assets/manifest.json"
    assert manifest.read_text() == '{"assets/logo.png": "engine-logo.png"}\n'
    assert not (tmp_path / "branded" / "config/owned-assets/fork-logo.png").exists()


def test_pipeline_includes_preserve_and_forkcheck_stages(tmp_path):
    hermes = _hermes_repo(tmp_path)
    # the fork: already branded (the nastech fork), plus a fork-local file
    fork = _tree(tmp_path / "fork", {
        "README.md": "readme\n",
        "nastech_cli/nastech_runner.py": "x\n",
        "config/owned-assets/manifest.json": "{}\n",
    })
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes, fork_root=str(fork)).run()
    assert [s.name for s in res.stages] == STAGES
    assert res.gate
    by_name = {s.name: s for s in res.stages}
    assert by_name["preserve"].status == "ok"
    assert by_name["forkcheck"].status == "ok"
    # fork-local files must be preserved into the snapshot
    assert os.path.exists(os.path.join(res.dir, "config", "owned-assets", "manifest.json"))


def test_pipeline_forkcheck_stage_skipped_without_fork_root(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert [s.name for s in res.stages] == STAGES
    assert res.gate
    by_name = {s.name: s for s in res.stages}
    assert by_name["forkcheck"].status == "ok"
    assert by_name["forkcheck"].detail.startswith("diff snapshot")
    # no fork_root -> empty report, no entries
    assert res.fork.entries == []


def test_forkcheck_report_summary_counts():
    report = ForkCheckReport()
    assert "0 identical" in report.summary()
    assert "0 violations" in report.summary()
    assert report.gate_passes()


def test_forkcheck_gate_fails_on_missing_file():
    from hundredways.forkcheck import ForkEntry

    report = ForkCheckReport()
    report.entries.append(ForkEntry(path="x.py", status="missing"))
    assert not report.gate_passes()
    report.entries = [ForkEntry(path="x.py", status="identical")]
    assert report.gate_passes()


def test_walk_uses_git_tracked_files_in_a_checkout(tmp_path):
    # A fork checkout is a git repo; build artifacts must not be compared.
    repo = tmp_path / "fork"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "tracked.txt").write_text("x\n")
    (repo / "website").mkdir()
    (repo / "website" / "node_modules").mkdir(parents=True)
    (repo / "website" / "node_modules" / "dep.js").write_text("// untracked junk\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "c"], check=True)
    (repo / "uncommitted.txt").write_text("dirty\n")
    files = _walk(str(repo))
    assert "tracked.txt" in files
    assert "uncommitted.txt" not in files
    assert "website/node_modules/dep.js" not in files


def test_walk_skips_node_modules_in_a_plain_tree(tmp_path):
    tree = _tree(tmp_path / "tree", {
        "a.txt": "x\n",
        "web/node_modules/pkg/index.js": "junk\n",
        "web/__pycache__/x.pyc": "junk\n",
    })
    files = _walk(str(tree))
    assert "a.txt" in files
    assert "web/node_modules/pkg/index.js" not in files
    assert "web/__pycache__/x.pyc" not in files


def test_brand_tree_preserves_executable_bit(tmp_path):
    # upstream's `nastech`/`hermes` launcher is 0755; branding must keep it
    src = _tree(tmp_path / "src", {"hermes": "#!/usr/bin/env python3\nprint(1)\n"})
    os.chmod(os.path.join(str(src), "hermes"), 0o755)
    dst = str(tmp_path / "dst")
    brand_tree(str(src), dst, BrandingRules())
    branded = os.path.join(dst, "nastech")
    assert os.path.exists(branded)
    assert os.stat(branded).st_mode & stat.S_IXUSR
    assert open(branded).read() == "#!/usr/bin/env python3\nprint(1)\n"


def test_preserve_fork_files_carries_executable_bit(tmp_path):
    rules = BrandingRules()
    upstream = _tree(tmp_path / "upstream", {"x.txt": "1\n"})
    fork = _tree(tmp_path / "fork", {"bin/tool": "#!/bin/sh\necho hi\n"})
    os.chmod(os.path.join(str(fork), "bin", "tool"), 0o755)
    branded = _tree(tmp_path / "branded", {"x.txt": "1\n"})
    preserved = preserve_fork_files(str(fork), str(branded), str(upstream), rules)
    assert "bin/tool" in preserved
    assert os.stat(os.path.join(str(branded), "bin", "tool")).st_mode & stat.S_IXUSR


def test_immutable_email_upstream_paths_map_verbatim(tmp_path):
    # Contributor emails are real data: upstream's agent@hermes.dev must NOT
    # map to agent@nastech.dev (that would wrongly classify the fork's
    # agent@nastech.dev as upstream-provided and let it get dropped).
    rules = BrandingRules()
    upstream = _tree(tmp_path / "upstream", {
        "contributors/emails/agent@hermes.dev": "real data\n",
        "README.md": "hi\n",
    })
    fork = _tree(tmp_path / "fork", {
        "contributors/emails/agent@nastech.dev": "real data\n",
        "README.md": "hi\n",
    })
    branded = _tree(tmp_path / "branded", {
        "contributors/emails/agent@hermes.dev": "real data\n",
        "README.md": "hi\n",
    })
    report = fork_consistency(str(fork), str(branded), str(upstream), rules)
    by_status = {e.path: e.status for e in report.entries}
    # the fork's corrected email has NO upstream twin -> fork-local
    assert by_status["contributors/emails/agent@nastech.dev"] == "local_only"
    assert not report.gate_passes()


def test_hermes_agent_subdirectory_is_not_pruned(tmp_path):
    # Regression: _walk_files used to prune ANY dir named "hermes-agent",
    # silently dropping upstream's real skills/autonomous-ai-agents/hermes-agent.
    from hundredways.updates import _walk_files

    tree = _tree(tmp_path / "tree", {
        "skills/autonomous-ai-agents/hermes-agent/SKILL.md": "# skill\n",
        "other.txt": "x\n",
    })
    files = _walk_files(str(tree))
    assert "skills/autonomous-ai-agents/hermes-agent/SKILL.md" in files
    assert "other.txt" in files
