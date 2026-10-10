"""Tests for the ordered update pipeline: staged runs, folder-name branding,
release zip layout, sequential numbering, and reports."""

import json
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

from hundredways.assets import OwnedAssets
from hundredways.integrity import audit_candidate_tree
from hundredways.updates import (
    STAGES,
    UpdateManager,
    _HARDENED_PREPARE_SCRIPT,
    _SKILL_DESC_FULL_NEW,
    _SKILL_DESC_FULL_OLD,
    _UPSTREAM_PREPARE_SCRIPT,
    _reconcile_anon_surface_copy,
    _reconcile_apt_pool_first_char,
    _reconcile_ci_runner_budgets,
    _reconcile_credential_display_test,
    _reconcile_desktop_export_order,
    _reconcile_desktop_path_fixtures,
    _reconcile_desktop_skin_alias,
    _reconcile_install_ps1_store_guard_test,
    _reconcile_locale_key_order,
    _reconcile_python_test_timeout,
    _reconcile_windows_boot_lifecycle_race,
    _reconcile_docs_posix_separators,
    _reconcile_fork_skill_catalogs,
    _reconcile_fork_skill_sidebars,
    _reconcile_portable_shebang,
    _reconcile_portal_override_test_collision,
    _reconcile_reasoning_effort_selection,
    _reconcile_sigv4_vectors,
    _reconcile_skill_description_hardline,
    _reconcile_skill_docs_order,
    _reconcile_telegram_mention_context_lengths,
    _reconcile_uv_lock_file,
    _reconcile_vercel_prepare,
    _sigv4_vector_signature,
    brand_tree,
    compare_trees,
    fork_manifest_upstream_sha,
    next_update_number,
    package_zip,
    reconcile_tree,
    update_path,
    verify_branded,
)
from hundredways.rules import BrandingRules, collision_safe_path_map
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


def test_case_colliding_contributor_records_are_disambiguated_without_data_loss(tmp_path):
    src = tmp_path / "hermes-agent"
    dst = tmp_path / "candidate"
    emails = src / "contributors" / "emails"
    emails.mkdir(parents=True)
    lower = emails / "agent@agents-Mac-mini.local"
    title = emails / "agent@Agents-Mac-mini.local"
    lower.write_text("first distinct contributor record\n")
    title.write_text("second distinct contributor record\n")

    rules = BrandingRules()
    paths = [
        "contributors/emails/agent@agents-Mac-mini.local",
        "contributors/emails/agent@Agents-Mac-mini.local",
    ]
    path_map = collision_safe_path_map(paths, rules)
    assert len({target.casefold() for target in path_map.values()}) == 2
    assert all("--case-" in target for target in path_map.values())

    brand_tree(str(src), str(dst), rules)
    assert (dst / path_map[paths[0]]).read_text() == lower.read_text()
    assert (dst / path_map[paths[1]]).read_text() == title.read_text()
    assert verify_branded(str(src), str(dst), rules).failed == []
    assert not [issue for issue in audit_candidate_tree(dst) if issue.code == "case-collision"]


def test_desktop_export_order_is_reconciled_after_nastech_rename(tmp_path):
    candidate = tmp_path / "candidate"
    plugin = candidate / "apps" / "desktop" / "src" / "contrib" / "plugin.ts"
    sdk = candidate / "apps" / "desktop" / "src" / "sdk" / "index.ts"
    plugin.parent.mkdir(parents=True)
    sdk.parent.mkdir(parents=True)
    plugin.write_text(
        "export type { PluginRestOptions } from '@/nastech'\n"
        "export type { NastechOpenTarget } from '@/lib/nastech-open-target'\n",
        encoding="utf-8",
    )
    sdk.write_text(
        "/** Grab-to-pan for overflow containers (boards, timelines, wide tables) —\n"
        " *  the shared scrub primitive; don't hand-roll drag-to-scroll. */\n"
        "export { type GrabScroll, useGrabScroll } from '@/hooks/use-grab-scroll'\n"
        "/** Localized copy. `useI18n` reuses the app's strings; `usePluginI18n(id)` +\n"
        " *  `ctx.i18n.register` let a plugin ship its OWN locale bundles, scoped like\n"
        " *  `ctx.storage` and resolved against the app's active locale — no core edit. */\n"
        "export {\n  type Locale,\n  type PluginI18n,\n  type PluginLocaleBundles,\n  type PluginMessages,\n  type PluginMessageValue,\n  type PluginTranslate,\n  useI18n,\n  usePluginI18n\n} from '@/i18n'\n"
        "/** The live gateway instance type — for typing the `gateway` prop `McpTab`\n"
        " *  takes; obtain the instance from `host.getGateway()`. */\n"
        "export type { NastechGateway } from '@/nastech'\n"
        "/** THE way to run a decorative rAF animation (avatars, shimmer, sprites):\n"
        " *  fps budget + hidden/minimized/unfocused pause + idle dormancy + teardown.\n"
        " *  Plugins must route animation clocks through this instead of raw rAF loops\n"
        " *  so a disabled plugin or an empty roster costs zero frames. */\n"
        "export { type BudgetedLoop, type BudgetedLoopOptions, createBudgetedLoop } from '@/lib/budgeted-loop'\n"
        "export { compactNumber } from '@/lib/format'\n"
        "export { cn } from '@/lib/utils'\n"
        "export { THEMES_AREA } from '@/themes/user-themes'\n",
        encoding="utf-8",
    )

    changed = _reconcile_desktop_export_order(str(candidate))
    assert set(changed) == {
        "apps/desktop/src/contrib/plugin.ts",
        "apps/desktop/src/sdk/index.ts",
    }
    plugin_text = plugin.read_text(encoding="utf-8")
    sdk_text = sdk.read_text(encoding="utf-8")
    assert plugin_text.index("@/lib/nastech-open-target") < plugin_text.index("@/nastech")
    assert sdk_text.index("@/lib/budgeted-loop") < sdk_text.index("@/nastech")
    assert sdk_text.index("@/lib/format") < sdk_text.index("@/nastech")
    assert sdk_text.index("@/lib/utils") < sdk_text.index("@/nastech")


def test_desktop_export_order_moves_branded_statements_to_sorted_position(tmp_path):
    candidate = tmp_path / "candidate"
    sdk = candidate / "apps" / "desktop" / "src" / "sdk" / "index.ts"
    sdk.parent.mkdir(parents=True)
    sdk.write_text(
        "export { pluginSettingsHref } from '@/contrib/settings-pages'\n"
        "/** The live gateway instance type — for typing the `gateway` prop `ConnectorsTab`\n"
        " *  takes; obtain the instance from `host.getGateway()`. */\n"
        "export type { NastechGateway } from '@/nastech'\n"
        "export { type GrabScroll, useGrabScroll } from '@/hooks/use-grab-scroll'\n"
        "export { triggerHaptic as haptic } from '@/lib/haptics'\n"
        "export { completeMcpDesktopOAuth } from '@/lib/mcp-dashboard-oauth'\n"
        "export * as icons from '@/lib/icons'\n"
        "export type { NastechOpenTarget } from '@/lib/nastech-open-target'\n"
        "export { isSubmitEnter } from '@/lib/ime'\n"
        "export { LruCache } from '@/lib/lru-cache'\n"
        "export { catalogProviderMatches } from '@/lib/model-options'\n"
        "export { cn } from '@/lib/utils'\n"
        "export type { StatusResponse } from '@/types/nastech'\n"
        "export { useStore as useValue } from '@nanostores/react'\n"
        "export { retintTheme, themeHue } from '@/themes/retint'\n"
        "export type { DesktopTheme } from '@/themes/types'\n"
        "export { compactNumber } from '@nastech/shared'\n",
        encoding="utf-8",
    )

    changed = _reconcile_desktop_export_order(str(candidate))
    assert changed == ["apps/desktop/src/sdk/index.ts"]
    text = sdk.read_text(encoding="utf-8")
    assert text.index("'@/lib/utils'") < text.index("'@/nastech'")
    assert text.index("'@/nastech'") < text.index("'@/themes/retint'")
    assert text.index("'@/lib/ime'") < text.index("'@/lib/nastech-open-target'")
    assert text.index("'@/lib/model-options'") < text.index("'@/lib/nastech-open-target'")
    assert text.index("'@nanostores/react'") < text.index("'@nastech/shared'")
    # The JSDoc travels with its statement instead of being orphaned.
    assert (
        "/** The live gateway instance type" in text
        and text.index("/** The live gateway instance type")
        < text.index("export type { NastechGateway }")
    )


