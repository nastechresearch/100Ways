#!/usr/bin/env python3
"""100Ways - rebrand-safe upstream sync engine with 200 named strategies.

Watches the upstream Hermes repo, ports commits onto the Nastech fork while
enforcing branding rules, verifies file-to-file parity, and notifies Telegram
and the opencode agent of anything that needs attention.

Commands
--------
ways       Browse the 200 ways: list, show, pick a strategy per category.
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

from .achievements import Achievements
from .ai import AIEngine
from .analyzer import analyze
from .assets import OwnedAssets
from .codes import CODE_DETAILS, code_name
from .dashboard import serve as serve_dashboard
from .git_ops import ensure_branch
from .notifier import Notification, notifier_from_env
from .port import new_upstream_commits, port_commits
from .release import release_check_table, release_summary, release_verify_incoming
from .research import research as run_research
from .rules import BrandingRules
from .scanner import classify_path
from .security import verify_token
from .updates import DEFAULT_HERMES_URL, UpdateManager, default_updates_dir, hermes_path
from .weekly_sync import build_weekly_report, save_ledger, write_weekly_report
from .verify import _git, _git_ok, verify_rebrand
from .watcher import Watcher, WatcherConfig
from .ways import build_registry

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
        """Regenerate README.md from the ways + achievements + owned-asset registries."""
        from .readme import ReadmeInputs, render_readme

        state_dir = self.args.state_dir or os.path.join(os.path.dirname(os.path.abspath(self.repo)), "100ways-state")
        owned = OwnedAssets(repo=self.repo)
        target = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "README.md"))
        content = render_readme(ReadmeInputs(
            state_dir=state_dir,
            owned_count=owned.count,
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
        owned = OwnedAssets(repo=self.repo)
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
                            fork_root=fork_root)
        zip_path = self.args.zip or ""
        if zip_path and zip_path.endswith(os.sep):
            zip_path = ""
        result = mgr.run(zip_path=zip_path, project_name=self.args.project_name)
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
        if self.args.state_dir:
            ach = Achievements(self.args.state_dir)
            unlocked = ach.apply_event("pull")
            unlocked += ach.apply_event("scan_1000") if result.scan.total >= 1000 else []
            for name in unlocked:
                print(f"  achievement: {name}")
        if not result.gate:
            print("GATE FAILED - snapshot kept for inspection", file=sys.stderr)
            sys.exit(1)

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

    p = sub.add_parser("ways", help="browse the 200 ways")
    p.add_argument("way_action", nargs="?", default="list", choices=["list", "show", "count", "defaults"])
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
    p.add_argument("--zip", default="", help="also build the release zip at this path (project folder + 2 md reports)")
    p.add_argument("--project-name", default="nastech-agent", help="name of the project folder inside the zip")
    p.add_argument("--state-dir", default="", help="state dir (default: repo-sibling 100ways-state)")
    p.add_argument("--fork-root", default="",
                   help="nastech-agent fork checkout to diff against (default: --repo); "
                        "enables the preserve + forkcheck stages")
    p.add_argument("--emit-outputs", default="",
                   help="write JSON {update_number, upstream_sha, gate} to this file "
                        "(CI emits these into $GITHUB_OUTPUT)")
    p.set_defaults(func="cmd_update")

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
