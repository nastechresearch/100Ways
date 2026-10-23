#!/usr/bin/env python3
"""100Ways - rebrand-safe upstream sync engine with 200+ named strategies.

Watches the upstream Hermes repo, ports commits onto the Nastech fork while
enforcing branding rules, verifies file-to-file parity, and notifies Telegram
and the opencode agent of anything that needs attention.

Commands
--------
ways       Browse the ways registry: list, show, pick a strategy per category.
update     Pull real Hermes, brand the whole tree, verify, save Nastech-Update#N.
forkcheck  Diff a snapshot against the nastech-agent fork (byte-identity + brand-clean).
check      One-shot: fetch upstream, report new commits + gaps.
watch      Live loop: poll upstream every --interval seconds.
plan       List upstream commits since our last port.
port       Apply rebranded upstream commits onto a branch (dry-run by default).
analyze    Full file-to-file gap analysis + brand violation scan.
diff       Two-phase: live upstream vs local Hermes, then branded vs Nastech.
scan       Report file formats across a tree (all formats, images, binaries).
research   Search open source (GitHub + offline catalog) for ideas to steal.
notify     Send a test notification to Telegram / agent.
status     Show repo state and rules validation.
verify     Parity gate vs the birth commit (exit 1 on fail).
ship       Verify parity then prepare the sync branch for shipping.
report     Write a markdown gap report to config/reports.
dashboard  Serve the live web dashboard (write access requires HUNDREDWAYS_ADMIN_TOKEN).
pull       Fetch upstream + write report + record achievement.
achievements  List achievements and unlock state.
profile    Rank a run's stages by cost, or compare two runs and name what got slower.
narrative  Build the PR title + body a reviewer reads, from the run's own manifest.
pubconfig  Predict which credential names a future run would read as an empty string.
why-red    Sort the reds in a check-runs payload into the class that owns the fix.
telegram   Show the command menu, resolve one command line, or send a probe.
lintsim    Run the fork's own lint contract over a candidate tree before publishing it.
urls       Find broken, fake, off-brand and placeholder links in a tree.
surface    Measure the surface upstream exposes, and what moved since a baseline.
verify-report  Write the one document saying what was measured and what was not.
errors     Report which lane turned red, which cleared, and which one nobody owns yet.
issues     Draft the issues the measured findings earn; open them only with --apply.
discuss    Draft the questions the engine is not allowed to answer for a human.
suggest    The next actions for what was measured, as a list or a bounded table.
table      Lay one measured family out so it fits the messages it will be sent in.
readme     Regenerate README.md from the ways + achievements + owned-asset registries.
release    Verify the code table and incoming codes.
admin      Compile/verify the admin password into its system-only token.
codes      Print the gap code legend.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from .achievements import Achievements
from .adapters import (
    RULE,
    BaselineError,
    diff_surface,
    find_orphans,
    load_baseline,
    save_baseline,
    scan_surface,
)
from .ai import AIEngine
from .analyzer import analyze
from .assets import OwnedAssets
from .brandaudit import create_issues, plan_issues, render_issues_markdown, run_audit, write_json
from .codes import CODE_DETAILS, code_name
from .coverage import coverage as ways_coverage
from .dashboard import serve as serve_dashboard
from .delivery import (
    deliver,
    deduplicate,
    open_discussion_keys,
    open_issue_keys,
    plan as plan_delivery,
    plan_json as delivery_json,
    render_plan,
    render_suggestions,
)
from .errorfeed import (
    ErrorFeedError,
    load_state,
    render as render_feed,
    save_state,
)
from .errorfeed import observe as observe_errors
from .errorfeed import to_json as to_json_feed
from .forklint import report_lines, run_fork_lint, summarize
from .git_ops import ensure_branch
from .narrative import body_from_manifest, garnish, title_from_manifest
from .notifier import Notification, notifier_from_env
from .port import new_upstream_commits, port_commits
from .profiling import (
    format_comparison,
    improvements,
    ranked,
    read_profile,
    regressions,
    total_ms,
)
from .pubconfig import (
    ActionsInventory,
    PublisherConfigurationError,
    analyze_root,
    fetch_inventory,
    render as render_pubconfig,
)
from .pubconfig import to_json as pubconfig_json
from .red_classifier import classify_runs
from .red_classifier import render as render_verdicts
from .release import release_check_table, release_summary, release_verify_incoming
from .research import research as run_research
from .rules import BrandingRules
from .scanner import classify_path
from .security import verify_token
from .telegram_agent import send_message as send_telegram
from .telegram_commands import LEVELS, grants, lookup, menu, parse, usage_for
from .telegram_tables import BLOCK, GRID, MAX_PAGES, TableError, answer, build
from .telegram_transport import TelegramDeliveryError
from .updates import DEFAULT_HERMES_URL, UpdateManager, default_updates_dir, hermes_path
from .urlcheck import (
    DECIDED,
    URL_POLICY_PATH,
    UrlPolicy,
    findings_to_json,
    probe_tally,
    render as render_urls,
    scan_tree as scan_url_tree,
)
from .verified_report import build as build_verified_report
from .verify import _git, _git_ok, gate_passes, verify_rebrand
from .watcher import Watcher, WatcherConfig
from .ways import build_registry
from .weekly_sync import build_weekly_report, save_ledger, write_weekly_report

DEFAULT_REPO = os.environ.get("HUNDREDWAYS_REPO", os.environ.get("SYNCBRIDGE_REPO", "/home/nascode/Documents/A1/nastech-agent"))
DEFAULT_HERMES = os.environ.get("HUNDREDWAYS_HERMES", "/home/nascode/Documents/A1/hermes-agent")
BIRTH_COMMIT = "0cafd22fb"
BIRTH_PARENT = "03fa32c92"
DEFAULT_UPSTREAM = "upstream/main"


class Cli:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.repo = args.repo
        self.rules = BrandingRules()

    # -- shared helpers ------------------------------------------------------

    def _upstream_head(self) -> str:
        _git(self.repo, "fetch", "upstream")
        return _git_ok(self.repo, "rev-parse", DEFAULT_UPSTREAM).strip()

    def _nastech_head(self) -> str:
        return _git_ok(self.repo, "rev-parse", "HEAD").strip()

    # -- commands ------------------------------------------------------------

    def cmd_check(self) -> None:
        watcher = Watcher(
            WatcherConfig(repo=self.repo, state_file=os.path.join(self.repo, "hundredways-state.json")),
            notifier_from_env(),
        )
        event = watcher.check_once()
        if event:
            print(event.title)
            print(event.body)
        else:
            print("no new events")

    def cmd_watch(self) -> None:
        watcher = Watcher(
            WatcherConfig(
                repo=self.repo,
                interval=self.args.interval,
                state_file=os.path.join(self.repo, "hundredways-state.json"),
            ),
            notifier_from_env(),
        )
        watcher.watch(max_cycles=self.args.max_cycles)

    def cmd_plan(self) -> None:
        _git(self.repo, "fetch", "upstream")
        head = _git_ok(self.repo, "rev-parse", "HEAD").strip()
        commits = new_upstream_commits(self.repo, DEFAULT_UPSTREAM, head)
        print(f"{len(commits)} upstream commit(s) not yet ported:")
        for sha in commits:
            subj = _git_ok(self.repo, "log", "-1", "--format=%s", sha).strip()
            date = _git_ok(self.repo, "log", "-1", "--format=%ad", "--date=short", sha).strip()
            print(f"  {sha[:8]} {date} {subj}")
        print("upstream:", _git_ok(self.repo, "rev-parse", DEFAULT_UPSTREAM).strip()[:8])
        print("our HEAD:", head[:8])

    def cmd_port(self) -> None:
        _git(self.repo, "fetch", "upstream")
        results = port_commits(
            self.repo,
            DEFAULT_UPSTREAM,
            branch=self.args.branch,
            rules=self.rules,
            threshold=self.args.threshold,
            dry_run=not self.args.apply,
        )
        if not results:
            print("nothing to port")
            return
        ai = AIEngine()
        for res in results:
            if res.status == "ported":
                print(f"  ported {res.port_sha[:8]} port({res.upstream_sha[:8]}): {res.subject}")
                print(f"    parity: {res.report.summary()}")
                print("    ai:", ai.review_port(res.report, res.upstream_sha))
            elif res.status == "failed":
                print(f"  FAILED {res.upstream_sha[:8]} {res.subject}: {res.error}")
            else:
                print(f"  {res.status}: {res.upstream_sha[:8]} {res.subject}")

    def cmd_analyze(self) -> None:
        _git(self.repo, "fetch", "upstream")
        upstream = _git_ok(self.repo, "rev-parse", DEFAULT_UPSTREAM).strip()
        head = _git_ok(self.repo, "rev-parse", "HEAD").strip()
        report = analyze(upstream, head, self.repo, self.rules)
        print(f"Gap {upstream[:8]} vs {head[:8]}: {report.summary}")
        for e in report.upstream_only():
            print(f"  [upstream-only] {e.path}")
        for e in report.changed():
            print(
                f"  [changed] {e.path} (+{e.added_lines}/-{e.deleted_lines}l, "
                f"+{e.added_chars}/-{e.deleted_chars}c)"
            )
        for e in report.violations():
            print(f"  [VIOLATION] {e.path}: {e.brand_violations}")
        ai = AIEngine()
        if self.args.ai:
            print("\n--- AI gap review ---")
            print(ai.review_gap(report, self.repo))

    def cmd_audit(self) -> None:
        """Brand-coverage audit: discover new brand spellings, residue, and secrets.

        Read-only by default.  ``--apply`` is the only path that opens issues, and
        it is never wired into a workflow: opening issues is a human decision.
        """
        source = self.args.source or self.args.candidate or self.repo
        audit = run_audit(
            source,
            self.args.candidate,
            self.rules,
            max_samples=self.args.max_samples,
            assets_root=self.args.assets_root,
        )
        print(audit.render_markdown())
        if self.args.json:
            write_json(audit, self.args.json)
            print(f"wrote {self.args.json}")
        drafts = plan_issues(audit, self.repo)
        if self.args.issues:
            with open(self.args.issues, "w", encoding="utf-8") as fh:
                fh.write(render_issues_markdown(drafts))
            print(f"wrote {self.args.issues} ({len(drafts)} draft(s))")
        if drafts and self.args.apply:
            for result in create_issues(self.repo, drafts, apply=True):
                print(result)
        elif drafts:
            print(f"{len(drafts)} issue draft(s) prepared; re-run with --apply to open them.")
        if self.args.require_clean and not audit.clean:
            sys.exit(1)

    def cmd_scan(self) -> None:
        paths = self.args.paths
        if not paths:
            paths = [self.repo]
        for path in paths:
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    if ".git" in root or "node_modules" in root or ".venv" in root or "venv" in root:
                        continue
                    for fn in files:
                        self._scan_file(os.path.join(root, fn))
            else:
                self._scan_file(path)

    def _scan_file(self, path: str) -> None:
        try:
            with open(path, "rb") as fh:
                data = fh.read(8192)
        except OSError:
            return
        ft = classify_path(data, path)
        if self.args.format and ft.fmt != self.args.format:
            return
        if self.args.category and ft.category != self.args.category:
            return
        print(f"{ft.fmt:>10} {ft.category:>8}  {path}")

    def cmd_notify(self) -> None:
        notifier = notifier_from_env()
        notifier.notify(
            Notification(
                "100Ways test",
                self.args.message,
                level=self.args.level,
                kind="sync",
            )
        )
        print("notification sent (best-effort)")

    def cmd_status(self) -> None:
        print("repo:      ", self.repo)
        print("branch:    ", _git_ok(self.repo, "branch", "--show-current").strip() or "(detached)")
        print("HEAD:      ", _git_ok(self.repo, "rev-parse", "HEAD").strip()[:12])
        report = verify_rebrand(
            self.repo, BIRTH_PARENT, BIRTH_COMMIT, self.rules, compute_deltas=self.args.deltas
        )
        print("rules validation:", report.summary())
        print("GATE:", "PASS" if not report.failed and report.pass_ratio >= 0.99 else "FAIL")
        if self.args.deltas:
            for f in report.failed:
                print(f"  {f.mapped_path}  +{f.added_lines}/-{f.deleted_lines}l  +{f.added_chars}/-{f.deleted_chars}c  {f.note}")

    def cmd_verify(self) -> None:
        """Gate: does our fork still hold parity with the birth commit + rules?"""
        report = verify_rebrand(self.repo, BIRTH_PARENT, BIRTH_COMMIT, self.rules)
        print(report.summary())
        for f in report.failed:
            print(f"  [FAIL] {f.mapped_path}: {f.note}")
        failed = bool(report.failed) or report.pass_ratio < 0.99
        sys.exit(1 if failed else 0)

    def cmd_ship(self) -> None:
        """Ship: verify parity, then commit any pending updates on the sync branch."""
        branch = ensure_branch(self.repo, self.args.branch)
        report = verify_rebrand(self.repo, BIRTH_PARENT, BIRTH_COMMIT, self.rules)
        print(f"parity check on {branch}:")
        print(report.summary())
        if report.failed or report.pass_ratio < 0.99:
            print("parity check FAILED - not shipping", file=sys.stderr)
            sys.exit(1)
        changed = _git_ok(self.repo, "status", "--porcelain").strip()
        if not changed:
            print("worktree clean - nothing to ship")
            return
        print("uncommitted changes on the sync branch:")
        print(changed)
        print(f"commit them manually, then push:\n  git -C {self.repo} push -u origin {branch}")

    def cmd_report(self) -> None:
        """Write a markdown gap report + state snapshot to config/reports."""
        report = analyze(self._upstream_head(), self._nastech_head(), self.repo, self.rules)
        state_dir = os.path.join(self.repo, "config", "reports")
        os.makedirs(state_dir, exist_ok=True)
        import time as _time

        out = os.path.join(state_dir, f"report-{_time.strftime('%Y%m%d-%H%M')}.md")
        lines = [
            "# 100Ways gap report",
            "",
            f"- upstream : `{report.upstream_commit[:12]}`",
            f"- nastech  : `{report.nastech_commit[:12]}`",
            f"- summary  : {report.summary}",
            "",
            "## Gaps",
            "",
        ]
        for e in report.violations() + report.upstream_only() + report.changed():
            code = code_name(e.code)
            lines.append(f"- `[{code}]` {e.path}" + (f" — {e.brand_violations}" if e.brand_violations else ""))
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"report written to {out}")

    def cmd_dashboard(self) -> None:
        """Serve the live web dashboard (Ctrl-C to stop)."""
        serve_dashboard(
            self.repo,
            host=self.args.host,
            port=self.args.port,
            home=self.args.state_dir,
            admin_token=self.args.admin_token,
            rules_override=os.path.join(self.args.state_dir, "config", "rules_override.json") if self.args.state_dir else None,
        )

    def cmd_pull(self) -> None:
        """Fetch upstream, run the gap report, and record a pull achievement."""
        self._upstream_head()
        self.cmd_report()
        if self.args.state_dir:
            ach = Achievements(self.args.state_dir)
            unlocked = ach.apply_event("pull")
            for name in unlocked:
                print(f"  achievement: {name}")

    def cmd_achievements(self) -> None:
        """List achievements and unlock state."""
        home = self.args.state_dir or os.path.join(os.path.dirname(os.path.abspath(self.repo)), "100ways-state")
        ach = Achievements(home)
        for name, meta, unlocked in ach.list_all():
            mark = "✅" if unlocked else "▢"
            print(f"{mark} {meta.emoji} {name} — {meta.description}")

    def cmd_codes(self) -> None:
        """Print the gap code legend."""
        print("code  name         meaning")
        print("----  -----------  -------")
        for code, meta in sorted(CODE_DETAILS.items()):
            print(f" {code:>3}  {meta.name:<11}  {meta.meaning}")

    def cmd_readme(self) -> None:
        """Regenerate README.md, or measure the copy that ships."""
        from .readme import ReadmeInputs, measure_coverage, render_readme, surface

        target = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "README.md"))
        items = surface()
        if self.args.check_coverage:
            bar = self.args.min
            # Measured against the shipped bytes: regenerating first would score the
            # copy the generator just wrote, which certifies nothing.
            try:
                with open(target, encoding="utf-8") as fh:
                    shipped = fh.read()
            except OSError as exc:
                print(f"cannot read {target}: {exc}", file=sys.stderr)
                sys.exit(2)
            cov = measure_coverage(shipped, items)
            for name in cov.missing:
                print(f"  [UNDOCUMENTED] {name}", file=sys.stderr)
            head = f"README coverage: {cov.documented}/{cov.total} of the command surface"
            print(f"{head} ({cov.ratio:.1%})")
            if cov.ratio < bar:
                print(f"COVERAGE: {cov.ratio:.1%} is under the {bar:.0%} bar", file=sys.stderr)
                sys.exit(1)
            print(f"COVERAGE: meets the {bar:.0%} bar; nothing was written")
            return
        state_dir = self.args.state_dir or os.path.join(
            os.path.dirname(os.path.abspath(self.repo)), "100ways-state"
        )
        owned = OwnedAssets(repo=self.repo)
        content = render_readme(ReadmeInputs(
            state_dir=state_dir,
            owned_count=owned.count,
            surface=items,
        ))
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content + "\n")
        print(f"README.md regenerated ({len(content)} bytes, {owned.count} owned-asset entries)")

    def cmd_release(self) -> None:
        """Verify the code table and (optionally) an incoming payload of codes."""
        errors = release_check_table()
        for err in errors:
            print(f"  [TABLE] {err}", file=sys.stderr)
        payload = None
        if self.args.payload:
            import json

            try:
                payload = json.loads(self.args.payload)
            except ValueError:
                print(f"payload is not valid JSON: {self.args.payload}", file=sys.stderr)
                sys.exit(2)
        if payload is not None:
            errors += release_verify_incoming(payload, where="incoming")
            for err in errors:
                if err.startswith("incoming"):
                    print(f"  [INCOMING] {err}", file=sys.stderr)
            print("summary:", release_summary(payload))
        if errors:
            print(f"\nRELEASE: {len(errors)} problem(s) found")
            sys.exit(1)
        print("RELEASE: code table verified, incoming codes clean")

    def cmd_admin(self) -> None:
        """Verify an operator-provided dashboard bearer token without echoing it."""
        configured = self.args.token or os.getenv("HUNDREDWAYS_ADMIN_TOKEN", "")
        if self.args.verify:
            ok = verify_token(self.args.verify, configured)
            print(f"verify: {'GRANTED' if ok else 'DENIED'}")
            sys.exit(0 if ok else 1)
        print("Admin authentication uses HUNDREDWAYS_ADMIN_TOKEN; no credential is generated or displayed.")

    def cmd_ways(self) -> None:
        registry = build_registry()
        action = self.args.way_action
        if action == "count":
            print(registry.count)
            return
        if action == "list":
            for category in registry.categories():
                print(f"[{category}]")
                for way in registry.by_category(category):
                    star = "*" if way.default else " "
                    print(f"  {star} {way.way_id:<22} {way.name}")
            print(f"\n{registry.count} ways across {len(registry.categories())} categories")
            return
        if action == "show":
            way = registry.get(self.args.way_id)
            if not way:
                print(f"no such way: {self.args.way_id}", file=sys.stderr)
                sys.exit(1)
            print(f"{way.way_id}  ({way.name})")
            print(f"  category: {way.category}   default: {'yes' if way.default else 'no'}")
            print(f"  uses:     {way.uses or '-'}")
            print(f"  {way.description}")
            return
        if action == "defaults":
            for category, way_id in registry.defaults().items():
                print(f"{category:<8} {way_id}")
            return
        if action == "coverage":
            self._print_coverage(registry)
            return

    def _print_coverage(self, registry) -> None:
        """Truth-table: which ways have a live engine consumer."""
        from .coverage import by_category, coverage, summary

        rows = coverage(registry)
        mark = {"built": "✓", "dangling": "✗", "catalog-only": "·"}
        for category, cat_rows in by_category(rows).items():
            built = sum(1 for r in cat_rows if r.status == "built")
            print(f"[{category}] {built}/{len(cat_rows)} built")
            for row in cat_rows:
                if row.status == "built":
                    print(
                        f"  {mark[row.status]} {row.way.way_id:<24} "
                        f"{row.way.name:<28} -> {row.way.uses} [{row.resolution.kind}]"
                    )
                elif row.status == "dangling":
                    print(
                        f"  {mark[row.status]} {row.way.way_id:<24} "
                        f"{row.way.name:<28} -> {row.way.uses} [DANGLING]"
                    )
                else:
                    print(f"  {mark[row.status]} {row.way.way_id:<24} {row.way.name:<28} (catalog-only)")
        counts = summary(rows)
        tail = f"{counts['built']} built, {counts['catalog-only']} catalog-only"
        if counts["dangling"]:
            tail += f", {counts['dangling']} DANGLING"
        print(f"\n{tail}  ({len(rows)} ways total)")

    def cmd_research(self) -> None:
        ideas = run_research(self.args.query, live=self.args.live)
        if not ideas:
            print("no ideas found")
            return
        for idea in ideas:
            print(f"[{idea.source}] {idea.title}")
            if idea.url:
                print(f"    {idea.url}")
            print(f"    {idea.summary}")

    def cmd_diff(self) -> None:
        """Two-phase diff: live upstream vs local Hermes, then branded vs Nastech."""
        hermes = self.args.hermes or DEFAULT_HERMES
        live_ref = self.args.live_ref or "origin/main"
        print(f"== phase 1: local Hermes ({hermes}) vs live upstream ({live_ref}) ==")
        _git(hermes, "fetch", "origin")
        live = _git_ok(hermes, "rev-parse", live_ref).strip()
        local = _git_ok(hermes, "rev-parse", "HEAD").strip()
        ahead = _git_ok(hermes, "rev-list", "--count", f"{local}..{live}").strip()
        behind = _git_ok(hermes, "rev-list", "--count", f"{live}..{local}").strip()
        print(f"  live upstream : {live[:12]}")
        print(f"  local hermes  : {local[:12]}")
        print(f"  local is {behind} commits behind live, {ahead} ahead")
        numstat = _git(hermes, "diff", "--numstat", live, local)
        total_add = sum(int(line.split("\t")[0]) for line in numstat.splitlines() if line and line.split("\t")[0].isdigit())
        total_del = sum(int(line.split("\t")[1]) for line in numstat.splitlines() if line and line.split("\t")[1].isdigit())
        print(f"  live has {total_add} added / {total_del} deleted lines vs local hermes")
        print(f"  ({len([l for l in numstat.splitlines() if l])} files touched)")

        print(f"\n== phase 2: branded live upstream vs Nastech ({self.repo}) ==")
        nastech_upstream = self._upstream_head()
        report = analyze(nastech_upstream, self._nastech_head(), self.repo, self.rules)
        print(f"  upstream {nastech_upstream[:12]} vs Nastech HEAD {self._nastech_head()[:12]}")
        print(f"  {report.summary}")
        for e in report.violations():
            print(f"  [VIOLATION] {e.path}: {e.brand_violations}")
        for e in report.upstream_only():
            print(f"  [upstream-only] {e.path}")

    def cmd_update(self) -> None:
        """Pull real Hermes, brand the whole tree, verify, save as Nastech-Update#N."""
        updates_dir = self.args.updates_dir or default_updates_dir(self.repo)
        owned = (
            OwnedAssets(root=self.args.owned_assets_root)
            if self.args.owned_assets_root
            else OwnedAssets(repo=self.repo)
        )
        if owned.count:
            print(f"owned-assets registry: {owned.count} target paths in {owned.root}")
        else:
            print("owned-assets registry: none found (expected at config/owned-assets/)")
        fork_root = self.args.fork_root or self.repo
        if fork_root and os.path.isdir(fork_root):
            print(f"fork-root: {fork_root} (fork-consistency check enabled)")
        else:
            print(f"fork-root: {fork_root or 'none'} — fork-consistency check will be skipped")
        mgr = UpdateManager(updates_dir, hermes_url=self.args.hermes_url,
                            rules=self.rules, owned=owned, ai=AIEngine(),
                            fork_root=fork_root,
                            source_provenance_url=self.args.source_provenance_url)
        zip_path = self.args.zip or ""
        if zip_path and zip_path.endswith(os.sep):
            zip_path = ""
        result = mgr.run(zip_path=zip_path, project_name=self.args.project_name,
                         profile_path=self.args.profile)
        if self.args.emit_outputs:
            with open(self.args.emit_outputs, "w") as fh:
                json.dump({
                    "update_number": result.number,
                    "upstream_sha": result.upstream_sha,
                    "gate": "PASS" if result.gate else "FAIL",
                }, fh)
        for stage in result.stages:
            mark = "ok" if stage.status == "ok" else ("SKIP" if stage.status == "skip" else "FAIL")
            print(f"  [{mark:>4}] {stage.name:<10} {stage.detail}")
        print(f"gate: {'PASS' if result.gate else 'FAIL'}  "
              f"{result.verify.passed}/{result.verify.total} files "
              f"({result.verify.pass_ratio * 100:.1f}%)")
        print(f"brand: {result.brand.total} files "
              f"({result.brand.renamed} renamed, {result.brand.rewritten} text-rewritten, "
              f"{result.brand.locked_copied} locked, {result.brand.binary_copied} binary, "
              f"{result.brand.owned} owned assets)")
        print(f"diff: {result.diff.summary()}")
        print(f"scan: {result.scan.summary()}")
        if result.zip_path:
            print(f"zip: {result.zip_path}")
        print(f"snapshot: {result.dir}")
        top = ", ".join(
            f"{item.name} {item.duration_ms / 1000:.1f}s" for item in ranked(result.stages)[:3]
        )
        print(f"stage time: {total_ms(result.stages) / 1000:.1f}s total, slowest: {top}")
        if self.args.state_dir:
            ach = Achievements(self.args.state_dir)
            unlocked = ach.apply_event("pull")
            unlocked += ach.apply_event("scan_1000") if result.scan.total >= 1000 else []
            for name in unlocked:
                print(f"  achievement: {name}")
        if not result.gate:
            print("GATE FAILED - snapshot kept for inspection", file=sys.stderr)
            sys.exit(1)

    def cmd_profile(self) -> None:
        """Rank one run's stages, or compare two runs and name what moved."""
        current = read_profile(self.args.current)
        if not self.args.against:
            measured = total_ms(current)
            print(f"{measured / 1000:.2f}s measured over {len(current)} stages")
            for item in ranked(current):
                share = (item.duration_ms / measured * 100.0) if measured else 0.0
                print(f"  {item.name:<16}{item.duration_ms / 1000:>9.2f}s{share:>8.1f}%  {item.status}")
            return
        baseline = read_profile(self.args.against)
        for line in format_comparison(baseline, current):
            print(line)
        slower = regressions(baseline, current)
        faster = improvements(baseline, current)
        if slower:
            print("regressed:")
            for delta in slower:
                print(f"  {delta.name}: {delta.before_ms / 1000:.2f}s -> "
                      f"{delta.after_ms / 1000:.2f}s ({delta.ratio:.1f}x)")
        else:
            print("no stage got materially slower")
        if faster:
            print("improved:")
            for delta in faster:
                print(f"  {delta.name}: {delta.before_ms / 1000:.2f}s -> "
                      f"{delta.after_ms / 1000:.2f}s")

    def cmd_weekly_full_sync(self) -> None:
        """Run or audit a complete weekly branded snapshot; never push or merge."""
        updates_dir = self.args.updates_dir or default_updates_dir(self.repo)
        branded_root = self.args.branded_root or self.repo
        upstream_repo = self.args.hermes_repo or hermes_path(updates_dir)

        if self.args.mode == "snapshot":
            owned = OwnedAssets(repo=self.repo)
            mgr = UpdateManager(
                updates_dir,
                hermes_url=self.args.hermes_url,
                rules=self.rules,
                owned=owned,
                ai=AIEngine(),
                fork_root=self.args.fork_root or self.repo,
            )
            snapshot = mgr.run(project_name=self.args.project_name)
            if not snapshot.gate:
                print("weekly snapshot failed the 100Ways file-parity gate", file=sys.stderr)
                sys.exit(1)
            branded_root = snapshot.dir
            upstream_repo = hermes_path(updates_dir)

        if not os.path.isdir(upstream_repo):
            print(f"Hermes checkout not found: {upstream_repo}", file=sys.stderr)
            sys.exit(2)
        if not os.path.isdir(branded_root):
            print(f"Branded tree not found: {branded_root}", file=sys.stderr)
            sys.exit(2)

        report = build_weekly_report(
            upstream_repo,
            branded_root,
            self.args.state_dir,
            mode=self.args.mode,
            ref=self.args.upstream_ref,
        )
        report_path = self.args.report or os.path.join(self.args.state_dir, "weekly-full-sync-report.md")
        write_weekly_report(report_path, report)
        print(json.dumps(report.to_dict(), indent=2))
        print(f"report: {report_path}")

        if report.gate_passes and self.args.record:
            path = save_ledger(self.args.state_dir, report)
            print(f"ledger: {path}")
        elif not report.gate_passes:
            print("weekly full-sync gate failed; no ledger update or publication", file=sys.stderr)
            if self.args.require_pass:
                sys.exit(1)

    def cmd_forkcheck(self) -> None:
        """Diff the branded snapshot against the nastech-agent fork checkout."""
        from .forkcheck import fork_consistency
        from .updates import _complete_update_dirs, hermes_path, update_path

        updates_dir = self.args.updates_dir or default_updates_dir(self.repo)
        branded = self.args.branded
        if not branded:
            complete = _complete_update_dirs(updates_dir)
            if not complete:
                print("no completed update snapshot found — pass --branded", file=sys.stderr)
                sys.exit(1)
            branded = update_path(updates_dir, max(int(c[len("Nastech-Update#"):]) for c in complete))
            if not os.path.isdir(branded):
                print(f"no branded snapshot at {branded} — pass --branded", file=sys.stderr)
                sys.exit(1)
        if not os.path.isdir(self.repo):
            print(f"fork checkout not found: {self.repo} (pass --repo)", file=sys.stderr)
            sys.exit(1)
        src = hermes_path(updates_dir)
        print(f"fork:     {self.repo}")
        print(f"branded:  {branded}")
        print(f"upstream: {src}")
        report = fork_consistency(self.repo, branded, src, self.rules)
        print(report.summary())
        if report.features_fork:
            print(f"features: fork {report.features_fork} -> branded {report.features_branded}")
        for e in report.entries:
            for v in e.violations:
                print(f"  [VIOLATION] {v.path}:{v.line}  {v.snippet}")
            if e.status in ("missing", "local_only"):
                print(f"  [{e.status.upper()}] {e.path}")
        if not report.gate_passes():
            print("FORKCHECK FAILED — snapshot diverges from the fork", file=sys.stderr)
            sys.exit(1)
        print("forkcheck: PASS")

    # -- verbs for capabilities that shipped without one ---------------------

    def cmd_narrative(self) -> None:
        """Build the title and body a reviewer reads, from a manifest alone."""
        manifest = _read_json(self.args.manifest)
        summary = _read_text(self.args.summary)
        paragraph = garnish(manifest, engine=AIEngine()) if self.args.ai else ""
        if self.args.ai and not paragraph:
            print(
                "narrative: the model offered no paragraph, so the text stays the engine's own",
                file=sys.stderr,
            )
        title = title_from_manifest(manifest)
        body = body_from_manifest(manifest, summary, paragraph=paragraph)
        if self.args.title_out:
            with open(self.args.title_out, "w", encoding="utf-8") as fh:
                fh.write(title + "\n")
        if self.args.body_out:
            with open(self.args.body_out, "w", encoding="utf-8") as fh:
                fh.write(body + "\n")
        print(title)
        print()
        print(body)

    def cmd_pubconfig(self) -> None:
        """Say which credential names a future run would read as an empty string."""
        tree = self.args.tree or self.repo
        if not os.path.isdir(tree):
            print(f"no fork tree to read workflows from: {tree} (pass --tree)", file=sys.stderr)
            sys.exit(2)
        try:
            if self.args.fetch:
                if not self.args.project:
                    print("--fetch needs --project org/name", file=sys.stderr)
                    sys.exit(2)
                inventory = fetch_inventory(self.args.project)
            elif self.args.inventory:
                inventory = _inventory_from_json(self.args.inventory)
            else:
                print(
                    "say where the provided names are: "
                    "--fetch --project org/name, or --inventory FILE",
                    file=sys.stderr,
                )
                sys.exit(2)
        except (PublisherConfigurationError, OSError, ValueError) as exc:
            print(f"cannot read the repository's Actions settings: {exc}", file=sys.stderr)
            sys.exit(1)
        findings = analyze_root(tree, inventory)
        print(render_pubconfig(findings, repo=self.args.project, inventory=inventory))
        if self.args.json_out:
            with open(self.args.json_out, "w", encoding="utf-8") as fh:
                fh.write(pubconfig_json(findings, repo=self.args.project))
        urgent = [finding for finding in findings if finding.actionable]
        if urgent:
            print(
                f"pubconfig: {len(urgent)} name(s) a run can still consume; a lane will go red "
                "on hardware that is configured at all",
                file=sys.stderr,
            )
            sys.exit(1)

    def cmd_why_red(self) -> None:
        """Sort the reds in a check-runs payload into the class that owns the fix."""
        verdicts = classify_runs(
            _read_json(self.args.runs),
            upstream_runs=_read_json(self.args.upstream_runs) if self.args.upstream_runs else None,
            upstream_changed_files=(
                _read_lines(self.args.changed_files) if self.args.changed_files else None
            ),
            baseline_sha=self.args.baseline,
            current_sha=self.args.current,
            checkout_path=self.args.checkout,
        )
        if not verdicts:
            print("why-red: no red check in that payload")
            return
        print(render_verdicts(verdicts))

    def cmd_telegram(self) -> None:
        """Inspect, or prove, the commanded surface — without a chat session."""
        action = self.args.telegram_action
        if action == "menu":
            print(menu())
            return
        if action == "check":
            request = parse(self.args.message)
            command = lookup(request)
            if command is None:
                print(
                    f"nothing in {self.args.message!r} names a command; "
                    "it is answered by the model or by the help line"
                )
                return
            print(f"{command.name} — {command.summary}")
            print(f"  needs: {command.level}")
            print(f"  arguments: {' '.join(request.words) or '(none)'}")
            print(f"  usage: {usage_for(command)}")
            print(
                "  who may run it: "
                + ", ".join(
                    f"{level} {'yes' if grants(level, command.level) else 'no'}" for level in LEVELS
                )
            )
            return
        token = self.args.token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat = self.args.chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        if not (token and chat):
            print("telegram test needs a bot token and a chat id (--token/--chat-id or the env)",
                  file=sys.stderr)
            sys.exit(1)
        try:
            send_telegram(token, chat, self.args.message)
        except TelegramDeliveryError as exc:
            print(f"not delivered: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"delivered to chat {chat}")

    def cmd_lintsim(self) -> None:
        """Run the fork's own lint contract over a candidate tree, before publishing it."""
        issues = run_fork_lint(self.args.fork or self.repo, self.args.candidate, self.args.upstream)
        for line in report_lines(issues):
            print(line)
        summary = summarize(issues)
        print(summary.line())
        if not summary.passed:
            sys.exit(1)

    def cmd_urls(self) -> None:
        """Judge every link in a tree by syntax and policy, and live only if asked."""
        tree = self.args.tree or self.repo
        if not os.path.isdir(tree):
            print(f"no tree to read links from: {tree} (pass --tree)", file=sys.stderr)
            sys.exit(2)
        policy = UrlPolicy.load(self.args.policy or URL_POLICY_PATH)
        findings = scan_url_tree(tree, policy, probe=self.args.probe, rules=self.rules)
        print(render_urls(findings, *probe_tally(findings)))
        if self.args.json_out:
            with open(self.args.json_out, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(findings_to_json(findings), indent=2, sort_keys=True) + "\n")
        decided = [item for item in findings if item.status in DECIDED]
        if decided:
            print(
                f"urls: {len(decided)} link(s) judged unfit to ship (see the statuses above)",
                file=sys.stderr,
            )
            sys.exit(1)

    def cmd_surface(self) -> None:
        """Measure the surface upstream actually exposes, and what moved since a baseline."""
        tree = self.args.tree or self.repo
        if not os.path.isdir(tree):
            print(f"no tree to read surface from: {tree} (pass --tree)", file=sys.stderr)
            sys.exit(2)
        ignore = [token.strip() for token in self.args.ignore.split(",") if token.strip()]
        surface = scan_surface(tree, ignore)
        print(
            "surface: "
            + ", ".join(f"{name}={count}" for name, count in sorted(surface.counts.items()))
        )
        for unparsed in surface.unparsed:
            print(f"  unparsed {unparsed}")
        if self.args.save:
            save_baseline(surface, self.args.save)
            print(f"baseline written: {self.args.save}")
        if not self.args.baseline:
            return
        try:
            before = load_baseline(self.args.baseline)
        except BaselineError as exc:
            print(f"cannot read the surface baseline: {exc}", file=sys.stderr)
            sys.exit(2)
        deltas = diff_surface(before, surface)
        if not deltas:
            print("surface unchanged since the baseline")
            return
        decisions = 0
        for item in sorted(
            deltas, key=lambda delta: (delta.field, delta.kind, delta.before, delta.after)
        ):
            moved = (
                f"{item.before} -> {item.after}"
                if item.kind == "changed"
                else (item.after or item.before)
            )
            print(f"  {item.field} {item.kind}: {moved} [{item.classification}]")
            if item.classification == RULE:
                decisions += 1
        print(f"{len(deltas)} movement(s), {decisions} needing an engine rule")
        if decisions:
            sys.exit(1)

    def cmd_verify_report(self) -> None:
        """Write the one document that names what was measured and what was not.

        Cheap local measurements run by default; each expensive one is opt-in, and an
        input that was not asked for leaves its section saying ``not verified`` rather
        than being guessed at.
        """
        from .forkcheck import walk_files

        tree = self.args.tree or self.repo
        threshold = self.args.threshold
        inputs: dict[str, object] = {"ways": ways_coverage(), "threshold": threshold}
        if os.path.isdir(tree):
            inputs["urls"] = scan_url_tree(
                tree, UrlPolicy.load(self.args.policy or URL_POLICY_PATH), rules=self.rules
            )
        if self.args.parity:
            inputs["parity"] = verify_rebrand(tree, BIRTH_PARENT, BIRTH_COMMIT, self.rules)
        if self.args.gaps:
            _git(tree, "fetch", "upstream")
            inputs["gaps"] = analyze(
                _git_ok(tree, "rev-parse", DEFAULT_UPSTREAM).strip(),
                _git_ok(tree, "rev-parse", "HEAD").strip(),
                tree,
                self.rules,
            )
        if self.args.baseline:
            inputs["surface"] = diff_surface(load_baseline(self.args.baseline), scan_surface(tree))
        if self.args.inventory:
            inputs["credentials"] = analyze_root(tree, _inventory_from_json(self.args.inventory))
        if self.args.lint:
            inputs["lint"] = summarize(run_fork_lint(tree, self.args.lint, self.args.upstream))
        if self.args.upstream:
            inputs["orphans"] = find_orphans(
                walk_files(tree), walk_files(self.args.upstream), self.rules
            )
        if self.args.receipts:
            inputs["receipts"] = sorted(
                str(path) for path in Path(self.args.receipts).glob("*.json")
            )
        report = build_verified_report(**inputs)
        if self.args.out:
            parent = os.path.dirname(os.path.abspath(self.args.out))
            os.makedirs(parent, exist_ok=True)
            with open(self.args.out, "w", encoding="utf-8") as fh:
                fh.write(report.text)
            print(f"verified report written: {self.args.out}")
        else:
            print(report.text, end="")
        print(f"measured: {', '.join(report.measured)}")
        if report.missing:
            print(f"not verified: {', '.join(report.missing)}")
        blockers: list[str] = []
        parity = inputs.get("parity")
        if parity is not None and not gate_passes(parity, threshold):
            blockers.append("parity gate")
        lint = inputs.get("lint")
        if lint is not None and not lint.passed:
            blockers.append("fork-lint")
        credentials = inputs.get("credentials") or []
        if any(getattr(item, "actionable", False) for item in credentials):
            blockers.append("credential names")
        urls = inputs.get("urls") or []
        if any(item.status in DECIDED for item in urls):
            blockers.append("links")
        orphans = inputs.get("orphans") or ()
        if any(item.kind == "deleted-twin" for item in orphans):
            blockers.append("deleted-twin orphans")
        if blockers:
            print(f"verify-report: red on {', '.join(blockers)}", file=sys.stderr)
            sys.exit(1)

    # -- delivery ------------------------------------------------------------

    def _run_facts(self) -> dict:
        """The classifier's upstream-side inputs, read exactly the way ``why-red`` reads them."""
        facts: dict = {}
        if self.args.upstream_runs:
            facts["upstream_runs"] = _read_json(self.args.upstream_runs)
        if self.args.changed_files:
            facts["upstream_changed_files"] = _read_lines(self.args.changed_files)
        if self.args.baseline_sha:
            facts["baseline_sha"] = self.args.baseline_sha
        if self.args.current:
            facts["current_sha"] = self.args.current
        if self.args.checkout:
            facts["checkout_path"] = self.args.checkout
        return facts

    def _plan_inputs(self) -> dict:
        """Every family the delivery planner reads, assembled from the flags actually given.

        A family whose input was not passed stays ``None``, so the plan reports it as not
        measured.  An empty list is only produced by a measurement that ran and found
        nothing -- which is the difference a commander has to be able to see.
        """
        from .forkcheck import walk_files

        tree = self.args.tree or self.repo
        inputs: dict = {}
        if self.args.runs:
            inputs["verdicts"] = classify_runs(_read_json(self.args.runs), **self._run_facts())
        if self.args.tree:
            # Opt-in like every other family here: the fork checkout holds hundreds of
            # thousands of files, so a delivery verb that scanned it by default would be
            # a minute late and would report links as measured when nobody asked.
            inputs["urls"] = scan_url_tree(
                tree, UrlPolicy.load(self.args.policy or URL_POLICY_PATH), rules=self.rules
            )
        if self.args.surface_baseline:
            inputs["deltas"] = diff_surface(
                load_baseline(self.args.surface_baseline),
                scan_surface(tree, [
                    token.strip()
                    for token in (self.args.ignore or "").split(",")
                    if token.strip()
                ]),
            )
        if self.args.inventory:
            inputs["credentials"] = analyze_root(tree, _inventory_from_json(self.args.inventory))
        if self.args.upstream:
            inputs["orphans"] = find_orphans(
                walk_files(tree), walk_files(self.args.upstream), self.rules
            )
        if self.args.state:
            inputs["feed"] = observe_errors(
                _read_json(self.args.runs),
                previous=load_state(self.args.state) if os.path.isfile(self.args.state) else None,
                **self._run_facts(),
            )
        return inputs

    def _plan_inputs_guarded(self) -> dict:
        """The measurements the given flags ask for, with an unreadable input as exit 2."""
        try:
            return self._plan_inputs()
        except (OSError, ValueError, BaselineError, ErrorFeedError) as exc:
            print(f"nothing can be planned: {exc}", file=sys.stderr)
            sys.exit(2)

    def _delivery_plan(self):
        return plan_delivery(**self._plan_inputs_guarded())

    def cmd_errors(self) -> None:
        """Report what changed between check-runs payloads -- including what cannot be proved."""
        if not self.args.runs:
            print("errors needs --runs: the check-runs payload of this cycle", file=sys.stderr)
            sys.exit(2)
        try:
            feed = observe_errors(
                _read_json(self.args.runs),
                previous=self._previous_state(),
                **self._run_facts(),
            )
        except (OSError, ValueError, ErrorFeedError) as exc:
            print(f"the feed cannot be read: {exc}", file=sys.stderr)
            sys.exit(2)
        print(render_feed(feed, limit=self.args.limit))
        if self.args.json_out:
            with open(self.args.json_out, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(to_json_feed(feed), indent=2, sort_keys=True) + "\n")
        if self.args.record:
            if not self.args.state:
                print("--record needs --state: there is no path to record the cycle at",
                      file=sys.stderr)
                sys.exit(2)
            save_state(feed, self.args.state)
            print(f"feed state recorded: {self.args.state}")

    def _previous_state(self) -> dict | None:
        """The last cycle's reds, or ``None`` when no state was recorded -- not an empty dict."""
        if not self.args.state:
            return None
        if not os.path.isfile(self.args.state):
            return None
        return load_state(self.args.state)

    def cmd_issues(self) -> None:
        """Draft the issues a measured finding earns, and open them only when told to."""
        plan = self._delivery_plan()
        if self.args.dedupe:
            project = self.args.project
            if not project:
                print("--dedupe needs --project owner/name", file=sys.stderr)
                sys.exit(2)
            plan, dropped = deduplicate(plan, open_issue_keys(project))
            if dropped:
                print(f"already asked for: {', '.join(dropped)}")
        for draft in plan.issues:
            print(f"[{draft.severity}] {draft.key}: {draft.title}")
        print(
            f"planned: {len(plan.issues)} issue(s), {len(plan.suggestions)} suggestion(s); "
            f"measured {', '.join(plan.measured) or 'nothing'}"
        )
        if plan.skipped:
            print(f"not measured: {', '.join(plan.skipped)}")
        if self.args.out:
            with open(self.args.out, "w", encoding="utf-8") as fh:
                fh.write(render_plan(plan))
        if self.args.json_out:
            with open(self.args.json_out, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(delivery_json(plan), indent=2, sort_keys=True) + "\n")
        if not self.args.apply:
            return
        if not self.args.project:
            print("--apply needs --project owner/name", file=sys.stderr)
            sys.exit(2)
        result = deliver(self.args.project, plan, apply=True, kinds=("issues",))
        for item in result["issues"]:
            print(f"  {item}")
        if result["errors"]:
            print(f"issues: {len(result['errors'])} write(s) failed", file=sys.stderr)
            sys.exit(1)

    def cmd_discuss(self) -> None:
        """Draft the questions the engine is not allowed to answer for a human."""
        plan = self._delivery_plan()
        if self.args.category:
            plan = replace(plan, discussions=tuple(
                replace(draft, category=self.args.category) for draft in plan.discussions
            ))
        if self.args.dedupe and self.args.project:
            plan, dropped = deduplicate(plan, open_discussion_keys(self.args.project))
            if dropped:
                print(f"already asked: {', '.join(dropped)}")
        if not plan.discussions:
            print(
                "no discussion to open: the families measured raised no question "
                f"({', '.join(plan.measured) or 'nothing'} measured, "
                f"{', '.join(plan.skipped) or 'nothing'} skipped)"
            )
        for draft in plan.discussions:
            print(f"[{draft.key}] {draft.title}")
        if self.args.out:
            with open(self.args.out, "w", encoding="utf-8") as fh:
                fh.write(render_plan(plan))
        if not self.args.apply:
            return
        if not self.args.project:
            print("--apply needs --project owner/name", file=sys.stderr)
            sys.exit(2)
        result = deliver(self.args.project, plan, apply=True, kinds=("discussions",))
        for item in result["discussions"]:
            print(f"  {item}")
        if result["errors"]:
            print(f"discussions: {len(result['errors'])} write(s) failed", file=sys.stderr)
            sys.exit(1)

    def cmd_suggest(self) -> None:
        """The next actions, in the order the engine measured them -- never a guess."""
        plan = self._delivery_plan()
        if self.args.table:
            pages = answer(
                build(
                    "100WAYS / NEXT ACTIONS",
                    ("kind", "action", "owner"),
                    [(tip.kind, tip.text, tip.owner) for tip in plan.suggestions],
                    notes=(f"{len(plan.suggestions)} action(s)",),
                ),
                style=BLOCK if self.args.style == BLOCK else GRID,
                max_pages=self.args.pages or MAX_PAGES,
            )
            for page in pages:
                print(page)
            return
        print(render_suggestions(plan, limit=self.args.limit))

    def cmd_table(self) -> None:
        """One measured family as a table that fits the messages it will be sent in."""
        inputs = self._plan_inputs_guarded()
        try:
            title, columns, rows = _table_source(
                self.args.source, inputs, plan_delivery(**inputs)
            )
        except TableError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(2)
        pages = answer(
            build(title, columns, rows),
            style=BLOCK if self.args.style == BLOCK else GRID,
            max_pages=self.args.pages or MAX_PAGES,
        )
        for page in pages:
            print(page)


_TABLE_SOURCES = {
    "ci": ("verdicts", "--runs"),
    "urls": ("urls", "--tree"),
    "surface": ("deltas", "--surface-baseline"),
    "credentials": ("credentials", "--inventory"),
    "orphans": ("orphans", "--upstream"),
    "feed": ("feed", "--state"),
    "suggestions": (None, None),
}


def _table_source(
    name: str, inputs: dict, plan
) -> tuple[str, tuple[str, ...], list[tuple[str, ...]]]:
    """One measured family, as a title and a rectangle of its own numbers.

    A family that was not measured is an error rather than an empty table: a table of
    zero rows printed under a heading reads as "checked and clean".
    """
    field, flag = _TABLE_SOURCES[name]
    if field is not None and field not in inputs:
        raise TableError(f"source {name!r} needs {flag}: nothing was measured for it")
    if name == "ci":
        rows = [
            (v.check or "(unnamed run)", v.kind.value, v.owner, v.action)
            for v in inputs["verdicts"]
        ]
        return "100WAYS / CI REDS", ("lane", "class", "owner", "next"), rows
    if name == "urls":
        rows = [
            (item.link.path, str(item.link.line), item.status, item.link.url)
            for item in inputs["urls"]
        ]
        return "100WAYS / LINKS", ("path", "line", "status", "url"), rows
    if name == "surface":
        rows = [
            (item.field, item.kind, item.value, item.classification)
            for item in inputs["deltas"]
        ]
        return "100WAYS / SURFACE MOVES", ("field", "kind", "name", "class"), rows
    if name == "credentials":
        rows = [
            (
                item.name,
                item.verdict,
                ", ".join(item.scopes) or "-",
                "yes" if item.actionable else "no",
            )
            for item in inputs["credentials"]
        ]
        return "100WAYS / CREDENTIAL NAMES", ("name", "verdict", "scope", "actionable"), rows
    if name == "orphans":
        rows = [(item.path, item.kind, item.evidence) for item in inputs["orphans"]]
        return "100WAYS / ORPHANS", ("path", "kind", "evidence"), rows
    if name == "feed":
        rows = [(item.lane, item.kind, item.cls or "-", item.stamp or "not in payload")
                for item in inputs["feed"].events]
        return "100WAYS / ERROR FEED", ("lane", "event", "class", "at"), rows
    rows = [(tip.kind, tip.text, tip.owner) for tip in plan.suggestions]
    return "100WAYS / NEXT ACTIONS", ("kind", "action", "owner"), rows


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]