def test_ci_runner_budgets_normalize_free_runner_timeouts(tmp_path):
    candidate = tmp_path / "candidate"
    wf = candidate / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "tests-os.yml").write_text(
        "jobs:\n"
        "    env:\n"
        "          NASTECH_TEST_WORKERS: ${{ matrix.marker == 'windows' && '16' || '' }}\n"
        "          NASTECH_TEST_SLICE: ${{ matrix.slice || '' }}\n"
        "        include:\n"
        "          - name: Windows-only tests\n"
        "            runner: windows-latest\n"
        "            marker: windows\n"
        "          - name: Windows-only tests (arm64)\n"
        "            runner: windows-latest\n"
        "            marker: windows\n"
        "            timeout: 60\n",
        encoding="utf-8",
    )
    (wf / "windows-install-update-e2e.yml").write_text(
        "jobs:\n"
        "  install-update:\n"
        "    # A typical run is ~17 min; one slow runner took 28 min for the same work "
        "(run 36295230146).\n"
        "    timeout-minutes: 75\n"
        "    steps:\n"
        "      - uses: x\n"
        "          # One journey per file, all in parallel (each is mostly network + subprocess "
        "wait).\n"
        "          NASTECH_TEST_WORKERS: '6'\n",
        encoding="utf-8",
    )
    machine = candidate / "tests" / "e2e" / "core" / "windows_update" / "_machine.py"
    machine.parent.mkdir(parents=True)
    machine.write_text(
        'OPT_IN_ENV = "NASTECH_E2E_WINDOWS_INSTALL"\n'
        "INSTALL_TIMEOUT = 1500.0\n"
        "UPDATE_TIMEOUT = 1200.0\n"
        "CMD_TIMEOUT = 300.0\n",
        encoding="utf-8",
    )

    changed = _reconcile_ci_runner_budgets(str(candidate))
    assert set(changed) == {
        ".github/workflows/tests-os.yml",
        ".github/workflows/windows-install-update-e2e.yml",
        "tests/e2e/core/windows_update/_machine.py",
    }
    os_yml = (wf / "tests-os.yml").read_text(encoding="utf-8")
    assert "            timeout: 45\n" in os_yml
    assert "            timeout: 60\n" in os_yml  # arm64 row untouched
    assert "          NASTECH_TEST_FILE_TIMEOUT: '900'\n" in os_yml
    install = (wf / "windows-install-update-e2e.yml").read_text(encoding="utf-8")
    assert "    timeout-minutes: 120\n" in install
    assert "          NASTECH_TEST_WORKERS: '4'\n" in install
    assert "INSTALL_TIMEOUT = 2400.0\nUPDATE_TIMEOUT = 1800.0\n" in machine.read_text(
        encoding="utf-8"
    )
    # Every edit is a fixed point.
    assert _reconcile_ci_runner_budgets(str(candidate)) == []


def test_python_test_timeout_reconcile_widens_templated_and_e2e_jobs(tmp_path):
    candidate = tmp_path / "candidate"
    wf = candidate / ".github" / "workflows"
    wf.mkdir(parents=True)
    path = wf / "tests.yml"
    path.write_text(
        "jobs:\n"
        "  test:\n"
        "    name: Run tests (${{ matrix.slice }}/2)\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 30\n"
        "  e2e:\n"
        "    if: inputs.e2e\n"
        "    timeout-minutes: 30\n"
        "  e2e-upgrade-plan:\n"
        "    timeout-minutes: 5\n"
        "  e2e-upgrade:\n"
        "    timeout-minutes: 60\n"
        "  unrelated:\n"
        "    name: e2e\n"
        "    timeout-minutes: 30\n",
        encoding="utf-8",
    )

    changed = _reconcile_python_test_timeout(str(candidate))
    assert changed == [".github/workflows/tests.yml"]
    text = path.read_text(encoding="utf-8")
    assert (
        "    name: Run tests (${{ matrix.slice }}/2)\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 75\n"
    ) in text
    assert text.count("timeout-minutes: 75") == 2
    # A job whose *name* is ``e2e`` keeps its budget; only the ``e2e`` key moves.
    assert "name: e2e\n    timeout-minutes: 30\n" in text
    # The auxiliary e2e upgrade lanes keep their own budgets.
    assert "timeout-minutes: 5\n" in text
    assert "timeout-minutes: 60\n" in text
    # Fixed point.
    assert _reconcile_python_test_timeout(str(candidate)) == []
    # A file without the target jobs is a no-op.
    path.write_text("jobs:\n  other:\n    timeout-minutes: 30\n", encoding="utf-8")
    assert _reconcile_python_test_timeout(str(candidate)) == []


def test_windows_boot_lifecycle_accepts_already_gone_taskkill(tmp_path):
    candidate = tmp_path / "candidate"
    path = candidate / "tests" / "e2e" / "core" / "windows" / "test_boot_lifecycle.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "import subprocess\n\n\n"
        "def pump() -> None:\n"
        "    return None\n\n\n"
        "def test_serve_tree_kill_leaves_no_orphans_and_reboots(tmp_path: Path) -> None:\n"
        "    for _ in range(1):\n"
        "        if True:\n"
        "            killed = taskkill_tree(first.pid)\n"
        "            assert killed.returncode == 0, killed.stderr\n",
        encoding="utf-8",
    )
    assert _reconcile_windows_boot_lifecycle_race(str(candidate)) is True
    text = path.read_text(encoding="utf-8")
    assert "def _kill_raced_its_target(" in text
    assert "killed.returncode == 0 or _kill_raced_its_target(killed)" in text
    assert _reconcile_windows_boot_lifecycle_race(str(candidate)) is False


def test_install_ps1_store_guard_test_collapses_host_wrapping(tmp_path):
    candidate = tmp_path / "candidate"
    path = candidate / "scripts" / "tests" / "test-install-ps1-store-guard.ps1"
    path.parent.mkdir(parents=True)
    path.write_text(
        "try {\n"
        "    Assert-True ($r.All -match 'tool store would land inside the checkout') "
        "'refusal names the tool-store cause'\n"
        "}\n",
        encoding="utf-8",
    )
    assert _reconcile_install_ps1_store_guard_test(str(candidate)) is True
    text = path.read_text(encoding="utf-8")
    assert "($r.All -replace '\\s+', ' ') -match" in text
    assert _reconcile_install_ps1_store_guard_test(str(candidate)) is False


def test_desktop_skin_alias_resolves_by_theme_name(tmp_path):
    candidate = tmp_path / "candidate"
    path = candidate / "apps" / "desktop" / "src" / "themes" / "use-skin-command.ts"
    path.parent.mkdir(parents=True)
    path.write_text(
        "const ALIASES = { default: 'nastech', nastech: 'classic' }\n"
        "export function useSkinCommand() {\n"
        "  return (rawArg: string) => {\n"
        "      const arg = rawArg.trim()\n"
        "      const normalized = arg.toLowerCase()\n"
        "      const targetName = ALIASES[normalized] || normalized\n"
        "\n"
        "      const target = availableThemes.find(\n"
        "        t => t.name.toLowerCase() === targetName || t.label.toLowerCase() === normalized\n"
        "      )\n"
        "      return target\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    assert _reconcile_desktop_skin_alias(str(candidate)) is True
    text = path.read_text(encoding="utf-8")
    assert "const alias = ALIASES[normalized]" in text
    assert "(!alias && t.label" in text
    assert _reconcile_desktop_skin_alias(str(candidate)) is False


def test_desktop_path_fixtures_use_branded_suffix(tmp_path):
    candidate = tmp_path / "candidate"
    electron = candidate / "apps" / "desktop" / "electron"
    scripts = candidate / "apps" / "desktop" / "scripts"
    electron.mkdir(parents=True)
    scripts.mkdir(parents=True)
    data_paths = electron / "data-paths.test.ts"
    data_paths.write_text("const expected = 'hermesmagic-test'\n", encoding="utf-8")
    bundle_env = scripts / "bundle-env.test.mjs"
    bundle_env.write_text("child: 'hermesmagic-test'\n", encoding="utf-8")

    changed = _reconcile_desktop_path_fixtures(str(candidate))
    assert set(changed) == {
        "apps/desktop/electron/data-paths.test.ts",
        "apps/desktop/scripts/bundle-env.test.mjs",
    }
    assert "hermesmagic-test" not in data_paths.read_text(encoding="utf-8")
    assert "nastechmagic-test" in data_paths.read_text(encoding="utf-8")
    assert "nastechmagic-test" in bundle_env.read_text(encoding="utf-8")
    assert _reconcile_desktop_path_fixtures(str(candidate)) == []


def test_locale_key_order_restores_generated_sort(tmp_path):
    candidate = tmp_path / "candidate"
    locales = candidate / "locales"
    locales.mkdir(parents=True)
    tui_keys = ["slashCmd.usage.old", "composer.hermesBalance", "settings.ares"]
    (locales / "_keys.tui.json").write_text(
        json.dumps(tui_keys, indent=2) + "\n", encoding="utf-8"
    )
    desktop_keys = ["skills.nastech_not_connected", "composer.missing_app"]
    (locales / "_keys.desktop.json").write_text(
        json.dumps({"surface": "desktop", "keys": desktop_keys}, indent=2) + "\n",
        encoding="utf-8",
    )

    changed = _reconcile_locale_key_order(str(candidate))
    assert changed == [
        "locales/_keys.desktop.json",
        "locales/_keys.tui.json",
    ]
    assert json.loads((locales / "_keys.tui.json").read_text(encoding="utf-8")) == sorted(
        tui_keys
    )
    assert json.loads((locales / "_keys.desktop.json").read_text(encoding="utf-8")) == {
        "surface": "desktop",
        "keys": sorted(desktop_keys),
    }
    assert _reconcile_locale_key_order(str(candidate)) == []


def test_reconcile_preserves_fixed_width_credential_mask_assertion(tmp_path):
    candidate = tmp_path / "candidate"
    target = candidate / "tests" / "nastech_cli" / "test_show_config_credential.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        'agent_key="nastech-REALKEY-abcdef9876"\n'
        'assert "nastech-REA" in out\n',
        encoding="utf-8",
    )

    assert _reconcile_credential_display_test(str(candidate)) == 1
    updated = target.read_text(encoding="utf-8")
    assert 'agent_key="nastech-REALKEY-abcdef9876"' in updated
    assert 'assert "nastech-" in out' in updated
    assert 'assert "nastech-REA" in out' not in updated
    assert _reconcile_credential_display_test(str(candidate)) == 0


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
    assert manifest["source_provenance"]["remote_url"] == str(hermes).replace("hermes", "nastech")
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


