"""Tests for the ordered update pipeline: staged runs, folder-name branding,
release zip layout, sequential numbering, and reports."""

import ast
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import types
import zipfile

import pytest

from hundredways.assets import OwnedAssets
from hundredways.integrity import audit_candidate_tree
from hundredways.updates import (
    STAGES,
    AssetsWorkerIndexError,
    BrowseBudgetFlockTestError,
    ChannelReaderUserAgentError,
    GatewayAllowlistReloadError,
    GatewayPluginGateError,
    TermuxAptKeyDocError,
    UpdateManager,
    WindowsRealGitLocateError,
    _BROWSE_BUDGET_EDITS,
    _BROWSE_BUDGET_TEST_REL,
    _DOCS_PUBLIC_ORIGIN,
    _HARDENED_PREPARE_SCRIPT,
    _SKILL_DESC_FULL_NEW,
    _SKILL_DESC_FULL_OLD,
    _PROMPT_SURFACE_CHARS_PER_TOKEN,
    _UPSTREAM_PREPARE_SCRIPT,
    _reconcile_assets_worker_config,
    _reconcile_assets_worker_index,
    _reconcile_anon_surface_copy,
    _reconcile_apt_pool_first_char,
    _reconcile_browse_budget_flock_tests,
    _reconcile_channel_reader_user_agent,
    _reconcile_ci_runner_budgets,
    _reconcile_connector_test_guest_offline,
    _reconcile_credential_display_test,
    _reconcile_desktop_export_order,
    _reconcile_desktop_notification_timer_teardown,
    _reconcile_desktop_path_fixtures,
    _reconcile_desktop_skin_alias,
    _reconcile_docusaurus_site_config,
    _reconcile_install_ps1_store_guard_test,
    _reconcile_js_test_timeouts,
    _reconcile_locale_key_order,
    _reconcile_preserved_brand_text,
    _reconcile_python_slow_runner_wait_budgets,
    _reconcile_python_test_timeout,
    _reconcile_sync_wait_budgets,
    _reconcile_windows_boot_lifecycle_race,
    _reconcile_windows_real_git_locate,
    _reconcile_docs_posix_separators,
    _reconcile_drop_vercel_deploy_job,
    _reconcile_fork_skill_catalogs,
    _reconcile_fork_skill_sidebars,
    _reconcile_gateway_allowlist_live_reload,
    _reconcile_gateway_plugin_gate_native_ticket,
    _reconcile_install_ps1_store_guard_test,
    _reconcile_locale_key_order,
    _reconcile_managed_gateway_wire_headers,
    _reconcile_pinned_source_clones,
    _reconcile_plugin_catalog_sync_exemption,
    _reconcile_plugin_guard_trusted_installer_hosts,
    _reconcile_portable_shebang,
    _reconcile_portal_override_test_collision,
    _reconcile_preserved_brand_text,
    _reconcile_prompt_surface_snapshot,
    _reconcile_python_test_timeout,
    _reconcile_reasoning_effort_selection,
    _reconcile_sigv4_vectors,
    _reconcile_skill_description_hardline,
    _reconcile_skill_docs_order,
    _reconcile_telegram_mention_context_lengths,
    _reconcile_dead_api_defaults,
    _reconcile_domains,
    _reconcile_security_contact,
    _reconcile_termux_apt_signing_key,
    _reconcile_ui_scope_pins,
    _reconcile_uv_lock_file,
    _reconcile_vercel_prepare,
    _reconcile_windows_boot_lifecycle_race,
    _NASTECH_UI_INTEGRITY,
    _UI_SCOPE_ASSET_EVIDENCE,
    UiScopePinError,
    _sigv4_vector_signature,
    _split_prompt_surface_tiers,
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
        "export type { PluginSettingsPage, PluginSettingsSubpage } from './settings-pages'\n"
        "import type { Contribution } from './types'\n"
        "\n"
        "export type { PluginRestOptions } from '@/nastech'\n"
        "export type { NastechOpenTarget } from '@/lib/nastech-open-target'\n"
        "export type { PluginAppActionId, PluginAppActionInfo, PluginRunActionResult } from '@/lib/keybinds/plugin-actions'\n"
        "export type { PluginNativeNotificationInput, PluginNotificationAction } from '@/store/native-notifications'\n"
        "export type { PetMessageTone, PetSayOptions } from '@/store/pet-plugin-messages'\n",
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
    assert plugin_text.index("@/lib/keybinds/plugin-actions") < plugin_text.index("@/lib/nastech-open-target")
    assert plugin_text.index("@/lib/nastech-open-target") < plugin_text.index("@/nastech")
    assert plugin_text.index("@/nastech") < plugin_text.index("@/store/native-notifications")
    assert plugin_text.index("@/store/native-notifications") < plugin_text.index("@/store/pet-plugin-messages")
    # Non-`@/` exports and the import that separates them stay untouched.
    assert plugin_text.startswith(
        "export type { PluginSettingsPage, PluginSettingsSubpage } from './settings-pages'\n"
        "import type { Contribution } from './types'\n"
    )
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
    # The injected fork-authored comments must not carry the upstream brand:
    # the hardened weekly gate (``audit_first_party_brand`` +
    # ``audit_branding_fixed_point``) rejects any leftover ``hermes``/``nous``
    # token, so the reconciler injects "Upstream ..." prose instead.
    lowered = install.lower()
    assert "hermes" not in install.lower()
    assert "nousresearch" not in lowered
    assert "Upstream runs this on a 32-core windows-latest image" in install
    assert "Upstream runs 6 on a 32-core host" in install
    assert "INSTALL_TIMEOUT = 3000.0\nUPDATE_TIMEOUT = 2400.0\n" in machine.read_text(
        encoding="utf-8"
    )
    # Every edit is a fixed point, and the rendered workflow is a canonical
    # brand fixed point (no residual upstream token the weekly gate would flag).
    rules = BrandingRules()
    assert rules.transform_text(install) == install
    assert _reconcile_ci_runner_budgets(str(candidate)) == []


def test_ci_runner_budgets_inserts_file_timeout_when_e2e_job_already_sets_it(tmp_path):
    """The os-tests Windows row must still receive NASTECH_TEST_FILE_TIMEOUT.

    Regression: the presence guard used to scan the WHOLE file.  Upstream's
    ``e2e-windows`` job already sets NASTECH_TEST_FILE_TIMEOUT, so the guard was
    always false and the ``os-tests`` env block kept the 300 s per-file default,
    which SIGKILLed the desktop-update hand-off test.  The check must be scoped
    to the ``os-tests`` step's env block.
    """
    candidate = tmp_path / "candidate"
    wf = candidate / ".github" / "workflows"
    wf.mkdir(parents=True)
    path = wf / "tests-os.yml"
    path.write_text(
        "jobs:\n"
        "  os-tests:\n"
        "    steps:\n"
        "      - name: Run ${{ matrix.marker }} tests\n"
        "        run: scripts/run_tests.sh\n"
        "        env:\n"
        "          NASTECH_TEST_WORKERS: ${{ matrix.marker == 'windows' && '4' || '2' }}\n"
        "          NASTECH_TEST_SLICE: ${{ matrix.slice || '' }}\n"
        "  e2e-windows:\n"
        "    steps:\n"
        "      - name: Run Windows E2E suite\n"
        "        env:\n"
        "          NASTECH_TEST_FILE_TIMEOUT: '900'\n"
        "          NASTECH_TEST_FILE_RETRIES: '0'\n",
        encoding="utf-8",
    )

    changed = _reconcile_ci_runner_budgets(str(candidate))
    assert changed == [".github/workflows/tests-os.yml"]
    text = path.read_text(encoding="utf-8")
    # Both the os-tests insertion and the pre-existing e2e-windows value.
    assert text.count("NASTECH_TEST_FILE_TIMEOUT: '900'") == 2
    os_env = (
        "          NASTECH_TEST_SLICE: ${{ matrix.slice || '' }}\n"
        "          # e2e-windows already runs NASTECH_TEST_FILE_TIMEOUT=900 on the\n"
        "          # same 4-core image; the Windows-only row needs the same headroom\n"
        "          # (its desktop-update hand-off file was killed at the 300 s\n"
        "          # default). Harmless for the macOS rows.\n"
        "          NASTECH_TEST_FILE_TIMEOUT: '900'\n"
    )
    assert os_env in text
    # The e2e-windows block itself is untouched.
    assert (
        "        env:\n"
        "          NASTECH_TEST_FILE_TIMEOUT: '900'\n"
        "          NASTECH_TEST_FILE_RETRIES: '0'\n"
    ) in text
    # Fixed point, and brand-clean for the first-party audit.
    assert _reconcile_ci_runner_budgets(str(candidate)) == []
    assert BrandingRules().transform_text(text) == text


def _browse_budget_tree(tmp_path, text):
    """A branded candidate tree carrying ``text`` as the drift-test file."""
    candidate = tmp_path / "candidate"
    path = candidate / _BROWSE_BUDGET_TEST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return candidate, path


def test_browse_budget_flock_tests_rebuild_the_host_aware_bounds(tmp_path):
    """Every rewrite pair applies, in order, and the result is a fixed point.

    The fixture is the rule's own pre-image stitched together, so a pair that no longer matches
    upstream -- or one that overlaps its neighbour's context -- fails here instead of on the
    fork's macOS lane.
    """
    pre = "".join(before for before, _ in _BROWSE_BUDGET_EDITS)
    expected = "".join(after for _, after in _BROWSE_BUDGET_EDITS)
    candidate, path = _browse_budget_tree(tmp_path, pre)

    assert _reconcile_browse_budget_flock_tests(str(candidate)) is True
    text = path.read_text(encoding="utf-8")
    assert text == expected
    assert _reconcile_browse_budget_flock_tests(str(candidate)) is False
    assert path.read_text(encoding="utf-8") == expected
    # The rewritten file must charge the budgeted window, not the child's whole wall time.
    assert "assert window < budget_s + 2, out" in text
    assert 'result.split(" ", 4)' in text
    assert "assert float(elapsed) < budget_s + 2" not in text


@pytest.mark.parametrize("drop", [0, 3, 7, 11])
def test_browse_budget_flock_tests_fail_loudly_when_upstream_moved(tmp_path, drop):
    """A rewrite that stopped matching raises; it never no-ops into a silent red lane."""
    pairs = list(_BROWSE_BUDGET_EDITS)
    pairs.pop(drop)
    candidate, _ = _browse_budget_tree(
        tmp_path, "".join(before for before, _ in pairs) + "\ndef _unrelated():\n    return 1\n"
    )

    with pytest.raises(BrowseBudgetFlockTestError) as excinfo:
        _reconcile_browse_budget_flock_tests(str(candidate))
    message = str(excinfo.value)
    assert f"rewrite #{drop + 1}" in message
    assert "_reconcile_browse_budget_flock_tests" in message
    assert "macOS" in message


def test_browse_budget_flock_reconcile_is_registered_in_reconcile_tree(tmp_path):
    """``reconcile_tree`` must dispatch the new rule, or the fork never receives it."""
    root = tmp_path / "candidate"
    path = root / _BROWSE_BUDGET_TEST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(before for before, _ in _BROWSE_BUDGET_EDITS), encoding="utf-8")

    result = reconcile_tree(str(root))
    assert _BROWSE_BUDGET_TEST_REL in result.fixed
    assert "def _hold_release_latency(" in path.read_text(encoding="utf-8")


def test_new_timeout_reconciles_are_registered_in_reconcile_tree(tmp_path):
    """A new reconcile only runs if ``reconcile_tree`` dispatches it."""
    root = tmp_path / "candidate"
    (root / "tests-js").mkdir(parents=True)
    (root / "tests-js" / "bundle-smoke-metadata.test.mjs").write_text(
        "test('x', () => {\n  expect(1).toBe(1)\n}, 15_000)\n", encoding="utf-8"
    )
    (root / "tests" / "tui_gateway").mkdir(parents=True)
    (root / "tests" / "tui_gateway" / "test_compute_host.py").write_text(
        "def _read_json_line(out, timeout: float = 2.0):\n    return out.get(timeout=timeout)\n\n\n"
        "def test_x():\n    proc.wait(timeout=2)\n",
        encoding="utf-8",
    )

    result = reconcile_tree(str(root))
    assert "tests-js/bundle-smoke-metadata.test.mjs" in result.fixed
    assert "tests/tui_gateway/test_compute_host.py" in result.fixed


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


def test_js_test_timeouts_reconcile_raises_slow_vitest_budgets(tmp_path):
    candidate = tmp_path / "candidate"
    js = candidate / "tests-js"
    js.mkdir(parents=True)
    smoke = js / "bundle-smoke-metadata.test.mjs"
    smoke.write_text(
        "test('channel smoke', () => {\n"
        "  expect(1).toBe(1)\n"
        "}, 15_000)\n",
        encoding="utf-8",
    )
    probe = js / "update-ui.test.mjs"
    probe.write_text(
        "test('other', () => {\n"
        "  expect(1).toBe(1)\n"
        "})\n\n"
        "test.skipIf(process.platform === 'win32')('probe Git reaches the staged main', () => {\n"
        "  expect(() => sourceBranchProbe.prepareSourceBranchEnvironment(checkout, "
        "'0'.repeat(40), git, capturedEnv, launchEnv)).toThrow(/does not match expected/)\n"
        "  } finally {\n"
        "    fs.rmSync(root, { recursive: true, force: true })\n"
        "  }\n"
        "})\n\n"
        "test('after', () => {\n"
        "  expect(1).toBe(1)\n"
        "})\n",
        encoding="utf-8",
    )

    changed = _reconcile_js_test_timeouts(str(candidate))
    assert set(changed) == {
        "tests-js/bundle-smoke-metadata.test.mjs",
        "tests-js/update-ui.test.mjs",
    }
    assert "}, 60_000)\n" in smoke.read_text(encoding="utf-8")
    assert "}, 15_000)" not in smoke.read_text(encoding="utf-8")
    probe_text = probe.read_text(encoding="utf-8")
    # Exactly the git-probe test gained the budget; the neighbours did not.
    assert probe_text.count(", 60_000)") == 1
    assert "  }\n}, 60_000)\n" in probe_text
    assert probe_text.count("\n})") == 2  # the two untouched tests
    assert _reconcile_js_test_timeouts(str(candidate)) == []
    rules = BrandingRules()
    smoke_text = smoke.read_text(encoding="utf-8")
    assert rules.transform_text(smoke_text) == smoke_text
    assert rules.transform_text(probe_text) == probe_text


