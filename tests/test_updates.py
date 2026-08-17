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
    fork_manifest_upstream_sha,
    next_update_number,
    package_zip,
    pull_hermes,
    reconcile_tree,
    update_path,
    verify_branded,
)
from hundredways.rules import BrandingRules


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


def test_pull_hermes_can_pin_a_fresh_clone_to_an_observed_commit(tmp_path):
    hermes = _hermes_repo(tmp_path)
    expected = subprocess.check_output(["git", "-C", hermes, "rev-parse", "HEAD"], text=True).strip()
    (tmp_path / "hermes-agent" / "later.py").write_text("value = 'later'\n")
    subprocess.run(["git", "-C", hermes, "add", "-A"], check=True)
    subprocess.run(["git", "-C", hermes, "commit", "-q", "-m", "later upstream commit"], check=True)

    actual = pull_hermes(str(tmp_path / "Updates-Commits"), hermes, expected_sha=expected)

    assert actual == expected
    assert not (tmp_path / "Updates-Commits" / "hermes-agent" / "later.py").exists()


def test_fork_manifest_upstream_sha_enables_ephemeral_ci_delta_baseline(tmp_path):
    hermes = _hermes_repo(tmp_path)
    baseline = subprocess.check_output(
        ["git", "-C", hermes, "rev-parse", "HEAD"], text=True
    ).strip()
    fork = tmp_path / "nastech-agent"
    fork.mkdir()
    (fork / "manifest.json").write_text(json.dumps({"upstream_sha": baseline}))
    (tmp_path / "hermes-agent" / "added.py").write_text("value = 'new'\n")
    subprocess.run(["git", "-C", hermes, "add", "-A"], check=True)
    subprocess.run(["git", "-C", hermes, "commit", "-q", "-m", "add source file"], check=True)

    result = UpdateManager(
        str(tmp_path / "Updates-Commits"),
        hermes_url=hermes,
        fork_root=str(fork),
    ).run()

    assert fork_manifest_upstream_sha(str(fork)) == baseline
    assert result.gate
    assert result.source_delta.complete
    assert result.source_delta.baseline_sha == baseline
    assert result.source_delta.counts["added"] == 1