def test_contributor_emails_are_mapped_without_data_loss(tmp_path):
    hermes = tmp_path / "hermes-agent"
    hermes.mkdir()
    subprocess.run(["git", "init", "-q", str(hermes)], check=True)
    subprocess.run(["git", "-C", str(hermes), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(hermes), "config", "user.name", "t"], check=True)
    emails = hermes / "contributors" / "emails"
    names = hermes / "contributors" / "names"
    emails.mkdir(parents=True)
    names.mkdir(parents=True)
    (emails / "hermesagent424@gmail.com").write_text("Hermes Agent contact\n")
    (emails / "mchermes@edu.example").write_text("mchermes@edu.example\n")
    (names / "Hermes Person").write_text("Hermes Person\n")
    (hermes / "README.md").write_text("# Hermes Agent\n")
    subprocess.run(["git", "-C", str(hermes), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(hermes), "commit", "-q", "-m", "fake hermes"], check=True)
    updates_dir = str(tmp_path / "Updates-Commits")
    res = UpdateManager(updates_dir, hermes_url=str(hermes)).run()
    assert res.gate
    mapped = os.path.join(res.dir, "contributors", "emails", "nastechagent424@gmail.com")
    assert os.path.isfile(mapped)
    assert open(mapped, encoding="utf-8").read() == "Nastech Agent contact\n"
    embedded = os.path.join(res.dir, "contributors", "emails", "mcnastech@edu.example")
    assert open(embedded, encoding="utf-8").read() == "mcnastech@edu.example\n"
    # The sole identity exception is contributor names.
    assert os.path.isfile(os.path.join(res.dir, "contributors", "names", "Hermes Person"))
    with open(os.path.join(res.dir, "README.md"), encoding="utf-8") as fh:
        assert "Nastech Agent" in fh.read()


def test_cli_banner_reconciliation_removes_opaque_upstream_art(tmp_path):
    from hundredways.updates import _reconcile_cli_banner_identity

    root = tmp_path / "candidate"
    cli = root / "nastech_cli"
    cli.mkdir(parents=True)
    (cli / "banner.py").write_text(
        'NASTECH_AGENT_LOGO = """OLD BLOCK ART"""\n\n'
        'NASTECH_CADUCEUS = """OLD SYMBOL"""\n\n\n# section\n'
        "_logo = _bskin.banner_logo if _bskin and hasattr(_bskin, 'banner_logo') and _bskin.banner_logo else NASTECH_AGENT_LOGO\n"
    )
    (cli / "skin_engine.py").write_text(
        'SKIN = {"banner_logo": """OPAQUE UPSTREAM ART""", "name": "default"}\n'
    )

    fixed = _reconcile_cli_banner_identity(str(root))
    banner = (cli / "banner.py").read_text(encoding="utf-8")
    skin = (cli / "skin_engine.py").read_text(encoding="utf-8")
    assert fixed == ["nastech_cli/banner.py", "nastech_cli/skin_engine.py"]
    assert "NASTECH AGENT" in banner
    assert "𓄃" in banner
    assert "OLD BLOCK ART" not in banner
    assert "OLD SYMBOL" not in banner
    assert "_logo = NASTECH_AGENT_LOGO" in banner
    assert "NASTECH AGENT 𓄃" in skin
    assert "OPAQUE UPSTREAM ART" not in skin


def test_cli_banner_reconciliation_handles_getattr_skin_logo_form(tmp_path):
    from hundredways.updates import _reconcile_cli_banner_identity

    root = tmp_path / "candidate"
    cli = root / "nastech_cli"
    cli.mkdir(parents=True)
    (cli / "banner.py").write_text(
        'NASTECH_AGENT_LOGO = """OLD BLOCK ART"""\n\n'
        'NASTECH_CADUCEUS = """OLD SYMBOL"""\n\n\n'
        '        console.print(getattr(_bskin, "banner_logo", None) or NASTECH_AGENT_LOGO)\n'
    )

    fixed = _reconcile_cli_banner_identity(str(root))
    banner = (cli / "banner.py").read_text(encoding="utf-8")
    assert fixed == ["nastech_cli/banner.py"]
    assert "_logo = NASTECH_AGENT_LOGO" in banner
    assert 'getattr(_bskin, "banner_logo"' not in banner
    assert "console.print(_logo)" in banner


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
        "Cloud regex: /ares-3009\\.agents\\.nastechresearch\\.com/i\n"
        "Assets: https://hermes-assets.nousresearch.com/upstream/sha256/abc\n"
        "Legacy assets: https://nastech-assets.nastechresearch.github.io/releases/x\n"
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
    # regex-escaped hostnames follow the same migration as literal URLs.
    assert r"ares-3009\.agents\.nastechresearch\.github\.io" in text
    assert r"ares-3009\.agents\.nastechresearch\.com" not in text
    # assets compound migrates to the R2-backed worker, never to github.io
    # (the Pages assets subdomain is a dead TLS origin)
    assert "https://nastech-assets.nastechresearch.workers.dev/upstream/sha256/abc" in text
    assert "nastech-assets.nastechresearch.com" not in text
    assert "nastech-assets.nastechresearch.github.io" not in text
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




def test_reconcile_pages_workflow_publishes_installer_and_root_redirect(tmp_path):
    root = tmp_path / "branded"
    workflow = root / ".github" / "workflows" / "deploy-site.yml"
    workflow.parent.mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\n")
    workflow.write_text(
        "      - name: Stage deployment\n"
        "        run: |\n"
        "          mkdir -p _site/docs\n"
        "          cp -r website/build/* _site/docs/\n"
    )

    result = reconcile_tree(str(root))
    text = workflow.read_text()

    assert ".github/workflows/deploy-site.yml" in result.fixed
    assert "cp scripts/install.sh _site/install.sh" in text
    assert "cat > _site/index.html <<'HTML'" in text
    assert "url=./docs/" in text


def test_reconcile_quickstart_hardware_fixture(tmp_path):
    root = tmp_path / "branded"
    test_path = root / "tests" / "nastech_cli" / "test_local_quickstart.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "def test_quickstart_runs_all_three_legs(client, monkeypatch, tmp_path):\n"
        "    calls: list[str] = []\n\n"
        "    # Leg 1:\n"
        "    monkeypatch.setattr(\n"
        "        \\\"nastech_cli.local_runtime.binaries.installed_tags\\\", lambda: [])\n\n"
        "def test_quickstart_skips_satisfied_legs(client, monkeypatch):\n"
        "    calls: list[str] = []\n\n"
        "    monkeypatch.setattr(\n"
        "        \\\"nastech_cli.local_runtime.binaries.installed_tags\\\", lambda: [\\\"b10362\\\"])\n"
    )
    result = reconcile_tree(str(root))
    assert result.fixed == ["tests/nastech_cli/test_local_quickstart.py"]
    text = test_path.read_text()
    assert text.count("100WAYS: hardware-independent quickstart fixture") == 2
    assert text.count("catalog.select_variant") == 2


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
    assert "baseUrl: '/nastech-agent/docs/'," in (website / "docusaurus.config.ts").read_text()
    assert "return project" in (paths / "paths.ts").read_text()
    lint_config = (root / "eslint.config.shared.mjs").read_text()
    assert "'perfectionist/sort-imports': [\n        'warn'," in lint_config
    assert "'perfectionist/sort-named-exports': ['warn'" in lint_config
    assert "'perfectionist/sort-named-imports': ['warn'" in lint_config


