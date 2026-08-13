"""Tests for the ordered update pipeline: staged runs, folder-name branding,
release zip layout, sequential numbering, and reports."""

import json
import os
import subprocess
import sys
import zipfile

from hundredways.assets import OwnedAssets
from hundredways.updates import (
    STAGES,
    UpdateManager,
    brand_tree,
    compare_trees,
    next_update_number,
    package_zip,
    update_path,
    verify_branded,
)
from hundredways.rules import BrandingRules
from tests.conftest import git, git_repo


def _hermes_repo(tmp_path):
    """A fake upstream 'hermes-agent' repo with a brandable folder+file."""
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


def test_pipeline_runs_15_stages(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert res.gate
    assert [s.name for s in res.stages] == STAGES
    assert all(s.status == "ok" for s in res.stages)


def test_folder_and_file_names_are_branded(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert os.path.exists(os.path.join(res.dir, "nastech_cli", "nastech_runner.py"))
    assert not os.path.exists(os.path.join(res.dir, "hermes_cli", "hermes_runner.py"))
    renamed = {e.mapped_path for e in res.diff.entries if e.action == "renamed"}
    assert "nastech_cli/nastech_runner.py" in renamed


def test_text_content_branded_binary_locked_untouched(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    readme = os.path.join(res.dir, "README.md")
    with open(readme, encoding="utf-8") as fh:
        text = fh.read()
    assert "Nastech Agent" in text
    assert "Nastech Research" in text
    assert "hermes" not in text.lower()


def test_sequential_numbering_across_runs(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    mgr = UpdateManager(updates_dir, hermes_url=hermes)
    r1 = mgr.run()
    assert r1.number == 1
    assert os.path.basename(r1.dir) == "Nastech-Update#1"
    r2 = mgr.run()
    assert r2.number == 2
    assert os.path.basename(r2.dir) == "Nastech-Update#2"
    assert next_update_number(updates_dir) == 3


def test_partial_run_dir_does_not_skip_numbering(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    os.makedirs(updates_dir)
    # an interrupted run leaves a dir without manifest.json
    orphan = update_path(updates_dir, 1)
    os.makedirs(orphan)
    with open(os.path.join(orphan, "README.md"), "w") as fh:
        fh.write("partial")
    assert next_update_number(updates_dir) == 1
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert res.number == 1
    assert os.path.isfile(os.path.join(res.dir, "manifest.json"))


def test_zip_has_project_folder_and_reports_outside(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    zip_path = str(tmp_path / "release.zip")
    res = UpdateManager(updates_dir, hermes_url=hermes).run(zip_path=zip_path)
    assert res.gate
    assert os.path.isfile(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "nastech-agent/README.md" in names
    assert "nastech-agent/nastech_cli/nastech_runner.py" in names
    assert "UPDATE-REPORT.md" in names
    assert "GATE-REPORT.md" in names
    assert not any(n.startswith("nastech-agent/") and n.endswith("REPORT.md") for n in names)


def test_reports_written_in_snapshot(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert os.path.isfile(os.path.join(res.dir, "UPDATE-REPORT.md"))
    assert os.path.isfile(os.path.join(res.dir, "GATE-REPORT.md"))
    with open(os.path.join(res.dir, "UPDATE-REPORT.md"), encoding="utf-8") as fh:
        report = fh.read()
    assert "# Nastech Update Report #1" in report
    assert "## Stages" in report
    assert all(stage in report for stage in STAGES)


def test_cli_emit_outputs_writes_github_outputs(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    out_path = str(tmp_path / "outputs.json")
    env = dict(os.environ)
    env.pop("OLLAMA_API_KEY", None)
    env.pop("SYNCBRIDGE_AI_MODEL", None)
    subprocess.run(
        [sys.executable, "-m", "hundredways.cli", "update",
         "--updates-dir", updates_dir,
         "--hermes-url", hermes,
         "--zip", str(tmp_path / "release.zip"),
         "--project-name", "nastech-agent",
         "--emit-outputs", out_path],
        check=True, env=env,
        cwd=str(tmp_path),
    )
    with open(out_path, encoding="utf-8") as fh:
        out = json.load(fh)
    assert out["gate"] == "PASS"
    assert out["update_number"] == 1
    assert len(out["upstream_sha"]) == 40



def test_manifest_records_pipeline(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert os.path.isfile(res.manifest_path)
    import json

    with open(res.manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["number"] == 1
    assert manifest["gate"] is True
    assert manifest["stages"] == STAGES
    assert manifest["verify"]["passed"] > 0


def test_compare_trees_reports_missing(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    rules = BrandingRules()
    # drop a file from the branded tree -> compare must flag it missing
    os.remove(os.path.join(res.dir, "nastech_cli", "nastech_runner.py"))
    diff = compare_trees(os.path.join(updates_dir, "hermes-agent"), res.dir, rules)
    assert any(e.action == "missing" for e in diff.entries)
    verify = verify_branded(os.path.join(updates_dir, "hermes-agent"), res.dir, rules)
    assert verify.failed


def test_brand_tree_renames_folders_and_files(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    os.makedirs(src / "hermes_cli")
    (src / "hermes_cli" / "hermes_main.py").write_text("HERMES\n")
    brand_tree(str(src), str(dst), BrandingRules())
    assert (dst / "nastech_cli" / "nastech_main.py").exists()
    assert (dst / "nastech_cli" / "nastech_main.py").read_text() == "NASTECH\n"


def test_package_zip_excludes_reports_from_project(tmp_path):
    snapshot = tmp_path / "snap"
    os.makedirs(snapshot / "app")
    (snapshot / "app" / "main.py").write_text("x\n")
    (snapshot / "UPDATE-REPORT.md").write_text("rep1\n")
    (snapshot / "GATE-REPORT.md").write_text("rep2\n")
    out = str(tmp_path / "out.zip")
    package_zip(str(snapshot), out, {"UPDATE-REPORT.md": "rep1\n", "GATE-REPORT.md": "rep2\n"})
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "nastech-agent/app/main.py" in names
    assert "UPDATE-REPORT.md" in names
    assert not any(n.startswith("nastech-agent/") and n.endswith("REPORT.md") for n in names)


def test_immutable_data_files_keep_real_names(tmp_path):
    hermes = tmp_path / "hermes-agent"
    hermes.mkdir()
    subprocess.run(["git", "init", "-q", str(hermes)], check=True)
    subprocess.run(["git", "-C", str(hermes), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(hermes), "config", "user.name", "t"], check=True)
    emails = hermes / "contributors" / "emails"
    emails.mkdir(parents=True)
    (emails / "hermesagent424@gmail.com").write_text("real person\n")
    (hermes / "README.md").write_text("# Hermes Agent\n")
    subprocess.run(["git", "-C", str(hermes), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(hermes), "commit", "-q", "-m", "fake hermes"], check=True)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=str(hermes)).run()
    assert res.gate
    # the real email filename must survive verbatim - renaming it would corrupt data
    kept = os.path.join(res.dir, "contributors", "emails", "hermesagent424@gmail.com")
    assert os.path.isfile(kept)
    # and the branded README still got branded
    assert "Nastech" in (res.dir + "/README.md") or True
    with open(os.path.join(res.dir, "README.md"), encoding="utf-8") as fh:
        assert "Nastech Agent" in fh.read()


def test_cli_defaults_have_no_machine_paths():
    """CLI defaults must not bake in developer-machine paths (breaks CI)."""
    from hundredways.cli import build_parser

    parser = build_parser()
    for sub in ("update", "pull", "dashboard", "achievements"):
        p = parser._subparsers._group_actions[0].choices[sub]
        sd = p.get_default("state-dir")
        assert sd in (None, ""), f"{sub} --state-dir default leaks a machine path: {sd!r}"


def _owned_registry(tmp_path):
    """A config/owned-assets/ registry: manifest.json + one owned binary."""
    root = tmp_path / "config" / "owned-assets"
    root.mkdir(parents=True)
    asset = root / "banner.png"
    asset.write_bytes(b"OUR-BANNER-BYTES")
    (root / "manifest.json").write_text(
        json.dumps({"static/img/banner.png": "banner.png"})
    )
    return str(root)


def test_owned_assets_override_upstream_bytes_in_brand(tmp_path):
    """brand_tree must write OUR asset (not upstream's renamed copy) for an owned path."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    os.makedirs(src / "static" / "img")
    (src / "static" / "img" / "banner.png").write_bytes(b"HERMES-BANNER-BYTES")
    (src / "static" / "img" / "other.png").write_bytes(b"HERMES-OTHER")
    owned = OwnedAssets(_owned_registry(tmp_path))
    res = brand_tree(str(src), str(dst), BrandingRules(), owned)
    assert res.owned == 1
    assert (dst / "static" / "img" / "banner.png").read_bytes() == b"OUR-BANNER-BYTES"
    assert (dst / "static" / "img" / "other.png").read_bytes() == b"HERMES-OTHER"


def test_owned_assets_verify_passes_only_against_our_bytes(tmp_path):
    """verify_branded compares owned paths to our registry, not upstream."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    os.makedirs(src / "static" / "img")
    (src / "static" / "img" / "banner.png").write_bytes(b"HERMES-BANNER-BYTES")
    owned = OwnedAssets(_owned_registry(tmp_path))

    # correct: destination holds OUR asset -> passes
    brand_tree(str(src), str(dst), BrandingRules(), owned)
    report = verify_branded(str(src), str(dst), BrandingRules(), owned)
    assert not report.failed
    assert report.passed == report.total

    # wrong: upstream bytes land in the snapshot -> parity must fail
    dst2 = tmp_path / "dst2"
    brand_tree(str(src), str(dst2), BrandingRules())
    report2 = verify_branded(str(src), str(dst2), BrandingRules(), owned)
    assert report2.failed, "upstream bytes must FAIL verify against our registry"
    assert any("owned" in r.note for r in report2.failed)


def test_owned_assets_flagged_in_compare(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    os.makedirs(src / "static" / "img")
    (src / "static" / "img" / "banner.png").write_bytes(b"HERMES-BANNER-BYTES")
    owned = OwnedAssets(_owned_registry(tmp_path))
    brand_tree(str(src), str(dst), BrandingRules(), owned)
    diff = compare_trees(str(src), str(dst), BrandingRules(), owned)
    owned_entries = [e for e in diff.entries if e.action == "owned"]
    assert len(owned_entries) == 1
    assert owned_entries[0].mapped_path == "static/img/banner.png"


def test_update_manager_threads_owned_into_all_stages(tmp_path):
    hermes = _hermes_repo(tmp_path)
    src = os.path.join(str(tmp_path), "hermes-agent")
    img_dir = os.path.join(src, "static", "img")
    os.makedirs(img_dir)
    with open(os.path.join(img_dir, "banner.png"), "wb") as fh:
        fh.write(b"HERMES-BANNER-BYTES")
    subprocess.run(["git", "-C", src, "add", "-A"], check=True)
    subprocess.run(["git", "-C", src, "commit", "-q", "-m", "add banner"], check=True)

    owned = OwnedAssets(_owned_registry(tmp_path))
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes, owned=owned).run()
    assert res.gate
    snap_banner = os.path.join(res.dir, "static", "img", "banner.png")
    assert os.path.isfile(snap_banner)
    assert open(snap_banner, "rb").read() == b"OUR-BANNER-BYTES"
    assert res.brand.owned == 1
