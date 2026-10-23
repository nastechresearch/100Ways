# 100Ways

A rebrand-safe fork-sync engine that ports the upstream Hermes agent
into the Nastech fork while keeping parity and the brand intact.

## At a glance

- 212 ways across 10 strategy categories
- 16 achievements, 2 unlocked
- 22 owned-asset registry entries (see config/owned-assets/)
- 19 pipeline stages per update run
- 39 CLI verbs and 22 Telegram commands

## Ways

### brand (20 ways)

- Anchored token regex
- Compounds before short forms
- Path remapping
- Rename-only assets
- Git rename detection
- Template graft
- Stream-edit patch
- Legacy symlinks
- Namespace import rewrite
- Resource swap table
- Case-preserving cascade
- Regex catalog
- Dotenv key map
- Config-file rewrite
- CLI argv rebrand
- URL/domain sweep
- Shell alias shim
- Package metadata rebrand
- Banner/branding text
- Logo byte copy

### detect (21 ways)

- Polling loop
- Push webhook
- Remote post-merge hook
- System crontab
- GitHub API events
- Jittered scheduler
- Systemd timer
- Scheduled sync PR
- Commit RSS/Atom feed
- GitHub GraphQL
- Signal-driven recheck
- Idle-time watch
- Startup scan
- PR-close detection
- Tag/ref change scan
- Local reflog probe
- Missed-cron catchup
- Webhook with fallback
- Selector event loop
- Multi-host heartbeat
- Live command inventory

### diff (20 ways)

- git diff plumbing
- Tree set difference
- Parse unified diff
- Worktree filesystem diff
- git range-diff
- Blob SHA compare
- SequenceMatcher ratio
- Pathspec-limited diff
- First-parent walk
- Batch blob read
- Mirror-ref compare
- Staged tree diff
- Three-way merge base
- Narrow pathspec
- Commit-count delta
- Blame window scan
- Submodule pointer diff
- Empty-tree baseline
- Rename-only diff
- Binary-safe diff

### gate (23 ways)

- Parity threshold
- Zero hard failures
- Locked-file sign-off
- Violation block
- Report only
- Review label
- N-of-M checks
- Effort budget
- Prompt-cache guard
- Rollback ref
- All-green gate
- Ratio-only gate
- Birth-commit gate
- Locked immutable
- Zero-violation gate
- Asset gate
- Test suite gate
- AI review gate
- Lockfile pinned
- Two-approval gate
- Open-draft dedup
- Proven-complete gate
- Documentation bar

### notify (25 ways)

- Telegram bot
- Agent prompt hook
- Desktop notification
- Terminal cue
- Markdown report
- Generic webhook
- SMTP digest
- Chat webhook
- User command
- JSONL event log
- Slack webhook
- Discord webhook
- Matrix message
- Signal sender
- GitLab comment
- Sentry event
- Pushover push
- ntfy topic
- Tail recap
- Atom feed publish
- Draft per measurement
- Forge discussion
- Bounded chat table
- Next-action list
- Cycle delta feed

### port (20 ways)

- Rebranded patch apply
- Isolated worktree
- Cherry-pick + amend
- Rebase onto upstream
- Squash per batch
- Dependency-ordered files
- format-patch pipeline
- Smudge filter graft
- Merge + branded overlay
- Prune empty ports
- Incremental port
- Manifest batch
- Dependency-ordered sort
- Atomic batch
- Chunked landing
- Fixup chain
- Rebase clean-up
- Author-preserving
- Branch per batch
- Signed commits

### research (20 ways)

- GitHub code search
- GitHub topic search
- npm registry search
- PyPI search
- Star-ranked shortlist
- Approach import
- Open-source PR mining
- License audit
- Commit archaeology
- Issue mining
- Fork network scan
- Release notes mining
- Ignore-pattern audit
- Symlink census
- Git hook census
- Regex code search
- Docs mining
- Config schema dump
- CI workflow mining
- Asset inventory

### scan (20 ways)

- Magic-byte signatures
- Extension fallback
- NUL byte probe
- UTF-8 decode probe
- Mimetype guess
- Media sample probe
- Known lockfile scan
- Archive central directory
- SHA1 dedup
- Size-band tiering
- XML prolog probe
- Strict JSON probe
- N-gram text guess
- Charset sniffer
- Shebang detection
- MIME database lookup
- Extension registry
- Deep binary probe
- Sample statistics
- Manifest index

### verify (23 ways)

- Brand round-trip
- Sampled spot check
- Byte parity
- Character deltas
- Line deltas
- Forbidden token audit
- English word guard
- Path twin check
- Orphan flag
- Merge result verify
- Cache-safe invariant
- Role alternation check
- Lockfile parity
- Gate consistency
- No cross-tool refs
- Schema stability
- Binary presence
- Charset clean
- Env-key audit
- Path depth parity
- Shipped-link audit
- Red ownership
- Documented surface

### watch (20 ways)

- Sleep loop
- Event callbacks
- Bounded cycles
- Supervised process
- Pidfile daemon
- Single check
- Adaptive cadence
- Stateful resume
- Multi-repo fan-out
- Self-maintaining
- Asyncio loop
- Multiprocess workers
- Timerfd wakeup
- Inotify local trigger
- Exponential backoff
- Heartbeat watchdog
- Single-flight guard
- Lockfile mutual exclusion
- Log rotation
- Graceful shutdown