def test_reconcile_anon_surface_copy_neutralizes_chat_copy(tmp_path):
    candidate = tmp_path / "candidate"
    root = candidate / "nastech_cli"
    root.mkdir(parents=True)
    (root / "anon_sign_in.py").write_text(
        'UPGRADE_REASON_COPY = {\n'
        '    "user_declined": "No problem, you\'re still on the free Nastech service. Sign in whenever you\'re ready.",\n'
        '}\n'
        'UPGRADE_SERVICE_BUSY = ("Signing in couldn\'t finish because the Nastech service is busy. "\n'
        '                        "Try again in {wait}. Your session is still here in the meantime.")\n'
        'UPGRADE_SERVICE_UNREACHABLE = ("The Nastech service couldn\'t be reached to finish signing you in. "\n'
        '                               "Check your internet connection and try again. Your session is still here.")\n',
        encoding="utf-8",
    )
    (root / "anon_auth.py").write_text(
        'ANON_FAILURE_COPY = {\n'
        '    "gate_closed": f"This version can\'t be used without a Nastech account. {_SIGNIN_IS_FREE}",\n'
        '    "gate_paused": f"Using Nastech without signing in is paused for a moment. {_SIGNIN_IS_FREE}",\n'
        '    "pow": "The Nastech server asked for a proof of work, but that isn\'t implemented in your "\n'
        '    "Agent yet. Sign in with a Nastech account to continue.",\n'
        '    "unreachable": "The Nastech service couldn\'t be reached. Check your internet connection and try again.",\n'
        '    "server_error": "The Nastech service had a hiccup. Try again in a moment.",\n'
        '}\n'
        'FREE_TIER_AVAILABLE_NOTICE = "Free Nastech inference and connectors are now available. "\n'
        'FREE_TIER_STATUS_LINE = f"{FREE_TIER_LABEL} · free tier · nastech/welcome · /login to sign in"\n',
        encoding="utf-8",
    )
    (root / "nastech_account.py").write_text(
        'FREE_TIER_NEEDS_ACCOUNT_CHAT = "This needs a Nastech account. Use /login to sign in."\n',
        encoding="utf-8",
    )
    gateway = candidate / "gateway"
    gateway.mkdir()
    (gateway / "run_notifications.py").write_text(
        'startup = "Inference: Nastech free tier (nastech/welcome). Sign in for more: /login"\n',
        encoding="utf-8",
    )
    tests_gateway = candidate / "tests" / "gateway"
    tests_gateway.mkdir(parents=True)
    (tests_gateway / "test_free_tier_startup_notice.py").write_text(
        'FREE_TIER_LINE = "Inference: Nastech free tier (nastech/welcome). Sign in for more: /login"\n'
        'assert lines[1:] == [FREE_TIER_LINE]\n',
        encoding="utf-8",
    )
    tests = candidate / "tests" / "nastech_cli"
    tests.mkdir(parents=True)
    (tests / "test_anon_failure_modes.py").write_text(
        '(anon_auth.ANON_GATE_CLOSED, 0, False, "Nastech account"),\n'
        'assert "Nastech account" in str(err) and "free" in str(err)\n'
        'assert str(err).startswith("The Nastech server asked for a proof of work, but that isn\'t implemented")\n',
        encoding="utf-8",
    )
    (tests / "test_anon_surfaces.py").write_text(
        '    assert all("/login" in text for text in command_copy)\n'
        '    for text in (*command_copy, *refusal_copy):\n'
        '        # The ruled refusal uses Nastech as the grammatical subject; only that exact product-name\n'
        '        # phrase is exempt from the broad top-level-command gate.\n'
        '        assert "nastech " not in text.replace("this Nastech can", "this product can").lower()\n',
        encoding="utf-8",
    )

    fixed = _reconcile_anon_surface_copy(str(candidate))
    assert set(fixed) == {
        "nastech_cli/anon_sign_in.py",
        "nastech_cli/anon_auth.py",
        "nastech_cli/nastech_account.py",
        "gateway/run_notifications.py",
        "tests/gateway/test_free_tier_startup_notice.py",
        "tests/nastech_cli/test_anon_failure_modes.py",
        "tests/nastech_cli/test_anon_surfaces.py",
    }

    sign_in = (root / "anon_sign_in.py").read_text(encoding="utf-8")
    assert "free service" in sign_in
    assert "because the service is busy" in sign_in
    assert "The service couldn't be reached" in sign_in
    assert "Nastech service" not in sign_in

    anon_auth = (root / "anon_auth.py").read_text(encoding="utf-8")
    assert "without an account" in anon_auth
    assert "Using the free tier without" in anon_auth
    assert '"The server asked for a proof of work' in anon_auth
    assert "Sign in with an account to continue" in anon_auth
    assert "The service couldn't be reached" in anon_auth
    assert "The service had a hiccup" in anon_auth
    assert "Free inference and connectors" in anon_auth
    # Cross-surface status line keeps the brand.
    assert "FREE_TIER_STATUS_LINE" in anon_auth
    assert "Nastech " not in anon_auth.replace("Using the free tier without", "Using X without")

    account = (root / "nastech_account.py").read_text(encoding="utf-8")
    assert "This needs an account" in account
    assert "Nastech account" not in account

    startup = (gateway / "run_notifications.py").read_text(encoding="utf-8")
    assert "Inference: Free tier" in startup
    assert "Nastech free tier" not in startup

    startup_test = (tests_gateway / "test_free_tier_startup_notice.py").read_text(encoding="utf-8")
    assert 'FREE_TIER_LINE = "Inference: Free tier' in startup_test
    assert "Nastech free tier" not in startup_test

    failure_modes = (tests / "test_anon_failure_modes.py").read_text(encoding="utf-8")
    assert '"an account"),' in failure_modes
    assert 'assert "an account" in str(err)' in failure_modes
    assert 'startswith("The server asked' in failure_modes
    assert "Nastech account" not in failure_modes

    anon_surfaces = (tests / "test_anon_surfaces.py").read_text(encoding="utf-8")
    assert "cross_surface = {anon_auth.FREE_TIER_STATUS_LINE}" in anon_surfaces
    assert 'assert "https" not in text.lower()' in anon_surfaces
    assert "continue" in anon_surfaces

    # Idempotent: a second pass fixes nothing.
    assert _reconcile_anon_surface_copy(str(candidate)) == []


def test_reconcile_portal_override_test_collision(tmp_path):
    candidate = tmp_path / "candidate"
    target = candidate / "tests" / "nastech_cli" / "test_nastech_nonproduction_inference_host.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        '    monkeypatch.setenv(var, "https://launch.example.nastechresearch.github.io")\n'
        '    monkeypatch.delenv("NASTECH_PORTAL_BASE_URL", raising=False)\n'
        '    assert helper() == "https://launch.example.nastechresearch.github.io"\n'
        '    monkeypatch.setenv("NASTECH_PORTAL_BASE_URL", NONPROD_PORTAL)\n'
        '    monkeypatch.delenv("NASTECH_INFERENCE_BASE_URL", raising=False)\n',
        encoding="utf-8",
    )

    assert _reconcile_portal_override_test_collision(str(candidate)) == 1
    updated = target.read_text(encoding="utf-8")
    assert 'monkeypatch.delenv("NASTECH_PORTAL_BASE_URL_LEGACY", raising=False)' in updated
    assert 'monkeypatch.delenv("NASTECH_INFERENCE_BASE_URL", raising=False)' in updated
    assert 'setenv("NASTECH_PORTAL_BASE_URL", NONPROD_PORTAL)' in updated
    # The collision line is gone; the second delete of the portal var must not remain.
    assert 'delenv("NASTECH_PORTAL_BASE_URL", raising=False)' not in updated
    # Idempotent.
    assert _reconcile_portal_override_test_collision(str(candidate)) == 0