def test_js_test_timeouts_reconcile_noops_on_fork_raised_probe(tmp_path):
    candidate = tmp_path / "candidate"
    js = candidate / "tests-js"
    js.mkdir(parents=True)
    probe = js / "update-ui.test.mjs"
    probe.write_text(
        "test.skipIf(process.platform === 'win32')('probe Git reaches the staged main', () => {\n"
        "  expect(() => sourceBranchProbe.prepareSourceBranchEnvironment(checkout, "
        "'0'.repeat(40), git, capturedEnv, launchEnv)).toThrow(/does not match expected/)\n"
        "  } finally {\n"
        "    fs.rmSync(root, { recursive: true, force: true })\n"
        "  }\n"
        "}, 60000)\n",
        encoding="utf-8",
    )
    assert _reconcile_js_test_timeouts(str(candidate)) == []
    assert "}, 60000)" in probe.read_text(encoding="utf-8")


def test_python_slow_runner_wait_budgets_doubles_each_probe(tmp_path):
    candidate = tmp_path / "candidate"
    files = {
        "tests/tui_gateway/test_compute_host.py": (
            "def _read_json_line(out: queue.Queue[dict], timeout: float = 2.0) -> dict:\n"
            "    return out.get(timeout=timeout)\n\n\n"
            "def test_compute_host_line_json_hello_and_shutdown():\n"
            "    proc.wait(timeout=2)\n"
        ),
        "tests/nastech_cli/test_cli_goal_parked_resume.py": (
            "def test_idle_hook(nastech_home):\n"
            "    mgr = goals.GoalManager(session_id='resume-idle')\n"
            '    mgr.wait_for_seconds(1, reason="cooldown")\n'
        ),
        "tests/nastech_cli/test_taskkill_identity_windows_live.py": (
            "def _cleanup(proc):\n"
            "    try:\n"
            "        proc.wait(timeout=10)\n\n\n"
            "class TestTerminatePidIdentityLive:\n"
            "    def test_matching_identity_kills_real_process(self):\n"
            "        proc = _spawn_sleeper()\n"
            "        try:\n"
            "            start = get_process_start_time(proc.pid)\n"
            "            assert proc.wait(timeout=15) is not None\n\n"
            "    def test_mismatched_identity(self):\n"
            "        victim.wait(timeout=30)\n\n"
            "    def test_dead_pid(self):\n"
            "        proc.wait(timeout=30)\n"
        ),
        "tests/scripts/desktop_update/test_desktop_update_windows_marker.py": (
            "    capture_output=True, text=True, timeout=60, check=True,\n"
            "    capture_output=True, text=True, timeout=60, check=True,\n\n\n"
            "def _run(home, *args, timeout: int = 120):\n"
            "    pass\n\n\n"
            "    capture_output=True, text=True, timeout=60, check=True,\n"
        ),
        "tests/scripts/desktop_update/test_desktop_update_windows_marker_lock.py": (
            "    subprocess.run(['x',\n"
            "                    '-Parent', str(script.pid), '-Seconds', '60'],\n"
            "                   capture_output=True, text=True, timeout=90, check=False)\n"
        ),
        "tests/scripts/desktop_update/test_desktop_update_windows_commit_outcome.py": (
            "def _handoff(tmp_path, *args, timeout: int = 150, prepare=None, **env):\n"
            "    pass\n\n\n"
            "def test_hanging():\n"
            "    _handoff(\n"
            "        tmp_path, '-NoGateway', '-ProbeTimeoutSeconds', '10', timeout=120, "
            "HANDOFF_HELP_HANG='1',\n"
            "    )\n"
        ),
    }
    for rel, text in files.items():
        path = candidate.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    changed = _reconcile_python_slow_runner_wait_budgets(str(candidate))
    assert set(changed) == set(files)

    def read(rel):
        return candidate.joinpath(*rel.split("/")).read_text(encoding="utf-8")

    host = read("tests/tui_gateway/test_compute_host.py")
    assert "timeout: float = 15.0" in host and "proc.wait(timeout=15)" in host
    assert "timeout: float = 2.0" not in host
    goal = read("tests/nastech_cli/test_cli_goal_parked_resume.py")
    assert 'wait_for_seconds(3600, reason="cooldown")' in goal
    taskkill = read("tests/nastech_cli/test_taskkill_identity_windows_live.py")
    assert "proc.wait(timeout=20)" in taskkill
    assert "proc.wait(timeout=30) is not None" in taskkill
    assert taskkill.count("wait(timeout=60)") == 2
    assert "wait(timeout=10)" not in taskkill and "wait(timeout=15)" not in taskkill
    assert (
        "        proc = _spawn_sleeper()\n"
        "        # Give the real-time scan a beat with the fresh process object\n"
        "        # before fingerprinting and killing it (the same OS-beat pattern\n"
        "        # the dead-PID sibling test uses for process teardown).\n"
        "        time.sleep(1.0)  # health: allow HX013 -- real-time beat for the free runner\n"
        "        try:\n"
        "            start = get_process_start_time(proc.pid)\n"
    ) in taskkill
    assert taskkill.count("time.sleep(1.0)") == 1
    marker = read("tests/scripts/desktop_update/test_desktop_update_windows_marker.py")
    assert marker.count("timeout=180") == 3 and "timeout=60" not in marker
    assert "timeout: int = 240)" in marker and "timeout: int = 120)" not in marker
    lock = read("tests/scripts/desktop_update/test_desktop_update_windows_marker_lock.py")
    assert "'-Seconds', '120']" in lock and "'-Seconds', '60']" not in lock
    assert "text=True, timeout=180, check=False" in lock and "timeout=90" not in lock
    outcome = read("tests/scripts/desktop_update/test_desktop_update_windows_commit_outcome.py")
    assert "timeout: int = 300" in outcome
    assert "'-ProbeTimeoutSeconds', '10', timeout=240," in outcome and "timeout=120," not in outcome

    # Fixed point: nothing is rewritten twice, and every edited file is a brand
    # transform fixed point (the candidate audit requires it).
    assert _reconcile_python_slow_runner_wait_budgets(str(candidate)) == []
    rules = BrandingRules()
    for rel in files:
        text = read(rel)
        assert rules.transform_text(text) == text


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


def test_windows_real_git_locate_picks_every_git_for_windows_layout(tmp_path):
    """The trampoline swap must not depend on ``libexec\\git-core\\git.exe``.

    Git for Windows 2.56 (the windows-latest image) no longer ships that copy,
    so the old one-path-per-arch candidate list returned ``None`` on a healthy
    install and every Windows update degraded to the ZIP path.  The patched
    locator enumerates every layout Git for Windows has shipped and lets the
    fork-bomb probe pick the real binary.
    """
    candidate = tmp_path / "candidate"
    path = candidate / "nastech_cli" / "update_cmd_git.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from contextlib import suppress\n"
        "from pathlib import Path\n\n\n"
        "def _locate_real_git() -> Optional[Path]:\n"
        "    candidates = [\n"
        '        Path(base) / "Git" / arch / "libexec" / "git-core" / "git.exe"\n'
        '        for base in (r"C:\\Program Files", r"C:\\Program Files (x86)")\n'
        '        for arch in ("clangarm64", "mingw64", "mingw32")\n'
        "    ] + _portable_git_candidates()\n"
        "    return next(c for c in candidates)\n",
        encoding="utf-8",
    )

    assert _reconcile_windows_real_git_locate(str(candidate)) is True
    text = path.read_text(encoding="utf-8")
    # Every layout the patch enumerates, not just the dropped legacy one.
    for pattern in (
        '"bin/git.exe"',
        '"cmd/git.exe"',
        '"*/bin/git.exe"',
        '"*/libexec/git-core/git.exe"',
    ):
        assert pattern in text, pattern
    assert "git_root.glob(pattern)" in text
    # The legacy one-path-per-arch list is gone: no dependency on that exact path.
    assert 'for arch in ("clangarm64", "mingw64", "mingw32")' not in text
    # Idempotent (fixed point) and brand-clean for the first-party audit.
    assert _reconcile_windows_real_git_locate(str(candidate)) is False
    assert BrandingRules().transform_text(text) == text


def test_windows_real_git_locate_noop_without_the_file_and_raises_on_shape_move(tmp_path):
    candidate = tmp_path / "candidate"
    # No upstream counterpart at all: a silent no-op, never an error.
    assert _reconcile_windows_real_git_locate(str(candidate)) is False

    # File exists but carries neither the legacy list nor the patched block:
    # upstream reformatted it, so fail loudly at sync time (a ValueError, which
    # conformance catches) rather than shipping an unpatched locator.
    path = candidate / "nastech_cli" / "update_cmd_git.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "def _locate_real_git():\n"
        "    return _candidates_from_unknown_shape()\n",
        encoding="utf-8",
    )
    with pytest.raises(WindowsRealGitLocateError) as excinfo:
        _reconcile_windows_real_git_locate(str(candidate))
    # Must be a ValueError so hundredways.conformance reports it, not crashes.
    assert isinstance(excinfo.value, ValueError)
    assert "update_cmd_git.py" in str(excinfo.value)


def test_sync_wait_budgets_widen_the_four_new_probes(tmp_path):
    """Widen the four 7bda2a33 probes that assume an idle host, once each."""
    candidate = tmp_path / "candidate"

    def write(rel: str, text: str) -> None:
        path = candidate / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write(
        "tests/agent/test_run_agent.py",
        "class TestRunConversation:\n"
        "    def test_redirect_from_input_thread_cancels_live_model_request(self, agent):\n"
        "        def _fake_api_call(_api_kwargs):\n"
        "                deadline = time.time() + 2\n"
        "            return final\n"
        "            assert entered.wait(timeout=2)\n"
        "            worker.join(timeout=5)\n",
    )
    write(
        "tests/test_session_daemon_reaper.py",
        "def test_session_teardown_terminates_daemons_under_the_session_basetemp_only(tmp_path):\n"
        "        wait_until(lambda: x <= y,\n"
        "            [basetemp, elsewhere])), timeout=10, what='both detached children started')\n",
    )
    write(
        "tests/gateway/fixtures/local_recovery_probe.py",
        "async def rpc(ws, method, **params):\n"
        "    async with asyncio.timeout(20):\n",
    )
    write(
        "tests/nastech_cli/test_gateway_chat_native.py",
        "        result = tmux(\"new-session\", text=True, timeout=10, check=False)\n"
        "    def until(text, timeout=45):\n",
    )

    changed = _reconcile_sync_wait_budgets(str(candidate))
    assert changed == [
        "tests/agent/test_run_agent.py",
        "tests/test_session_daemon_reaper.py",
        "tests/gateway/fixtures/local_recovery_probe.py",
        "tests/nastech_cli/test_gateway_chat_native.py",
    ]
    # Each bounded poll grows to the fork's standard-runner budget; the red is
    # the timeout, so a genuine regression still fails loudly.
    assert "assert entered.wait(timeout=15)" in (candidate / "tests/agent/test_run_agent.py").read_text(encoding="utf-8")
    assert "worker.join(timeout=30)" in (candidate / "tests/agent/test_run_agent.py").read_text(encoding="utf-8")
    assert "timeout=30, what='both detached children started'" in (candidate / "tests/test_session_daemon_reaper.py").read_text(encoding="utf-8")
    assert "async with asyncio.timeout(60)" in (candidate / "tests/gateway/fixtures/local_recovery_probe.py").read_text(encoding="utf-8")
    assert "def until(text, timeout=90)" in (candidate / "tests/nastech_cli/test_gateway_chat_native.py").read_text(encoding="utf-8")
    # Fixed point: a second pass rewrites nothing.
    assert _reconcile_sync_wait_budgets(str(candidate)) == []
    # Brand-clean for the first-party audit on every edited file.
    rules = BrandingRules()
    for rel in changed:
        assert rules.transform_text((candidate / rel).read_text(encoding="utf-8")) == (candidate / rel).read_text(encoding="utf-8")


def test_windows_git_locate_and_sync_wait_budgets_run_from_reconcile_tree(tmp_path):
    """A new reconcile only runs if ``reconcile_tree`` dispatches it."""
    root = tmp_path / "candidate"
    path = root / "nastech_cli" / "update_cmd_git.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "def _locate_real_git():\n"
        "    candidates = [\n"
        '        Path(base) / "Git" / arch / "libexec" / "git-core" / "git.exe"\n'
        '        for base in (r"C:\\Program Files", r"C:\\Program Files (x86)")\n'
        '        for arch in ("clangarm64", "mingw64", "mingw32")\n'
        "    ] + _portable_git_candidates()\n",
        encoding="utf-8",
    )
    probe = root / "tests" / "gateway" / "fixtures" / "local_recovery_probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "async def rpc(ws, method, **params):\n"
        "    async with asyncio.timeout(20):\n",
        encoding="utf-8",
    )

    result = reconcile_tree(str(root))
    assert "nastech_cli/update_cmd_git.py" in result.fixed
    assert "tests/gateway/fixtures/local_recovery_probe.py" in result.fixed
    # Both are fixed points on the next run.
    assert _reconcile_windows_real_git_locate(str(root)) is False
    assert _reconcile_sync_wait_budgets(str(root)) == []


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