def _inventory_from_json(path: str) -> ActionsInventory:
    """Rebuild a provided-name inventory from a snapshot file.

    The keys are the dataclass's own field names, so one job that holds an Actions-settings
    token can hand the same answer to a job that does not.  Names only, never values.
    """
    payload = _read_json(path)
    environments: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for name, scopes in (payload.get("environments") or {}).items():
        environments[str(name)] = (
            frozenset(scopes.get("secrets") or ()),
            frozenset(scopes.get("vars") or ()),
        )
    return ActionsInventory(
        repo_secrets=frozenset(payload.get("repo_secrets") or ()),
        repo_vars=frozenset(payload.get("repo_vars") or ()),
        environments=environments,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="100ways", description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO, help="path to the nastech-agent checkout")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="one-shot watch cycle")
    p.set_defaults(func="cmd_check")

    p = sub.add_parser("watch", help="live watch loop")
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--max-cycles", type=int, default=None)
    p.set_defaults(func="cmd_watch")

    p = sub.add_parser("plan", help="list unported upstream commits")
    p.set_defaults(func="cmd_plan")

    p = sub.add_parser("port", help="port upstream commits (dry-run unless --apply)")
    p.add_argument("--branch", default="sync-upstream")
    p.add_argument("--threshold", type=float, default=0.99)
    p.add_argument("--apply", action="store_true", help="actually commit ports")
    p.set_defaults(func="cmd_port")

    p = sub.add_parser("analyze", help="file-to-file gap analysis")
    p.add_argument("--ai", action="store_true", help="include LLM review")
    p.set_defaults(func="cmd_analyze")

    p = sub.add_parser(
        "audit",
        help="brand-coverage audit: discover future brand spellings, residue, and secrets",
    )
    p.add_argument("--source", default="", help="upstream tree to discover future brand spellings")
    p.add_argument("--candidate", default="", help="branded tree to check for residue + secrets")
    p.add_argument("--max-samples", type=int, default=8, help="sample paths kept per new spelling")
    p.add_argument(
        "--assets-root",
        default="",
        help="owned-assets registry dir (default: <audited>/config/owned-assets)",
    )
    p.add_argument("--json", default="", help="write the audit result to this JSON path")
    p.add_argument("--issues", default="", help="write GitHub-issue drafts to this markdown path")
    p.add_argument(
        "--apply", action="store_true", help="open the drafts as issues (default: dry run)"
    )
    p.add_argument("--require-clean", action="store_true", help="exit 1 when any finding remains")
    p.set_defaults(func="cmd_audit")

    p = sub.add_parser("ways", help="browse the ways registry")
    p.add_argument("way_action", nargs="?", default="list", choices=["list", "show", "count", "defaults", "coverage"])
    p.add_argument("way_id", nargs="?", default="", help="way id for 'show', e.g. brand.token-regex")
    p.set_defaults(func="cmd_ways")

    p = sub.add_parser("research", help="search open source for ideas to steal")
    p.add_argument("query", help="topic to research, e.g. 'fork sync'")
    p.add_argument("--no-live", action="store_false", dest="live", help="offline catalog only")
    p.set_defaults(func="cmd_research")

    p = sub.add_parser("diff", help="two-phase diff: live upstream vs local Hermes, then branded vs Nastech")
    p.add_argument("--hermes", default=DEFAULT_HERMES, help="path to the local hermes checkout")
    p.add_argument("--live-ref", default="origin/main", help="live upstream ref in the hermes repo")
    p.set_defaults(func="cmd_diff")

    p = sub.add_parser("scan", help="scan file formats")
    p.add_argument("paths", nargs="*", help="files or dirs; default: repo")
    p.add_argument("--format", default="", help="only show this format, e.g. png")
    p.add_argument("--category", default="", help="only show this category, e.g. image")
    p.set_defaults(func="cmd_scan")

    p = sub.add_parser("notify", help="send a test notification")
    p.add_argument("--message", default="100ways alive", help="message body")
    p.add_argument("--level", default="info", choices=["info", "warn", "error"])
    p.set_defaults(func="cmd_notify")

    p = sub.add_parser("status", help="repo + rules state")
    p.add_argument("--deltas", action="store_true", help="detail per-failed-file line/char deltas")
    p.set_defaults(func="cmd_status")

    p = sub.add_parser("verify", help="parity gate vs the birth commit (exit 1 on fail)")
    p.set_defaults(func="cmd_verify")

    p = sub.add_parser("ship", help="verify parity then prepare the sync branch for shipping")
    p.add_argument("--branch", default="sync-upstream")
    p.set_defaults(func="cmd_ship")

    p = sub.add_parser("report", help="write a markdown gap report to config/reports")
    p.set_defaults(func="cmd_report")

    p = sub.add_parser("dashboard", help="serve the live web dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8333)
    p.add_argument("--state-dir", default="", help="state dir (default: repo-sibling 100ways-state)")
    p.add_argument("--admin-token", default=os.environ.get("HUNDREDWAYS_ADMIN_TOKEN", ""), help="token for rule editing (default: env)")
    p.set_defaults(func="cmd_dashboard")

    p = sub.add_parser("pull", help="fetch upstream + write report + record achievement")
    p.add_argument("--state-dir", default="", help="state dir (default: repo-sibling 100ways-state)")
    p.set_defaults(func="cmd_pull")

    p = sub.add_parser("achievements", help="list achievements and unlock state")
    p.add_argument("--state-dir", default="", help="state dir (default: repo-sibling 100ways-state)")
    p.set_defaults(func="cmd_achievements")

    p = sub.add_parser("codes", help="print the gap code legend")
    p.set_defaults(func="cmd_codes")

    p = sub.add_parser("readme", help="regenerate README.md from the registries")
    p.add_argument("--state-dir", default="", help="state dir (default: repo-sibling 100ways-state)")
    p.add_argument(
        "--check-coverage",
        action="store_true",
        help="score the shipped README against the live command surface; writes nothing",
    )
    p.add_argument(
        "--min",
        type=float,
        default=0.7,
        metavar="RATIO",
        help="coverage bar for --check-coverage (default: 0.7)",
    )
    p.set_defaults(func="cmd_readme")

    p = sub.add_parser("release", help="verify the code table and incoming codes")
    p.add_argument("--payload", default="", help="JSON with a 'codes'/'entries' list to verify")
    p.set_defaults(func="cmd_release")

    p = sub.add_parser("admin", help="compile/verify the admin password into its system token")
    p.add_argument("--password", default="", help="deprecated; ignored (use HUNDREDWAYS_ADMIN_TOKEN)")
    p.add_argument("--token", default="", help="stored compiled token to verify against")
    p.add_argument("--verify", default="", help="a candidate to check for access")
    p.set_defaults(func="cmd_admin")

    p = sub.add_parser("update", help="pull real Hermes -> brand whole tree -> verify -> save Nastech-Update#N")
    p.add_argument("--updates-dir", default="", help="Updates-Commits dir (default: sibling of the repo)")
    p.add_argument("--hermes-url", default=DEFAULT_HERMES_URL, help="Hermes remote or local path")
    p.add_argument("--source-provenance-url", default="", help="approved HTTPS source URL recorded in the candidate manifest")
    p.add_argument("--zip", default="", help="also build the release zip at this path (project folder + 2 md reports)")
    p.add_argument("--project-name", default="nastech-agent", help="name of the project folder inside the zip")
    p.add_argument("--state-dir", default="", help="state dir (default: repo-sibling 100ways-state)")
    p.add_argument("--fork-root", default="",
                   help="nastech-agent fork checkout to diff against (default: --repo); "
                        "enables the preserve + forkcheck stages")
    p.add_argument("--owned-assets-root", default="",
                    help="explicit config/owned-assets directory managed by 100Ways; "
                         "overrides the registry located in --repo")
    p.add_argument("--emit-outputs", default="",
                   help="write JSON {update_number, upstream_sha, gate} to this file "
                        "(CI emits these into $GITHUB_OUTPUT)")
    p.add_argument("--profile", default="",
                   help="write stage timing JSON here (a directory becomes <dir>/stage-profile.json); "
                        "feed it back to `profile --against` to catch a regression")
    p.set_defaults(func="cmd_update")

    p = sub.add_parser("profile", help="rank a run's stage costs, or compare two runs")
    p.add_argument("current", help="stage-profile.json from the run to inspect")
    p.add_argument("--against", default="",
                   help="an earlier stage-profile.json to compare against")
    p.set_defaults(func="cmd_profile")

    p = sub.add_parser("weekly-full-sync", help="weekly complete rebrand audit or snapshot; never pushes or merges")
    p.add_argument("--mode", choices=["report", "snapshot"], default="report",
                   help="report audits an existing branded tree; snapshot runs the full 100Ways update first")
    p.add_argument("--updates-dir", default="", help="Updates-Commits dir used for snapshot mode")
    p.add_argument("--hermes-url", default=DEFAULT_HERMES_URL, help="Hermes remote or local path for snapshot mode")
    p.add_argument("--hermes-repo", default="", help="existing Hermes checkout for report mode")
    p.add_argument("--upstream-ref", default="origin/main", help="upstream ref to fetch and verify")
    p.add_argument("--branded-root", default="", help="existing branded tree for report mode; default: --repo")
    p.add_argument("--fork-root", default="", help="NasTech fork root preserved during snapshot mode")
    p.add_argument("--project-name", default="nastech-agent", help="snapshot project name")
    p.add_argument("--state-dir", default="", help="state directory for the upstream ledger and reports")
    p.add_argument("--report", default="", help="output markdown report path")
    p.add_argument("--record", action="store_true", help="record a passing report in the upstream ledger")
    p.add_argument("--require-pass", action="store_true", help="exit nonzero when any weekly gate fails")
    p.set_defaults(func="cmd_weekly_full_sync")

    p = sub.add_parser("forkcheck", help="diff the branded snapshot against the nastech-agent fork")
    p.add_argument("--branded", default="", help="branded snapshot dir (default: latest Nastech-Update#N)")
    p.add_argument("--updates-dir", default="", help="Updates-Commits dir (default: sibling of the repo)")
    p.add_argument("--hermes-url", default=DEFAULT_HERMES_URL, help="Hermes remote or local path")
    p.set_defaults(func="cmd_forkcheck")

    p = sub.add_parser("narrative", help="build the PR title + body from a sync manifest")
    p.add_argument("--manifest", required=True, help="the update manifest JSON the pipeline writes")
    p.add_argument("--summary", required=True, help="markdown summary file to sit under the title")
    p.add_argument("--title-out", default="", help="write the title here (CI emits it into the PR)")
    p.add_argument("--body-out", default="", help="write the body here")
    p.add_argument("--ai", action="store_true", help="add the model's one paragraph when reachable")
    p.set_defaults(func="cmd_narrative")

    p = sub.add_parser(
        "pubconfig",
        help="predict which credential names a future run would read as an empty string",
    )
    p.add_argument(
        "--tree", default="", help="fork checkout holding .github/workflows (default: --repo)"
    )
    p.add_argument(
        "--project", default="", help="org/repo the settings belong to, for the report header"
    )
    p.add_argument(
        "--fetch", action="store_true", help="read live settings through gh (needs a token)"
    )
    p.add_argument(
        "--inventory",
        default="",
        help="snapshot JSON of provided names, from a job that already fetched them",
    )
    p.add_argument("--json-out", default="", help="write the machine-readable verdicts here")
    p.set_defaults(func="cmd_pubconfig")

    p = sub.add_parser("why-red", help="sort the reds in a check-runs payload by who owns the fix")
    p.add_argument("--runs", required=True, help="fork commits/{sha}/check-runs JSON")
    p.add_argument(
        "--upstream-runs", default="", help="the same payload for upstream's matching run"
    )
    p.add_argument(
        "--changed-files",
        default="",
        help="upstream file delta since --baseline, one path per line",
    )
    p.add_argument("--baseline", default="", help="upstream commit the candidate synced from")
    p.add_argument("--current", default="", help="candidate head sha")
    p.add_argument("--checkout", default="", help="local checkout to measure sizes against")
    p.set_defaults(func="cmd_why_red")

    p = sub.add_parser("telegram", help="the commanded surface: menu, resolve a line, send a probe")
    p.add_argument(
        "telegram_action",
        choices=["menu", "check", "test"],
        help="menu prints what a commander may say; check resolves one line offline; test sends",
    )
    p.add_argument("--message", default="100ways CLI probe", help="text for check / test")
    p.add_argument("--token", default="", help="bot token (default: TELEGRAM_BOT_TOKEN)")
    p.add_argument("--chat-id", default="", help="chat id (default: TELEGRAM_CHAT_ID)")
    p.set_defaults(func="cmd_telegram")

    p = sub.add_parser("lintsim", help="run the fork's own lint contract over a candidate tree")
    p.add_argument("--candidate", required=True, help="branded tree that would be published")
    p.add_argument("--fork", default="", help="fork checkout defining the checks (default: --repo)")
    p.add_argument("--upstream", default="", help="upstream checkout for the inflation prediction")
    p.set_defaults(func="cmd_lintsim")

    p = sub.add_parser("urls", help="find broken, fake, off-brand and placeholder links in a tree")
    p.add_argument("--tree", default="", help="tree to read links from (default: --repo)")
    p.add_argument(
        "--policy",
        default="",
        help="path to a URL policy (default: the engine's own config/url-policy.yml)",
    )
    p.add_argument(
        "--probe",
        action="store_true",
        help="make a live request per link, within the policy's budgets (off by default)",
    )
    p.add_argument("--json-out", default="", help="write the machine-readable findings here")
    p.set_defaults(func="cmd_urls")

    p = sub.add_parser(
        "surface", help="measure the CLI, workflow, env, entry-point and signature surface"
    )
    p.add_argument("--tree", default="", help="checkout to read (default: --repo)")
    p.add_argument(
        "--baseline",
        default="",
        help="surface JSON to diff against; its absence is reported, not assumed",
    )
    p.add_argument("--save", default="", help="write the current surface to this baseline path")
    p.add_argument(
        "--ignore",
        default="",
        help="comma-separated kind:name labels (e.g. cli-verb:probe) marked as decided",
    )
    p.set_defaults(func="cmd_surface")

    p = sub.add_parser(
        "verify-report",
        help="write the one document saying what was measured and what was not",
    )
    p.add_argument("--tree", default="", help="fork checkout to measure (default: --repo)")
    p.add_argument("--out", default="", help="markdown path to write the document to")
    p.add_argument("--parity", action="store_true", help="run the parity gate")
    p.add_argument("--gaps", action="store_true", help="run the gap analysis (fetches upstream)")
    p.add_argument("--baseline", default="", help="surface JSON to diff the current surface from")
    p.add_argument("--inventory", default="", help="provided credential names JSON (enables creds)")
    p.add_argument("--lint", default="", help="candidate tree to run the fork's lint contract on")
    p.add_argument("--upstream", default="", help="upstream checkout, for the orphan pass")
    p.add_argument("--receipts", default="", help="directory of *.json receipts to digest")
    p.add_argument("--policy", default="", help="path to a URL policy")
    p.add_argument("--threshold", type=float, default=0.99, help="parity gate threshold")
    p.set_defaults(func="cmd_verify_report")

    def measure(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """The inputs every delivery verb can be handed, with nothing measured by default."""
        p.add_argument("--runs", default="", help="this cycle's commits/{sha}/check-runs JSON")
        p.add_argument("--upstream-runs", default="", help="the same payload for upstream")
        p.add_argument("--changed-files", default="", help="upstream delta paths, one per line")
        p.add_argument("--baseline-sha", default="", help="upstream commit the sync started from")
        p.add_argument("--current", default="", help="candidate head sha")
        p.add_argument("--checkout", default="", help="local checkout to measure sizes against")
        p.add_argument("--tree", default="", help="tree to read links and surface from")
        p.add_argument("--policy", default="", help="path to a URL policy")
        p.add_argument(
            "--surface-baseline", default="", help="surface JSON to diff the current surface from"
        )
        p.add_argument("--ignore", default="", help="comma-separated kind:name labels decided")
        p.add_argument("--inventory", default="", help="provided credential names JSON")
        p.add_argument("--upstream", default="", help="upstream checkout, for the orphan pass")
        p.add_argument(
            "--state",
            default="",
            help="error-feed state JSON to compare against (and to record with --record)",
        )
        p.add_argument("--json-out", default="", help="write the machine form here")
        p.add_argument("--limit", type=int, default=0, help="print at most this many lines")
        p.add_argument("--pages", type=int, default=0, help="cap chat pages (0: the default cap)")
        p.add_argument(
            "--style",
            choices=["block", "grid"],
            default="block",
            help="block reads in any font; grid needs a monospace one",
        )
        return p

    p = measure(
        sub.add_parser(
            "errors",
            help="what turned red, what cleared, and what a partial read cannot prove",
        )
    )
    p.add_argument(
        "--record",
        action="store_true",
        help="save this cycle's reds at --state so the next cycle can report a change",
    )
    p.set_defaults(func="cmd_errors")

    p = measure(
        sub.add_parser("issues", help="draft the issues the measured findings earn (dry run)")
    )
    p.add_argument("--project", default="", help="owner/name to read or write issues in")
    p.add_argument(
        "--apply", action="store_true", help="create them with gh -- off unless asked"
    )
    p.add_argument("--dedupe", action="store_true", help="skip keys the repo already has open")
    p.add_argument("--out", default="", help="write the full markdown plan here")
    p.set_defaults(func="cmd_issues")

    p = measure(
        sub.add_parser(
            "discuss", help="draft the questions the engine must not answer for a human"
        )
    )
    p.add_argument("--project", default="", help="owner/name to read or write discussions in")
    p.add_argument(
        "--category", default="", help="discussion category (default: the repo's own)"
    )
    p.add_argument("--apply", action="store_true", help="create them with gh -- off unless asked")
    p.add_argument("--dedupe", action="store_true", help="skip keys the board already has")
    p.add_argument("--out", default="", help="write the full markdown plan here")
    p.set_defaults(func="cmd_discuss")

    p = measure(sub.add_parser("suggest", help="the next actions, ordered as they were measured"))
    p.add_argument(
        "--table", action="store_true", help="answer as a paginated table instead of a list"
    )
    p.set_defaults(func="cmd_suggest")

    p = measure(sub.add_parser("table", help="print one measured family as a bounded table"))
    p.add_argument(
        "--source",
        choices=sorted(_TABLE_SOURCES),
        default="ci",
        help="which measurement to lay out",
    )
    p.set_defaults(func="cmd_table")

    return parser


def _load_secret_env() -> None:
    """Load secrets from the repo-local .env (gitignored) into the process env.

    Only secrets live here — API keys, tokens.  The file is gitignored so it
    never reaches the repository.  In CI the same values come from GitHub
    Secrets, not from a committed file.  Missing file is fine (no AI).
    """
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def main() -> None:
    _load_secret_env()
    parser = build_parser()
    args = parser.parse_args()
    cli = Cli(args)
    getattr(cli, args.func)()


if __name__ == "__main__":
    main()