def test_reconcile_telegram_mention_context_lengths(tmp_path):
    candidate = tmp_path / "candidate"
    target = candidate / "tests" / "gateway" / "test_telegram_mention_context.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        'def test_sole_addressee_text_stays_clean_and_prompt_is_session_stable():\n'
        '    msg = _group_message("/new@nastech_bot", entities=[SimpleNamespace(type="bot_command", offset=0, length=15)])\n'
        '    text = "😀 @nastech_bot 2"\n'
        '    msg = _group_message(text, entities=[SimpleNamespace(type="mention", offset=3, length=11)])\n',
        encoding="utf-8",
    )

    assert _reconcile_telegram_mention_context_lengths(str(candidate)) == 1
    updated = target.read_text(encoding="utf-8")
    assert 'type="bot_command", offset=0, length=16' in updated
    assert 'type="mention", offset=3, length=12' in updated
    assert 'length=15' not in updated
    assert 'length=11' not in updated
    # Idempotent.
    assert _reconcile_telegram_mention_context_lengths(str(candidate)) == 0


def test_reconcile_reasoning_effort_selection(tmp_path):
    candidate = tmp_path / "candidate"
    target = candidate / "nastech_cli" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        'def some_existing(): pass\n'
        '# ---- END PLUGIN-COMPAT ----\n',
        encoding="utf-8",
    )

    assert _reconcile_reasoning_effort_selection(str(candidate)) == 1
    updated = target.read_text(encoding="utf-8")
    assert 'def _prompt_reasoning_effort_selection(efforts, current_effort=""):' in updated
    assert '# ---- END PLUGIN-COMPAT ----\n' in updated
    assert updated.index("# ---- END PLUGIN-COMPAT ----") < updated.index(
        "def _prompt_reasoning_effort_selection"
    )
    # Idempotent.
    assert _reconcile_reasoning_effort_selection(str(candidate)) == 0


def test_reconcile_reasoning_effort_selection_falls_back_before_main_def(tmp_path):
    """When the PLUGIN-COMPAT anchor is gone, graft before ``def main():``.

    Upstream dropped the anchor that the graft used to key on, so the fork's
    preserved curses-migration test could no longer import the symbol.  The
    fallback inserts the module-scope function right before ``def main():``.
    """
    candidate = tmp_path / "candidate"
    target = candidate / "nastech_cli" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        'import sys\n\n'
        'CONST = 1\n\n\n'
        'def helper():\n'
        '    return 2\n\n\n'
        'def main():\n'
        '    print("hi")\n',
        encoding="utf-8",
    )

    assert _reconcile_reasoning_effort_selection(str(candidate)) == 1
    updated = target.read_text(encoding="utf-8")
    assert 'def _prompt_reasoning_effort_selection(efforts, current_effort=""):' in updated
    # The graft lands at module scope, before main, not inside helper().
    assert updated.index("def _prompt_reasoning_effort_selection(") > updated.index("def helper()")
    assert updated.index("def _prompt_reasoning_effort_selection(") < updated.index("def main():")
    # Idempotent.
    assert _reconcile_reasoning_effort_selection(str(candidate)) == 0


def test_reconcile_apt_pool_first_char_rewrites_stale_pool_dirs(tmp_path):
    """Branded deb names leave the test's hardcoded first-char pool dir stale.

    ``scripts/termux/stage_apt_repo.py`` derives the pool directory from
    ``deb.name[0].lower()``; branding renames the fixture deb but not the
    single-letter ``"h"`` literal in the expected paths.  The reconcile
    rewrites those literals to the branded package's first character, exactly
    mirroring the runtime derivation, and never touches consistent rows.
    """
    candidate = tmp_path / "candidate"
    target = candidate / "tests" / "scripts" / "test_stage_apt_repo.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        'def _stage(pool, out, suite, pool_subdir=""):\n'
        '    return 3\n'
        '\n'
        'def _pool_keys(out, suite):\n'
        '    return set()\n'
        '\n'
        'def test_pool_subdir_is_in_the_path_and_the_index(tmp_path):\n'
        '    make_deb(pool / "nastech-agent_0.21.5_aarch64.deb", "nastech-agent", "0.21.5-1")\n'
        '    assert _stage(pool, out, "nastech-stable", pool_subdir="rc.2-v0.21.5") == 3\n'
        '    deb = out / "pool" / "rc.2-v0.21.5" / "h" / "nastech-agent_0.21.5_aarch64.deb"\n'
        '    assert deb.is_file()\n'
        '    assert _pool_keys(out, "nastech-stable") == {"pool/rc.2-v0.21.5/h/nastech-agent_0.21.5_aarch64.deb"}\n'
        '\n'
        'def test_no_pool_subdir_keeps_the_plain_layout(tmp_path):\n'
        '    make_deb(pool / "nastech-agent_1.2.3_aarch64.deb", "nastech-agent", "1.2.3-1")\n'
        '    assert (out / "pool" / "h" / "nastech-agent_1.2.3_aarch64.deb").is_file()\n'
        '    assert _pool_keys(out, "nastech-canary") == {"pool/h/nastech-agent_1.2.3_aarch64.deb"}\n'
        '\n'
        'def test_two_attempts_of_one_version_use_different_pool_keys(tmp_path):\n'
        '    keys = {"pool/rc.1-v0.21.5/h/nastech-agent_0.21.5_aarch64.deb",\n'
        '            "pool/rc.2-v0.21.5/h/nastech-agent_0.21.5_aarch64.deb"}\n'
        '    assert {k.split("/")[1] for k in keys} == {"rc.1-v0.21.5", "rc.2-v0.21.5"}\n',
        encoding="utf-8",
    )

    assert _reconcile_apt_pool_first_char(str(candidate)) is True
    updated = target.read_text(encoding="utf-8")
    # The stale "h" literals all become the branded package's first char.
    assert 'out / "pool" / "rc.2-v0.21.5" / "n" / "nastech-agent_0.21.5_aarch64.deb"' in updated
    assert '{"pool/rc.2-v0.21.5/n/nastech-agent_0.21.5_aarch64.deb"}' in updated
    assert '(out / "pool" / "n" / "nastech-agent_1.2.3_aarch64.deb").is_file()' in updated
    assert '{"pool/n/nastech-agent_1.2.3_aarch64.deb"}' in updated
    # The attempt-ref test only reads index 1, so its subdir literals stand.
    assert 'k.split("/")[1]' in updated
    # Idempotent: the second call is a fixed-point no-op.
    assert _reconcile_apt_pool_first_char(str(candidate)) is False


def test_reconcile_apt_pool_first_char_noops_on_consistent_rows(tmp_path):
    """Files whose pool dir already matches the deb's first char stay untouched."""
    candidate = tmp_path / "candidate"
    target = candidate / "tests" / "scripts" / "test_stage_apt_repo.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        '    deb = out / "pool" / "rc.2-v0.21.5" / "n" / "nastech-agent_0.21.5_aarch64.deb"\n'
        '    assert _pool_keys(out, "nastech-stable") == {"pool/rc.2-v0.21.5/n/nastech-agent_0.21.5_aarch64.deb"}\n'
        '    make_deb(pool / "a.deb", "alpha", "1.0-1")\n'
        '    copied = out / "pool" / "a" / "a.deb"\n',
        encoding="utf-8",
    )
    before = target.read_text(encoding="utf-8")
    assert _reconcile_apt_pool_first_char(str(candidate)) is False
    assert target.read_text(encoding="utf-8") == before


def test_reconcile_fork_skill_catalogs_adds_row_and_resolves_link(tmp_path):
    """Fork-preserved skills invisible to the upstream catalog get a row.

    The upstream docs-contract test requires every shipped SKILL.md to have a
    catalog row whose link resolves.  A fork-only optional skill (blender-mcp)
    is preserved into the tree but absent from the upstream-derived catalog;
    the reconcile appends its row and links to the fork's existing docs page.
    """
    candidate = tmp_path / "candidate"
    cat = candidate / "website" / "docs" / "reference" / "optional-skills-catalog.md"
    cat.parent.mkdir(parents=True)
    cat.write_text(
        "---\n"
        "title: Optional Skills Catalog\n"
        "---\n"
        "\n"
        "# Optional Skills Catalog\n"
        "\n"
        "## creative\n"
        "\n"
        "| Skill | Description |\n"
        "|-------|-------------|\n"
        "| [**canvas-design**](../user-guide/skills/optional/creative/creative-canvas-design.md) | Design art. |\n",
        encoding="utf-8",
    )
    skill = candidate / "optional-skills" / "creative" / "blender-mcp" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: blender-mcp\n"
        "description: Drive Blender via the catalog blender MCP, with bpy recipes.\n"
        "---\n",
        encoding="utf-8",
    )
    fork_page = (
        candidate / "website" / "docs" / "user-guide" / "skills" / "optional" / "creative"
        / "creative-blender-mcp.md"
    )
    fork_page.parent.mkdir(parents=True)
    fork_page.write_text("# Blender MCP\n", encoding="utf-8")

    changed = _reconcile_fork_skill_catalogs(
        str(candidate), ["optional-skills/creative/blender-mcp/SKILL.md"]
    )
    assert changed == ["website/docs/reference/optional-skills-catalog.md"]
    updated = cat.read_text(encoding="utf-8")
    assert "| [**blender-mcp**](../user-guide/skills/optional/creative/creative-blender-mcp.md) | Drive Blender via the catalog blender MCP, with bpy recipes. |" in updated
    # Existing rows stay in place.
    assert "| [**canvas-design**]" in updated
    # New rows land inside the matching ## creative section.
    assert updated.index("## creative") < updated.index("blender-mcp")
    # Idempotent.
    assert (
        _reconcile_fork_skill_catalogs(
            str(candidate), ["optional-skills/creative/blender-mcp/SKILL.md"]
        )
        == []
    )