## CLI verbs

- `check` — one-shot watch cycle
- `watch` — live watch loop
- `plan` — list unported upstream commits
- `port` — port upstream commits (dry-run unless --apply)
- `analyze` — file-to-file gap analysis
- `audit` — brand-coverage audit: discover future brand spellings, residue, and secrets
- `ways` — browse the ways registry
- `research` — search open source for ideas to steal
- `diff` — two-phase diff: live upstream vs local Hermes, then branded vs Nastech
- `scan` — scan file formats
- `notify` — send a test notification
- `status` — repo + rules state
- `verify` — parity gate vs the birth commit (exit 1 on fail)
- `ship` — verify parity then prepare the sync branch for shipping
- `report` — write a markdown gap report to config/reports
- `dashboard` — serve the live web dashboard
- `pull` — fetch upstream + write report + record achievement
- `achievements` — list achievements and unlock state
- `codes` — print the gap code legend
- `readme` — regenerate README.md from the registries
- `release` — verify the code table and incoming codes
- `admin` — compile/verify the admin password into its system token
- `update` — pull real Hermes -> brand whole tree -> verify -> save Nastech-Update#N
- `profile` — rank a run's stage costs, or compare two runs
- `weekly-full-sync` — weekly complete rebrand audit or snapshot; never pushes or merges
- `forkcheck` — diff the branded snapshot against the nastech-agent fork
- `narrative` — build the PR title + body from a sync manifest
- `pubconfig` — predict which credential names a future run would read as an empty string
- `why-red` — sort the reds in a check-runs payload by who owns the fix
- `telegram` — the commanded surface: menu, resolve a line, send a probe
- `lintsim` — run the fork's own lint contract over a candidate tree
- `urls` — find broken, fake, off-brand and placeholder links in a tree
- `surface` — measure the CLI, workflow, env, entry-point and signature surface
- `verify-report` — write the one document saying what was measured and what was not
- `errors` — what turned red, what cleared, and what a partial read cannot prove
- `issues` — draft the issues the measured findings earn (dry run)
- `discuss` — draft the questions the engine must not answer for a human
- `suggest` — the next actions, ordered as they were measured
- `table` — print one measured family as a bounded table

## Telegram commands

- `/status` — Gate, threshold and the real refs behind this run _(read)_
- `/progress` — Pending commits and the subjects detected this cycle _(read)_
- `/errors` — The latest failed step, and what changed since the last cycle _(read)_
- `/remaining` — What is left before a PR may be created or updated _(read)_
- `/gates` — Every verification gate and its recorded outcome _(read)_
- `/suggest` — The next actions this run measured, in the order it measured them _(read)_
- `/table` — One measured family as a table that fits one message _(read)_
- `/issues` — The issues the findings would earn; nothing is created from chat _(operate)_
- `/discuss` — The questions the engine will not answer for you; nothing is created _(operate)_
- `/pr status` — The review PR this run produced _(read)_
- `/commit` — Explain one detected commit _(read)_
- `/search` — Find commit subjects matching a phrase _(read)_
- `/explain` — What a pipeline term means in this fork _(read)_
- `/log` — The last commands this chat was given _(operate)_
- `/refresh` — Re-read the run state from disk before answering _(read)_
- `/stay` — Keep listening for another bounded window _(operate)_
- `/pending` — The edits proposed from chat that have not landed _(operate)_
- `/edit` — Propose a literal change to an engine rule module _(edit)_
- `/apply` — Verify a proposed edit, push its branch and open it for review _(edit)_
- `/cancel` — Drop a proposed edit without writing anything _(edit)_
- `/whoami` — The ids this bot sees and what you are allowed to do _(read)_
- `/help` — This menu _(read)_

## Achievements

- ▢ ✅ first_gate_pass — The parity gate passed for the first time.
- ▢ 🔟 gate_pass_10 — The parity gate passed 10 times.
- ▢ 💯 gate_pass_100 — The parity gate passed 100 times.
- ▢ 🐕 first_violation — Caught a brand-rule violation (error 82).
- ▢ 🗺️ first_missing — Found an upstream file missing from Nastech (error 404).
- ▢ 🧾 first_extra — Every extra file got an explanation (error 84).
- ▢ 🚢 port_1 — Ported the first upstream commit.
- ✅ 🔽 first_pull — Ran the first upstream pull + report.
- ▢ 🔁 pulls — Cumulative upstream pulls.
- ▢ ⛵ port_10 — Ported 10 upstream commits.
- ▢ ⚓ port_100 — Ported 100 upstream commits.
- ✅ 🔍 scan_1000 — Verified 1000+ files in a single run.
- ▢ 👑 admin_edit — An admin edited a branding rule in the dashboard.
- ▢ 🌐 polyglot — Generated reports in 3 or more languages.
- ▢ 📜 first_report — Wrote the first markdown report.
- ▢ 📦 shipped_pr — Shipped a verified update as a pull request.

## Trophies

- 🔽 First Pull — Ran the first upstream pull + report.
- 🔍 Deep Scan — Verified 1000+ files in a single run.

## Pipeline stages

1. pull
2. source-evidence
3. census
4. plan
5. brand
6. reconcile
7. preserve
8. scan
9. compare
10. verify
11. forkcheck
12. report
13. package
14. manifest
15. record
16. notify
17. gate
18. summary
19. release

---

Generated by `100ways readme`; do not edit by hand.