def test_desktop_notification_timer_survives_jsdom_teardown(tmp_path):
    candidate = tmp_path / "candidate"
    path = candidate / "apps" / "desktop" / "src" / "store" / "notifications.ts"
    path.parent.mkdir(parents=True)
    path.write_text(
        "  if (duration > 0) {\n"
        "    timers.set(\n"
        "      id,\n"
        "      window.setTimeout(() => dismissNotification(id), duration)\n"
        "    )\n"
        "  }\n",
        encoding="utf-8",
    )

    assert _reconcile_desktop_notification_timer_teardown(str(candidate)) is True
    text = path.read_text(encoding="utf-8")
    # Braced body: eslint ``curly: ['error', 'all']`` rejects the single-statement
    # form, so the guard must open a block.
    assert "if (typeof window !== 'undefined') {\n" in text
    assert "          dismissNotification(id)\n" in text
    assert "if (typeof window !== 'undefined') dismissNotification(id)" not in text
    assert "window.setTimeout(() => dismissNotification(id), duration)" not in text
    # Idempotent: the guarded callback no longer matches the unguarded needle.
    assert _reconcile_desktop_notification_timer_teardown(str(candidate)) is False


def test_desktop_notification_timer_reconcile_skips_missing_file(tmp_path):
    assert _reconcile_desktop_notification_timer_teardown(str(tmp_path)) is False


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
        "url: 'https://nastechresearch.github.io',\n"
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
    docs_config = (website / "docusaurus.config.ts").read_text()
    assert f"url: '{_DOCS_PUBLIC_ORIGIN}'," in docs_config
    assert "url: 'https://nastechresearch.github.io/nastech-agent'," not in docs_config
    assert "baseUrl: '/nastech-agent/docs/'," in docs_config
    assert "return project" in (paths / "paths.ts").read_text()
    lint_config = (root / "eslint.config.shared.mjs").read_text()
    assert "'perfectionist/sort-imports': [\n        'warn'," in lint_config
    assert "'perfectionist/sort-named-exports': ['warn'" in lint_config
    assert "'perfectionist/sort-named-imports': ['warn'" in lint_config


def test_reconcile_plugin_guard_trusted_installer_hosts(tmp_path):
    root = tmp_path / "branded"
    tools = root / "tools"
    tools.mkdir(parents=True)
    target = tools / "plugin_guard_context.py"
    target.write_text(
        "_TRUSTED_INSTALLER_HOSTS = {\n"
        '    "nastechresearch.github.io/nastech-agent", "ollama.com", "bun.sh",\n'
        '    "get.docker.com", "sh.rustup.rs", "deno.land", "astral.sh", "app.factory.ai",\n'
        "}\n"
    )

    assert _reconcile_plugin_guard_trusted_installer_hosts(str(root)) == 1
    text = target.read_text()
    assert '"nastechresearch.github.io", "ollama.com", "bun.sh",' in text
    assert '"nastechresearch.github.io/nastech-agent",' not in text

    # Idempotent once the allowlist carries the bare host.
    assert _reconcile_plugin_guard_trusted_installer_hosts(str(root)) == 0


def test_reconcile_plugin_guard_leaves_install_paths_untouched(tmp_path):
    root = tmp_path / "branded"
    tools = root / "tools"
    tools.mkdir(parents=True)
    target = tools / "plugin_guard_context.py"
    original = 'url = "https://nastechresearch.github.io/nastech-agent/install.sh"\n'
    target.write_text(original)

    # A docs/install URL keeps the path form and must not be rewritten; only the
    # trusted-installer allowlist entry collapses to the bare host.
    assert _reconcile_plugin_guard_trusted_installer_hosts(str(root)) == 0
    assert target.read_text() == original


def test_reconcile_docusaurus_site_config_subpath_url_collapses(tmp_path):
    root = tmp_path / "branded"
    website = root / "website"
    website.mkdir(parents=True)
    target = website / "docusaurus.config.ts"
    target.write_text(
        "url: 'https://nastechresearch.github.io/nastech-agent',\n"
        "baseUrl: '/docs/',\n"
    )

    # The fork serves docs at the project path /nastech-agent/docs/ (Pages
    # project site); Docusaurus 3.10 rejects a sub-path inside `url`, so the
    # reconcile collapses the branded origin to the bare Pages host and moves
    # the project path into baseUrl.
    assert _reconcile_docusaurus_site_config(str(root)) == 1
    text = target.read_text()
    assert f"url: '{_DOCS_PUBLIC_ORIGIN}'," in text
    assert "url: 'https://nastechresearch.github.io/nastech-agent'," not in text
    assert "baseUrl: '/nastech-agent/docs/'," in text
    assert "baseUrl: '/docs/'," not in text

    # Idempotent once the config carries the project-pages contract.
    assert _reconcile_docusaurus_site_config(str(root)) == 0