def test_reconcile_fork_skill_catalogs_creates_missing_page(tmp_path):
    """A fork skill with no docs page gets the generator-convention page."""
    candidate = tmp_path / "candidate"
    cat = candidate / "website" / "docs" / "reference" / "optional-skills-catalog.md"
    cat.parent.mkdir(parents=True)
    cat.write_text(
        "# Optional Skills Catalog\n\n## tts\n\n| Skill | Description |\n|-------|-------------|\n",
        encoding="utf-8",
    )
    skill = candidate / "optional-skills" / "tts" / "nastech-voice" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: nastech-voice\ndescription: Voice output for agents.\n---\n",
        encoding="utf-8",
    )

    changed = _reconcile_fork_skill_catalogs(str(candidate), ["optional-skills/tts/nastech-voice/SKILL.md"])
    page = candidate / "website" / "docs" / "user-guide" / "skills" / "optional" / "tts" / "tts-nastech-voice.md"
    assert page.is_file()
    assert "website/docs/user-guide/skills/optional/tts/tts-nastech-voice.md" in changed
    updated = cat.read_text(encoding="utf-8")
    assert "tts-nastech-voice.md" in updated
    assert "Voice output for agents." in updated
    # Links resolve relative to the catalog file.
    assert (
        "| [**nastech-voice**](../user-guide/skills/optional/tts/tts-nastech-voice.md)" in updated
    )


def test_reconcile_docs_posix_separators_normalizes_preserved_pages_and_catalogs(tmp_path):
    """A fork-preserved Windows-generated docs page is normalized in place.

    The docs-contract suite fails any generated page or catalog whose ``Path``
    row or relative ``.md`` link carries a backslash -- the signature of an
    artifact committed from a Windows host.  The fork preserves such a page
    byte-for-byte; the reconcile rewrites only the two flagged contexts and
    leaves every other byte (including literal ``\\n`` escapes in prose)
    untouched, and a second pass is a no-op.
    """
    candidate = tmp_path / "candidate"
    page_rel = (
        "website/docs/user-guide/skills/bundled/autonomous-ai-agents/"
        "autonomous-ai-agents-merge-reconciler.md"
    )
    page = candidate / page_rel
    page.parent.mkdir(parents=True)
    page.write_text(
        "| Path | `skills/autonomous-ai-agents\\merge-reconciler` |\n"
        "- [blender](../user-guide/skills/optional/creative\\creative-blender-mcp.md)\n"
        "# prose keeping a literal escape \\n must survive untouched\n",
        encoding="utf-8",
    )
    catalog_rel = "website/docs/reference/optional-skills-catalog.md"
    catalog = candidate / catalog_rel
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        "## creative\n\n"
        "| Skill | Description |\n"
        "|-------|-------------|\n"
        "| [**x**](../user-guide/skills/optional/creative\\x.md) | a |\n"
        "| Path | `skills/optional/creative\\x` |\n",
        encoding="utf-8",
    )

    changed = _reconcile_docs_posix_separators(str(candidate), [page_rel])

    assert sorted(changed) == sorted([page_rel, catalog_rel])
    page_text = page.read_text(encoding="utf-8")
    assert "skills/autonomous-ai-agents/merge-reconciler" in page_text
    assert "optional/creative/creative-blender-mcp.md" in page_text
    assert "skills/autonomous-ai-agents\\merge-reconciler" not in page_text
    assert "creative\\creative-blender-mcp.md" not in page_text
    # A literal escape in prose is not a flagged context and must survive.
    assert "# prose keeping a literal escape \\n must survive untouched" in page_text
    catalog_text = catalog.read_text(encoding="utf-8")
    assert "optional/creative/x.md" in catalog_text
    assert "skills/optional/creative/x" in catalog_text
    assert "\\" not in catalog_text
    # Idempotent.
    assert _reconcile_docs_posix_separators(str(candidate), [page_rel]) == []


def test_reconcile_docs_posix_separators_scopes_to_preserved_pages_and_catalogs(tmp_path):
    """Upstream-branded pages are never rewritten; catalogs always are.

    Preserved pages are the drift source (a fork inherits stale Windows
    artifacts); an upstream page is regenerated by upstream CI on Linux and is
    already clean.  Only preserved skill pages and the two reference catalogs
    are in scope.
    """
    candidate = tmp_path / "candidate"
    stale_upstream = (
        candidate
        / "website" / "docs" / "user-guide" / "skills" / "bundled" / "other" / "other.md"
    )
    stale_upstream.parent.mkdir(parents=True)
    stale_upstream.write_text("| Path | `skills/other\\stale` |\n", encoding="utf-8")

    changed = _reconcile_docs_posix_separators(str(candidate), [])

    assert changed == []
    assert "skills/other\\stale" in stale_upstream.read_text(encoding="utf-8")


def test_reconcile_uv_lock_rewrites_misaki_source_to_the_fork(tmp_path):
    """uv.lock's third-party misaki Git source is rebranded to the fork.

    The lock is a LOCKED path (byte-copied, never token-branded), so the
    upstream ``NousResearch`` URL would survive branding and trip the audit.
    The fork serves the same pinned rev/hash, so reconcile rebrands just the
    host and preserves ``?rev=...`` + ``#sha`` exactly.
    """
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    lock = candidate / "uv.lock"
    lock.write_text(
        'version = 1\n'
        '[[package]]\n'
        'name = "nastech-agent"\n'
        'version = "0.28.0"\n'
        'source = { editable = "." }\n'
        'dependencies = [\n'
        "{ name = \"misaki\", extras = [\"en\"], marker = \"sys_platform != 'win32'\", "
        'git = "https://github.com/NousResearch/misaki.git?rev=f03fd2be7346952a83d3d4845c217fc7667f322d" },\n'
        ']\n'
        '[[package]]\n'
        'name = "misaki"\n'
        'version = "0.5.4"\n'
        'source = { git = "https://github.com/NousResearch/misaki.git?rev=f03fd2be7346952a83d3d4845c217fc7667f322d#f03fd2be7346952a83d3d4845c217fc7667f322d" }\n'
    )

    assert _reconcile_uv_lock_file(str(lock), "nastech-agent") == 1

    text = lock.read_text(encoding="utf-8")
    assert "github.com/NastechResearch/misaki.git" in text
    assert "github.com/NousResearch/misaki.git" not in text
    # rev + sha untouched
    assert "?rev=f03fd2be7346952a83d3d4845c217fc7667f322d" in text
    assert "#f03fd2be7346952a83d3d4845c217fc7667f322d" in text

    # Idempotent fixed point.
    assert _reconcile_uv_lock_file(str(lock), "nastech-agent") == 0


def test_sigv4_vector_signature_port_matches_upstream_vectors():
    """The pure SigV4 port reproduces upstream's botocore-pinned vectors.

    These are external fixed-point fixtures (botocore 1.43.81 at a pinned
    timestamp/creds), so this asserts the fork's recompute path signs
    identically to the candidate's r2.py — the property that makes the
    vector reconcile safe to run on every branded tree.
    """
    vectors = [
        ("GET", "/", {}, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
         "us-east-1/service", "33399fd3d4a9d6104710c7c04005f7c959f8b1f8bf41b823587ed36b079e453f"),
        ("PUT", "/hermes-releases/HermesBundled-0.28.0-win-x64.msix", {},
         "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072", "auto/s3",
         "05ba50acfb54042fac330848af50877e5fb477c4f2063c2f77f9cc80855eb1e9"),
        ("GET", "/hermes-releases",
         {"list-type": "2", "prefix": "HermesBundled-0.28.0-", "max-keys": "1000"},
         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "auto/s3",
         "3ec423c452a318664c85fbcc25667ad07201aedce688e3bb6b345b4baaa39d90"),
        ("DELETE", "/hermes-releases/HermesBundled-0.28.0+canary.20260818T000000Z-win-arm64.msix", {},
         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "auto/s3",
         "40dba7bf7356837cf496d950605dfc2c62d82ca2b30aa45c8c0dc8dab7bc1bd1"),
    ]
    for method, path, query, payload, scope, expected in vectors:
        assert _sigv4_vector_signature(method, path, query, payload, scope) == expected


