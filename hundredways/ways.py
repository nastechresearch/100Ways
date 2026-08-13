"""The 200 Ways: a registry of distinct strategies for every sub-problem.

Every way is a real, named approach to one of the ten problems the sync
engine must solve.  The engine picks one active way per category; the CLI
(``100ways ways list|show|pick``) explores them and lets you switch strategy
without changing code.  The count is a product promise, not a gimmick: each
entry names a genuinely different method with its own tradeoffs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Way:
    way_id: str        # e.g. "detect.poll"
    name: str
    category: str      # detect | diff | brand | scan | verify | port | research | notify | gate | watch
    description: str
    uses: str = ""     # what engine function consumes this way
    default: bool = False


WAYS: list[Way] = [
    # ---- detect: how we know upstream moved --------------------------------
    Way("detect.poll", "Polling loop", "detect", "git fetch upstream && rev-parse HEAD every cycle", uses="Watcher.cycle", default=True),
    Way("detect.webhook", "Push webhook", "detect", "upstream push event hits a local endpoint, triggers one cycle"),
    Way("detect.ssh-hook", "Remote post-merge hook", "detect", "post-merge hook in the upstream checkout fires the check"),
    Way("detect.crontab", "System crontab", "detect", "cron line runs the one-shot check on a schedule"),
    Way("detect.api-watch", "GitHub API events", "detect", "poll the upstream repo's events feed for push types"),
    Way("detect.scheduler", "Jittered scheduler", "detect", "in-process loop with random jitter to avoid thundering herd"),
    Way("detect.systemd-timer", "Systemd timer", "detect", "systemd timer unit triggers the check and logs to journal"),
    Way("detect.sync-pr", "Scheduled sync PR", "detect", "bot opens a PR that merges upstream into a branch on schedule"),
    Way("detect.feed", "Commit RSS/Atom feed", "detect", "subscribe to the upstream repo's atom feed of new commits"),
    Way("detect.graphql", "GitHub GraphQL", "detect", "poll ref-updated events via the GraphQL API"),
    # ---- diff: how we compare two trees ------------------------------------
    Way("diff.git-diff", "git diff plumbing", "diff", "git diff --name-status + --numstat between two refs", uses="analyzer._numstat", default=True),
    Way("diff.tree-compare", "Tree set difference", "diff", "ls-tree both commits and diff the path sets", uses="verify._tree_files"),
    Way("diff.patch-read", "Parse unified diff", "diff", "read patch hunks directly, no working tree"),
    Way("diff.worktree", "Worktree filesystem diff", "diff", "check out both commits, diff the filesystem"),
    Way("diff.range-diff", "git range-diff", "diff", "compare two commit series to see what really changed"),
    Way("diff.blob-hash", "Blob SHA compare", "diff", "compare per-path blob SHAs; identical blob means identical file"),
    Way("diff.similarity", "SequenceMatcher ratio", "diff", "difflib ratio after branding to grade closeness", uses="analyzer._similarity"),
    Way("diff.attributes", "Pathspec-limited diff", "diff", "diff only paths matching a gitattributes spec"),
    Way("diff.first-parent", "First-parent walk", "diff", "step along the first-parent chain, diffing each hop"),
    Way("diff.cat-file", "Batch blob read", "diff", "git cat-file --batch for fast raw byte reads", uses="verify._blob"),
    # ---- brand: how we rewrite hermes into nastech --------------------------
    Way("brand.token-regex", "Anchored token regex", "brand", "word-boundary anchored token replacement", uses="rules.transform_text", default=True),
    Way("brand.compound-first", "Compounds before short forms", "brand", "longest match tokens listed first so hermes-agent beats hermes", uses="rules.DEFAULT_TOKENS"),
    Way("brand.path-mapper", "Path remapping", "brand", "transform tree paths separately from file content", uses="rules.transform_path"),
    Way("brand.locked-assets", "Rename-only assets", "brand", "binary/lockfile paths renamed but never content-rewritten", uses="rules.is_locked_path"),
    Way("brand.gitattributes", "Git rename detection", "brand", "let git detect renames; brand only the content side"),
    Way("brand.template-graft", "Template graft", "brand", "swap in whole nastech templates for known upstream files"),
    Way("brand.sed-port", "Stream-edit patch", "brand", "rewrite patch text before applying", uses="port._rebrand_patch"),
    Way("brand.symlink-shim", "Legacy symlinks", "brand", "keep branded names, symlink old names for compat"),
    Way("brand.import-only", "Namespace import rewrite", "brand", "rewrite import statements only, leave prose alone"),
    Way("brand.resource-map", "Resource swap table", "brand", "map upstream icons/labels/frames to nastech equivalents"),
    # ---- scan: how we identify file types -----------------------------------
    Way("scan.magic-bytes", "Magic-byte signatures", "scan", "identify format from leading bytes, not extension", uses="scanner.detect", default=True),
    Way("scan.extension-hint", "Extension fallback", "scan", "use the extension when magic bytes are ambiguous", uses="scanner.classify_path"),
    Way("scan.nul-heuristic", "NUL byte probe", "scan", "text vs binary split on presence of NUL bytes", uses="scanner.is_text"),
    Way("scan.utf8-probe", "UTF-8 decode probe", "scan", "try decoding as UTF-8 to confirm text", uses="scanner.is_text"),
    Way("scan.mime-guess", "Mimetype guess", "scan", "content type from the stdlib mimetypes table"),
    Way("scan.media-probe", "Media sample probe", "scan", "audio/video container signature detection"),
    Way("scan.lockfile", "Known lockfile scan", "scan", "lockfile names and formats flagged without content reads", uses="rules.LOCKED_EXTENSIONS"),
    Way("scan.archive-peek", "Archive central directory", "scan", "read zip central directory to find embedded branded assets"),
    Way("scan.hash-dedup", "SHA1 dedup", "scan", "group files by blob hash to find duplicates before scanning"),
    Way("scan.size-tier", "Size-band tiering", "scan", "classify by size band first, then content"),
    # ---- verify: how we prove a port is faithful -----------------------------
    Way("verify.round-trip", "Brand round-trip", "verify", "branding(upstream) must equal the nastech tree", uses="verify.verify_rebrand", default=True),
    Way("verify.spot-check", "Sampled spot check", "verify", "full check on changed files, sample of the rest"),
    Way("verify.byte-parity", "Byte parity", "verify", "per-file byte equality after the transform"),
    Way("verify.char-delta", "Character deltas", "verify", "report added/deleted characters per file", uses="verify._char_delta"),
    Way("verify.line-delta", "Line deltas", "verify", "report added/deleted lines per file via numstat", uses="verify._numstat"),
    Way("verify.token-audit", "Forbidden token audit", "verify", "scan for brand tokens that must never survive", uses="analyzer._VIOLATION_TOKENS"),
    Way("verify.english-guard", "English word guard", "verify", "assert venous/anonymous/thermometer survive branding"),
    Way("verify.path-parity", "Path twin check", "verify", "every upstream path must have a mapped nastech twin"),
    Way("verify.orphan-flag", "Orphan flag", "verify", "every nastech-only file must be explainable or flagged"),
    Way("verify.merge-verify", "Merge result verify", "verify", "verify the merge commit's tree, not a cherry-pick"),
    # ---- port: how we land the work ------------------------------------------
    Way("port.patch-apply", "Rebranded patch apply", "port", "rebrand patch text then git apply --3way", uses="port._port_one", default=True),
    Way("port.worktree-isolated", "Isolated worktree", "port", "apply in a detached throwaway worktree, gate, commit", uses="port._worktree"),
    Way("port.cherry-pick", "Cherry-pick + amend", "port", "cherry-pick then amend with brand fixes on top"),
    Way("port.rebase-onto", "Rebase onto upstream", "port", "rebase our branch onto the new upstream head"),
    Way("port.squash", "Squash per batch", "port", "one cumulative port commit per batch of commits"),
    Way("port.per-file", "Dependency-ordered files", "port", "apply file-by-file in dependency order"),
    Way("port.format-patch", "format-patch pipeline", "port", "git format-patch then stream-edit and am"),
    Way("port.smudge-graft", "Smudge filter graft", "port", "copy files into place under a smudge filter"),
    Way("port.merge-overlay", "Merge + branded overlay", "port", "merge upstream, keep a branded overlay branch on top"),
    Way("port.prune-empty", "Prune empty ports", "port", "drop commits with no net change after branding"),
    # ---- research: how we find open-source ideas ------------------------------
    Way("research.github-code", "GitHub code search", "research", "search upstream code for hermes token usage", uses="research.search_repos", default=True),
    Way("research.topics", "GitHub topic search", "research", "search fork-sync and rebrand topics"),
    Way("research.npm-search", "npm registry search", "research", "find JS-side tooling alternatives"),
    Way("research.pypi-search", "PyPI search", "research", "find Python sync/rebrand libraries"),
    Way("research.star-sort", "Star-ranked shortlist", "research", "rank candidate repos by stars then inspect"),
    Way("research.sample-import", "Approach import", "research", "pull a repo's approach and record it in the catalog"),
    Way("research.diff-mine", "Open-source PR mining", "research", "read public PRs that solved the same rebrand"),
    Way("research.license-scan", "License audit", "research", "check licenses so we know what is safe to adapt"),
    Way("research.history-archaeology", "Commit archaeology", "research", "upstream history reveals the rename patterns they used"),
    Way("research.issue-mine", "Issue mining", "research", "GitHub issues mentioning the fork or rebrand"),
    # ---- notify: how we surface events ----------------------------------------
    Way("notify.telegram", "Telegram bot", "notify", "sendMessage to a chat via bot token", uses="notifier._telegram", default=True),
    Way("notify.agent", "Agent prompt hook", "notify", "pipe a prompt to the opencode agent", uses="notifier._agent"),
    Way("notify.desktop", "Desktop notification", "notify", "notify-send / osascript alert"),
    Way("notify.bell", "Terminal cue", "notify", "visual/audible bell in the terminal"),
    Way("notify.markdown-file", "Markdown report", "notify", "append a report to a docs file"),
    Way("notify.webhook-out", "Generic webhook", "notify", "POST a JSON payload to any HTTP endpoint"),
    Way("notify.email", "SMTP digest", "notify", "send an email summary"),
    Way("notify.chat-hook", "Chat webhook", "notify", "matrix/discord/slack webhook with markdown"),
    Way("notify.exec", "User command", "notify", "exec a user-provided command with the event in argv/stdin"),
    Way("notify.log", "JSONL event log", "notify", "append structured events to a JSONL file", uses="watcher._save_state"),
    # ---- gate: how we decide a port ships --------------------------------------
    Way("gate.threshold", "Parity threshold", "gate", "pass_ratio must exceed N (0.99)", uses="verify.gate_passes", default=True),
    Way("gate.zero-fail", "Zero hard failures", "gate", "no failed files regardless of ratio"),
    Way("gate.locked-review", "Locked-file sign-off", "gate", "every locked-file diff needs explicit operator review"),
    Way("gate.violation-block", "Violation block", "gate", "any brand violation blocks the port"),
    Way("gate.dry-run", "Report only", "gate", "never write; produce the plan", uses="port.port_commits.dry_run"),
    Way("gate.review-tag", "Review label", "gate", "mark the branch PR with requires-review"),
    Way("gate.quorum", "N-of-M checks", "gate", "a quorum of check types must all pass"),
    Way("gate.cost-limit", "Effort budget", "gate", "abort when parity work would exceed a budget"),
    Way("gate.cache-guard", "Prompt-cache guard", "gate", "block ports that would break conversation prompt caching"),
    Way("gate.rollback-ready", "Rollback ref", "gate", "keep last_good so any gate failure reverts cleanly", uses="port._port_one"),
    # ---- watch: how we run the loop --------------------------------------------
    Way("watch.loop", "Sleep loop", "watch", "poll, sleep interval, repeat", uses="watcher.watch", default=True),
    Way("watch.callback", "Event callbacks", "watch", "fire a callback per event, no polling"),
    Way("watch.batch", "Bounded cycles", "watch", "run N cycles then exit (tests, ci)", uses="watcher.watch.max_cycles"),
    Way("watch.supervisor", "Supervised process", "watch", "run under a supervisor that restarts on crash"),
    Way("watch.daemon", "Pidfile daemon", "watch", "long-lived process with a pidfile and signal handling"),
    Way("watch.one-shot", "Single check", "watch", "check once and exit (cron, hooks)", uses="watcher.check_once"),
    Way("watch.cadence", "Adaptive cadence", "watch", "interval grows when idle, shrinks right after a change"),
    Way("watch.stateful", "Stateful resume", "watch", "persist last-seen heads so restarts don't re-notify", uses="watcher._save_state"),
    Way("watch.fanout", "Multi-repo fan-out", "watch", "run cycles in parallel across many repos"),
    Way("watch.maintenance", "Self-maintaining", "watch", "prune state and logs each cycle"),
    # ---- detect (second decade): how we know upstream moved --------------------
    Way("detect.sigterm", "Signal-driven recheck", "detect", "SIGUSR1 wakes the loop immediately instead of waiting"),
    Way("detect.idle-watch", "Idle-time watch", "detect", "recheck only after the process has been idle N seconds"),
    Way("detect.startup-scan", "Startup scan", "detect", "always run one full cycle at boot, then poll"),
    Way("detect.pr-review", "PR-close detection", "detect", "watch merged/closed PRs as a movement signal"),
    Way("detect.tag-scan", "Tag/ref change scan", "detect", "fetch refs and diff the tag map for new releases"),
    Way("detect.reflog", "Local reflog probe", "detect", "detect upstream movement via our own remote-tracking reflog"),
    Way("detect.cron-miss", "Missed-cron catchup", "detect", "after downtime, backfill missed checks on resume"),
    Way("detect.webhook-fallback", "Webhook with fallback", "detect", "webhook primary, poll interval as the safety net"),
    Way("detect.event-loop", "Selector event loop", "detect", "asyncio selector that multiplexes sockets + timers"),
    Way("detect.multihost", "Multi-host heartbeat", "detect", "several hosts each check and share a lease via git notes"),
    # ---- diff (second decade): how we compare two trees ------------------------
    Way("diff.mirror-compare", "Mirror-ref compare", "diff", "diff local mirror refs against a fully fetched upstream"),
    Way("diff.staged-tree", "Staged tree diff", "diff", "compare the staged index tree, not just HEAD"),
    Way("diff.three-way", "Three-way merge base", "diff", "diff base..ours vs base..theirs to isolate the fork delta"),
    Way("diff.pathspec-narrow", "Narrow pathspec", "diff", "diff only directories upstream recently touched"),
    Way("diff.commit-count", "Commit-count delta", "diff", "when only head-sha changed, use rev-list counts as a proxy"),
    Way("diff.blame-window", "Blame window scan", "diff", "diff the last N commits' blames to find recent authorship"),
    Way("diff.submodule", "Submodule pointer diff", "diff", "compare submodule SHAs and recurse into moved submodules"),
    Way("diff.empty-tree", "Empty-tree baseline", "diff", "diff against the empty tree to enumerate the whole tree"),
    Way("diff.renames-only", "Rename-only diff", "diff", "diff with -M to surface pure renames before content edits"),
    Way("diff.binary-skip", "Binary-safe diff", "diff", "skip binary paths when computing numeric diffs"),
    # ---- brand (second decade): how we rewrite hermes into nastech -------------
    Way("brand.case-first", "Case-preserving cascade", "brand", "try compound, then title, then lowercase per token"),
    Way("brand.regex-catalog", "Regex catalog", "brand", "hand-written regexes for tricky context-dependent tokens"),
    Way("brand.dotenv-map", "Dotenv key map", "brand", "rename HERMES_* env keys and their default-value mirrors"),
    Way("brand.json-config", "Config-file rewrite", "brand", "walk JSON/YAML config and rename keys + string values"),
    Way("brand.cli-name", "CLI argv rebrand", "brand", "rewrite subcommand/flag strings inside code and docs"),
    Way("brand.url-sweep", "URL/domain sweep", "brand", "rebrand all https links and repo paths in text"),
    Way("brand.shell-alias", "Shell alias shim", "brand", "install nastech aliases that still resolve hermes names"),
    Way("brand.pkg-meta", "Package metadata rebrand", "brand", "rewrite pyproject/package.json name, desc, URLs"),
    Way("brand.banner-text", "Banner/branding text", "brand", "swap terminal banner art and product string literals"),
    Way("brand.logo-copy", "Logo byte copy", "brand", "copy the canonical nastech logo over upstream art files"),
    # ---- scan (second decade): how we identify file types ----------------------
    Way("scan.xml-hint", "XML prolog probe", "scan", "recognize XML docs via the <?xml prolog before extension"),
    Way("scan.json-decode", "Strict JSON probe", "scan", "try json.loads to confirm a text file is JSON"),
    Way("scan.corpus-ngrams", "N-gram text guess", "scan", "byte n-gram statistics to separate text from compressed"),
    Way("scan.encoding-sniff", "Charset sniffer", "scan", "detect utf-16/latin-1 from BOM and byte patterns"),
    Way("scan.shebang-hint", "Shebang detection", "scan", "executable scripts detected by their #! first line"),
    Way("scan.mime-db", "MIME database lookup", "scan", "use the system MIME db for long-form signatures"),
    Way("scan.extension-map", "Extension registry", "scan", "extension -> category lookup table as first pass"),
    Way("scan.deep-binary", "Deep binary probe", "scan", "scan whole file for NULs, not just the head"),
    Way("scan.sample-stats", "Sample statistics", "scan", "entropy + printable ratio on a random sample"),
    Way("scan.manifest-index", "Manifest index", "scan", "index file names from lockfiles to hint at formats"),
    # ---- verify (second decade): how we prove a port is faithful ----------------
    Way("verify.cache-invariant", "Cache-safe invariant", "verify", "assert a port keeps the prompt-cache prefix byte-stable"),
    Way("verify.role-alternation", "Role alternation check", "verify", "assert message roles never collide after the port"),
    Way("verify.lockfile-parity", "Lockfile parity", "verify", "lockfiles must match upstream exactly (never rewritten)"),
    Way("verify.gate-consistency", "Gate consistency", "verify", "same report must yield same gate verdict across runs"),
    Way("verify.no-hallucination", "No cross-tool refs", "verify", "schemas must not mention tools missing from the toolset"),
    Way("verify.schema-stable", "Schema stability", "verify", "tool schemas unchanged except where the port intends them"),
    Way("verify.binary-exists", "Binary presence", "verify", "every branded binary path must exist in the target tree"),
    Way("verify.charset-clean", "Charset clean", "verify", "no mojibake: files decode clean under their detected charset"),
    Way("verify.no-leftover-env", "Env-key audit", "verify", "no HERMES_* env keys may remain in code or docs"),
    Way("verify.path-depth", "Path depth parity", "verify", "branded trees keep the same directory depth per file"),
    # ---- port (second decade): how we land the work ----------------------------
    Way("port.incremental", "Incremental port", "port", "port only the delta since the last port, not everything"),
    Way("port.manifest-batch", "Manifest batch", "port", "port commits listed in an explicit manifest in order"),
    Way("port.dependency-sort", "Dependency-ordered sort", "port", "order ports by intra-tree import dependencies"),
    Way("port.atomic-batch", "Atomic batch", "port", "all ports of a cycle land as one atomic push"),
    Way("port.chunked", "Chunked landing", "port", "split a huge port into reviewable chunks with gates"),
    Way("port.fixup-chain", "Fixup chain", "port", "apply brand fixups as separate follow-up commits"),
    Way("port.rebase-clean", "Rebase clean-up", "port", "rebase our branch then drop conflicts with brand re-resolves"),
    Way("port.keep-author", "Author-preserving", "port", "cherry-pick -x so upstream authorship survives"),
    Way("port.branch-per-batch", "Branch per batch", "port", "one dedicated branch per batch, merged after gate"),
    Way("port.signed", "Signed commits", "port", "GPG-sign ported commits to prove the pipeline produced them"),
    # ---- research (second decade): how we find open-source ideas ----------------
    Way("research.fork-network", "Fork network scan", "research", "explore forks of upstream for naming/rename conventions"),
    Way("research.release-notes", "Release notes mining", "research", "read upstream release notes for rebrand-relevant changes"),
    Way("research.gitignore-audit", "Ignore-pattern audit", "research", "compare .gitignore sets for files that silently vanish"),
    Way("research.symlink-census", "Symlink census", "research", "list symlinks upstream so the brand copies them correctly"),
    Way("research.hooks-census", "Git hook census", "research", "inventory upstream hooks that must be ported as-is"),
    Way("research.codesearch-regex", "Regex code search", "research", "search code for context-broken rebrand leftovers"),
    Way("research.docs-mine", "Docs mining", "research", "scan docs for hermes references a rename would miss"),
    Way("research.config-schema", "Config schema dump", "research", "extract the upstream config schema to map keys 1:1"),
    Way("research.ci-mine", "CI workflow mining", "research", "copy upstream CI patterns that protect parity"),
    Way("research.asset-inventory", "Asset inventory", "research", "enumerate all images/frames so rename-only is exhaustive"),
    # ---- notify (second decade): how we surface events --------------------------
    Way("notify.slack", "Slack webhook", "notify", "POST a markdown message to a Slack incoming webhook"),
    Way("notify.discord", "Discord webhook", "notify", "POST an embed to a Discord webhook URL"),
    Way("notify.matrix", "Matrix message", "notify", "PUT a message to a Matrix room"),
    Way("notify.signal", "Signal sender", "notify", "send a message through a local signal-cli daemon"),
    Way("notify.gitlab", "GitLab comment", "notify", "post the report as a comment on a merge request"),
    Way("notify.sentry", "Sentry event", "notify", "send gate failures as Sentry issues"),
    Way("notify.pushover", "Pushover push", "notify", "mobile push via the Pushover API"),
    Way("notify.ntfy", "ntfy topic", "notify", "publish to a public ntfy topic with a token"),
    Way("notify.tail", "Tail recap", "notify", "append a short recap line to a running log stream"),
    Way("notify.atom", "Atom feed publish", "notify", "emit the report as a new entry in a local atom feed"),
    # ---- gate (second decade): how we decide a port ships ------------------------
    Way("gate.everything-green", "All-green gate", "gate", "every file must pass before anything ships"),
    Way("gate.ratio-only", "Ratio-only gate", "gate", "gate on the parity ratio alone, tolerate small drift"),
    Way("gate.since-birth", "Birth-commit gate", "gate", "prove parity against the birth commit each cycle"),
    Way("gate.locked-unchanged", "Locked immutable", "gate", "locked files must be byte-identical to their twin"),
    Way("gate.violation-zero", "Zero-violation gate", "gate", "any brand-rule violation fails the gate"),
    Way("gate.asset-ok", "Asset gate", "gate", "every image/binary must exist; content free to differ"),
    Way("gate.test-suite", "Test suite gate", "gate", "run the repo's own tests; failures block the port"),
    Way("gate.gen-ai", "AI review gate", "gate", "an LLM reviews the diff and vetoes suspicious hunks"),
    Way("gate.lockfile-pinned", "Lockfile pinned", "gate", "dependency lockfiles must match the expected hashes"),
    Way("gate.two-approval", "Two-approval gate", "gate", "two independent checkers (rule + AI) must both pass"),
    # ---- watch (second decade): how we run the loop ------------------------------
    Way("watch.asyncio", "Asyncio loop", "watch", "single-threaded async loop with cooperative cycles"),
    Way("watch.multiprocess", "Multiprocess workers", "watch", "one supervisor process, worker processes per repo"),
    Way("watch.timerfd", "Timerfd wakeup", "watch", "wake the poll on timerfd instead of sleeping"),
    Way("watch.inotify", "Inotify local trigger", "watch", "local filesystem events trigger the cycle locally"),
    Way("watch.backoff", "Exponential backoff", "watch", "interval doubles on repeated no-change, resets on change"),
    Way("watch.heartbeat", "Heartbeat watchdog", "watch", "emit a heartbeat and fail if the cycle stalls"),
    Way("watch.singleflight", "Single-flight guard", "watch", "never run two cycles concurrently even if triggered"),
    Way("watch.lockfile", "Lockfile mutual exclusion", "watch", "a lockfile ensures one watcher per repo at a time"),
    Way("watch.rotate-logs", "Log rotation", "watch", "rotate cycle logs by size and keep only recent tails"),
    Way("watch.graceful", "Graceful shutdown", "watch", "drain the current cycle before stopping on SIGTERM"),
]


class WaysRegistry:
    """Registry facade over the WAYS list."""

    def __init__(self, ways: list[Way] | None = None):
        self._ways = list(ways if ways is not None else WAYS)

    def all(self) -> list[Way]:
        return self._ways

    def categories(self) -> list[str]:
        seen: list[str] = []
        for w in self._ways:
            if w.category not in seen:
                seen.append(w.category)
        return seen

    def by_category(self, category: str) -> list[Way]:
        return [w for w in self._ways if w.category == category]

    def get(self, way_id: str) -> Way | None:
        for w in self._ways:
            if w.way_id == way_id:
                return w
        return None

    def defaults(self) -> dict[str, str]:
        return {w.category: w.way_id for w in self._ways if w.default}

    @property
    def count(self) -> int:
        return len(self._ways)


def build_registry() -> WaysRegistry:
    return WaysRegistry()