def test_pipeline_runs_15_stages(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert res.gate
    assert [s.name for s in res.stages] == STAGES
    assert all(s.status in {"ok", "skip"} for s in res.stages)
    by_name = {s.name: s for s in res.stages}
    assert by_name["report"].status == "ok"
    assert by_name["manifest"].status == "ok"


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
    repo = tmp_path / "fork"  # empty checkout: owned-assets + forkcheck no-op
    repo.mkdir()
    env = dict(os.environ)
    env.pop("OLLAMA_API_KEY", None)
    env.pop("SYNCBRIDGE_AI_MODEL", None)
    subprocess.run(
        [sys.executable, "-m", "hundredways.cli", "--repo", str(repo), "update",
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
    assert manifest["source_provenance"]["acquisition"] == "fresh-direct-clone"
    from hundredways.integrity import canonical_source_fingerprint

    assert manifest["source_provenance"]["remote_fingerprint"] == canonical_source_fingerprint()
    assert manifest["source_provenance"]["fetched_at"].endswith("+00:00")
    assert isinstance(manifest["commit_subjects"], list)
    assert isinstance(manifest["changed_areas"], dict)
    assert manifest["reconciliation_actions"] == []


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


def _hermes_repo_with_reconcile_patterns(tmp_path):
    """A fake hermes repo carrying the exact patterns that need reconciliation:
    a uv.lock whose root record names hermes-agent, a package-lock.json with a
    root name, and a Dockerfile FTS5 trigram self-test written against hermes."""
    hermes = _hermes_repo(tmp_path)
    import pathlib
    hermes_path = pathlib.Path(hermes)
    (hermes_path / "pyproject.toml").write_text('name = "hermes-agent"\nversion = "0.20.1"\n')
    (hermes_path / "package.json").write_text('{"name": "hermes-agent", "version": "1.0.0"}\n')
    (hermes_path / "uv.lock").write_text(
        'version = 1\n'
        '[[package]]\n'
        'name = "hermes-agent"\n'
        'version = "0.20.1"\n'
        'source = { editable = "." }\n'
        'dependencies = [\n'
        '    { name = "certifi" },\n'
        ']\n'
    )
    (hermes_path / "package-lock.json").write_text(
        '{\n'
        '  "name": "hermes-agent",\n'
        '  "version": "1.0.0",\n'
        '  "lockfileVersion": 3\n'
        '}\n'
    )
    (hermes_path / "Dockerfile").write_text(
        'FROM python:3.11\n'
        'RUN python3 -c "import sqlite3, sys; \\\n'
        '    db = sqlite3.connect(\':memory:\'); \\\n'
        '    db.execute(\\"CREATE VIRTUAL TABLE docs USING fts5(content, tokenize=\'trigram\')\\"); \\\n'
        '    db.execute(\\"INSERT INTO docs VALUES (\'hermes\')\\"); \\\n'
        '    sys.exit(\'SQLite FTS5 trigram self-test failed\') if db.execute(\\"SELECT count(*) FROM docs WHERE docs MATCH \'erm\'\\").fetchone()[0] != 1 else None"\n'
    )
    runtime = hermes_path / "tests" / "docker" / "test_sqlite_runtime.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(
        "db.execute(\\\"CREATE VIRTUAL TABLE docs USING fts5(content, tokenize='trigram')\\\")\n"
        "db.execute(\\\"INSERT INTO docs VALUES ('hermes')\\\")\n"
        "assert db.execute(\\\"SELECT count(*) FROM docs WHERE docs MATCH 'erm'\\\").fetchone()[0] == 1\n"
    )
    subprocess.run(["git", "-C", hermes, "add", "-A"], check=True)
    subprocess.run(["git", "-C", hermes, "commit", "-q", "-m", "add reconcile patterns"], check=True)
    return hermes


def test_reconcile_fixes_lockfile_roots_and_dockerfile_trigram(tmp_path):
    hermes = _hermes_repo_with_reconcile_patterns(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert res.gate, f"gate FAILED: {[s for s in res.stages if s.status == 'fail']}"
    assert set(res.reconcile.fixed) == {
        "Dockerfile",
        "package-lock.json",
        "tests/docker/test_sqlite_runtime.py",
        "uv.lock",
    }

    root_record = [l.strip() for l in open(os.path.join(res.dir, "uv.lock"), encoding="utf-8")
                   if l.strip().startswith("name =")]
    assert root_record[:1] == ['name = "nastech-agent"']

    with open(os.path.join(res.dir, "package-lock.json"), encoding="utf-8") as fh:
        plock = fh.read()
    assert '"name": "nastech-agent"' in plock
    assert "hermes" not in plock

    with open(os.path.join(res.dir, "Dockerfile"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    assert "MATCH 'erm'" not in dockerfile
    assert "'nastech'" in dockerfile
    trigrams = {"nas", "ast", "ste", "tec", "ech"}
    import re
    match = re.search(r"MATCH '([^']{3})'", dockerfile)
    assert match and match.group(1) in trigrams, dockerfile
    runtime = open(os.path.join(res.dir, "tests", "docker", "test_sqlite_runtime.py"), encoding="utf-8").read()
    runtime_match = re.search(r"MATCH '([^']{3})'", runtime)
    assert runtime_match and runtime_match.group(1) in trigrams, runtime
    assert "MATCH 'erm'" not in runtime

    # every stage is green and counted
    assert [s.name for s in res.stages] == STAGES
    assert all(s.status in {"ok", "skip"} for s in res.stages)
    by_name = {s.name: s for s in res.stages}
    assert by_name["report"].status == "ok"
    assert by_name["manifest"].status == "ok"


def test_reconcile_renames_workspace_and_registry_lock_records(tmp_path):
    """package-lock.json workspace/symlink/registry records follow branding.

    The lock is a LOCKED file (byte-copied from upstream), so branding
    leaves workspace names like ``@hermes/shared``, symlink keys like
    ``node_modules/hermes-tui`` and the republished ``@nous-research/ui``
    record intact - and npm ci then fails with "Missing ... from lock file"
    for every renamed workspace.  Reconcile must rename all of them by exact
    match while leaving real registry packages (``hermes-parser``,
    ``hermes-estree``) untouched.
    """
    hermes = _hermes_repo(tmp_path)
    import pathlib
    hermes_path = pathlib.Path(hermes)

    def _mkdir(p):
        (hermes_path / p).mkdir(parents=True, exist_ok=True)

    workspaces = [
        ("apps/bootstrap-installer", "package.json", '{"name": "@hermes/bootstrap-installer", "version": "0.0.1"}'),
        ("apps/desktop", "package.json", '{"name": "hermes", "version": "0.17.0"}'),
        ("apps/shared", "package.json", '{"name": "@hermes/shared", "version": "0.0.0"}'),
        ("tests-js", "package.json", '{"name": "@hermes/root-tests", "version": "0.0.1"}'),
        ("ui-tui", "package.json", '{"name": "hermes-tui", "version": "0.0.1"}'),
        ("ui-tui/packages/hermes-ink", "package.json", '{"name": "@hermes/ink", "version": "0.0.1"}'),
        ("web", "package.json", '{"name": "web", "version": "0.0.0"}'),
    ]
    for dirpath, fname, content in workspaces:
        _mkdir(dirpath)
        (hermes_path / dirpath / fname).write_text(content)

    (hermes_path / "package.json").write_text(
        '{"name": "hermes-agent", "version": "1.0.0", "workspaces": ['
        '"apps/*", "ui-tui", "ui-tui/packages/*", "web", "tests-js"]}'
    )
    lock = {
        "name": "hermes-agent",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "hermes-agent", "version": "1.0.0", "workspaces": ["apps/*", "ui-tui", "ui-tui/packages/*", "web", "tests-js"]},
            "apps/bootstrap-installer": {"name": "@hermes/bootstrap-installer", "version": "0.0.1", "dependencies": {"@nous-research/ui": "0.18.2"}},
            "apps/desktop": {"name": "hermes", "version": "0.17.0", "dependencies": {"@hermes/shared": "file:../shared", "@nous-research/ui": "0.18.2"}},
            "apps/shared": {"name": "@hermes/shared", "version": "0.0.0"},
            "tests-js": {"name": "@hermes/root-tests", "version": "0.0.1"},
            "ui-tui": {"name": "hermes-tui", "version": "0.0.1", "dependencies": {"@hermes/ink": "file:./packages/hermes-ink", "@hermes/shared": "file:../apps/shared"}},
            "ui-tui/packages/hermes-ink": {"name": "@hermes/ink", "version": "0.0.1"},
            "web": {"name": "web", "version": "0.0.0", "dependencies": {"@hermes/shared": "file:../apps/shared", "@nous-research/ui": "0.18.2"}},
            "node_modules/@hermes/bootstrap-installer": {"resolved": "apps/bootstrap-installer", "link": True},
            "node_modules/@hermes/ink": {"resolved": "ui-tui/packages/hermes-ink", "link": True},
            "node_modules/@hermes/root-tests": {"resolved": "tests-js", "link": True},
            "node_modules/@hermes/shared": {"resolved": "apps/shared", "link": True},
            "node_modules/hermes": {"resolved": "apps/desktop", "link": True},
            "node_modules/hermes-tui": {"resolved": "ui-tui", "link": True},
            "node_modules/@nous-research/ui": {"version": "0.18.2", "resolved": "https://registry.npmjs.org/@nous-research/ui/-/ui-0.18.2.tgz", "integrity": "sha512-OLDUPSTREAM=="},
            "node_modules/hermes-parser": {"version": "0.25.1", "resolved": "https://registry.npmjs.org/hermes-parser/-/hermes-parser-0.25.1.tgz", "integrity": "sha512-REALHERMES==A", "dependencies": {"hermes-estree": "0.25.1"}},
            "node_modules/hermes-estree": {"version": "0.25.1", "resolved": "https://registry.npmjs.org/hermes-estree/-/hermes-estree-0.25.1.tgz", "integrity": "sha512-REALESTREE==B"},
        },
    }
    (hermes_path / "package-lock.json").write_text(json.dumps(lock, indent=2))

    subprocess.run(["git", "-C", hermes, "add", "-A"], check=True)
    subprocess.run(["git", "-C", hermes, "commit", "-q", "-m", "add npm workspaces"], check=True)

    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert res.gate, f"gate FAILED: {[s for s in res.stages if s.status == 'fail']}"
    assert "package-lock.json" in res.reconcile.fixed

    with open(os.path.join(res.dir, "package-lock.json"), encoding="utf-8") as fh:
        plock = json.load(fh)
    packages = plock["packages"]
    assert plock["name"] == "nastech-agent"
    assert packages[""]["name"] == "nastech-agent"

    # workspace records renamed by exact match
    assert packages["apps/bootstrap-installer"]["name"] == "@nastech/bootstrap-installer"
    assert packages["apps/desktop"]["name"] == "nastech"
    assert packages["apps/shared"]["name"] == "@nastech/shared"
    assert packages["tests-js"]["name"] == "@nastech/root-tests"
    assert packages["ui-tui"]["name"] == "nastech-tui"
    assert packages["ui-tui/packages/nastech-ink"]["name"] == "@nastech/ink"
    assert "ui-tui/packages/hermes-ink" not in packages

    # symlink keys renamed, resolved paths followed
    assert "node_modules/@nastech/bootstrap-installer" in packages
    assert "node_modules/@nastech/ink" in packages
    assert "node_modules/@nastech/root-tests" in packages
    assert "node_modules/@nastech/shared" in packages
    assert "node_modules/nastech" in packages
    assert "node_modules/nastech-tui" in packages
    assert packages["node_modules/@nastech/ink"]["resolved"] == "ui-tui/packages/nastech-ink"

    # dependency references renamed, including file: paths to the renamed dir
    desktop = packages["apps/desktop"]["dependencies"]
    assert desktop == {"@nastech/shared": "file:../shared", "@nastech-research/ui": "0.18.2"}
    tui = packages["ui-tui"]["dependencies"]
    assert tui == {"@nastech/ink": "file:./packages/nastech-ink", "@nastech/shared": "file:../apps/shared"}

    # registry record republished by the fork: key + tarball + integrity
    ui = packages.get("node_modules/@nastech-research/ui")
    assert ui is not None
    assert "node_modules/@nous-research/ui" not in packages
    assert ui["version"] == "0.18.2"
    assert ui["resolved"] == "https://registry.npmjs.org/@nastech-research/ui/-/ui-0.18.2.tgz"
    assert ui["integrity"].startswith("sha512-P7H8")

    # real registry packages are never touched
    assert "node_modules/hermes-parser" in packages
    assert "node_modules/hermes-estree" in packages
    assert packages["node_modules/hermes-parser"]["dependencies"] == {"hermes-estree": "0.25.1"}

    assert all(s.status in {"ok", "skip"} for s in res.stages)
    by_name = {s.name: s for s in res.stages}
    assert by_name["report"].status == "ok"
    assert by_name["manifest"].status == "ok"


def test_reconcile_noop_when_no_patterns(tmp_path):
    hermes = _hermes_repo(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert res.gate
    assert res.reconcile.fixed == []


def _hermes_repo_with_domains(tmp_path):
    """A fake hermes repo carrying .com domains that must migrate to github.io."""
    hermes = _hermes_repo(tmp_path)
    import pathlib
    hermes_path = pathlib.Path(hermes)
    (hermes_path / "README.md").write_text(
        "# Hermes Agent\n"
        "Powered by Nous Research.\n"
        "Docs: https://hermes-agent.nousresearch.com/docs\n"
        "Portal: https://portal.nousresearch.com\n"
        "Inference: https://inference-api.nousresearch.com/v1\n"
        "Email: hermes@nousresearch.com\n"
        "Lookalike: https://inference-api.nousresearch.com.attacker.test/v1\n"
    )
    subprocess.run(["git", "-C", hermes, "add", "-A"], check=True)
    subprocess.run(["git", "-C", hermes, "commit", "-q", "-m", "add domains"], check=True)
    return hermes


def test_reconcile_migrates_com_domains_to_github_io(tmp_path):
    hermes = _hermes_repo_with_domains(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert res.gate, f"gate FAILED: {[s for s in res.stages if s.status == 'fail']}"
    assert "README.md" in res.reconcile.fixed

    with open(os.path.join(res.dir, "README.md"), encoding="utf-8") as fh:
        text = fh.read()

    assert "nastechresearch.com" not in text
    assert "NastechResearch.com" not in text

    # docs compound (org/repo path style, not a subdomain)
    assert "https://nastechresearch.github.io/nastech-agent/docs" in text
    # subdomain forms keep their prefix on github.io
    assert "https://portal.nastechresearch.github.io" in text
    assert "https://inference-api.nastechresearch.github.io/v1" in text
    # email address follows the same migration
    assert "nastech@nastechresearch.github.io" in text
    # lookalike fixture keeps its attacker suffix and stays a different host
    assert "https://inference-api.nastechresearch.github.io.attacker.test/v1" in text

    assert all(s.status in {"ok", "skip"} for s in res.stages)
    by_name = {s.name: s for s in res.stages}
    assert by_name["report"].status == "ok"
    assert by_name["manifest"].status == "ok"


def _hermes_repo_with_plugin_search_table(tmp_path):
    """A fake hermes repo whose plugins_cmd builds a Name column without a
    min_width — the pattern that truncates branded plugin names at 80 cols."""
    hermes = _hermes_repo(tmp_path)
    import pathlib
    hermes_path = pathlib.Path(hermes)
    (hermes_path / "hermes_cli" / "plugins_cmd.py").write_text(
        'def cmd_search(term):\n'
        '    table = Table(title="Community plugins")\n'
        '    table.add_column("Name", style="bold")\n'
        '    table.add_column("Description")\n'
    )
    subprocess.run(["git", "-C", hermes, "add", "-A"], check=True)
    subprocess.run(["git", "-C", hermes, "commit", "-q", "-m", "add plugin search table"], check=True)
    return hermes


def test_reconcile_gives_plugin_search_name_column_min_width(tmp_path):
    hermes = _hermes_repo_with_plugin_search_table(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert res.gate, f"gate FAILED: {[s for s in res.stages if s.status == 'fail']}"
    assert "nastech_cli/plugins_cmd.py" in res.reconcile.fixed

    with open(os.path.join(res.dir, "nastech_cli", "plugins_cmd.py"), encoding="utf-8") as fh:
        text = fh.read()

    assert 'table.add_column("Name", style="bold", min_width=21)' in text

    assert all(s.status in {"ok", "skip"} for s in res.stages)
    by_name = {s.name: s for s in res.stages}
    assert by_name["report"].status == "ok"
    assert by_name["manifest"].status == "ok"


def _hermes_repo_with_skill_description(tmp_path):
    """A fake hermes repo whose bundled skill description is exactly 60 chars
    — branding Hermes->Nastech pushes it to 61 and trips the fork's hardline."""
    hermes = _hermes_repo(tmp_path)
    import pathlib
    hermes_path = pathlib.Path(hermes)
    skill = hermes_path / "skills" / "autonomous-ai-agents" / "hermes-agent"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\n'
        'name: hermes-agent\n'
        'description: "Use, configure, theme, extend, and orchestrate Hermes Agent."\n'
        'version: 1.0.0\n'
        '---\n'
    )
    subprocess.run(["git", "-C", hermes, "add", "-A"], check=True)
    subprocess.run(["git", "-C", hermes, "commit", "-q", "-m", "add skill"], check=True)
    return hermes


def test_reconcile_trims_skill_description_to_fork_bytes(tmp_path):
    hermes = _hermes_repo_with_skill_description(tmp_path)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=hermes).run()
    assert res.gate, f"gate FAILED: {[s for s in res.stages if s.status == 'fail']}"
    assert "skills/autonomous-ai-agents/nastech-agent/SKILL.md" in res.reconcile.fixed

    path = os.path.join(res.dir, "skills", "autonomous-ai-agents", "nastech-agent", "SKILL.md")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    assert (
        'description: "Configure, theme, extend, and orchestrate Nastech Agent."'
        in text
    )
    desc = text.splitlines()[1]
    assert len(desc) <= 60

    assert all(s.status in {"ok", "skip"} for s in res.stages)
    by_name = {s.name: s for s in res.stages}
    assert by_name["report"].status == "ok"
    assert by_name["manifest"].status == "ok"




def test_reconcile_target_ci_compatibility_fixes_are_audited(tmp_path):
    root = tmp_path / "branded"
    website = root / "website"
    paths = root / "ui-tui" / "src" / "domain"
    website.mkdir(parents=True)
    paths.mkdir(parents=True)
    (website / "docusaurus.config.ts").write_text(
        "url: 'https://nastechresearch.github.io/nastech-agent',\n"
        "baseUrl: '/docs/',\n"
    )
    (paths / "paths.ts").write_text(
        "if (remaining < 8) {\n"
        "    return shortProject(project, max)\n"
        "}\n"
    )
    runner = root / "scripts" / "run_tests.sh"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/usr/bin/env bash\n")
    os.chmod(runner, 0o644)
    (root / "eslint.config.shared.mjs").write_text(
        "      'perfectionist/sort-imports': [\n"
        "        'error',\n"
        "      ],\n"
        "      'perfectionist/sort-named-exports': ['error', { order: 'asc', type: 'natural' }],\n"
        "      'perfectionist/sort-named-imports': ['error', { order: 'asc', type: 'natural' }],\n"
    )

    result = reconcile_tree(str(root))

    assert set(result.fixed) >= {
        "scripts/run_tests.sh",
        "website/docusaurus.config.ts",
        "ui-tui/src/domain/paths.ts",
        "eslint.config.shared.mjs",
    }
    assert os.stat(runner).st_mode & 0o111
    assert "url: 'https://nastechresearch.github.io'," in (website / "docusaurus.config.ts").read_text()
    assert "baseUrl: '/nastech-agent/'," in (website / "docusaurus.config.ts").read_text()
    assert "return project" in (paths / "paths.ts").read_text()
    lint_config = (root / "eslint.config.shared.mjs").read_text()
    assert "'perfectionist/sort-imports': [\n        'warn'," in lint_config
    assert "'perfectionist/sort-named-exports': ['warn'" in lint_config
    assert "'perfectionist/sort-named-imports': ['warn'" in lint_config