def test_reconcile_sigv4_vectors_recomputes_branded_hexes(tmp_path):
    """Branded r2 vector rows get their signature hex recomputed in place.

    Branding rewrites the path/query literals (``/hermes-releases`` ->
    ``/nastech-releases``) but hex constants are not brand tokens, so the
    precomputed signatures go stale.  Reconcile recomputes each row's
    signature from the branded inputs; rows with no brand tokens are
    unchanged, and a second pass is a no-op.
    """
    candidate = tmp_path / "candidate"
    target = candidate / "tests" / "scripts" / "test_release_r2.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        'EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"\n'
        "AKID = \"AKIDEXAMPLE\"\n"
        "SECRET = \"wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY\"\n"
        "NOW = \"20150830T123600Z\"\n"
        "@pytest.mark.parametrize('method,path,query,payload,scope,signature', [\n"
        "    ('GET', '/', {}, EMPTY_SHA, 'us-east-1/service',\n"
        "     '33399fd3d4a9d6104710c7c04005f7c959f8b1f8bf41b823587ed36b079e453f'),\n"
        "    ('PUT', '/nastech-releases/NastechBundled-0.28.0-win-x64.msix', {},\n"
        "     '44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072', 'auto/s3',\n"
        "     '05ba50acfb54042fac330848af50877e5fb477c4f2063c2f77f9cc80855eb1e9'),\n"
        "    ('GET', '/nastech-releases', {'list-type': '2', 'prefix': 'NastechBundled-0.28.0-', 'max-keys': '1000'},\n"
        "     EMPTY_SHA, 'auto/s3', '3ec423c452a318664c85fbcc25667ad07201aedce688e3bb6b345b4baaa39d90'),\n"
        "    ('DELETE', '/nastech-releases/NastechBundled-0.28.0+canary.20260818T000000Z-win-arm64.msix', {},\n"
        "     EMPTY_SHA, 'auto/s3', '40dba7bf7356837cf496d950605dfc2c62d82ca2b30aa45c8c0dc8dab7bc1bd1'),\n"
        "])\n"
    )

    assert _reconcile_sigv4_vectors(str(candidate)) is True
    text = target.read_text(encoding="utf-8")

    # GET '/' row has no brand tokens -> stored hex stays valid
    assert "33399fd3d4a9d6104710c7c04005f7c959f8b1f8bf41b823587ed36b079e453f" in text
    # branded rows recomputed: PUT / list / DELETE
    assert "2781cf57ac770a3175ba199468d272af2e0ad1d86ed150fae52cdb04cbd5ef22" in text
    assert "567516db50bdfecc6b7d2ce91fa5d18be90116a235663f58b313eeaf79314c58" in text
    assert "257071a42395280668f771db569fafc88aa65b63b16e5e44acc2c4c337d897a1" in text
    # stale hexes gone
    assert "05ba50acfb54042fac330848af50877e5fb477c4f2063c2f77f9cc80855eb1e9" not in text
    assert "3ec423c452a318664c85fbcc25667ad07201aedce688e3bb6b345b4baaa39d90" not in text
    assert "40dba7bf7356837cf496d950605dfc2c62d82ca2b30aa45c8c0dc8dab7bc1bd1" not in text

    # Idempotent fixed point.
    assert _reconcile_sigv4_vectors(str(candidate)) is False


def test_reconcile_sigv4_vectors_upstream_rows_are_a_noop(tmp_path):
    """An unskewed (upstream) vector table is already a fixed point."""
    candidate = tmp_path / "candidate"
    target = candidate / "tests" / "scripts" / "test_release_r2.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        'EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"\n'
        "@pytest.mark.parametrize('method,path,query,payload,scope,signature', [\n"
        "    ('GET', '/', {}, EMPTY_SHA, 'us-east-1/service',\n"
        "     '33399fd3d4a9d6104710c7c04005f7c959f8b1f8bf41b823587ed36b079e453f'),\n"
        "    ('PUT', '/hermes-releases/HermesBundled-0.28.0-win-x64.msix', {},\n"
        "     '44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072', 'auto/s3',\n"
        "     '05ba50acfb54042fac330848af50877e5fb477c4f2063c2f77f9cc80855eb1e9'),\n"
        "])\n"
    )

    assert _reconcile_sigv4_vectors(str(candidate)) is False
    text = target.read_text(encoding="utf-8")
    assert "/hermes-releases/HermesBundled-0.28.0-win-x64.msix" in text
    assert "05ba50acfb54042fac330848af50877e5fb477c4f2063c2f77f9cc80855eb1e9" in text


def test_reconcile_skill_description_hardline_propagates_to_generated_docs(tmp_path):
    """The trim reaches every generated artifact the docs generator derives.

    docs-site-checks reruns website/scripts/generate-skill-docs.py and diffs;
    the catalog row and per-skill page embed the SKILL.md description, so
    trimming only the SKILL.md leaves the generated tree stale.
    """
    candidate = tmp_path / "candidate"
    old = "Use, configure, theme, extend, and orchestrate Nastech Agent"
    new = "Configure, theme, extend, and orchestrate Nastech Agent"

    skill = candidate / "skills" / "autonomous-ai-agents" / "nastech-agent" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(f'name: nastech-agent\ndescription: "{old}."\n', encoding="utf-8")

    catalog = candidate / "website" / "docs" / "reference" / "skills-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        f"| [`nastech-agent`](x) | {old}. | `autonomous-ai-agents/nastech-agent` |\n",
        encoding="utf-8",
    )

    page = (
        candidate
        / "website"
        / "docs"
        / "user-guide"
        / "skills"
        / "bundled"
        / "autonomous-ai-agents"
        / "autonomous-ai-agents-nastech-agent.md"
    )
    page.parent.mkdir(parents=True)
    page.write_text(f'title: "Nastech Agent — {old}"\n\n{old}.\n', encoding="utf-8")

    changed = _reconcile_skill_description_hardline(str(candidate))
    assert "skills/autonomous-ai-agents/nastech-agent/SKILL.md" in changed
    assert "website/docs/reference/skills-catalog.md" in changed
    assert (
        "website/docs/user-guide/skills/bundled/autonomous-ai-agents/"
        "autonomous-ai-agents-nastech-agent.md" in changed
    )
    for path in (skill, catalog, page):
        text = path.read_text(encoding="utf-8")
        assert old not in text
        assert new in text
    assert _reconcile_skill_description_hardline(str(candidate)) == []


def test_reconcile_skill_description_hardline_propagates_after_source_trim(tmp_path):
    """The trim reaches the generated docs even once the SKILL.md is trimmed.

    An earlier sync trims the SKILL.md, so the source-side guard no longer
    fires; nesting the generated-docs propagation behind it made the reconcile
    silently stop propagating after the first sync — leaving a stale catalog
    row and page that fail docs-site-checks.
    """
    candidate = tmp_path / "candidate"
    old = _SKILL_DESC_FULL_OLD
    new = _SKILL_DESC_FULL_NEW

    skill = candidate / "skills" / "autonomous-ai-agents" / "nastech-agent" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(f'name: nastech-agent\ndescription: "{new}."\n', encoding="utf-8")

    catalog = candidate / "website" / "docs" / "reference" / "skills-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        f"| [`nastech-agent`](x) | {old}. | `autonomous-ai-agents/nastech-agent` |\n",
        encoding="utf-8",
    )

    page = (
        candidate
        / "website"
        / "docs"
        / "user-guide"
        / "skills"
        / "bundled"
        / "autonomous-ai-agents"
        / "autonomous-ai-agents-nastech-agent.md"
    )
    page.parent.mkdir(parents=True)
    page.write_text(f'title: "Nastech Agent — {old}"\n\n{old}.\n', encoding="utf-8")

    changed = _reconcile_skill_description_hardline(str(candidate))
    assert "skills/autonomous-ai-agents/nastech-agent/SKILL.md" not in changed
    assert "website/docs/reference/skills-catalog.md" in changed
    assert (
        "website/docs/user-guide/skills/bundled/autonomous-ai-agents/"
        "autonomous-ai-agents-nastech-agent.md" in changed
    )
    for path in (catalog, page):
        text = path.read_text(encoding="utf-8")
        assert old not in text
        assert new in text
    # Idempotent: the trimmed SKILL.md is left untouched, nothing changes.
    assert _reconcile_skill_description_hardline(str(candidate)) == []