def test_reconcile_prompt_surface_snapshot(tmp_path):
    root = tmp_path / "branded"
    snap = root / "tests" / "fixtures" / "prompt_surface"
    prompts = snap / "prompts"
    tools = snap / "tools"
    prompts.mkdir(parents=True)
    tools.mkdir(parents=True)

    stable = "System prompt header.\nBe helpful.\n"
    context = "Context block.\n"
    volatile = (
        "# installed skills\n"
        "  software-development:\n"
        "    - github: GitHub via gh CLI: PRs, issues, reviews, repos, auth.\n"
        "    - nastech-agent-skill-authoring: Author in-repo SKILL.md files: frontmatter and structure.\n"
        "    - inspecting-nastech-desktop-dom: Read the live Nastech desktop DOM/CSS over CDP.\n"
        "  autonomous-ai-agents:\n"
        "    - nastech-agent: Use, configure, theme, extend, and orchestrate Nastech Agent.\n"
    )
    (prompts / "cli.claude.txt").write_text(
        "===== stable =====\n"
        f"{stable}\n\n"
        "===== context =====\n"
        f"{context}\n\n"
        "===== volatile =====\n"
        f"{volatile}\n",
        encoding="utf-8",
    )
    tool_schema = {
        "name": "frobnicate",
        "description": "Do things.",
        "parameters": {"type": "object", "properties": {}},
    }
    (tools / "frobnicate.json").write_text(
        json.dumps(tool_schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (snap / "surfaces.json").write_text(
        json.dumps(
            {
                "cli.claude": {
                    # Stale token counts: branding moved the text but upstream
                    # numbers were never folded back (the engine's signature).
                    "prompt_tokens": {"context": 3, "stable": 3, "volatile": 3},
                    "tool_tokens": 3,
                    "tools": ["frobnicate"],
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    changed = _reconcile_prompt_surface_snapshot(str(root))
    assert changed == [
        "tests/fixtures/prompt_surface/prompts/cli.claude.txt",
        "tests/fixtures/prompt_surface/surfaces.json",
    ]

    text = (prompts / "cli.claude.txt").read_text(encoding="utf-8")
    assert _SKILL_DESC_FULL_NEW in text
    assert _SKILL_DESC_FULL_OLD not in text
    assert text.index("    - inspecting-nastech-desktop-dom:") < text.index(
        "    - nastech-agent-skill-authoring:"
    )

    expected_volatile = (
        "# installed skills\n"
        "  software-development:\n"
        "    - github: GitHub via gh CLI: PRs, issues, reviews, repos, auth.\n"
        "    - inspecting-nastech-desktop-dom: Read the live Nastech desktop DOM/CSS over CDP.\n"
        "    - nastech-agent-skill-authoring: Author in-repo SKILL.md files: frontmatter and structure.\n"
        "  autonomous-ai-agents:\n"
        f"    - nastech-agent: {_SKILL_DESC_FULL_NEW}.\n"
    )
    expected = {
        "cli.claude": {
            # The renderer's own length formulas (scripts/ci/prompt_surface.py
            # _write_snapshot): len(tier)//4 and len(json.dumps(tools,
            # sort_keys=True))//4, reproduced from the corrected prompt text.
            "prompt_tokens": {
                "context": len(context) // 4,
                "stable": len(stable) // 4,
                "volatile": len(expected_volatile) // 4,
            },
            "tool_tokens": len(json.dumps({"frobnicate": tool_schema}, sort_keys=True)) // 4,
            "tools": ["frobnicate"],
        }
    }
    assert json.loads((snap / "surfaces.json").read_text(encoding="utf-8")) == expected

    # Idempotent once the snapshot carries the branded render.
    assert _reconcile_prompt_surface_snapshot(str(root)) == []


def test_reconcile_drop_vercel_deploy_job(tmp_path):
    root = tmp_path / "branded"
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    target = wf / "deploy-site.yml"
    target.write_text(
        "name: Deploy Site\n"
        "jobs:\n"
        "  deploy-vercel:\n"
        "    # Triggered automatically on release publish (production cuts) and\n"
        "    # manually via `gh workflow run deploy-site.yml` when an out-of-band\n"
        "    # main commit needs to ship live before the next release tag \u2014 e.g.\n"
        "    # a skills-index PR that doesn't touch website/** paths and so\n"
        "    # doesn't auto-deploy via the deploy-docs path.\n"
        "    if: github.event_name == 'release' || github.event_name == 'workflow_dispatch'\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 30\n"
        "    steps:\n"
        "      - name: Trigger Vercel Deploy\n"
        '        run: curl -fsS --retry 3 --retry-delay 10 -X POST "${{ secrets.VERCEL_DEPLOY_HOOK }}"\n'
        "\n"
        "  deploy-docs:\n"
        "    runs-on: ubuntu-latest\n"
    )

    assert _reconcile_drop_vercel_deploy_job(str(root)) == 1
    text = target.read_text()
    assert "deploy-vercel" not in text
    assert "VERCEL_DEPLOY_HOOK" not in text
    assert "deploy-docs:" in text
    assert text.startswith("name: Deploy Site\njobs:\n  deploy-docs:\n")

    # Idempotent once the job is gone.
    assert _reconcile_drop_vercel_deploy_job(str(root)) == 0


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


def test_reconcile_preserved_brand_text_brands_fork_only_text(tmp_path):
    """A fork-only text file preserved verbatim is branded to a fixed point.

    ``preserve_fork_files`` copies fork-only files byte-for-byte, so a
    fork-authored workflow comment that says ``# Hermes runs this ...`` trips
    both ``first-party-brand`` and ``brand-text-not-fixed-point`` on the
    candidate.  The reconcile applies the canonical transform, leaves an
    already-clean file untouched, and skips locked/immutable paths.
    """
    candidate = tmp_path / "candidate"
    workflow_rel = ".github/workflows/windows-install-update-e2e.yml"
    workflow = candidate / workflow_rel
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "# Hermes runs this on a 32-core windows-latest image (~17 min).\n"
        "name: windows-install-update-e2e\n",
        encoding="utf-8",
    )
    clean_rel = ".github/workflows/tests-os.yml"
    clean = candidate / clean_rel
    clean.write_text("name: tests-os\n", encoding="utf-8")
    locked_rel = "uv.lock"
    locked = candidate / locked_rel
    locked.write_text("source = 'NousResearch/misaki'\n", encoding="utf-8")

    changed = _reconcile_preserved_brand_text(
        str(candidate), [workflow_rel, clean_rel, locked_rel]
    )

    assert changed == [workflow_rel]
    branded = workflow.read_text(encoding="utf-8")
    assert "Hermes" not in branded
    assert "Nastech runs this" in branded
    # The rewritten file is a canonical fixed point, and locked files are
    # left byte-for-byte (the audit covers their residue with an allow-list).
    assert BrandingRules().transform_text(branded) == branded
    assert "NousResearch" in locked.read_text(encoding="utf-8")
    # Idempotent.
    assert (
        _reconcile_preserved_brand_text(
            str(candidate), [workflow_rel, clean_rel, locked_rel]
        )
        == []
    )


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


def test_update_gate_allows_engine_registry_retirement(tmp_path):
    """The owned-assets registry can shrink engine-side without hand-editing
    the fork: old fork registry sources the authoritative registry no longer
    owns are sanctioned retirements (reviewable via the engine manifest
    diff), so the fork-consistency gate passes instead of failing local_only.
    Mirrors PR #101's owned-registry prune."""
    hermes = _hermes_repo(tmp_path)
    src = tmp_path / "hermes-agent"
    (src / "static" / "img").mkdir(parents=True, exist_ok=True)
    (src / "static" / "img" / "banner.png").write_bytes(b"HERMES-BANNER-BYTES")
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "add banner"], check=True)
    baseline = subprocess.check_output(
        ["git", "-C", str(src), "rev-parse", "HEAD"], text=True
    ).strip()

    # The fork was previously branded with the OLD 2-asset registry.
    fork = tmp_path / "nastech-agent"
    fork.mkdir()
    (fork / "manifest.json").write_text(json.dumps({"upstream_sha": baseline}))
    (fork / "config" / "owned-assets").mkdir(parents=True)
    (fork / "config" / "owned-assets" / "manifest.json").write_text(json.dumps({
        "static/img/banner.png": "banner.png",
        "static/img/old-banner.png": "old-banner.png",
    }))
    (fork / "config" / "owned-assets" / "banner.png").write_bytes(b"FORK-BANNER")
    (fork / "config" / "owned-assets" / "old-banner.png").write_bytes(b"FORK-OLD-BANNER")
    (fork / "static" / "img").mkdir(parents=True)
    (fork / "static" / "img" / "banner.png").write_bytes(b"FORK-BANNER")
    (fork / "static" / "img" / "old-banner.png").write_bytes(b"FORK-OLD-BANNER")

    # The NEW engine registry pruned old-banner.png (#101-style shrink).
    registry = tmp_path / "config" / "owned-assets"
    registry.mkdir(parents=True)
    (registry / "banner.png").write_bytes(b"ENGINE-BANNER")
    (registry / "manifest.json").write_text(json.dumps({
        "static/img/banner.png": "banner.png",
    }))
    owned = OwnedAssets(root=str(registry))

    result = UpdateManager(
        str(tmp_path / "Updates-Commits"),
        hermes_url=hermes, owned=owned, fork_root=str(fork),
    ).run()
    assert result.gate, result.fork.summary()
    statuses = result.fork.statuses
    assert statuses.get("registry_retired") == 1, statuses
    assert not statuses.get("missing") and not statuses.get("local_only"), statuses
    # pruned registry source is gone from the candidate; the retained one stays
    snap_owned = os.path.join(result.dir, "config", "owned-assets")
    assert os.path.exists(os.path.join(snap_owned, "banner.png"))
    assert not os.path.exists(os.path.join(snap_owned, "old-banner.png"))


def test_reconcile_managed_gateway_wire_headers_restores_product_token(tmp_path):
    """Branding collapses the two-token ``x-<org>-<product>-`` prefix.

    Upstream names correlation headers ``x-nous-hermes-<field>``; the fork's
    token rules map both ``nous`` and ``hermes`` to ``nastech``, collapsing the
    compound to ``x-nastech-nastech-``.  The wire-identity test guards the
    *product* prefix, so branded it matches its own headers.  Give the product
    token its own name again.
    """
    root = tmp_path / "branded"
    (root / "tools").mkdir(parents=True)
    (root / "tests" / "tools").mkdir(parents=True)
    source = root / "tools" / "managed_gateway_identity.py"
    source.write_text(
        'SESSION_HEADER = "x-nastech-nastech-session"\n'
        'TURN_HEADER = "x-nastech-nastech-turn"\n',
        encoding="utf-8",
    )
    test = root / "tests" / "tools" / "test_connector_wire_identity.py"
    test.write_text(
        '    headers = {k for k in first if k.startswith("x-nastech-nastech-")}\n'
        '    assert not any(k.startswith("x-nastech-") for k in first)\n',
        encoding="utf-8",
    )

    changed = _reconcile_managed_gateway_wire_headers(str(root))
    assert changed == [
        "tools/managed_gateway_identity.py",
        "tests/tools/test_connector_wire_identity.py",
    ]
    source_text = source.read_text(encoding="utf-8")
    assert "x-nastech-nastech-" not in source_text
    assert '"x-nastech-agent-session"' in source_text
    test_text = test.read_text(encoding="utf-8")
    assert 'startswith("x-nastech-agent-")' in test_text
    assert 'startswith("x-agent-")' in test_text
    assert "x-nastech-nastech-" not in test_text

    # Idempotent: the compound token is gone, so nothing matches again.
    assert _reconcile_managed_gateway_wire_headers(str(root)) == []


def test_reconcile_managed_gateway_wire_headers_noop_without_compound_token(tmp_path):
    """An unskewed (upstream) file has no ``x-nastech-nastech-`` to fix."""
    root = tmp_path / "branded"
    (root / "tools").mkdir(parents=True)
    source = root / "tools" / "managed_gateway_identity.py"
    source.write_text('SESSION_HEADER = "x-nous-hermes-session"\n', encoding="utf-8")
    (root / "tests" / "tools").mkdir(parents=True)
    test = root / "tests" / "tools" / "test_connector_wire_identity.py"
    test.write_text(
        'assert not any(k.startswith("x-hermes-") for k in first)\n',
        encoding="utf-8",
    )

    assert _reconcile_managed_gateway_wire_headers(str(root)) == []
    assert source.read_text(encoding="utf-8") == 'SESSION_HEADER = "x-nous-hermes-session"\n'
    assert 'x-hermes-' in test.read_text(encoding="utf-8")


def test_reconcile_connector_test_guest_offline_pins_both_fixtures(tmp_path):
    """The fork runs no portal, so ``ensure_guest_identity`` 503s.

    Both connector RPC modules must pin the identity helper (via both of its
    import paths) so the tests exercise reissue/alias logic, not the network.
    """
    root = tmp_path / "branded"
    tg = root / "tests" / "tui_gateway"
    tg.mkdir(parents=True)
    accounts = tg / "test_connector_accounts_rpc.py"
    accounts.write_text(
        "@pytest.fixture(autouse=True)\n"
        "def _gate(monkeypatch):\n"
        "    live.reset_for_tests()\n"
        '    monkeypatch.setattr("tools.connectors.connectors_available", lambda: True)\n'
        "    yield\n",
        encoding="utf-8",
    )
    operation = tg / "test_connector_operation_rpc.py"
    operation.write_text(
        "@pytest.fixture\n"
        "def owned(monkeypatch):\n"
        "    live.reset_for_tests()\n"
        "    with ExitStack() as stack:\n"
        "        yield\n",
        encoding="utf-8",
    )

    changed = _reconcile_connector_test_guest_offline(str(root))
    assert changed == [
        "tests/tui_gateway/test_connector_accounts_rpc.py",
        "tests/tui_gateway/test_connector_operation_rpc.py",
    ]
    for path in (accounts, operation):
        text = path.read_text(encoding="utf-8")
        assert (
            '    monkeypatch.setattr("tools.connectors.ensure_guest_identity", lambda: None)\n'
            in text
        )
        assert (
            '    monkeypatch.setattr("tools.connectors.managed.ensure_guest_identity", '
            "lambda: None)\n" in text
        )

    # Idempotent: the patch block is present, so the guard skips both.
    assert _reconcile_connector_test_guest_offline(str(root)) == []


def test_reconcile_connector_test_guest_offline_skips_unrelated_tree(tmp_path):
    """A tree without the fixture needles is left byte-identical."""
    root = tmp_path / "branded"
    tg = root / "tests" / "tui_gateway"
    tg.mkdir(parents=True)
    path = tg / "test_connector_accounts_rpc.py"
    original = "def test_plain():\n    assert True\n"
    path.write_text(original, encoding="utf-8")
    assert _reconcile_connector_test_guest_offline(str(root)) == []
    assert path.read_text(encoding="utf-8") == original


def test_reconcile_plugin_catalog_sync_exemption(tmp_path):
    """The engine's 100WAYS sync PR must skip the data-only catalog gate."""
    root = tmp_path / "branded"
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    target = wf / "plugin-catalog-ci.yml"
    target.write_text(
        "name: Plugin Catalog CI\n"
        "jobs:\n"
        "  pinned-source-validate:\n"
        "    steps:\n"
        "      - name: Find changed catalog entries\n"
        "        id: changed\n"
        "        run: |\n"
        "          set -euo pipefail\n"
        "          # HEAD is the PR merge commit:\n"
        "          git diff --name-only HEAD^1 HEAD\n",
        encoding="utf-8",
    )

    assert _reconcile_plugin_catalog_sync_exemption(str(root)) is True
    text = target.read_text(encoding="utf-8")
    assert 'GITHUB_HEAD_REF' in text
    assert '"100WAYS"' in text
    assert 'nastechresearch/nastech-agent' in text
    assert "files<<__EOF__" in text
    # The original body is preserved after the guarded early exit.
    assert "          # HEAD is the PR merge commit:\n" in text

    # Idempotent: the marker now sits behind the guard, so the needle is gone.
    assert _reconcile_plugin_catalog_sync_exemption(str(root)) is False


def test_reconcile_plugin_catalog_sync_exemption_skips_missing_workflow(tmp_path):
    assert _reconcile_plugin_catalog_sync_exemption(str(tmp_path)) is False


def test_reconcile_pinned_source_clones_names_every_external_ref(tmp_path):
    """The engine workflows that clone Hermes must name the ref they consume.

    A bare ``git clone <url>`` follows whatever NousResearch/hermes-agent's
    default branch is at run time, so an upstream default-branch flip changes
    the revision CI consumes with no diff landing in this repository.
    """
    root = tmp_path / "branded"
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    stream = wf / "stage-commit-stream.yml"
    stream.write_text(
        "      - name: Fetch\n"
        "        run: |\n"
        "          git clone https://github.com/NousResearch/hermes-agent.git upstream-agent\n",
        encoding="utf-8",
    )
    pages = wf / "pages.yml"
    pages.write_text(
        "          git clone https://github.com/NousResearch/hermes-agent.git upstream-agent\n",
        encoding="utf-8",
    )
    pipeline = wf / "stage-pipeline.yml"
    pipeline.write_text(
        "          if git -c http.version=HTTP/1.1 clone "
        '"$HUNDREDWAYS_HERMES_URL" upstream-agent; then\n',
        encoding="utf-8",
    )

    changed = _reconcile_pinned_source_clones(str(root))

    # Reported in the table's order, so a missing pin is traceable to a row.
    assert changed == [
        ".github/workflows/stage-commit-stream.yml",
        ".github/workflows/pages.yml",
        ".github/workflows/stage-pipeline.yml",
    ]
    assert (
        "git clone --branch main https://github.com/NousResearch/hermes-agent.git "
        "upstream-agent" in stream.read_text(encoding="utf-8")
    )
    assert "--branch main" in pages.read_text(encoding="utf-8")
    assert 'clone --branch main "$HUNDREDWAYS_HERMES_URL"' in pipeline.read_text(
        encoding="utf-8"
    )

    # Idempotent: the pinned needle is now present, so the bare needle is gone.
    assert _reconcile_pinned_source_clones(str(root)) == []


def test_reconcile_pinned_source_clones_registered_in_reconcile_tree(tmp_path):
    """reconcile_tree must carry the pin (a fork-local edit is not durable)."""
    root = tmp_path / "branded"
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "pages.yml").write_text(
        "          git clone https://github.com/NousResearch/hermes-agent.git upstream-agent\n",
        encoding="utf-8",
    )

    result = reconcile_tree(str(root))

    assert ".github/workflows/pages.yml" in result.fixed
    assert "--branch main" in (wf / "pages.yml").read_text(encoding="utf-8")


def test_reconcile_pinned_source_clones_skips_absent_and_raises_on_unknown(tmp_path):
    """Absent = no-op; present in neither shape = fail closed as ValueError."""
    root = tmp_path / "branded"
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    assert _reconcile_pinned_source_clones(str(root)) == []

    # Already pinned: idempotent no-op, byte-identical output.
    pinned = wf / "pages.yml"
    pinned.write_text(
        "          git clone --branch main "
        "https://github.com/NousResearch/hermes-agent.git upstream-agent\n",
        encoding="utf-8",
    )
    before = pinned.read_bytes()
    assert _reconcile_pinned_source_clones(str(root)) == []
    assert pinned.read_bytes() == before

    # Neither form: the reconciliation cannot reason about the file.
    pinned.write_text("jobs:\n  monitor:\n    steps: []\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        _reconcile_pinned_source_clones(str(root))
    assert "pages.yml" in str(excinfo.value)


def test_prompt_surface_snapshot_is_computed_after_domain_migration(tmp_path):
    """The manifest counts characters of the final rendered prompt.

    ``_reconcile_domains`` rewrites hosts inside the prompt ``.txt``; the
    prompt-surface snapshot must run *after* it or ``surfaces.json`` describes
    the pre-migration text (the fork's stale-prompt-surface failure).  Driving
    the real ``reconcile_tree`` proves the dispatch order, not just the math.
    """
    root = tmp_path / "branded"
    snap = root / "tests" / "fixtures" / "prompt_surface"
    prompts = snap / "prompts"
    prompts.mkdir(parents=True)
    (snap / "tools").mkdir(parents=True)
    (prompts / "cli.claude.txt").write_text(
        "===== stable =====\n"
        "Docs live at https://nastechresearch.com/guide today.\n"
        "\n===== context =====\n"
        "Context body.\n"
        "\n===== volatile =====\n"
        "Volatile body.\n",
        encoding="utf-8",
    )
    (snap / "surfaces.json").write_text(
        json.dumps(
            {
                "cli.claude": {
                    # Stale all-zero counts, as an upstream snapshot would be.
                    "prompt_tokens": {"context": 0, "stable": 0, "volatile": 0},
                    "tool_tokens": 0,
                    "tools": [],
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = reconcile_tree(str(root))

    migrated = (prompts / "cli.claude.txt").read_text(encoding="utf-8")
    assert "nastechresearch.com" not in migrated
    assert "nastechresearch.github.io" in migrated
    tiers = _split_prompt_surface_tiers(migrated)
    manifest = json.loads((snap / "surfaces.json").read_text(encoding="utf-8"))
    assert manifest["cli.claude"]["prompt_tokens"] == {
        tier: len(body) // _PROMPT_SURFACE_CHARS_PER_TOKEN
        for tier, body in tiers.items()
    }
    assert "tests/fixtures/prompt_surface/surfaces.json" in result.fixed


def _channel_reader_source() -> str:
    """A minimal branded ``release_channels.py`` carrying the no-UA needle."""
    return (
        "from urllib.request import Request\n"
        "\n"
        "\n"
        "class ChannelReader:\n"
        "    def read_bytes(self):\n"
        "        def read():\n"
        '            with self.opener(Request(url, headers={"Cache-Control": "no-cache"}),'
        " timeout=30) as response:\n"
        "                return response.read()\n"
        "\n"
        "        return read()\n"
    )


def test_reconcile_channel_reader_user_agent(tmp_path):
    """The reader must send a real UA or the workers.dev host 403s (1010)."""
    root = tmp_path / "branded"
    pkg = root / "nastech_cli"
    pkg.mkdir(parents=True)
    target = pkg / "release_channels.py"
    target.write_text(_channel_reader_source(), encoding="utf-8")

    assert _reconcile_channel_reader_user_agent(str(root)) is True
    text = target.read_text(encoding="utf-8")
    assert '"User-Agent": "nastech-update"' in text
    assert '"Cache-Control": "no-cache"' in text
    # The rewrite stays valid Python with the read body intact.
    compile(text, str(target), "exec")

    # Idempotent: the single-line needle is gone, so a second pass is a no-op.
    assert _reconcile_channel_reader_user_agent(str(root)) is False


def test_reconcile_channel_reader_user_agent_skips_missing_file(tmp_path):
    assert _reconcile_channel_reader_user_agent(str(tmp_path)) is False


def test_reconcile_tree_registers_channel_reader_user_agent(tmp_path):
    """The new reconcile must actually run inside ``reconcile_tree``."""
    root = tmp_path / "branded"
    pkg = root / "nastech_cli"
    pkg.mkdir(parents=True)
    target = pkg / "release_channels.py"
    target.write_text(_channel_reader_source(), encoding="utf-8")

    result = reconcile_tree(str(root))
    assert "nastech_cli/release_channels.py" in result.fixed
    text = target.read_text(encoding="utf-8")
    assert '"User-Agent": "nastech-update"' in text
    compile(text, str(target), "exec")


def test_reconcile_channel_reader_user_agent_flags_a_reformatted_reader(tmp_path):
    """A moved reader must fail loudly, not drop the UA and wait for a red canary.

    Upstream reformatting the ``self.opener(Request(...))`` line used to take the
    silent ``False`` path — the same return as "already patched" — so the header
    vanished and only surfaced when the workers.dev edge started answering 403
    (error code: 1010). ``reconcile_tree`` must now propagate
    ``ChannelReaderUserAgentError`` so the ``reconcile`` stage fails the update
    gate at sync time.
    """
    root = tmp_path / "branded"
    pkg = root / "nastech_cli"
    pkg.mkdir(parents=True)
    target = pkg / "release_channels.py"
    target.write_text(
        "from urllib.request import Request\n"
        "\n"
        "\n"
        "class ChannelReader:\n"
        "    def read_bytes(self):\n"
        "        def read():\n"
        "            request = Request(url, headers=self.headers, timeout=30)\n"
        "            with self.opener(request) as response:\n"
        "                return response.read()\n"
        "\n"
        "        return read()\n",
        encoding="utf-8",
    )

    with pytest.raises(ChannelReaderUserAgentError):
        _reconcile_channel_reader_user_agent(str(root))
    with pytest.raises(ChannelReaderUserAgentError):
        reconcile_tree(str(root))


# ---------------------------------------------------------------------------
# Shared-gateway approval-gate ports (upstream eea7c437eb)
# ---------------------------------------------------------------------------
# Upstream eea7c437eb ("fix(security): honour allowlist removals and the plugin
# gate on the shared gateway") closes two gates that stopped holding once one
# long-lived gateway daemon owned every local session.  ``brand_tree`` rewrites
# every upstream-owned file on each sync, so both fixes live in the engine as
# ``_reconcile_*`` ports (see ``hundredways/updates.py``).  The fixtures below
# are the pre-fix and post-fix UPSTREAM text (unbranded); each test brands them
# with the engine's own rules, exactly as ``brand_tree`` would, so the ports are
# proved end to end and the behavioural tests can execute the emitted code.

_APPROVAL_PORT_PREFIX = '''def _permanent_set() -> set:
    """The permanent allowlist that governs the ACTIVE profile. Unscoped (single-profile process,
    or the multiplexer's own launch profile) → the module-level set tests and the CLI seed. A routed
    profile (HERMES_HOME override) → its own set, lazily loaded from ITS ``command_allowlist``: the
    launch profile's "always" approvals must not pre-approve commands for a secondary, nor may a
    secondary's "always" choice be written back into the launch profile's config. Callers hold ``_lock``.
    """
    from hermes_constants import get_hermes_home_override, hermes_home_key
    if get_hermes_home_override() is None:
        return _permanent_approved
    home_key = hermes_home_key()
    approved = _permanent_approved_by_home.get(home_key)
    if approved is None:
        try:
            approved = _read_permanent_allowlist()
        except Exception as e:
            logger.warning("Failed to load permanent allowlist: %s", e)
            approved = set()
        _permanent_approved_by_home[home_key] = approved
    return approved


def is_approved(session_key: str, pattern_key: str) -> bool:
    """Session-scoped or permanent approval. Accepts the canonical key and the legacy
    regex-derived key so existing command_allowlist entries survive key migrations."""
    aliases = _approval_key_aliases(pattern_key)
    with _lock:
        approved = _permanent_set() | _session_approved.get(session_key, set())
    return any(alias in approved for alias in aliases)


def save_permanent_allowlist(patterns: set):
    """Save permanently allowed command patterns to config, reconciling with the file.

    ``command_allowlist`` is a file an operator edits by hand; removing an entry
    there is the documented way to withdraw a standing approval. This process read
    it once at import and ``load_permanent`` only ever unions, so writing the
    in-memory set straight back deleted entries added on disk since import and
    resurrected the ones removed. The result written is ``what is on disk now``
    plus ``what this process approved since its own baseline``; revoked entries are
    also dropped from the governing permanent set so ``is_approved()`` stops
    honouring them. Nothing re-reads the file on the approval hot path.

    ``patterns`` may only ADD: an entry left out of it is not removed, because the
    on-disk list wins for anything this process did not approve itself. Remove
    entries by editing ``command_allowlist`` in config.yaml.
    """
    pass
'''

_APPROVAL_PORT_POSTFIX = '''def _permanent_set() -> set:
    """The permanent allowlist that governs the ACTIVE profile. Unscoped (single-profile process,
    or the multiplexer's own launch profile) → the module-level set tests and the CLI seed. A routed
    profile (HERMES_HOME override) → its own set, lazily loaded from ITS ``command_allowlist``: the
    launch profile's "always" approvals must not pre-approve commands for a secondary, nor may a
    secondary's "always" choice be written back into the launch profile's config. Callers hold ``_lock``.
    """
    from hermes_constants import get_hermes_home_override, hermes_home_key
    if get_hermes_home_override() is None:
        approved, key = _permanent_approved, ""
    else:
        key = hermes_home_key()
        approved = _permanent_approved_by_home.setdefault(key, set())
    _sync_permanent_from_disk(approved, key)
    return approved


def _sync_permanent_from_disk(approved: set, key: str) -> None:
    """Apply hand edits to ``command_allowlist`` made since the last sync. The gateway daemon
    outlives any one session, so an entry the operator removed must stop being honoured now, not
    at restart. Only the on-disk delta is applied: approvals this process made but could not save
    survive. The config read is cached on the file signature. Callers hold ``_lock``."""
    try:
        on_disk = _read_permanent_allowlist()
    except Exception as e:
        logger.warning("Failed to load permanent allowlist: %s", e, exc_info=True)
        return
    baseline = _permanent_baseline_by_home.get(key, set())
    if on_disk == baseline:
        return
    approved.difference_update(baseline - on_disk)
    approved.update(on_disk - baseline)
    _permanent_baseline_by_home[key] = on_disk


def is_approved(session_key: str, pattern_key: str) -> bool:
    """Session-scoped or permanent approval. Accepts the canonical key and the legacy
    regex-derived key so existing command_allowlist entries survive key migrations."""
    aliases = _approval_key_aliases(pattern_key)
    with _lock:
        approved = _permanent_set() | _session_approved.get(session_key, set())
    return any(alias in approved for alias in aliases)


def save_permanent_allowlist(patterns: set):
    """Save permanently allowed command patterns to config, reconciling with the file.

    ``command_allowlist`` is a file an operator edits by hand; removing an entry
    there is the documented way to withdraw a standing approval. This process read
    it once at import and ``load_permanent`` only ever unions, so writing the
    in-memory set straight back deleted entries added on disk since import and
    resurrected the ones removed. The result written is ``what is on disk now``
    plus ``what this process approved since its own baseline``; revoked entries are
    also dropped from the governing permanent set so ``is_approved()`` stops
    honouring them (``_sync_permanent_from_disk`` does the same on every check).

    ``patterns`` may only ADD: an entry left out of it is not removed, because the
    on-disk list wins for anything this process did not approve itself. Remove
    entries by editing ``command_allowlist`` in config.yaml.
    """
    pass
'''

_GATE_PORT_PREFIX = '''async def _plugin_api_runtime_gate(request: Request, call_next):
    """Block requests to disabled plugin API routes at request time.

    :func:`_mount_plugin_api_routes` gates at import time; a plugin disabled
    while running keeps its router mounted until restart, so enforce on every
    ``/api/plugins/{name}/...`` request. Registered BEFORE the auth middlewares
    (runs AFTER them): an unauthenticated caller must get auth's 401, never this
    404, or the status code becomes a plugin-name oracle.
    """
    path = request.url.path
    # parts: ['', 'api', 'plugins', '<name>', ...]
    parts = path.split("/")
    plugin_name = parts[3] if path.startswith("/api/plugins/") and len(parts) >= 4 else ""
    # Only gate authenticated requests. Unauthenticated ones fall through so
    # auth_middleware / the OAuth gate return 401 first and this route can't
    # be used as a plugin-name oracle.
    if plugin_name and (
        getattr(request.state, "token_authenticated", False)
        or getattr(request.app.state, "auth_required", False)
        or _has_valid_session_token(request)
        or _has_valid_query_token(request, path)
    ):
        try:
            from hermes_cli.plugins_cmd import _get_enabled_set, _get_disabled_set
            enabled_set = _get_enabled_set()
            disabled_set = _get_disabled_set()
        except Exception:
            enabled_set = set()
            disabled_set = set()
        plugin = next((p for p in _get_dashboard_plugins() if p.get("name") == plugin_name), None)
        source = plugin.get("source") if plugin else "user"
        blocked = plugin_name in disabled_set or (source == "user" and plugin_name not in enabled_set)
        if blocked and source in ("user", "bundled"):
            return JSONResponse(status_code=404, content={"detail": "Plugin not found"})
    return await call_next(request)
'''

_GATE_PORT_POSTFIX = '''async def _plugin_api_runtime_gate(request: Request, call_next):
    """Block requests to disabled plugin API routes at request time.

    :func:`_mount_plugin_api_routes` gates at import time; a plugin disabled
    while running keeps its router mounted until restart, so enforce on every
    ``/api/plugins/{name}/...`` request. Registered BEFORE the auth middlewares
    (runs AFTER them): an unauthenticated caller must get auth's 401, never this
    404, or the status code becomes a plugin-name oracle.
    """
    path = request.url.path
    # parts: ['', 'api', 'plugins', '<name>', ...]
    parts = path.split("/")
    plugin_name = parts[3] if path.startswith("/api/plugins/") and len(parts) >= 4 else ""
    # Only gate authenticated requests. Unauthenticated ones fall through so
    # auth_middleware / the OAuth gate return 401 first and this route can't
    # be used as a plugin-name oracle. Desktop authenticates with a native ticket.
    if plugin_name and (
        getattr(request.state, "token_authenticated", False)
        or getattr(request.state, "native_http_principal", None)
        or getattr(request.app.state, "auth_required", False)
        or _has_valid_session_token(request)
        or _has_valid_query_token(request, path)
    ):
        try:
            from hermes_cli.plugins_cmd import _get_enabled_set, _get_disabled_set
            enabled_set = _get_enabled_set()
            disabled_set = _get_disabled_set()
        except Exception:
            enabled_set = set()
            disabled_set = set()
        plugin = next((p for p in _get_dashboard_plugins() if p.get("name") == plugin_name), None)
        source = plugin.get("source") if plugin else "user"
        blocked = plugin_name in disabled_set or (source == "user" and plugin_name not in enabled_set)
        if blocked and source in ("user", "bundled"):
            return JSONResponse(status_code=404, content={"detail": "Plugin not found"})
    return await call_next(request)
'''


def _port_branded(text: str) -> str:
    """Brand a fixture the way ``brand_tree`` brands an upstream file."""
    return BrandingRules().transform_text(text)


def _port_normalise_tokens(text: str) -> str:
    """Collapse every brand spelling to ``X`` so a branded fork file and its
    upstream source can be compared for semantic (not spelling) equality."""
    return re.sub(r"(?i)(hermes|nous|nastech)", "X", text)


def _port_write(tmp_path, rel: str, text: str):
    root = tmp_path / "branded"
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return root, target


def test_reconcile_gateway_allowlist_live_reload_emits_upstream_post_fix(tmp_path):
    """The port must reproduce upstream's own post-fix text (brand-normalised)."""
    root, target = _port_write(
        tmp_path, "tools/approval.py", _port_branded(_APPROVAL_PORT_PREFIX)
    )

    assert _reconcile_gateway_allowlist_live_reload(str(root)) is True
    emitted = target.read_text(encoding="utf-8")
    expected = _port_branded(_APPROVAL_PORT_POSTFIX)
    assert emitted == expected
    assert _port_normalise_tokens(emitted) == _port_normalise_tokens(expected)
    compile(emitted, str(target), "exec")

    # Idempotent: the helper is the fixed point, so a second pass is a no-op.
    assert _reconcile_gateway_allowlist_live_reload(str(root)) is False


def test_reconcile_gateway_plugin_gate_emits_upstream_post_fix(tmp_path):
    """The native-ticket condition must land exactly as upstream wrote it."""
    root, target = _port_write(
        tmp_path, "nastech_cli/web_server.py", _port_branded(_GATE_PORT_PREFIX)
    )

    assert _reconcile_gateway_plugin_gate_native_ticket(str(root)) is True
    emitted = target.read_text(encoding="utf-8")
    expected = _port_branded(_GATE_PORT_POSTFIX)
    assert emitted == expected
    assert _port_normalise_tokens(emitted) == _port_normalise_tokens(expected)
    compile(emitted, str(target), "exec")

    assert _reconcile_gateway_plugin_gate_native_ticket(str(root)) is False


def test_gateway_security_ports_skip_absent_files(tmp_path):
    """Files upstream may move away: nothing to patch, no error."""
    assert _reconcile_gateway_allowlist_live_reload(str(tmp_path)) is False
    assert _reconcile_gateway_plugin_gate_native_ticket(str(tmp_path)) is False


def test_reconcile_gateway_allowlist_live_reload_flags_a_reshaped_file(tmp_path):
    """A moved ``_permanent_set`` must fail the sync, not silently drop the port."""
    root, _ = _port_write(
        tmp_path,
        "tools/approval.py",
        "def _permanent_set() -> set:\n    return _permanent_approved\n",
    )

    with pytest.raises(GatewayAllowlistReloadError):
        _reconcile_gateway_allowlist_live_reload(str(root))
    with pytest.raises(GatewayAllowlistReloadError):
        reconcile_tree(str(root))


def test_reconcile_gateway_allowlist_live_reload_flags_a_missing_call_site(tmp_path):
    """A present helper with no call site is still the unpatched defect."""
    root, _ = _port_write(
        tmp_path,
        "tools/approval.py",
        "def _sync_permanent_from_disk(approved: set, key: str) -> None:\n"
        "    return None\n",
    )

    with pytest.raises(GatewayAllowlistReloadError):
        _reconcile_gateway_allowlist_live_reload(str(root))


def test_reconcile_gateway_plugin_gate_flags_a_reshaped_file(tmp_path):
    root, _ = _port_write(
        tmp_path, "nastech_cli/web_server.py", "async def _plugin_api_runtime_gate():\n    pass\n"
    )

    with pytest.raises(GatewayPluginGateError):
        _reconcile_gateway_plugin_gate_native_ticket(str(root))
    with pytest.raises(GatewayPluginGateError):
        reconcile_tree(str(root))


def test_reconcile_tree_registers_the_gateway_security_ports(tmp_path):
    root = tmp_path / "branded"
    (root / "tools").mkdir(parents=True)
    (root / "nastech_cli").mkdir(parents=True)
    (root / "tools" / "approval.py").write_text(
        _port_branded(_APPROVAL_PORT_PREFIX), encoding="utf-8"
    )
    (root / "nastech_cli" / "web_server.py").write_text(
        _port_branded(_GATE_PORT_PREFIX), encoding="utf-8"
    )

    result = reconcile_tree(str(root))

    assert "tools/approval.py" in result.fixed
    assert "nastech_cli/web_server.py" in result.fixed


def _port_function_source(source: str, name: str) -> str | None:
    """Source of module-level ``name`` in ``source``, decorators stripped so the
    function can be compiled without the FastAPI app that upstream decorates it with."""
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            node.decorator_list = []
            return ast.unparse(node)
    return None


def _install_nastech_constants_stub(monkeypatch):
    """Stand in for the branded ``nastech_constants`` the approval fixture imports."""
    stub = types.ModuleType("nastech_constants")
    stub.get_nastech_home_override = lambda: None  # unscoped, single-profile process
    stub.nastech_home_key = lambda: ""
    monkeypatch.setitem(sys.modules, "nastech_constants", stub)


def _approval_namespace(source: str, disk: set) -> dict:
    """Exec the fixture's approval-check functions over module state, exactly as the
    running gateway holds it: one long-lived process, ``disk`` = config.yaml."""
    namespace = {
        "logger": logging.getLogger("gateway-allowlist-port-test"),
        "_permanent_approved": set(disk),
        "_permanent_approved_by_home": {},
        "_permanent_baseline_by_home": {"": set(disk)},
        "_read_permanent_allowlist": lambda: set(disk),
        "_lock": threading.Lock(),
        "_session_approved": {},
        "_approval_key_aliases": lambda key: {key},
    }
    for name in ("_permanent_set", "_sync_permanent_from_disk", "is_approved"):
        found = _port_function_source(source, name)
        if found is not None:
            exec(found, namespace)
    assert "_permanent_set" in namespace and "is_approved" in namespace
    return namespace


def _assert_allowlist_removal_takes_effect(monkeypatch, source: str) -> None:
    """A hand removal must reach the very next approval check of a live process."""
    _install_nastech_constants_stub(monkeypatch)
    disk = {"rm -rf build", "ls *"}
    namespace = _approval_namespace(source, disk)

    assert namespace["is_approved"]("s1", "rm -rf build") is True  # standing approval

    disk.discard("rm -rf build")  # the operator edits command_allowlist in config.yaml

    # No re-import and no restart: the next check must see the withdrawal.
    assert namespace["is_approved"]("s1", "rm -rf build") is False, (
        "the removal must reach a live process without a restart"
    )
    assert namespace["is_approved"]("s1", "ls *") is True


def test_allowlist_removal_needs_a_restart_on_the_pre_fix_text(monkeypatch):
    """Documents the defect the port closes: a removed ``rm -rf`` stays auto-approved."""
    with pytest.raises(AssertionError, match="without a restart"):
        _assert_allowlist_removal_takes_effect(
            monkeypatch, _port_branded(_APPROVAL_PORT_PREFIX)
        )


def test_allowlist_removal_takes_effect_without_restart_once_reconciled(monkeypatch, tmp_path):
    root, target = _port_write(
        tmp_path, "tools/approval.py", _port_branded(_APPROVAL_PORT_PREFIX)
    )
    assert _reconcile_gateway_allowlist_live_reload(str(root)) is True

    _assert_allowlist_removal_takes_effect(monkeypatch, target.read_text(encoding="utf-8"))


class _FakeJSONResponse:
    """Starlette's ``JSONResponse`` reduced to what the gate returns."""

    def __init__(self, status_code: int = 200, content=None):
        self.status_code = status_code
        self.content = content


def _install_plugins_cmd_stub(monkeypatch, enabled: set, disabled: set):
    """Stand in for the branded ``nastech_cli.plugins_cmd`` the gate imports lazily."""
    plugins_cmd = types.ModuleType("nastech_cli.plugins_cmd")
    plugins_cmd._get_enabled_set = lambda: set(enabled)
    plugins_cmd._get_disabled_set = lambda: set(disabled)
    package = types.ModuleType("nastech_cli")
    package.plugins_cmd = plugins_cmd
    monkeypatch.setitem(sys.modules, "nastech_cli", package)
    monkeypatch.setitem(sys.modules, "nastech_cli.plugins_cmd", plugins_cmd)


def _plugin_gate_response(source: str):
    """Run the fixture's gate for a Desktop native-ticket request to a disabled plugin."""
    found = _port_function_source(source, "_plugin_api_runtime_gate")
    assert found is not None, "fixture lost _plugin_api_runtime_gate"
    namespace = {
        "Request": object,  # only the parameter annotation; never used at runtime
        "JSONResponse": _FakeJSONResponse,
        "_has_valid_session_token": lambda request: False,
        "_has_valid_query_token": lambda request, path: False,
        "_get_dashboard_plugins": lambda: [{"name": "hot", "source": "user"}],
    }
    exec(found, namespace)
    request = types.SimpleNamespace(
        url=types.SimpleNamespace(path="/api/plugins/hot/probe"),
        # Desktop's native ticket, and neither a session token nor the OAuth gate.
        state=types.SimpleNamespace(
            native_http_principal={"profile_id": "default", "purpose": "native-http"}
        ),
        app=types.SimpleNamespace(state=types.SimpleNamespace(auth_required=False)),
    )

    async def call_next(next_request):
        return _FakeJSONResponse(status_code=200)

    return asyncio.run(namespace["_plugin_api_runtime_gate"](request, call_next))


def _assert_plugin_gate_blocks_native_ticket(source: str) -> None:
    response = _plugin_gate_response(source)
    assert response.status_code == 404, "a disabled plugin answered a native-ticket request"
    assert response.content == {"detail": "Plugin not found"}


def test_plugin_gate_ignores_a_native_ticket_on_the_pre_fix_text(monkeypatch):
    """Documents the defect the port closes: Desktop reaches a disabled plugin."""
    _install_plugins_cmd_stub(monkeypatch, enabled={"hot"}, disabled={"hot"})

    with pytest.raises(AssertionError, match="answered a native-ticket request"):
        _assert_plugin_gate_blocks_native_ticket(_port_branded(_GATE_PORT_PREFIX))


def test_plugin_gate_blocks_a_native_ticket_once_reconciled(monkeypatch, tmp_path):
    _install_plugins_cmd_stub(monkeypatch, enabled={"hot"}, disabled={"hot"})
    root, target = _port_write(
        tmp_path, "nastech_cli/web_server.py", _port_branded(_GATE_PORT_PREFIX)
    )
    assert _reconcile_gateway_plugin_gate_native_ticket(str(root)) is True

    _assert_plugin_gate_blocks_native_ticket(target.read_text(encoding="utf-8"))


_TERMUX_DOC_REL = "website/docs/getting-started/termux.md"
_TERMUX_I18N_REL = (
    "website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/"
    "getting-started/termux.md"
)
_TERMUX_DOC_UPSTREAM = (
    "# Nastech on Android with Termux\n"
    "\n"
    "3. Verify its primary fingerprint:\n"
    "\n"
    "   ```bash\n"
    "   gpg --show-keys --with-fingerprint \"$PREFIX/etc/apt/keyrings/nastech-agent.asc\"\n"
    "   ```\n"
    "\n"
    "   The repository key fingerprint is:\n"
    "\n"
    "   ```text\n"
    "   C572 B5FD D1A2 9CCF A9A9 12B6 840B 0848 E139 156D\n"
    "   ```\n"
    "\n"
    "   If the fingerprint differs, stop. Do not disable signature verification.\n"
)


def _write_termux_docs(root):
    """Write the English page and its zh-Hans translation, as upstream ships them."""
    primary = root / _TERMUX_DOC_REL
    i18n = root / _TERMUX_I18N_REL
    for path in (primary, i18n):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_TERMUX_DOC_UPSTREAM, encoding="utf-8")
    return primary, i18n


def test_reconcile_termux_apt_signing_key_rewrites_upstreams_fingerprint(tmp_path):
    """The docs must name the fork's key, not the one upstream publishes.

    Upstream hardcodes its own repository fingerprint; branding renames the host
    in the surrounding curl/sources lines but not the fingerprint, and the fork
    cannot sign with a key it does not hold.  Left alone, the page tells every
    user to stop, because the ``key.asc`` the fork publishes is signed by a
    different primary key.
    """
    root = tmp_path / "branded"
    primary, i18n = _write_termux_docs(root)

    assert _reconcile_termux_apt_signing_key(str(root)) == [
        _TERMUX_DOC_REL,
        _TERMUX_I18N_REL,
    ]
    for path in (primary, i18n):
        text = path.read_text(encoding="utf-8")
        assert "7F58 93A2 C517 FF53 185B 7C66 BE65 AFD0 8530 2A09" in text
        assert "C572 B5FD" not in text
        # The rest of the block, including the stop instruction, survives.
        assert "If the fingerprint differs, stop." in text


def test_reconcile_termux_apt_signing_key_is_a_fixed_point(tmp_path):
    """A second pass changes nothing, and reconcile_tree reports it exactly once."""
    root = tmp_path / "branded"
    _write_termux_docs(root)

    result = reconcile_tree(str(root))
    assert _TERMUX_DOC_REL in result.fixed
    assert result.fixed.count(_TERMUX_DOC_REL) == 1

    # The fork fingerprint is the fixed point.
    assert _reconcile_termux_apt_signing_key(str(root)) == []


def test_reconcile_termux_apt_signing_key_flags_a_moved_block(tmp_path):
    """A page that lost its fingerprint must fail the sync, not ship anchorless.

    The fingerprint is the only trust anchor the install instructions give; a
    silent no-op would publish a ``key.asc`` the page never names.
    """
    root = tmp_path / "branded"
    primary = root / _TERMUX_DOC_REL
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text("# Termux\n\nInstall steps moved elsewhere.\n", encoding="utf-8")

    with pytest.raises(TermuxAptKeyDocError):
        _reconcile_termux_apt_signing_key(str(root))
    with pytest.raises(TermuxAptKeyDocError):
        reconcile_tree(str(root))


def _assets_worker_dir(root):
    path = root / "scripts" / "cloudflare" / "assets-worker"
    path.mkdir(parents=True, exist_ok=True)
    return path


_STALE_WRANGLER = (
    'name = "nastech-agent"\n'
    'main = "src/index.js"\n'
    'compatibility_date = "2026-10-05"\n'
    "workers_dev = true\n"
    "\n"
    "[[kv_namespaces]]\n"
    'binding = "NASTECH_ASSETS"\n'
    'id = "ff12d5a5324d44b599d8319edabc4adb"\n'
)

# The fork's Worker entry point before the Termux fix: a verbatim-key, KV-only
# lookup.  It carries the shapes the reconcile pins on -- the ``[previews]``
# sibling ``const LANDING = `<!doctype html>`` anchor and the pre-fix releases
# block -- so a stale snapshot of this file must be upgraded, not shipped.
_STALE_WORKER_INDEX = (
    "const LANDING = `<!doctype html>\n"
    "<html></html>`;\n"
    "\n"
    "function json(body, status = 200) {\n"
    "  return new Response(JSON.stringify(body), {\n"
    "    status,\n"
    '    headers: { "content-type": "application/json; charset=utf-8" },\n'
    "  });\n"
    "}\n"
    "\n"
    "export default {\n"
    "  async fetch(request, env) {\n"
    "    const path = new URL(request.url).pathname;\n"
    "    // Everything under /releases/ is served from the KV store (publication\n"
    "    // target). Unknown objects answer a protocol-correct 404, never a redirect.\n"
    '    if (path.startsWith("/releases/")) {\n'
    '      const key = path.replace(/^\\/+/, "");\n'
    '      const value = await env.NASTECH_ASSETS.get(key, "text");\n'
    '      return new Response(value ?? "", { status: value === null ? 404 : 200 });\n'
    "    }\n"
    '    return new Response("", { status: 404 });\n'
    "  },\n"
    "};\n"
)


def test_reconcile_assets_worker_config_pins_name_entry_and_previews(tmp_path):
    """The preserved Worker config must match the deployed host the CLI reads."""
    root = tmp_path / "branded"
    worker = _assets_worker_dir(root)
    target = worker / "wrangler.toml"
    target.write_text(_STALE_WRANGLER, encoding="utf-8")
    rel = "scripts/cloudflare/assets-worker/wrangler.toml"

    changed = _reconcile_assets_worker_config(str(root), [rel])

    assert changed == [rel]
    text = target.read_text(encoding="utf-8")
    assert 'name = "nastech-assets"' in text
    assert 'main = "index.js"' in text
    assert "[previews]" in text
    assert "src/index.js" not in text
    # Every other fork-authored byte is untouched.
    assert 'compatibility_date = "2026-10-05"' in text
    assert 'binding = "NASTECH_ASSETS"' in text


def test_reconcile_assets_worker_config_is_idempotent(tmp_path):
    root = tmp_path / "branded"
    worker = _assets_worker_dir(root)
    target = worker / "wrangler.toml"
    target.write_text(_STALE_WRANGLER, encoding="utf-8")
    rel = "scripts/cloudflare/assets-worker/wrangler.toml"

    assert _reconcile_assets_worker_config(str(root), [rel]) == [rel]
    fixed = target.read_text(encoding="utf-8")
    # A second pass has nothing left to pin.
    assert _reconcile_assets_worker_config(str(root), [rel]) == []
    assert target.read_text(encoding="utf-8") == fixed


def test_reconcile_assets_worker_config_only_touches_preserved_paths(tmp_path):
    root = tmp_path / "branded"
    worker = _assets_worker_dir(root)
    target = worker / "wrangler.toml"
    target.write_text(_STALE_WRANGLER, encoding="utf-8")

    # A file the preserve stage did not carry over is left alone.
    assert _reconcile_assets_worker_config(str(root), ["other/file.toml"]) == []
    assert target.read_text(encoding="utf-8") == _STALE_WRANGLER


def test_reconcile_assets_worker_config_skips_missing_file(tmp_path):
    rel = "scripts/cloudflare/assets-worker/wrangler.toml"
    assert _reconcile_assets_worker_config(str(tmp_path), [rel]) == []


def test_pipeline_pins_preserved_assets_worker_config(tmp_path):
    """End to end: preserve carries the fork-only Worker in, the post-preserve
    reconcile pins the config AND upgrades the handler, and the gate stays green.

    ``scripts/cloudflare/assets-worker`` has no upstream source, so a stale
    snapshot restored by an update would otherwise replay
    ``main = "src/index.js"`` (breaking the Cloudflare Workers Builds deploy)
    and the verbatim-key, KV-only handler that 404s every Termux artifact.
    """
    hermes = _hermes_repo(tmp_path)
    baseline = subprocess.check_output(
        ["git", "-C", hermes, "rev-parse", "HEAD"], text=True
    ).strip()
    fork = tmp_path / "nastech-agent"
    worker = _assets_worker_dir(fork)
    (fork / "manifest.json").write_text(json.dumps({"upstream_sha": baseline}))
    (worker / "index.js").write_text(_STALE_WORKER_INDEX, encoding="utf-8")
    (worker / "wrangler.toml").write_text(_STALE_WRANGLER, encoding="utf-8")

    res = UpdateManager(
        str(tmp_path / "Updates-Commits"),
        hermes_url=hermes,
        fork_root=str(fork),
    ).run()

    assert res.gate
    candidate = os.path.join(res.dir, "scripts", "cloudflare", "assets-worker", "wrangler.toml")
    pinned = open(candidate, encoding="utf-8").read()
    assert 'name = "nastech-assets"' in pinned
    assert 'main = "index.js"' in pinned
    assert "[previews]" in pinned
    assert "src/index.js" not in pinned
    # The R2 binding the live Worker needs must survive the replay.
    assert "[[r2_buckets]]" in pinned
    assert 'bucket_name = "nastech-assets"' in pinned
    # And the handler must come out decoding the key and reading R2.
    index = os.path.join(res.dir, "scripts", "cloudflare", "assets-worker", "index.js")
    handler = open(index, encoding="utf-8").read()
    assert "function decodeChannelKey(" in handler
    assert "const object = await env.R2.get(key);" in handler
    assert 'const key = path.replace(/^\\/+/, "");' not in handler


# ---------------------------------------------------------------------------
# Dead configuration produced by the .com -> github.io substitution
# ---------------------------------------------------------------------------

_DEAD_AUTH_CONSTANTS = (
    "AUTH_STORE_VERSION = 1\n"
    "\n"
    "# Nastech Portal defaults\n"
    'DEFAULT_NASTECH_PORTAL_URL = "https://portal.nastechresearch.github.io"\n'
    'DEFAULT_NASTECH_INFERENCE_URL = "https://inference-api.nastechresearch.github.io/v1"\n'
    'DEFAULT_NASTECH_WELCOME_URL = "https://welcome-api.nastechresearch.github.io/v1"\n'
    'DEFAULT_NASTECH_CLIENT_ID = "nastech-cli"\n'
)


def _exec_module(text):
    """Compile+exec a reconciled module body; return its namespace."""
    namespace = {}
    exec(compile(text, "<reconciled>", "exec"), namespace)  # noqa: S102 - test fixture
    return namespace


def test_reconcile_dead_api_defaults_fails_closed(tmp_path):
    """portal/inference/welcome defaults must not silently point at a dead host.

    Verified live: https://portal.nastechresearch.github.io and
    https://inference-api.nastechresearch.github.io/v1 both fail TLS with
    "no alternative certificate subject name matches" (GitHub's certificate
    carries only `*.github.io`, one label).  So the reconciled constant must
    never hand back the dead URL, yet stay safe to stringify and strip: real
    consumers build their authorize/token/JWKS URLs from it while they
    register (nastech-agent#157), so the marker is an RFC 2606 `.invalid`
    origin that names the environment variable to set.  A request then fails
    at DNS -- nothing is ever sent to a host that cannot answer.
    """
    cli = tmp_path / "nastech_cli"
    cli.mkdir()
    (cli / "auth_constants.py").write_text(_DEAD_AUTH_CONSTANTS)

    fixed = _reconcile_dead_api_defaults(str(tmp_path))
    assert fixed == ["nastech_cli/auth_constants.py"]

    text = (cli / "auth_constants.py").read_text()
    # The dead URL survives only as the recorded provenance of the default
    # (the dead_default= argument); it is never the assigned value any more.
    assert '= "https://portal.nastechresearch.github.io"' not in text
    assert '= "https://inference-api.nastechresearch.github.io/v1"' not in text
    assert '= "https://welcome-api.nastechresearch.github.io/v1"' not in text
    assert "DEFAULT_NASTECH_PORTAL_URL = _nastech_endpoint_default(" in text
    assert "_NastechUnconfiguredEndpoint" in text

    namespace = _exec_module(text)
    for name, env in (
        ("DEFAULT_NASTECH_PORTAL_URL", "NASTECH_PORTAL_URL"),
        ("DEFAULT_NASTECH_INFERENCE_URL", "NASTECH_INFERENCE_URL"),
        ("DEFAULT_NASTECH_WELCOME_URL", "NASTECH_WELCOME_URL"),
    ):
        value = namespace[name]
        assert not value, f"{name} must read as unconfigured"
        # Stringifying must NOT raise: consumers -- the dashboard-auth
        # provider among them -- build their authorize/token/JWKS URLs from
        # this constant while they register.  A default whose str() raised
        # took the whole provider down: no auth provider, a fail-closed
        # non-loopback bind that therefore never happened, and
        # ConnectionRefused for every request (nastech-agent#157).  The marker
        # is an RFC 2606 `.invalid` origin instead: no resolver can answer it,
        # so the branded dead origin is still never used and no credential is
        # ever sent to a real host.
        marker = str(value)
        assert "nastechresearch.github.io" not in marker
        assert marker.startswith("https://")
        assert ".invalid" in marker
        assert env.lower().replace("_", "-") in marker
        # The shapes the auth code uses while registering must work.
        assert value.rstrip("/") == marker
        assert f"{value}/models" == f"{marker}/models"
    # an unrelated constant is untouched
    assert namespace["AUTH_STORE_VERSION"] == 1
    assert namespace["DEFAULT_NASTECH_CLIENT_ID"] == "nastech-cli"

    # idempotent: the dead literal is gone, so a second pass changes nothing
    assert _reconcile_dead_api_defaults(str(tmp_path)) == []


def test_reconcile_dead_api_defaults_survives_construction_time_use(tmp_path):
    """A consumer may build URLs from the default while it registers.

    ``plugins/dashboard_auth/nastech`` does ``portal_url.rstrip("/")`` and then
    ``f"{url}/oauth/authorize"`` inside ``NastechDashboardAuthProvider.__init__``.
    While the default raised there, registration died, the dashboard failed
    closed on a non-loopback bind, and every request was refused for the whole
    probe (nastech-agent#157, docker run 39022093014).  The unconfigured default
    must therefore behave like an ordinary string -- and still be unusable.
    """
    cli = tmp_path / "nastech_cli"
    cli.mkdir()
    (cli / "auth_constants.py").write_text(_DEAD_AUTH_CONSTANTS)

    assert _reconcile_dead_api_defaults(str(tmp_path)) == ["nastech_cli/auth_constants.py"]
    namespace = _exec_module((cli / "auth_constants.py").read_text())

    portal = namespace["DEFAULT_NASTECH_PORTAL_URL"]
    base = portal.rstrip("/")  # the provider's first use of the default
    assert base == str(portal)
    assert base.endswith(".invalid")
    assert "nastechresearch.github.io" not in base
    for derived in (f"{base}/oauth/authorize", f"{base}/.well-known/jwks.json"):
        assert derived.startswith("https://")
        assert ".invalid/" in derived

    # the /v1 suffix shape is preserved, so request URLs look the same
    inference = namespace["DEFAULT_NASTECH_INFERENCE_URL"]
    assert str(inference).endswith("/v1")
    assert f"{inference}/models".endswith("/v1/models")


def test_reconcile_dead_api_defaults_honours_configured_env(tmp_path, monkeypatch):
    """An operator-supplied endpoint is used verbatim (rstripped)."""
    cli = tmp_path / "nastech_cli"
    cli.mkdir()
    (cli / "auth_constants.py").write_text(_DEAD_AUTH_CONSTANTS)
    _reconcile_dead_api_defaults(str(tmp_path))
    monkeypatch.setenv("NASTECH_PORTAL_URL", "https://portal.test.invalid/")
    namespace = _exec_module((cli / "auth_constants.py").read_text())
    assert namespace["DEFAULT_NASTECH_PORTAL_URL"] == "https://portal.test.invalid"
    # only the configured variable is replaced; the others stay fail-closed
    assert not namespace["DEFAULT_NASTECH_INFERENCE_URL"]


def test_reconcile_dead_api_defaults_leaves_live_and_apex_hosts(tmp_path):
    """Only dead subdomains are touched; the real worker host and the apex stay."""
    cli = tmp_path / "nastech_cli"
    cli.mkdir()
    (cli / "source_releases.py").write_text(
        '_PUBLIC_BASE = "https://nastech-assets.nastechresearch.workers.dev"\n'
    )
    (cli / "auth_constants.py").write_text(
        'DEFAULT_NASTECH_DOCS_URL = "https://nastechresearch.github.io/nastech-agent/docs"\n'
    )
    assert _reconcile_dead_api_defaults(str(tmp_path)) == []
    assert "_NastechUnconfiguredEndpoint" not in (
        (cli / "auth_constants.py").read_text())


def test_reconcile_security_contact_removes_undeliverable_mailbox(tmp_path):
    """github.io accepts no mail, so the security address must not survive.

    Verified: github.io has no MX record, so
    security@nastechresearch.github.io silently loses every report.  The
    live GitHub Security Advisory channel (302 to sign-in on the fork's
    advisory page) is kept.
    """
    (tmp_path / "SECURITY.md").write_text(
        "## 1. Reporting a Vulnerability\n"
        "\n"
        "Report privately via [GitHub Security Advisories](https://github.com/NastechResearch/nastech-agent/security/advisories/new)\n"
        "or **security@nastechresearch.github.io**. Do not open public issues for\n"
        "security vulnerabilities.\n"
        "\n"
        "## 5. Disclosure\n"
        "\n"
        "- **Channel:** the GHSA thread or email correspondence with\n"
        "  security@nastechresearch.github.io.\n"
    )
    fixed = _reconcile_security_contact(str(tmp_path))
    assert fixed == ["SECURITY.md"]

    text = (tmp_path / "SECURITY.md").read_text()
    assert "security@" not in text
    assert "github.io" in text  # the reason is stated, not the address
    assert "has no MX record" in text
    # the deliverable channel is still advertised, once and untouched
    assert text.count(
        "https://github.com/NastechResearch/nastech-agent/security/advisories/new"
    ) == 1
    # grammatical in both spots
    assert "only; this fork publishes no mailbox" in text
    assert "no MX record. Do not open public issues" in text
    assert "the GHSA thread (this fork publishes no mailbox" in text

    # idempotent
    assert _reconcile_security_contact(str(tmp_path)) == []
    assert (tmp_path / "SECURITY.md").read_text() == text


def test_reconcile_ui_scope_pins_rejects_range_and_unknown_version(tmp_path):
    """A range silently installs a different package: fail closed instead.

    Verified against registry.npmjs.org: @nous-research/ui latest is 1.5.2,
    @nastech-research/ui latest is 0.18.4 -- so "^1.5.2" or "latest" cannot
    mean the same thing after branding.
    """
    app = tmp_path / "apps" / "desktop"
    app.mkdir(parents=True)
    manifest = app / "package.json"

    manifest.write_text(json.dumps(
        {"name": "desktop", "dependencies": {"@nastech-research/ui": "^1.5.2"}}))
    with pytest.raises(ValueError) as excinfo:
        _reconcile_ui_scope_pins(str(tmp_path))
    # conformance catches ValueError; keep that contract explicit
    assert issubclass(UiScopePinError, ValueError)
    assert isinstance(excinfo.value, UiScopePinError)
    assert "must be pinned to an exact version" in str(excinfo.value)
    assert "@nastech-research/ui" in str(excinfo.value)

    manifest.write_text(json.dumps(
        {"name": "desktop", "dependencies": {"@nastech-research/ui": "latest"}}))
    with pytest.raises(ValueError):
        _reconcile_ui_scope_pins(str(tmp_path))

    manifest.write_text(json.dumps(
        {"name": "desktop", "dependencies": {"@nastech-research/ui": "9.9.9"}}))
    with pytest.raises(ValueError) as excinfo:
        _reconcile_ui_scope_pins(str(tmp_path))
    assert "not a version the fork has published" in str(excinfo.value)
    assert "_NASTECH_UI_INTEGRITY" in str(excinfo.value)

    # the upstream scope must never survive branding
    manifest.write_text(json.dumps(
        {"name": "desktop", "dependencies": {"@nous-research/ui": "0.18.2"}}))
    with pytest.raises(ValueError) as excinfo:
        _reconcile_ui_scope_pins(str(tmp_path))
    assert "survived branding" in str(excinfo.value)


def test_reconcile_ui_scope_pins_allows_the_upstream_pinned_version(tmp_path):
    """Upstream pins 0.18.2 (verified in its package.json/package-lock.json)."""
    app = tmp_path / "apps" / "desktop"
    app.mkdir(parents=True)
    version = sorted(_NASTECH_UI_INTEGRITY)[-1]
    (app / "package.json").write_text(json.dumps(
        {"name": "desktop", "dependencies": {"@nastech-research/ui": version}}))
    assert _reconcile_ui_scope_pins(str(tmp_path)) == []
    assert "0.18.2" in _NASTECH_UI_INTEGRITY


def test_reconcile_domains_keeps_the_real_assets_worker_host(tmp_path):
    """Contrast case: the one real Nastech origin is never sent to github.io."""
    (tmp_path / "notes.md").write_text(
        "Assets: https://nastech-assets.nastechresearch.com/upstream/sha256/abc\n"
        "Legacy: https://nastech-assets.nastechresearch.github.io/releases/x\n"
        "Docs: https://nastechresearch.github.io/nastech-agent/docs\n"
    )
    assert _reconcile_domains(str(tmp_path)) == ["notes.md"]
    text = (tmp_path / "notes.md").read_text()
    assert "https://nastech-assets.nastechresearch.workers.dev/upstream/sha256/abc" in text
    assert "nastech-assets.nastechresearch.github.io" not in text
    assert "https://nastechresearch.github.io/nastech-agent/docs" in text


def test_pipeline_registers_the_dead_config_reconciles(tmp_path):
    """reconcile_tree must actually run the three new stages on a real run."""
    hermes = _hermes_repo(tmp_path)
    hermes_path = os.path.join(hermes, "hermes_cli")
    with open(os.path.join(hermes_path, "auth_constants.py"), "w", encoding="utf-8") as fh:
        fh.write(_DEAD_AUTH_CONSTANTS)
    with open(os.path.join(hermes, "SECURITY.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "Report privately via [GitHub Security Advisories](https://github.com/NousResearch/hermes-agent/security/advisories/new)\n"
            "or **security@nousresearch.com**. Do not open public issues.\n"
        )
    # Upstream's own scope, at a version the fork never published: branding
    # renames the scope to one whose 0.4.0 does not exist, so the stage must
    # repin it (via verified byte-identity) instead of failing the sync.
    tpl_dir = os.path.join(
        hermes, "optional-skills", "creative", "pretext", "templates")
    os.makedirs(tpl_dir)
    with open(os.path.join(tpl_dir, "donut-orbit.html"), "w", encoding="utf-8") as fh:
        fh.write(_REAL_DONUT_FONT_REFS.replace("nastech-research", "nous-research"))
    subprocess.run(["git", "-C", hermes, "add", "-A"], check=True)
    subprocess.run(["git", "-C", hermes, "commit", "-q", "-m", "dead config"], check=True)

    res = UpdateManager(
        str(tmp_path / "Updates-Commits"), hermes_url=hermes).run()

    assert res.gate, f"gate FAILED: {[s for s in res.stages if s.status == 'fail']}"
    assert "nastech_cli/auth_constants.py" in res.reconcile.fixed
    assert "SECURITY.md" in res.reconcile.fixed

    candidate = open(
        os.path.join(res.dir, "nastech_cli", "auth_constants.py"), encoding="utf-8").read()
    assert '= "https://portal.nastechresearch.github.io"' not in candidate
    assert "DEFAULT_NASTECH_PORTAL_URL = _nastech_endpoint_default(" in candidate
    assert "_NastechUnconfiguredEndpoint" in candidate

    security = open(os.path.join(res.dir, "SECURITY.md"), encoding="utf-8").read()
    assert "security@" not in security
    assert "nousresearch" not in security

    # the dead scoped-font reference became a published, byte-identical pin,
    # and the sync still produced a candidate (the gate did not fail)
    tpl_rel = "optional-skills/creative/pretext/templates/donut-orbit.html"
    assert tpl_rel in res.reconcile.fixed
    template = open(os.path.join(res.dir, tpl_rel), encoding="utf-8").read()
    assert "ui@0.4.0" not in template
    assert template.count("@nastech-research/ui@0.18.4") == 2
    assert "nous-research" not in template


def test_reconcile_ui_scope_pins_rejects_unpublished_cdn_version(tmp_path):
    """A scoped CDN reference must not point at a version nobody published.

    Verified live: upstream's @nous-research/ui@0.4.0 exists (npm 200) but the
    branded @nastech-research/ui@0.4.0 is 404 on npm AND on esm.sh, so the
    rebrand turns this template's font URL into a dead link that resolves to
    a package the fork never shipped.
    """
    tpl = tmp_path / "optional-skills" / "creative" / "pretext" / "templates"
    tpl.mkdir(parents=True)
    (tpl / "donut-orbit.html").write_text(
        '    src: url("https://esm.sh/@nastech-research/ui@0.4.0/dist/fonts/x.woff2");\n'
    )
    with pytest.raises(ValueError) as excinfo:
        _reconcile_ui_scope_pins(str(tmp_path))
    assert "@nastech-research/ui@0.4.0 is not a version the fork has published" in (
        str(excinfo.value))
    assert "never" not in str(excinfo.value)  # the message names the remedies

    # a reference at a published version is fine
    (tpl / "donut-orbit.html").write_text(
        '    src: url("https://esm.sh/@nastech-research/ui@0.18.2/dist/fonts/x.woff2");\n'
    )
    assert _reconcile_ui_scope_pins(str(tmp_path)) == []

    # a leftover upstream reference is always an error
    (tpl / "donut-orbit.html").write_text(
        '    src: url("https://esm.sh/@nous-research/ui@0.4.0/dist/fonts/x.woff2");\n'
    )
    with pytest.raises(ValueError) as excinfo:
        _reconcile_ui_scope_pins(str(tmp_path))
    assert "survived branding" in str(excinfo.value)


# The real optional-skills/creative/pretext/templates/donut-orbit.html (fork
# commit 907460ad) uses the renamed scope for FONTS only, in exactly the two
# shapes below; pretext/gsap/lil-gui keep their own upstream packages.
_REAL_DONUT_FONT_REFS = (
    "<style>\n"
    "@font-face {\n"
    '  font-family: "Mondwest";\n'
    '  src: url("https://esm.sh/@nastech-research/ui@0.4.0/dist/fonts/'
    'Mondwest-Regular.woff2") format("woff2");\n'
    '  font-weight: 400; font-style: normal; font-display: block;\n'
    "}\n"
    "</style>\n"
    "<script type=\"module\">\n"
    'import { Pretext } from "https://esm.sh/pretext@1.4.1/dist/index.js";\n'
    'import gsap from "https://esm.sh/gsap@3.12.5/dist/gsap.min.js";\n'
    'const DS_CDN = "https://esm.sh/@nastech-research/ui@0.4.0/dist/fonts";\n'
    "const FACES = [new FontFace(\"Mondwest\", `url(${DS_CDN}/"
    'Mondwest-Regular.woff2) format("woff2")`, { weight: "400", display: "block" })];\n'
    "</script>\n"
)


def test_ui_scope_asset_evidence_records_verified_byte_identity():
    """The repin is authorised by recorded evidence, not by assumption.

    sha256sum of dist/fonts/*.woff2 in the three published tarballs
    (@nous-research/ui@0.4.0, @nastech-research/ui@0.18.2 and @0.18.4) yields
    the same digest for all 14 files; these values are the recorded proof.
    """
    assert _UI_SCOPE_ASSET_EVIDENCE["dist/fonts/Mondwest-Regular.woff2"] == (
        "9cf6e6e1cba70f7e991a92ce4225785725b6cc9a48cc77cbfaabeba26c18f072",
        ("0.4.0", "0.18.2", "0.18.4"),
    )
    assert _UI_SCOPE_ASSET_EVIDENCE["dist/fonts/Collapse-Regular.woff2"] == (
        "ce8c481f56da3a46074ab696c16481bea49ea83e75e7eb7cb42fd2114593f33a",
        ("0.4.0", "0.18.2", "0.18.4"),
    )
    assert len(_UI_SCOPE_ASSET_EVIDENCE) == 14
    for asset, (digest, versions) in _UI_SCOPE_ASSET_EVIDENCE.items():
        assert asset.startswith("dist/fonts/")
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        # the unpublished source and at least one published target are covered
        assert "0.4.0" in versions
        assert set(versions) & set(_NASTECH_UI_INTEGRITY)


def test_reconcile_ui_scope_repins_verified_identical_font_version(tmp_path):
    """A dead version with a proven-identical published twin is repinned.

    Reproduces the real template: the direct font URL and the DS_CDN base
    both point at the unpublished 0.4.0; every asset they name is proven
    byte-identical in the published 0.18.4, so both references are repinned
    (instead of the stage failing and stopping the sync).
    """
    tpl = tmp_path / "optional-skills" / "creative" / "pretext" / "templates"
    tpl.mkdir(parents=True)
    template = tpl / "donut-orbit.html"
    template.write_text(_REAL_DONUT_FONT_REFS)

    assert _reconcile_ui_scope_pins(str(tmp_path)) == [
        "optional-skills/creative/pretext/templates/donut-orbit.html"]

    text = template.read_text()
    assert "ui@0.4.0" not in text
    assert text.count("@nastech-research/ui@0.18.4") == 2
    # the repin touches only the dead scope: other CDN packages are untouched
    assert "https://esm.sh/pretext@1.4.1/dist/index.js" in text
    assert "https://esm.sh/gsap@3.12.5/dist/gsap.min.js" in text

    # idempotent: the repinned version is published, so a second pass no-ops
    assert _reconcile_ui_scope_pins(str(tmp_path)) == []
    assert template.read_text() == text


def test_reconcile_ui_scope_pins_still_fails_closed_without_verified_target(tmp_path):
    """No evidence for the asset means no repin: raise, do not guess.

    A version the fork never published whose asset has no recorded
    byte-identical twin cannot be repaired safely, so the reference must stay
    loud -- this is the guarantee that keeps the repin from being blind.
    """
    tpl = tmp_path / "optional-skills" / "creative" / "pretext" / "templates"
    tpl.mkdir(parents=True)
    template = tpl / "donut-orbit.html"
    template.write_text(
        '    src: url("https://esm.sh/@nastech-research/ui@0.9.9/dist/fonts/'
        'Mondwest-Regular.woff2");\n'
    )
    with pytest.raises(UiScopePinError) as excinfo:
        _reconcile_ui_scope_pins(str(tmp_path))
    assert "no byte-identical copy in a published version" in str(excinfo.value)
    # unverified source version: even an evidenced asset must not be repinned
    assert template.read_text().count("ui@0.9.9") == 1

    # a directory base that resolves to an asset nothing verified is refused
    template.write_text(
        'const CDN = "https://esm.sh/@nastech-research/ui@0.4.0/dist/fonts";\n'
        'const F = [`url(${CDN}/Mystery-Regular.woff2)`];\n'
    )
    with pytest.raises(UiScopePinError):
        _reconcile_ui_scope_pins(str(tmp_path))

    # a bare directory base with no interpolation cannot be resolved: refuse
    template.write_text(
        'const CDN = "https://esm.sh/@nastech-research/ui@0.4.0/dist/fonts";\n'
    )
    with pytest.raises(UiScopePinError):
        _reconcile_ui_scope_pins(str(tmp_path))