def test_reconcile_skill_docs_order_sorts_rows_and_sidebar_by_slug(tmp_path):
    """Branding renames change slugs, so the inherited order drifts.

    The generator sorts catalog sections and sidebar arrays by slug; this
    reconcile reproduces only that order and is idempotent.
    """
    candidate = tmp_path / "candidate"
    for slug in ("nastech-agent-skill-authoring", "inspecting-nastech-desktop-dom"):
        p = candidate / "skills" / "software-development" / slug / "SKILL.md"
        p.parent.mkdir(parents=True)
        p.write_text("name: x\n", encoding="utf-8")

    catalog = candidate / "website" / "docs" / "reference" / "skills-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        "## software-development\n\n"
        "| Skill | Description | Path |\n"
        "|-------|-------------|------|\n"
        "| [`nastech-agent-skill-authoring`](x) | a | "
        "`software-development/nastech-agent-skill-authoring` |\n"
        "| [`inspecting-nastech-desktop-dom`](x) | b | "
        "`software-development/inspecting-nastech-desktop-dom` |\n",
        encoding="utf-8",
    )

    sidebar = candidate / "website" / "sidebars.ts"
    sidebar.parent.mkdir(parents=True, exist_ok=True)
    sidebar.write_text(
        "        {\n"
        "          type: 'category',\n"
        "          label: 'software-development',\n"
        "          key: 'skills-bundled-software-development',\n"
        "          items: [\n"
        "            'user-guide/skills/bundled/software-development/"
        "software-development-nastech-agent-skill-authoring',\n"
        "            'user-guide/skills/bundled/software-development/"
        "software-development-inspecting-nastech-desktop-dom',\n"
        "          ],\n"
        "        },\n",
        encoding="utf-8",
    )

    changed = _reconcile_skill_docs_order(str(candidate))
    assert "website/docs/reference/skills-catalog.md" in changed
    assert "website/sidebars.ts" in changed

    rows = [ln for ln in catalog.read_text().splitlines() if ln.startswith("| [`")]
    assert rows[0].startswith("| [`inspecting-nastech-desktop-dom`")
    assert rows[1].startswith("| [`nastech-agent-skill-authoring`")

    ids = [
        ln.strip().strip(",").strip("'")
        for ln in sidebar.read_text().splitlines()
        if "user-guide/skills" in ln
    ]
    assert ids[0].endswith("software-development-inspecting-nastech-desktop-dom")
    assert _reconcile_skill_docs_order(str(candidate)) == []


def test_reconcile_skill_docs_order_resolves_nested_optional_slugs(tmp_path):
    """A nested optional category (mlops/training/axolotl) sorts by slug.

    The page id is ``mlops-training-axolotl``; the sort key is the directory
    slug ``axolotl``, so the SKILL.md tree must be scanned, not string-split.
    """
    candidate = tmp_path / "candidate"
    for sub, slug in (("training", "axolotl"), ("research", "dspy")):
        p = candidate / "optional-skills" / "mlops" / sub / slug / "SKILL.md"
        p.parent.mkdir(parents=True)
        p.write_text("name: x\n", encoding="utf-8")

    catalog = candidate / "website" / "docs" / "reference" / "optional-skills-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        "## mlops\n\n"
        "| Skill | Description |\n"
        "|-------|-------------|\n"
        "| [**dspy**](../user-guide/skills/optional/mlops/mlops-research-dspy.md) | d. |\n"
        "| [**axolotl**](../user-guide/skills/optional/mlops/mlops-training-axolotl.md) | a. |\n",
        encoding="utf-8",
    )

    changed = _reconcile_skill_docs_order(str(candidate))
    assert changed == ["website/docs/reference/optional-skills-catalog.md"]
    rows = [ln for ln in catalog.read_text().splitlines() if ln.startswith("| [**")]
    assert "axolotl" in rows[0]
    assert "dspy" in rows[1]
    assert _reconcile_skill_docs_order(str(candidate)) == []


def test_reconcile_fork_skill_sidebars_adds_missing_category_entry(tmp_path):
    """A fork-only skill preserved into the tree needs a sidebar doc id.

    The Skills sidebar is upstream-derived and lacks the entry; insert it as
    its own item line inside the category's items array (idempotent).
    """
    candidate = tmp_path / "candidate"
    skill = candidate / "optional-skills" / "creative" / "blender-mcp" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("name: blender-mcp\n", encoding="utf-8")

    sidebar = candidate / "website" / "sidebars.ts"
    sidebar.parent.mkdir(parents=True, exist_ok=True)
    sidebar.write_text(
        "        {\n"
        "          type: 'category',\n"
        "          label: 'creative',\n"
        "          key: 'skills-optional-creative',\n"
        "          items: [\n"
        "            'user-guide/skills/optional/creative/creative-archify',\n"
        "          ],\n"
        "        },\n",
        encoding="utf-8",
    )

    preserved = ["optional-skills/creative/blender-mcp/SKILL.md"]
    assert _reconcile_fork_skill_sidebars(str(candidate), preserved) == [
        "website/sidebars.ts"
    ]
    text = sidebar.read_text(encoding="utf-8")
    assert (
        "creative-archify',\n"
        "            'user-guide/skills/optional/creative/creative-blender-mcp',\n"
        "          ]," in text
    )
    assert _reconcile_fork_skill_sidebars(str(candidate), preserved) == []


def _upstream_prepare_package_json() -> str:
    payload = {"scripts": {"prepare": f'node -e "{_UPSTREAM_PREPARE_SCRIPT}"'}}
    return json.dumps(payload, indent=2) + "\n"


def test_reconcile_vercel_prepare_hardens_hook_install(tmp_path):
    """Vercel installs with .git present but no devDeps -> lefthook absent.

    The upstream ``prepare`` then exits 127 and fails the deployment before the
    build.  Harden it to probe lefthook, and remain idempotent.
    """
    candidate = tmp_path / "candidate"
    pkg = candidate / "package.json"
    pkg.parent.mkdir(parents=True)
    pkg.write_text(_upstream_prepare_package_json(), encoding="utf-8")

    assert _reconcile_vercel_prepare(str(candidate)) is True
    assert _reconcile_vercel_prepare(str(candidate)) is False
    script = json.loads(pkg.read_text(encoding="utf-8"))["scripts"]["prepare"]
    assert script == f'node -e "{_HARDENED_PREPARE_SCRIPT}"'
    assert "!existsSync('.git')" in script
    assert "['--version']" in script


def test_reconcile_vercel_prepare_leaves_fork_variant_untouched(tmp_path):
    """A fork-authored prepare script is never rewritten."""
    candidate = tmp_path / "candidate"
    pkg = candidate / "package.json"
    pkg.parent.mkdir(parents=True)
    pkg.write_text(
        json.dumps({"scripts": {"prepare": "echo fork-owned"}}), encoding="utf-8"
    )
    assert _reconcile_vercel_prepare(str(candidate)) is False
    assert json.loads(pkg.read_text(encoding="utf-8"))["scripts"]["prepare"] == "echo fork-owned"


def test_reconcile_vercel_prepare_exits_zero_without_lefthook(tmp_path):
    """Behaviour: with .git but no lefthook on PATH, prepare must exit 0."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    candidate = tmp_path / "candidate"
    pkg = candidate / "package.json"
    pkg.parent.mkdir(parents=True)
    pkg.write_text(_upstream_prepare_package_json(), encoding="utf-8")
    assert _reconcile_vercel_prepare(str(candidate)) is True

    (candidate / ".git").mkdir()
    prepare = json.loads(pkg.read_text(encoding="utf-8"))["scripts"]["prepare"]
    inner = prepare[len('node -e "') : -1]
    result = subprocess.run(
        [node, "-e", inner],
        cwd=candidate,
        env={**os.environ, "PATH": str(candidate)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_reconcile_portable_shebang_rewrites_embedded_bash_payload(tmp_path):
    """An embedded ``#!/bin/bash`` in a TS payload is a Windows footgun.

    lint.yml's "Require portable Bash shebangs" step fails it; the corrective
    rewrite is ``#!/usr/bin/env bash`` and must be idempotent.
    """
    candidate = tmp_path / "candidate"
    script = candidate / "scripts" / "gen.ts"
    script.parent.mkdir(parents=True)
    script.write_bytes(b'return `#!/bin/bash\nset -u\n')
    stray = candidate / "src" / "ok.py"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b'#!/usr/bin/env bash\n#!/bin/sh\n')

    assert _reconcile_portable_shebang(str(candidate)) == ["scripts/gen.ts"]
    assert b"#!/usr/bin/env bash" in script.read_bytes()
    assert stray.read_bytes() == b'#!/usr/bin/env bash\n#!/bin/sh\n'
    assert _reconcile_portable_shebang(str(candidate)) == []
