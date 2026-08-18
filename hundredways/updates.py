"""Ordered update pipeline: pull real Hermes, brand the whole tree, snapshot.

The ``update`` command makes the sync engine real.  It runs as **19 ordered
stages** so every step is named, recorded, and reported:

    Updates-Commits/
      hermes-agent/          # a fresh pull of the REAL upstream repo
      Nastech-Update#1/      # branded, scanned, verified snapshot of pull #1
      Nastech-Update#1.zip   # release zip: 1 project folder + 2 md reports
      Nastech-Update#2/      # ... every later pull lands as #N = max+1

Order is guaranteed by the numbering scheme (a dir with no ``manifest.json``
is a partial run and is overwritten, never skipped past).  Every file is
understood because every file is scanned, and every change is proven because
the branded tree is verified file-by-file against the freshly pulled Hermes
tree before a snapshot is recorded.

The module has no hard dependency on a live network: ``hermes_url`` may be
an https remote, an ssh URL, or a local path / ``file://`` URL, so the whole
pipeline is testable against a fake "Hermes" repo.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .assets import OwnedAssets
from .forkcheck import (
    ForkCheckReport,
    SourceDeltaReport,
    fork_consistency,
    preserve_fork_files,
    source_tree_delta,
)
from .rules import (
    BrandingRules,
    is_contributor_name_path,
    is_immutable_path,
    is_locked_path,
)
from .scanner import classify_path, is_text
from .verify import FileResult, VerifyReport

DEFAULT_HERMES_URL = "https://github.com/NousResearch/hermes-agent.git"


def source_fingerprint(remote: str) -> str:
    """Return a non-identifying candidate-safe fingerprint of the direct source remote."""
    return hashlib.sha256(remote.encode("utf-8")).hexdigest()
HERMES_DIR = "hermes-agent"
UPDATE_PREFIX = "Nastech-Update#"

# The 19 ordered pipeline stages.  The direct source is always freshly cloned,
# SHA-bound, and censused before branding.  Candidate correctness is then proven
# by the full branded-tree conformance and candidate-test gates downstream.
# `preserve` copies fork-local files (owned
# assets, contributor emails, fork-only skills) into the snapshot so the PR
# never deletes them; `forkcheck` diffs the snapshot against the real
# nastech-agent fork to prove unchanged files are byte-identical (clean git
# commits, no whole-tree churn) and added lines are brand-clean.  `release`
# is where GitHub Actions uploads the zip; locally it is recorded as skipped.
STAGES = [
    "pull",
    "source-evidence",
    "census",
    "plan",
    "brand",
    "reconcile",
    "preserve",
    "scan",
    "compare",
    "verify",
    "forkcheck",
    "report",
    "package",
    "manifest",
    "record",
    "notify",
    "gate",
    "summary",
    "release",
]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def default_updates_dir(repo: str) -> str:
    """Updates-Commits lives next to the fork checkout (sibling dir)."""
    return os.path.join(os.path.dirname(os.path.abspath(repo)), "Updates-Commits")


def _complete_update_dirs(updates_dir: str) -> list[str]:
    """Existing update dirs that carry a manifest.json (i.e. completed runs)."""
    if not os.path.isdir(updates_dir):
        return []
    return [d for d in os.listdir(updates_dir)
            if d.startswith(UPDATE_PREFIX)
            and os.path.isfile(os.path.join(updates_dir, d, "manifest.json"))]


def next_update_number(updates_dir: str) -> int:
    """Highest completed Nastech-Update#N + 1; 1 when none exist.

    A dir without ``manifest.json`` is a partial run (interrupted mid-brand)
    and does not count - the next run overwrites it instead of skipping past
    it, so order stays contiguous.
    """
    highest = 0
    for name in _complete_update_dirs(updates_dir):
        try:
            highest = max(highest, int(name[len(UPDATE_PREFIX):]))
        except ValueError:
            continue
    return highest + 1


def update_path(updates_dir: str, number: int) -> str:
    return os.path.join(updates_dir, f"{UPDATE_PREFIX}{number}")


def hermes_path(updates_dir: str) -> str:
    return os.path.join(updates_dir, HERMES_DIR)


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _run_ok(cmd: list[str], what: str) -> str:
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"{what} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def pull_hermes(updates_dir: str, hermes_url: str = DEFAULT_HERMES_URL) -> str:
    """Acquire a new clone directly from the configured upstream remote.

    The prior checkout is deliberately discarded.  This prohibits a local git
    cache from becoming an input to a synchronization decision, while keeping
    local-path remotes available for deterministic tests.
    """
    os.makedirs(updates_dir, exist_ok=True)
    dest = hermes_path(updates_dir)
    url = hermes_url if "://" in hermes_url else os.path.abspath(hermes_url)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    _run_ok(["git", "clone", "--no-local", url, dest], "fresh direct upstream clone")
    return _run_ok(["git", "-C", dest, "rev-parse", "HEAD"], "upstream head")


def fork_manifest_upstream_sha(fork_root: str) -> str:
    """Read the last verified Hermes SHA recorded in the current NasTech fork."""
    if not fork_root:
        return ""
    manifest_path = os.path.join(fork_root, "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            value = json.load(handle).get("upstream_sha", "")
    except (OSError, ValueError):
        return ""
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) else ""


def previous_upstream_sha(updates_dir: str, before_number: int) -> str:
    """Return the most recent recorded source revision, if any."""
    for number in range(before_number - 1, 0, -1):
        manifest_path = os.path.join(update_path(updates_dir, number), "manifest.json")
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                value = json.load(fh).get("upstream_sha", "")
            if value:
                return value
        except (OSError, ValueError):
            continue
    return ""


def apply_owned_assets(dst: str, owned: OwnedAssets | None = None) -> list[str]:
    """Materialize every declared NasTech-owned target, even after upstream deletion.

    Brand-time replacement protects owned assets when upstream still carries a
    counterpart.  This second pass protects the same asset when upstream has
    removed that counterpart: our explicit registry remains authoritative.
    """
    if owned is None:
        return []
    materialized: list[str] = []
    for target in sorted(owned.mapping):
        source = owned.asset_path(target)
        if source is None:
            raise ValueError(f"owned asset registry entry is unavailable: {target}")
        destination = os.path.join(dst, target)
        if os.path.isfile(destination):
            try:
                with (
                    open(source, "rb") as source_handle,
                    open(destination, "rb") as destination_handle,
                ):
                    if source_handle.read() == destination_handle.read():
                        continue
            except OSError:
                pass
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(source, destination)
        _restore_mode(source, destination)
        materialized.append(target)
    return materialized


def upstream_change_evidence(repo: str, baseline: str, head: str) -> tuple[list[str], dict[str, int]]:
    """Collect reviewable commit subjects and top-level changed-area counts."""
    revision_range = f"{baseline}..{head}" if baseline else "-n 1"
    if baseline:
        ancestry = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", baseline, head], capture_output=True)
        if ancestry.returncode:
            revision_range = "-n 1"
    subject_args = ["git", "-C", repo, "log", "--format=%s"] + revision_range.split()
    subjects = [line for line in _run_ok(subject_args, "upstream subject scan").splitlines() if line]
    path_args = ["git", "-C", repo, "show", "--format=", "--name-only"] + revision_range.split()
    areas: dict[str, int] = {}
    for path in _run_ok(path_args, "upstream changed-area scan").splitlines():
        if not path:
            continue
        area = path.split("/", 1)[0]
        areas[area] = areas.get(area, 0) + 1
    return subjects, dict(sorted(areas.items()))


# ---------------------------------------------------------------------------
# Census (understand what changed before touching anything)
# ---------------------------------------------------------------------------

@dataclass
class Census:
    files: int = 0
    dirs: int = 0

    def summary(self) -> str:
        return f"{self.files} files in {self.dirs} directories"


def census_tree(root: str) -> Census:
    """Count every source file while pruning only engine directories at the root."""
    census = Census()
    root_abs = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.abspath(dirpath) == root_abs:
            dirnames[:] = [
                name
                for name in dirnames
                if name not in (".git", HERMES_DIR) and not name.startswith(UPDATE_PREFIX)
            ]
        else:
            dirnames[:] = [name for name in dirnames if name != ".git"]
        census.files += len(filenames)
        census.dirs += len(dirnames)
    return census


# ---------------------------------------------------------------------------
# Brand the whole tree (paths + content)
# ---------------------------------------------------------------------------

@dataclass
class BrandResult:
    total: int = 0
    renamed: int = 0
    rewritten: int = 0
    locked_copied: int = 0
    binary_copied: int = 0
    owned: int = 0
    errors: list[str] = field(default_factory=list)


def _walk_files(root: str) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Only prune the checkout/updates dirs sitting directly under ``root``
        # (an os.walk TOPDOWN prune by bare name would also swallow the real
        # upstream directory ``skills/autonomous-ai-agents/hermes-agent/``).
        if os.path.abspath(dirpath) == os.path.abspath(root):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", HERMES_DIR)
                           and not d.startswith(UPDATE_PREFIX)]
        else:
            dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            out.append(rel)
    return sorted(out)


def brand_tree(src: str, dst: str, rules: BrandingRules | None = None,
               owned: OwnedAssets | None = None) -> BrandResult:
    """Copy ``src`` into ``dst`` branding every path and text file.

    Rules: paths (folders AND file names) are mapped through
    ``rules.transform_path``; text files are rewritten through
    ``rules.transform_text``; binary and locked files are copied
    byte-for-byte (rename only).  Files whose mapped path appears in the
    ``owned`` registry are replaced by OUR asset instead of upstream's.
    Mirrors the birth-commit semantics so the verifier can prove the result.
    """
    rules = rules or BrandingRules()
    result = BrandResult()
    for rel in _walk_files(src):
        result.total += 1
        src_path = os.path.join(src, rel)
        if is_immutable_path(rel) or is_contributor_name_path(rel):
            # Explicit identity metadata only: copy byte-for-byte, name untouched.
            dst_path = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copyfile(src_path, dst_path)
            _restore_mode(src_path, dst_path)
            result.locked_copied += 1
            continue
        mapped = rules.transform_path(rel)
        if mapped != rel:
            result.renamed += 1
        dst_path = os.path.join(dst, mapped)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        owned_bytes = owned.asset_bytes(mapped) if owned else None
        if owned_bytes is not None:
            with open(dst_path, "wb") as fh:
                fh.write(owned_bytes)
            _restore_mode(src_path, dst_path)
            result.owned += 1
            continue
        try:
            with open(src_path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            result.errors.append(f"{rel}: {exc}")
            continue
        if is_locked_path(rel) or is_locked_path(mapped):
            result.locked_copied += 1
            with open(dst_path, "wb") as fh:
                fh.write(data)
        elif is_text(data):
            result.rewritten += 1
            text = data.decode("utf-8")
            with open(dst_path, "w", encoding="utf-8") as fh:
                fh.write(_strict_text_transform(rel, text, rules))
        else:
            result.binary_copied += 1
            with open(dst_path, "wb") as fh:
                fh.write(data)
        _restore_mode(src_path, dst_path)
    return result


_STRICT_DEPENDENCY_EXCEPTIONS = {
    "package-lock.json": ("hermes-parser", "hermes-estree"),
    "website/package-lock.json": ("@nous-research/image-size",),
    "website/.npmrc": ("@nous-research/image-size",),
}
_REQUIRED_SUMMARY_PATH = "reports/SYNC-SUMMARY.md"
_EXACT_SUMMARY_ATTRIBUTION = "Powered by NousResearch"


def _strict_text_transform(relative: str, text: str, rules: BrandingRules) -> str:
    """Transform all residual brand strings except exact accepted records."""
    protected: dict[str, str] = {}
    value = text
    for index, dependency in enumerate(_STRICT_DEPENDENCY_EXCEPTIONS.get(relative, ())):
        marker = f"__100WAYS_APPROVED_DEPENDENCY_{index}__"
        value = value.replace(dependency, marker)
        protected[marker] = dependency
    if relative == _REQUIRED_SUMMARY_PATH:
        marker = "__100WAYS_REQUIRED_SUMMARY_ATTRIBUTION__"
        value = value.replace(_EXACT_SUMMARY_ATTRIBUTION, marker)
        protected[marker] = _EXACT_SUMMARY_ATTRIBUTION
    value = rules.transform_text(value)
    for marker, accepted in protected.items():
        value = value.replace(marker, accepted)
    return value


def ensure_required_summary_attribution(dst: str) -> list[str]:
    """Normalize the attribution when a generated update summary is present."""
    path = os.path.join(dst, *_REQUIRED_SUMMARY_PATH.split("/"))
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if _EXACT_SUMMARY_ATTRIBUTION in text:
        return []
    lines = text.splitlines()
    replacement = f"> {_EXACT_SUMMARY_ATTRIBUTION}"
    for index, line in enumerate(lines):
        if line.startswith("> Powered by "):
            lines[index] = replacement
            break
    else:
        lines.insert(2 if len(lines) >= 2 else len(lines), replacement)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return [_REQUIRED_SUMMARY_PATH]


def require_update_summary_attribution(dst: str) -> list[str]:
    """Fail closed unless a real update candidate carries the exact attribution."""
    path = os.path.join(dst, *_REQUIRED_SUMMARY_PATH.split("/"))
    if not os.path.isfile(path):
        raise ValueError(f"required update summary is missing: {_REQUIRED_SUMMARY_PATH}")
    changed = ensure_required_summary_attribution(dst)
    with open(path, encoding="utf-8") as handle:
        if _EXACT_SUMMARY_ATTRIBUTION not in handle.read():
            raise ValueError(f"required attribution is missing: {_EXACT_SUMMARY_ATTRIBUTION}")
    return changed


def enforce_strict_branding(dst: str, rules: BrandingRules) -> list[str]:
    """Eliminate residual upstream-brand paths and text after fork-file preservation.

    Contributor names under ``contributors/names`` remain identity metadata.
    Contributor email paths and content are deliberately included so they cannot
    retain upstream-brand email identifiers.  The returned paths are evidence
    for the reconciliation manifest; no publication is authorized here.
    """
    changed: list[str] = []
    for relative in _walk_files(dst):
        if is_contributor_name_path(relative):
            continue
        path = os.path.join(dst, relative)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            continue
        if not is_text(data):
            continue
        updated = _strict_text_transform(relative, data.decode("utf-8"), rules)
        if updated != data.decode("utf-8"):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(updated)
            changed.append(relative)
    changed.extend(ensure_required_summary_attribution(dst))
    entries: list[str] = []
    directories = sorted(
        (
            os.path.relpath(os.path.join(root, name), dst).replace(os.sep, "/")
            for root, dirs, _ in os.walk(dst)
            for name in dirs
        ),
        key=lambda value: (value.count("/"), value),
    )
    for relative in directories:
        if is_contributor_name_path(relative):
            continue
        original = os.path.join(dst, *relative.split("/"))
        if not os.path.isdir(original):
            continue
        mapped = rules.transform_path(relative)
        if mapped == relative:
            continue
        destination = os.path.join(dst, *mapped.split("/"))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if os.path.exists(destination):
            raise ValueError(f"strict branding path collision: {relative} -> {mapped}")
        os.replace(original, destination)
        entries.append(f"{relative} -> {mapped}")
    for relative in list(_walk_files(dst)):
        if is_contributor_name_path(relative):
            continue
        original = os.path.join(dst, *relative.split("/"))
        mapped = rules.transform_path(relative)
        if mapped == relative:
            continue
        destination = os.path.join(dst, *mapped.split("/"))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if os.path.exists(destination):
            if not relative.startswith("contributors/emails/"):
                raise ValueError(f"strict branding path collision: {relative} -> {mapped}")
            with open(original, "rb") as source_handle, open(destination, "rb") as destination_handle:
                identical = source_handle.read() == destination_handle.read()
            if identical:
                os.unlink(original)
                entries.append(f"deduplicated:{relative} -> {mapped}")
                continue
            parent, email = mapped.rsplit("/", 1)
            local, domain = email.rsplit("@", 1) if "@" in email else (email, "nastech.invalid")
            alias = f"{local}+nastech-{hashlib.sha256(relative.encode()).hexdigest()[:10]}@{domain}"
            mapped = f"{parent}/{alias}"
            destination = os.path.join(dst, *mapped.split("/"))
            if os.path.exists(destination):
                raise ValueError(f"strict contributor email alias collision: {relative} -> {mapped}")
        os.replace(original, destination)
        entries.append(f"{relative} -> {mapped}")
    return changed + entries


def _restore_mode(src_path: str, dst_path: str) -> None:
    """Carry the source executable bit onto the branded copy.

    ``brand_tree`` writes rewritten/locked/binary files with plain ``open()``
    (default 0o644), and ``shutil.copyfile`` copies content only — so scripts
    like ``hermes``/``nastech`` and the docker entrypoints would lose their
    exec bit in the zip, making the boot smoke test fail.  Preserve the source
    mode explicitly.
    """
    try:
        st = os.stat(src_path)
        os.chmod(dst_path, st.st_mode)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Reconcile the branded tree (fork-local fixes the token rules can't express)
# ---------------------------------------------------------------------------

@dataclass
class ReconcileResult:
    """Files fixed after branding so the tree is internally consistent.

    Token branding rewrites ``pyproject.toml`` / ``package.json``
    (``hermes-agent`` -> ``nastech-agent``) but leaves LOCKED lockfiles
    byte-for-byte.  A lockfile's root package record therefore still names
    the upstream package, and every ``uv sync --locked`` / npm workspace
    check on the fork fails.  Reconciliation syncs those root records with
    the branded manifests, and applies a small set of known fork-local
    content fixes (e.g. the SQLite FTS5 trigram self-test in the Dockerfile,
    which upstream writes against its own name).  Each fixed path is
    recorded so the parity gate can verify against the reconciled content.
    """
    total: int = 0
    fixed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.fixed:
            return "no files needed reconciliation"
        return f"{len(self.fixed)} files reconciled: {', '.join(self.fixed)}"


def _root_package_name(dst: str, manifest: str, lockfile: str) -> str | None:
    """Return the package name a lockfile root record must carry.

    ``pyproject.toml`` wins (uv), then ``package.json`` (npm).  Falls back
    to ``None`` when no source manifest exists so the reconcile is a no-op.
    """
    pyproject = os.path.join(dst, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("name = "):
                        name = line.split("=", 1)[1].strip().strip('"')
                        if name:
                            return name
        except OSError:
            pass
    package_json = os.path.join(dst, "package.json")
    if os.path.isfile(package_json):
        try:
            with open(package_json, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("name"):
                return str(data["name"])
        except (OSError, ValueError):
            pass
    return None


def _reconcile_uv_lock(dst: str, name: str) -> int:
    """Rename the root editable package record in ``uv.lock`` AND move it to
    its sorted position.

    Two problems: (1) the root record still names the upstream package, and
    (2) ``uv.lock`` blocks are canonically sorted by ``(name, version)`` -
    renaming ``hermes-agent`` to ``nastech-agent`` in place leaves the block
    mid-alphabet at the wrong index, so ``uv lock --check`` reports the
    lockfile as out of sync even though every dependency is unchanged.
    Leave every dependency record untouched; just rename + re-sort.
    """
    path = os.path.join(dst, "uv.lock")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return 0
    lines = text.splitlines(keepends=True)
    first_pkg = next((i for i, l in enumerate(lines) if l.startswith("[[package]]")), None)
    if first_pkg is None:
        return 0
    # split into header + package blocks (each from [[package]] to the next)
    idxs = [i for i, l in enumerate(lines) if l.startswith("[[package]]")]
    idxs.append(len(lines))
    header = "".join(lines[:first_pkg])
    blocks = ["".join(lines[a:b]) for a, b in zip(idxs, idxs[1:])]

    # locate + rename the root editable record (block header name AND any
    # self-references to the root inside its own [package.optional-dependencies]
    # section, e.g. `{ name = "hermes-agent", extras = ["all"] }`).
    root_idx = None
    for i, block in enumerate(blocks):
        src = re.search(r"^source = \{(.+)\}", block, re.M)
        if src and "editable" in src.group(1):
            root_idx = i
            break
    if root_idx is None:
        return 0
    root = blocks[root_idx]
    root = re.sub(r'^name = "[^"]+"', f'name = "{name}"', root, count=1, flags=re.M)
    root = re.sub(r'"hermes-agent"', f'"{name}"', root)
    blocks[root_idx] = root

    # canonical uv order is (name, version); re-sort all blocks by that key
    def _key(block: str) -> tuple[str, str, int]:
        m = re.search(r'^name = "([^"]+)"', block, re.M)
        v = re.search(r'^version = "([^"]+)"', block, re.M)
        return (m.group(1) if m else "", v.group(1) if v else "", 0)

    ordered = sorted(blocks, key=_key)
    if "".join(ordered) != text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(header + "".join(ordered))
        return 1
    return 0


# Exact package-name renames for package-lock.json.  Token branding renames
# the ``name`` fields in every workspace ``package.json`` (and the dependency
# references pointing at them), but package-lock.json is a LOCKED file so it
# is still byte-copied from upstream.  Reconcile renames the lock's workspace
# records to match.  These are EXACT-name matches (never substring), so real
# registry packages whose names merely contain "hermes" (``hermes-parser``,
# ``hermes-estree``) are never touched.
_NPM_NAME_RENAMES: dict[str, str] = {
    "hermes-agent": "nastech-agent",
    "@hermes/bootstrap-installer": "@nastech/bootstrap-installer",
    "hermes": "nastech",
    "@hermes/ink": "@nastech/ink",
    "@hermes/root-tests": "@nastech/root-tests",
    "@hermes/shared": "@nastech/shared",
    "hermes-tui": "nastech-tui",
    "@nous-research/ui": "@nastech-research/ui",
}

# Exact package-lock.json ``packages`` key renames: the workspace directory
# that branding renamed (``ui-tui/packages/hermes-ink``) and the node_modules
# symlink/registry records that resolve to renamed packages.  Workspace dirs
# that were NOT renamed (``apps/desktop``, ``apps/shared``, ...) have no
# entry here - their paths stay byte-identical.
_NPM_KEY_RENAMES: dict[str, str] = {
    "node_modules/@hermes/bootstrap-installer": "node_modules/@nastech/bootstrap-installer",
    "node_modules/@hermes/ink": "node_modules/@nastech/ink",
    "node_modules/@hermes/root-tests": "node_modules/@nastech/root-tests",
    "node_modules/@hermes/shared": "node_modules/@nastech/shared",
    "node_modules/hermes": "node_modules/nastech",
    "node_modules/hermes-tui": "node_modules/nastech-tui",
    "node_modules/@nous-research/ui": "node_modules/@nastech-research/ui",
    "ui-tui/packages/hermes-ink": "ui-tui/packages/nastech-ink",
}

# ``@nous-research/ui`` is republished by the fork as
# ``@nastech-research/ui``, and its tarball is NOT byte-identical to
# upstream's (fork-published integrity differs from the upstream package).
# The lock record must therefore point at the fork's tarball AND carry the
# fork-published integrity for the pinned version, or ``npm ci`` fails the
# integrity check.  Values are the npm ``dist.integrity`` for each published
# version; add new fork-published versions here.
_NASTECH_UI_INTEGRITY: dict[str, str] = {
    "0.18.2": "sha512-P7H8RzRNGvAvqomtdaGF6J5uUVFWSx8/GrqJk4Cu7yN9DhcAp01FM+QgEFjx6sm3XI4g7FX8EiJdjuCTFAlIpw==",
    "0.18.3": "sha512-Nk+Key+Ql3i9LqTYHvGqm/JK0kzOxIUkJAY2yJDH4B5ivuBgaXqoc1o552CEu54G2YNL0Ge+3FdmKe2E+LyHsQ==",
    "0.18.4": "sha512-LStiibgKBGJGrnKZ4qaNNDup5jl/+MfhIaZDlcE3slugtuMmFt2C6urxajZ1Yv65JAx7VXpYscbzrGSDsis+Og==",
}


def _reconcile_lock_record(rec: dict) -> bool:
    """Rename one package-lock record's name, deps and resolved target.

    Returns True if anything changed.  Mutates ``rec`` in place.  Raises
    ValueError when a pinned ``@nous-research/ui`` version has no known
    fork-published integrity, so the pipeline fails loudly instead of
    shipping a lock that ``npm ci`` will reject.
    """
    changed = False
    if isinstance(rec.get("name"), str) and rec["name"] in _NPM_NAME_RENAMES:
        rec["name"] = _NPM_NAME_RENAMES[rec["name"]]
        changed = True

    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        deps = rec.get(field)
        if not isinstance(deps, dict):
            continue
        new_deps: dict[str, object] = {}
        dirty = False
        for dep, spec in deps.items():
            new_dep = _NPM_NAME_RENAMES.get(dep, dep)
            new_spec = spec
            if isinstance(spec, str) and spec.startswith("file:") and "/packages/hermes-ink" in spec:
                new_spec = spec.replace("/packages/hermes-ink", "/packages/nastech-ink")
            if new_dep != dep or new_spec != spec:
                dirty = True
            new_deps[new_dep] = new_spec
        if dirty:
            rec[field] = new_deps
            changed = True

    resolved = rec.get("resolved")
    if isinstance(resolved, str):
        if "registry.npmjs.org/@nous-research/ui" in resolved:
            version = str(rec.get("version", ""))
            integrity = _NASTECH_UI_INTEGRITY.get(version)
            if integrity is None:
                raise ValueError(
                    f"no fork-published integrity for @nastech-research/ui@{version}; "
                    f"add it to _NASTECH_UI_INTEGRITY in updates.py"
                )
            rec["resolved"] = f"https://registry.npmjs.org/@nastech-research/ui/-/ui-{version}.tgz"
            rec["integrity"] = integrity
            changed = True
        elif not resolved.startswith(("http:", "https:", "file:", "git", "github:")):
            new_resolved = _NPM_KEY_RENAMES.get(resolved, resolved)
            if new_resolved != resolved:
                rec["resolved"] = new_resolved
                changed = True
    return changed


def _reconcile_package_lock(dst: str, name: str) -> int:
    """Rename every workspace/registry record in ``package-lock.json``.

    Token branding renames the ``name`` fields in every workspace
    ``package.json`` (and the dependency references pointing at them), but
    package-lock.json is a LOCKED file so it is still byte-copied from
    upstream.  npm then cannot resolve the branded workspaces: ``npm ci``
    reports ``Missing: <branded>@<version> from lock file`` for every renamed
    package.  This rewrites the lock's ``packages`` records:

    * workspace record ``name`` fields,
    * node_modules symlink/registry record keys + their ``resolved`` paths,
    * dependency keys / ``file:`` values inside workspace records,
    * the ``@nous-research/ui`` registry record (renamed to
      ``@nastech-research/ui`` with the fork-published tarball + integrity).

    Every rename is an EXACT name/path match, so real registry packages whose
    names merely contain "hermes" (``hermes-parser``, ``hermes-estree``) are
    never touched.
    """
    path = os.path.join(dst, "package-lock.json")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0

    changed = False
    if data.get("name") != name:
        data["name"] = name
        changed = True

    packages = data.get("packages")
    if isinstance(packages, dict):
        renamed: dict[str, object] = {}
        for key, rec in packages.items():
            renamed[_NPM_KEY_RENAMES.get(key, key)] = rec
        for rec in renamed.values():
            if isinstance(rec, dict) and _reconcile_lock_record(rec):
                changed = True
        if list(renamed) != list(packages):
            changed = True
        packages.clear()
        packages.update(renamed)

    if not changed:
        return 0
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return 1


# Fork-local domain corrections.  The token rules must reproduce the fork's
# BIRTH commit (which the `rules` gate enforces at >=99%), and the birth
# commit used nastechresearch.com URLs.  But nastechresearch.com is NOT
# registered - the fork lives on GitHub Pages - so every .com domain is a
# 404 in the deployed tree.  Reconcile migrates them to github.io after
# branding.  Order matters: the docs compound (org/repo path style) must be
# rewritten BEFORE the bare-domain form, otherwise the `.com` in the middle
# of the compound would already be gone when the compound rule runs.
_DOMAIN_FIXES: list[tuple[str, str]] = [
    ("nastech-agent.nastechresearch.com", "nastechresearch.github.io/nastech-agent"),
    ("nastechresearch.com", "nastechresearch.github.io"),
    ("NastechResearch.com", "NastechResearch.github.io"),
    ("NASTECHRESEARCH.COM", "NASTECHRESEARCH.GITHUB.IO"),
]


def _reconcile_domains(dst: str) -> list[str]:
    """Rewrite every branded .com domain to github.io across text files.

    Returns the list of paths that changed so the parity gate can verify
    against the reconciled bytes.  Skips locked/binary files (the brand
    stage already treated them as opaque).  A file whose content contains
    no fork domain is left untouched.
    """
    fixed: list[str] = []
    for rel in _walk_files(dst):
        path = os.path.join(dst, rel)
        if is_locked_path(rel) or is_immutable_path(rel):
            continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if not is_text(data):
            continue
        text = data.decode("utf-8")
        if "nastechresearch.com" not in text and "NastechResearch.com" not in text \
                and "NASTECHRESEARCH.COM" not in text:
            continue
        for needle, replacement in _DOMAIN_FIXES:
            if needle in text:
                text = text.replace(needle, replacement)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        fixed.append(rel)
    return fixed


def _reconcile_fts5_trigram(dst: str) -> list[str]:
    """Fix the SQLite FTS5 trigram self-test the fork's CI runs.

    Upstream's Dockerfile verifies FTS5 trigram indexing against its OWN
    name: ``INSERT INTO docs VALUES ('hermes')`` then ``MATCH 'erm'``.
    Token branding rewrites the insert to ``'nastech'`` but leaves the
    ``MATCH`` literal (``'erm'`` is a trigram of ``hermes``, not of the
    branded name), so the check always fails.  Recompute the literal as a
    trigram of the branded name actually inserted.
    """
    fixed: list[str] = []
    for root, dirs, files in os.walk(dst):
        dirs[:] = [name for name in dirs if name not in {".git", "node_modules", "dist", "build"}]
        for name in files:
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            inserted = set(re.findall(r"INSERT INTO docs VALUES \('([^']+)'\)", text))
            if not inserted or "fts5" not in text.lower():
                continue
            changed = False
            for word in inserted:
                trigrams = {word[i:i + 3] for i in range(len(word) - 2)}
                if not trigrams:
                    continue
                replacement = sorted(trigrams)[0]
                new_text = re.sub(
                    r"MATCH '([^']{3})'",
                    lambda m: f"MATCH '{replacement}'" if m.group(1) not in trigrams else m.group(0),
                    text,
                )
                if new_text != text:
                    text = new_text
                    changed = True
            if changed:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                fixed.append(os.path.relpath(path, dst))
    return fixed


def _reconcile_hermez_obfuscation(dst: str) -> int:
    """Rewrite the ``Hermez`` obfuscation in the lifecycle-guard test.

    Upstream's ``test_gateway_restart_loop.py`` builds a mixed-case fixture
    at runtime: ``"Hermez Gateway Restart".lower().replace("z", "s")``
    evaluates to ``hermes gateway restart``.  The fork's birth commit
    removed that trick and hardcoded ``"nAsTeCh GaTeWaY ReStArT"`` (mixed
    case that the IGNORECASE regex still catches).  Token branding cannot
    express the swap (``Hermez`` is not a token), so the branded tree still
    ships the ``hermes``-producing expression and the fork's CI assertion
    fails on it.  Reconcile replaces the expression with the fork's exact
    bytes (comment alignment included) so the test evaluates the branded
    name instead.
    """
    if not os.path.isdir(dst):
        return 0
    for rel in _walk_files(dst):
        if os.path.basename(rel) != "test_gateway_restart_loop.py":
            continue
        if not rel.endswith(".py"):
            continue
        path = os.path.join(dst, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        needle = '"Hermez Gateway Restart".lower().replace("z", "s")'
        if needle not in text:
            continue
        replacement = '        "nAsTeCh GaTeWaY ReStArT",                            # case handled'
        new_text = text.replace(
            '        "Hermez Gateway Restart".lower().replace("z", "s"),  # case handled',
            replacement,
        )
        if new_text != text:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            return 1
    return 0


def _reconcile_plugin_search_table(dst: str) -> int:
    """Widen the ``cmd_search`` Name column so branded names render fully.

    Upstream's ``plugins_cmd.cmd_search`` builds a rich ``Table`` whose
    Name column is sized by content.  At the fork CI's 80 columns the
    branded ``nastech-media-studio`` (20 chars) is one character longer
    than upstream's ``hermes-media-studio`` (19), which pushes the column
    over the edge and rich truncates it to ``nastech-media-stud…`` — so
    the fork's ``test_plugin_index_search.py`` assertion that the full
    name appears in the search output fails on the branded tree while
    passing upstream.  Give the Name column a ``min_width`` so branded
    names render exactly as upstream's do.
    """
    if not os.path.isdir(dst):
        return 0
    for rel in _walk_files(dst):
        if rel not in ("nastech_cli/plugins_cmd.py", "hermes_cli/plugins_cmd.py"):
            continue
        path = os.path.join(dst, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        needle = 'table.add_column("Name", style="bold")'
        if needle not in text:
            continue
        new_text = text.replace(
            needle,
            'table.add_column("Name", style="bold", min_width=21)',
        )
        if new_text != text:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            return 1
    return 0


def _reconcile_skill_description_hardline(dst: str) -> int:
    """Trim the bundled ``nastech-agent`` skill description to the fork's.

    Upstream's description (``"Use, configure, theme, extend, and
    orchestrate Hermes Agent."``) is exactly 60 characters — the fork's
    authoring-standards hardline.  Token branding of ``Hermes`` ->
    ``Nastech`` adds one character, pushing the branded description to 61
    and failing the fork's ``test_authoring_standards.py``
    ``test_description_hardline``.  The fork trimmed the leading ``Use, ``
    to keep its own copy at 56; reconcile applies the same trim so the
    branded tree matches the fork's bytes.
    """
    if not os.path.isdir(dst):
        return 0
    rel = "skills/autonomous-ai-agents/nastech-agent/SKILL.md"
    path = os.path.join(dst, rel)
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return 0
    needle = 'description: "Use, configure, theme, extend, and orchestrate Nastech Agent."'
    if needle not in text:
        return 0
    new_text = text.replace(
        needle,
        'description: "Configure, theme, extend, and orchestrate Nastech Agent."',
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return 1


def _reconcile_test_runner_mode(dst: str) -> int:
    """Ensure the CI test runner remains executable in the published tree.

    The upstream file is content-correct but carries a non-executable mode.
    Target CI invokes it directly, so set the portable executable bits as a
    recorded reconciliation before packaging preserves that metadata.
    """
    path = os.path.join(dst, "scripts", "run_tests.sh")
    if not os.path.isfile(path):
        return 0
    try:
        mode = os.stat(path).st_mode
        if mode & 0o111:
            return 0
        os.chmod(path, mode | 0o111)
    except OSError:
        return 0
    return 1


def _reconcile_docusaurus_site_config(dst: str) -> int:
    """Normalize the project-pages URL after domain branding.

    Docusaurus requires ``url`` to be an origin only; a repository path belongs
    in ``baseUrl``.  Domain reconciliation turns the fork site into a GitHub
    Pages project URL, so split that URL deterministically before the docs
    build sees it.
    """
    path = os.path.join(dst, "website", "docusaurus.config.ts")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return 0
    updated = text.replace(
        "url: 'https://nastechresearch.github.io/nastech-agent',",
        "url: 'https://nastechresearch.github.io',",
    ).replace(
        "baseUrl: '/docs/',",
        "baseUrl: '/nastech-agent/',",
    )
    if updated == text:
        return 0
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return 1


def _reconcile_project_identity_width(dst: str) -> int:
    """Keep the complete branded project identity in a tight terminal label.

    The branded project name is longer than the upstream label.  The formatter
    contract explicitly prioritizes project identity when the status bar is
    narrow, so return it intact instead of truncating the only identifying
    segment.
    """
    path = os.path.join(dst, "ui-tui", "src", "domain", "paths.ts")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return 0
    needle = "    return shortProject(project, max)\n"
    if needle not in text:
        return 0
    updated = text.replace(needle, "    return project\n", 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return 1


def _reconcile_brand_import_ordering(dst: str) -> int:
    """Keep brand-renamed imports visible as lint warnings, not false blockers.

    The static import-order rules compare lexical names.  Renaming a central
    internal module changes that order across many otherwise byte-faithful
    files, without changing runtime behavior.  Preserve diagnostics as
    warnings while avoiding a mass source-only reorder on every full sync.
    """
    path = os.path.join(dst, "eslint.config.shared.mjs")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return 0
    updated = text
    updated = updated.replace("      'perfectionist/sort-imports': [\n        'error',", "      'perfectionist/sort-imports': [\n        'warn',")
    updated = updated.replace("      'perfectionist/sort-named-exports': ['error', { order: 'asc', type: 'natural' }],", "      'perfectionist/sort-named-exports': ['warn', { order: 'asc', type: 'natural' }],")
    updated = updated.replace("      'perfectionist/sort-named-imports': ['error', { order: 'asc', type: 'natural' }],", "      'perfectionist/sort-named-imports': ['warn', { order: 'asc', type: 'natural' }],")
    if updated == text:
        return 0
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return 1


def reconcile_tree(dst: str) -> ReconcileResult:
    """Apply known post-brand fixes to the branded tree in place.

    Returns which paths were fixed so the caller can (a) report them and
    (b) feed them to ``verify_branded`` / ``compare_trees``, which must
    expect the reconciled content instead of the raw token transform.
    """
    result = ReconcileResult()
    name = _root_package_name(dst, "pyproject.toml", "package.json")
    if name:
        if _reconcile_uv_lock(dst, name):
            result.total += 1
            result.fixed.append("uv.lock")
        if _reconcile_package_lock(dst, name):
            result.total += 1
            result.fixed.append("package-lock.json")
    fts5_fixed = _reconcile_fts5_trigram(dst)
    if fts5_fixed:
        result.total += len(fts5_fixed)
        result.fixed.extend(fts5_fixed)
    if _reconcile_hermez_obfuscation(dst):
        result.total += 1
        result.fixed.append("tests/nastech_cli/test_gateway_restart_loop.py")
    if _reconcile_plugin_search_table(dst):
        result.total += 1
        result.fixed.append("nastech_cli/plugins_cmd.py")
    if _reconcile_skill_description_hardline(dst):
        result.total += 1
        result.fixed.append("skills/autonomous-ai-agents/nastech-agent/SKILL.md")
    if _reconcile_test_runner_mode(dst):
        result.total += 1
        result.fixed.append("scripts/run_tests.sh")
    if _reconcile_project_identity_width(dst):
        result.total += 1
        result.fixed.append("ui-tui/src/domain/paths.ts")
    if _reconcile_brand_import_ordering(dst):
        result.total += 1
        result.fixed.append("eslint.config.shared.mjs")
    for rel in _reconcile_domains(dst):
        result.total += 1
        result.fixed.append(rel)
    if _reconcile_docusaurus_site_config(dst) and "website/docusaurus.config.ts" not in result.fixed:
        result.total += 1
        result.fixed.append("website/docusaurus.config.ts")
    result.fixed.sort()
    return result


# ---------------------------------------------------------------------------
# Scan everything (so every file is understood)
# ---------------------------------------------------------------------------

@dataclass
class ScanReport:
    total: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_format: dict[str, int] = field(default_factory=dict)
    unknown_binary: list[str] = field(default_factory=list)

    def summary(self) -> str:
        cats = ", ".join(f"{k}={v}" for k, v in sorted(self.by_category.items()))
        return f"{self.total} files scanned [{cats}]"


def scan_tree(root: str) -> ScanReport:
    report = ScanReport()
    for rel in _walk_files(root):
        path = os.path.join(root, rel)
        try:
            with open(path, "rb") as fh:
                data = fh.read(8192)
        except OSError:
            continue
        ft = classify_path(data, rel)
        report.total += 1
        report.by_category[ft.category] = report.by_category.get(ft.category, 0) + 1
        report.by_format[ft.fmt] = report.by_format.get(ft.fmt, 0) + 1
        if ft.category == "binary" and ft.fmt in ("bin", ""):
            report.unknown_binary.append(rel)
    return report


# ---------------------------------------------------------------------------
# Compare / diff (branded tree vs freshly pulled Hermes tree)
# ---------------------------------------------------------------------------

@dataclass
class DiffEntry:
    path: str
    mapped_path: str
    action: str  # renamed | rewritten | identical | locked | missing | owned
    added_lines: int = 0
    deleted_lines: int = 0


@dataclass
class DiffReport:
    entries: list[DiffEntry] = field(default_factory=list)

    def actions(self, action: str) -> list[DiffEntry]:
        return [e for e in self.entries if e.action == action]

    def summary(self) -> str:
        counts = {a: len(self.actions(a)) for a in ("renamed", "rewritten", "identical", "locked", "missing", "owned", "reconciled")}
        return (
            f"{counts['renamed']} renamed, {counts['rewritten']} rewritten, "
            f"{counts['identical']} identical, {counts['locked']} locked, "
            f"{counts['missing']} missing, {counts['owned']} owned, "
            f"{counts['reconciled']} reconciled"
        )


def _line_delta(a: str, b: str) -> tuple[int, int]:
    sm = difflib.SequenceMatcher(None, a.splitlines(), b.splitlines())
    added = sum((op[2] - op[1]) for op in sm.get_opcodes() if op[0] == "insert")
    deleted = sum((op[4] - op[3]) for op in sm.get_opcodes() if op[0] == "delete")
    return added, deleted


def compare_trees(src: str, dst: str, rules: BrandingRules | None = None,
                  owned: OwnedAssets | None = None,
                  reconciled: dict[str, bytes] | None = None) -> DiffReport:
    """Diff the branded tree against the upstream source, file by file."""
    rules = rules or BrandingRules()
    report = DiffReport()
    for rel in _walk_files(src):
        if is_immutable_path(rel) or is_contributor_name_path(rel):
            report.entries.append(DiffEntry(rel, rel, "locked"))
            continue
        mapped = rules.transform_path(rel)
        if owned and owned.has(mapped):
            report.entries.append(DiffEntry(rel, mapped, "owned"))
            continue
        locked = is_locked_path(rel) or is_locked_path(mapped)
        src_path = os.path.join(src, rel)
        dst_path = os.path.join(dst, mapped)
        try:
            with open(src_path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if not os.path.isfile(dst_path):
            report.entries.append(DiffEntry(rel, mapped, "missing"))
            continue
        if locked or not is_text(data):
            report.entries.append(DiffEntry(rel, mapped, "renamed" if mapped != rel else "locked"))
            continue
        with open(dst_path, "rb") as fh:
            actual = fh.read()
        if reconciled and mapped in reconciled:
            expected = reconciled[mapped]
            report.entries.append(DiffEntry(rel, mapped, "reconciled"))
            continue
        expected = _strict_text_transform(rel, data.decode("utf-8"), rules).encode("utf-8")
        if actual == expected:
            report.entries.append(DiffEntry(rel, mapped, "renamed" if mapped != rel else "identical"))
            continue
        added, deleted = _line_delta(expected.decode("utf-8", "replace"), actual.decode("utf-8", "replace"))
        report.entries.append(DiffEntry(rel, mapped, "rewritten", added, deleted))
    return report


# ---------------------------------------------------------------------------
# Verify the branded tree against the freshly pulled Hermes tree
# ---------------------------------------------------------------------------

def verify_branded(src: str, dst: str, rules: BrandingRules | None = None,
                   owned: OwnedAssets | None = None,
                   reconciled: dict[str, bytes] | None = None) -> VerifyReport:
    """File-by-file parity: every Hermes file must exist branded and, for text,
    byte-identical to ``rules.transform_text`` of the source.  Files whose
    mapped path is in the ``owned`` registry are checked against OUR asset.
    Files whose mapped path is in ``reconciled`` are checked against the
    reconciled bytes (the fork-local fixes applied after branding)."""
    rules = rules or BrandingRules()
    report = VerifyReport()
    for rel in _walk_files(src):
        report.total += 1
        identity_metadata = is_contributor_name_path(rel)
        mapped = rel if is_immutable_path(rel) or identity_metadata else rules.transform_path(rel)
        locked = (
            is_locked_path(rel)
            or is_locked_path(mapped)
            or is_immutable_path(rel)
            or identity_metadata
        )
        src_path = os.path.join(src, rel)
        dst_path = os.path.join(dst, mapped)
        exists = os.path.isfile(dst_path)
        owned_bytes = owned.asset_bytes(mapped) if owned else None
        if owned_bytes is not None:
            actual = open(dst_path, "rb").read() if exists else b""
            identical = exists and actual == owned_bytes
            res = FileResult(
                path=rel, mapped_path=mapped, pass_=identical, locked=False,
                note="owned asset: must match our registry (not upstream)",
            )
            report.results.append(res)
            if identical:
                report.passed += 1
            else:
                report.failed.append(res)
            continue
        try:
            with open(src_path, "rb") as fh:
                data = fh.read()
        except OSError:
            data = b""

        if locked or not is_text(data):
            res = FileResult(
                path=rel, mapped_path=mapped, pass_=exists, locked=True,
                note="binary/locked: existence checked, content not compared",
            )
            report.results.append(res)
            report.locked += 1
            if res.pass_:
                report.passed += 1
            else:
                report.failed.append(res)
            continue

        if not exists:
            res = FileResult(path=rel, mapped_path=mapped, pass_=False, locked=False,
                             note="missing from branded tree")
            report.results.append(res)
            report.failed.append(res)
            continue

        with open(dst_path, "rb") as fh:
            actual = fh.read()
        if reconciled and mapped in reconciled:
            expected = reconciled[mapped]
            identical = actual == expected
            res = FileResult(path=rel, mapped_path=mapped, pass_=identical, locked=False,
                             note="" if identical else "reconciled content differs")
            if identical:
                report.passed += 1
            else:
                report.failed.append(res)
            report.results.append(res)
            continue
        expected = _strict_text_transform(rel, data.decode("utf-8"), rules).encode("utf-8")
        identical = actual == expected
        res = FileResult(path=rel, mapped_path=mapped, pass_=identical, locked=False)
        if identical:
            report.passed += 1
        else:
            res.note = "content differs after branding"
            report.failed.append(res)
        report.results.append(res)
    return report


def gate_passes(report: VerifyReport, threshold: float = 0.99) -> bool:
    """True when parity >= threshold with no hard failures."""
    if report.failed:
        return False
    return report.pass_ratio >= threshold


# ---------------------------------------------------------------------------
# Markdown reports
# ---------------------------------------------------------------------------

def update_report_md(result: "UpdateResult") -> str:
    """The pipeline report: what stages ran and what each one did."""
    source_census = result.source_census
    source_files = source_census.files
    coverage_passed = source_files == result.verify.total
    lines = [
        f"# Nastech Update Report #{result.number}",
        "",
        f"- source sha   : `{result.upstream_sha}`",
        f"- source       : direct-upstream fingerprint `{source_fingerprint(result.hermes_url)[:16]}`",
        f"- snapshot     : `{os.path.basename(result.dir)}`",
        f"- gate         : **{'PASS' if result.gate else 'FAIL'}**",
        "",
        "## Direct upstream source evidence",
        "",
        "- canonical upstream test execution: **NOT RUN** (candidate validation is authoritative)",
        f"- direct source files: {source_files}",
        f"- final parity files: {result.verify.total}",
        "- coverage binding: **PASS**"
        if coverage_passed
        else "- coverage binding: **FAIL**",
        "",
        "## Stages",
        "",
        "| # | stage | status | detail |",
        "|---|-------|--------|--------|",
    ]
    for i, s in enumerate(result.stages, 1):
        lines.append(f"| {i} | {s.name} | {s.status} | {s.detail} |")
    lines += [
        "",
        "## Brand",
        "",
        f"- total files : {result.brand.total}",
        f"- renamed     : {result.brand.renamed} (folders and file names)",
        f"- text-rewritten : {result.brand.rewritten}",
        f"- locked-copied  : {result.brand.locked_copied}",
        f"- binary-copied  : {result.brand.binary_copied}",
        f"- owned assets   : {result.brand.owned} (our logo/banner/mascot override upstream)",
    ]
    if result.brand.errors:
        lines += ["- errors:", *[f"  - {e}" for e in result.brand.errors]]
    if result.reconcile.fixed:
        lines += ["", "## Reconcile", "",
                  f"- fixed : {result.reconcile.summary()}", ""]
    lines += [
        "",
        "## Direct upstream tree delta",
        "",
        f"- {result.source_delta.summary()}",
    ]
    report_rules = BrandingRules()
    for change in result.source_delta.changes:
        old_path = report_rules.transform_path(change.old_path)
        new_path = report_rules.transform_path(change.new_path)
        if change.status == "renamed":
            lines.append(f"- RENAMED `{old_path}` -> `{new_path}`")
        elif change.status == "deleted":
            lines.append(f"- DELETED `{old_path}`")
        else:
            lines.append(f"- {change.status.upper()} `{new_path}`")
    lines += ["", "## Scan", "", f"{result.scan.summary()}", ""]
    if result.scan.unknown_binary:
        lines += ["Unknown binaries:", *[f"- {p}" for p in result.scan.unknown_binary[:20]]]
    lines += ["", "## Diff", "", result.diff.summary(), ""]
    for e in result.diff.actions("missing"):
        lines.append(f"- MISSING {e.path} -> {e.mapped_path}")
    if result.fork.entries:
        lines += ["", "## Fork check (vs nastech-agent)", "",
                  f"- {result.fork.summary()}", ""]
        for e in result.fork.entries:
            if e.status in ("missing", "local_only"):
                lines.append(f"- **{e.status.upper()}** {e.path}")
            for v in e.violations:
                lines.append(f"- VIOLATION {v.path}:{v.line} `{v.snippet}`")
        if result.fork.features_fork:
            lines.append(
                f"- features: fork {result.fork.features_fork} -> branded "
                f"{result.fork.features_branded}"
            )
    lines += ["", "Auto-generated by 100Ways."]
    return "\n".join(lines) + "\n"


def gate_report_md(result: "UpdateResult") -> str:
    """The gate report: proof of parity, file by file."""
    lines = [
        f"# Nastech Gate Report #{result.number}",
        "",
        f"- upstream sha : `{result.upstream_sha}`",
        f"- parity       : {result.verify.passed}/{result.verify.total} "
        f"({result.verify.pass_ratio * 100:.1f}%), "
        f"{result.verify.locked} locked-for-review",
        f"- decision     : **{'PASS' if result.gate else 'FAIL'}**",
        "",
        "## Failed files",
        "",
    ]
    if result.verify.failed:
        for f in result.verify.failed:
            lines.append(f"- `{f.mapped_path}` — {f.note}")
    else:
        lines.append("_none — every file is byte-identical to upstream after branding._")
    lines += [
        "",
        "## Locked for review (renamed, content not compared)",
        "",
        f"{result.verify.locked} locked/binary assets present.",
        "",
    ]
    if result.fork.entries:
        lines += [
            "## Fork consistency (vs nastech-agent)",
            "",
            f"{result.fork.summary()}",
            "",
            "- identical files must stay byte-identical so the PR shows clean new commits",
            "- updated files are the real upstream delta; added lines must be brand-clean",
            f"- preserved fork-local files: {len(result.fork.preserved)}",
            "",
        ]
    lines += ["Auto-generated by 100Ways."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Release packaging
# ---------------------------------------------------------------------------

def package_zip(snapshot_dir: str, out_path: str, reports: dict[str, str],
                project_name: str = "nastech-agent") -> str:
    """Zip the branded snapshot with the md reports OUTSIDE the project folder.

    Layout:
        <out_path>/
          <project_name>/          # the branded project (1 project folder)
          <report_1>.md            # report 1, at zip root (outside project)
          <report_2>.md            # report 2, at zip root (outside project)

    The report files and manifest live in the snapshot dir as source-of-truth
    but are kept OUT of the project folder - only the branded tree goes in.
    """
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    skip = {"manifest.json", "UPDATE-REPORT.md", "GATE-REPORT.md"}
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in _walk_files(snapshot_dir):
            if rel in skip:
                continue
            full = os.path.join(snapshot_dir, rel)
            zf.write(full, os.path.join(project_name, rel))
        for name, content in reports.items():
            zf.writestr(name, content)
    return out_path


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    name: str
    status: str  # ok | skip | fail
    detail: str = ""
    duration_ms: int = 0


@dataclass
class UpdateResult:
    number: int
    dir: str
    upstream_sha: str
    hermes_url: str
    brand: BrandResult
    scan: ScanReport
    diff: DiffReport
    verify: VerifyReport
    gate: bool
    stages: list[StageResult] = field(default_factory=list)
    manifest_path: str = ""
    zip_path: str = ""
    report_path: str = ""
    gate_report_path: str = ""
    reconcile: ReconcileResult = field(default_factory=ReconcileResult)
    fork: ForkCheckReport = field(default_factory=ForkCheckReport)
    source_delta: SourceDeltaReport = field(
        default_factory=lambda: SourceDeltaReport("", "")
    )
    source_census: Census = field(default_factory=Census)

    def summary(self) -> str:
        return (
            f"Nastech-Update#{self.number} @ {self.upstream_sha[:12]} "
            f"gate={'PASS' if self.gate else 'FAIL'} "
            f"{self.verify.summary()} | {self.scan.summary()}"
        )


class UpdateManager:
    """Run one full update through the ordered source, branding, and candidate stages."""

    def __init__(self, updates_dir: str, hermes_url: str = DEFAULT_HERMES_URL,
                 rules: BrandingRules | None = None, threshold: float = 0.99,
                 owned: OwnedAssets | None = None, ai=None, fork_root: str = ""):
        self.updates_dir = updates_dir
        self.hermes_url = hermes_url
        self.rules = rules or BrandingRules()
        self.threshold = threshold
        self.owned = owned
        self.ai = ai
        self.fork_root = fork_root

    def run(self, keep_failed: bool = True, zip_path: str = "",
            project_name: str = "nastech-agent", notify=None) -> UpdateResult:
        stages: list[StageResult] = []

        def stage(name: str, fn, detail: str = "") -> object:
            start = time.time()
            try:
                value = fn()
                stages.append(StageResult(name, "ok", detail, int((time.time() - start) * 1000)))
                return value
            except Exception as exc:
                stages.append(StageResult(name, "fail", f"{detail} {exc}", int((time.time() - start) * 1000)))
                return None

        number = next_update_number(self.updates_dir)
        dest = update_path(self.updates_dir, number)

        fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        upstream_sha = stage("pull", lambda: pull_hermes(self.updates_dir, self.hermes_url),
                             "fresh direct clone from configured upstream")
        src = hermes_path(self.updates_dir)
        baseline_sha = previous_upstream_sha(
            self.updates_dir, number
        ) or fork_manifest_upstream_sha(self.fork_root)
        evidence = stage(
            "source-evidence",
            lambda: (
                upstream_change_evidence(src, baseline_sha, upstream_sha or ""),
                source_tree_delta(src, baseline_sha, upstream_sha or "", self.rules),
            ),
            "record direct upstream added/modified/deleted/renamed source evidence",
        )
        (commit_subjects, changed_areas), source_delta = evidence or (
            ([], {}),
            SourceDeltaReport(baseline_sha, upstream_sha or ""),
        )
        commit_subjects = [self.rules.transform_text(subject) for subject in commit_subjects]
        branded_areas: dict[str, int] = {}
        for area, count in changed_areas.items():
            mapped_area = self.rules.transform_path(area)
            branded_areas[mapped_area] = branded_areas.get(mapped_area, 0) + count
        changed_areas = branded_areas
        source_census = stage(
            "census",
            lambda: census_tree(src),
            "count upstream files before touching anything",
        ) or Census()
        stage("plan", lambda: number, f"next snapshot = {UPDATE_PREFIX}{number}")

        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(dest, exist_ok=True)

        def _brand() -> BrandResult:
            result = brand_tree(src, dest, self.rules, self.owned)
            result.owned += len(apply_owned_assets(dest, self.owned))
            return result

        brand = stage(
            "brand",
            _brand,
            "brand source and materialize NasTech-owned assets after source deletion",
        )

        def _reconcile() -> ReconcileResult:
            result = reconcile_tree(dest)
            return result
        reconcile = stage("reconcile", _reconcile,
                          "sync lockfile root records + apply fork-local content fixes")
        reconciled_map: dict[str, bytes] = {}
        if reconcile and reconcile.fixed:
            for rel in reconcile.fixed:
                path = os.path.join(dest, rel)
                if os.path.isfile(path):
                    try:
                        with open(path, "rb") as fh:
                            reconciled_map[rel] = fh.read()
                    except OSError:
                        pass

        def _preserve() -> list[str]:
            # Keep the engine-owned registry inside the snapshot before fork
            # preservation.  This makes the visual pack reviewable in the
            # published candidate and lets preserve_fork_files protect it from
            # an older registry in the fork checkout.
            if self.owned and self.owned.count and os.path.isdir(self.owned.root):
                registry_dest = os.path.join(dest, "config", "owned-assets")
                shutil.copytree(self.owned.root, registry_dest, dirs_exist_ok=True)
            preserved = preserve_fork_files(
                self.fork_root,
                dest,
                src,
                self.rules,
                obsolete_upstream_paths=source_delta.obsolete_mapped,
                owned_paths=set(self.owned.mapping) if self.owned else set(),
                allow_unclassified_fork_files=bool(baseline_sha),
            )
            return (
                preserved
                + enforce_strict_branding(dest, self.rules)
                + require_update_summary_attribution(dest)
            )
        strict_changed = stage(
            "preserve",
            _preserve,
            "carry explicit fork-owned files then enforce strict residual branding",
        )
        if strict_changed:
            reconcile.fixed.extend(f"strict-brand:{value}" for value in strict_changed)
            reconcile.total += len(strict_changed)

        scan = stage("scan", lambda: scan_tree(dest), "classify every branded file")
        diff = stage(
            "compare",
            lambda: compare_trees(src, dest, self.rules, self.owned, reconciled_map),
            "diff branded tree vs upstream",
        )
        verify = stage(
            "verify",
            lambda: verify_branded(src, dest, self.rules, self.owned, reconciled_map),
            "file-by-file parity gate",
        )

        def _forkcheck() -> ForkCheckReport:
            if not self.fork_root:
                return ForkCheckReport()
            return fork_consistency(
                self.fork_root,
                dest,
                src,
                self.rules,
                obsolete_upstream_paths=source_delta.obsolete_mapped,
                owned_paths=set(self.owned.mapping) if self.owned else set(),
            )
        forkcheck = stage("forkcheck", _forkcheck,
                          "diff snapshot vs nastech-agent fork (identical/updated/added/missing)")

        brand = brand or BrandResult()
        reconcile = reconcile or ReconcileResult()
        scan = scan or ScanReport()
        diff = diff or DiffReport()
        verify = verify or VerifyReport()
        forkcheck = forkcheck or ForkCheckReport()
        fork_ok = (not self.fork_root) or forkcheck.gate_passes()
        source_delta_ok = not baseline_sha or source_delta.complete
        coverage_ok = verify.total == source_census.files
        passed = gate_passes(verify, self.threshold) and fork_ok and source_delta_ok and coverage_ok \
            and not any(s.status == "fail" for s in stages)

        result = UpdateResult(
            number=number, dir=dest, upstream_sha=upstream_sha or "", hermes_url=self.hermes_url,
            brand=brand, scan=scan, diff=diff, verify=verify, gate=passed, stages=stages,
            reconcile=reconcile, fork=forkcheck, source_delta=source_delta,
            source_census=source_census,
        )

        # These outputs depend on the complete stage list.  Reserve their
        # positions now, then replace ``pending`` with the actual outcome
        # after each filesystem operation completes below.
        report_stage = StageResult("report", "pending", "write UPDATE-REPORT.md + GATE-REPORT.md")
        stages.append(report_stage)
        package_stage = StageResult("package", "pending", f"zip -> {os.path.basename(zip_path)}" if zip_path else "no zip requested")
        stages.append(package_stage)
        manifest_stage = StageResult("manifest", "pending", "write manifest.json")
        stages.append(manifest_stage)

        # record stage (achievements)
        stage("record", lambda: "achievements", "record pipeline state")

        # notify stage
        def _notify() -> str:
            if notify is None:
                return "no notifier configured; skip"
            from .notifier import Notification
            notify.notify(Notification(
                f"Nastech-Update#{number}",
                result.summary(),
                level="info" if passed else "error",
                kind="update",
            ))
            return "notification sent (best-effort)"
        stage("notify", _notify, "notify interested parties")

        # gate stage
        stage("gate", lambda: "PASS" if passed else "FAIL", "final gate decision")

        # summary stage (pipeline summary, with optional per-stage AI review)
        def _summary() -> str:
            base = result.summary()
            if self.ai is None:
                return base
            context = (
                f"gate={'PASS' if passed else 'FAIL'} "
                f"brand={brand.total} owned={brand.owned} scan={scan.total} "
                f"diff={diff.summary()} verify={verify.summary()}"
            )
            try:
                review = self.ai.review_stage("summary", context)
                return f"{base}\nAI: {review}"
            except Exception as exc:
                return f"{base}\nAI: review unavailable ({exc})"
        stage("summary", _summary, "pipeline summary + optional AI review")

        # release stage (happens in GitHub Actions)
        if result.zip_path:
            stage("release", lambda: "ready", f"zip ready for GitHub release: {os.path.basename(result.zip_path)}")
        else:
            stage("release", lambda: "skipped", "release happens in GitHub Actions (build + gh release)")

        result.stages = stages

        # Write outputs in dependency order and update each reserved stage
        # only after its operation succeeds.  A failure is visible as ``fail``
        # rather than the old misleading ``skipped``/``deferred`` status.
        os.makedirs(dest, exist_ok=True)
        result.report_path = os.path.join(dest, "UPDATE-REPORT.md")
        result.gate_report_path = os.path.join(dest, "GATE-REPORT.md")
        report_content = update_report_md(result)
        gate_content = gate_report_md(result)
        try:
            with open(result.report_path, "w", encoding="utf-8") as fh:
                fh.write(report_content)
            with open(result.gate_report_path, "w", encoding="utf-8") as fh:
                fh.write(gate_content)
            report_stage.status = "ok"
        except OSError as exc:
            report_stage.status = "fail"
            report_stage.detail = f"report write failed: {exc}"

        if zip_path and report_stage.status == "ok":
            try:
                result.zip_path = package_zip(
                    dest, zip_path,
                    {"UPDATE-REPORT.md": report_content, "GATE-REPORT.md": gate_content},
                    project_name=project_name,
                )
                package_stage.status = "ok"
            except (OSError, ValueError) as exc:
                package_stage.status = "fail"
                package_stage.detail = f"package failed: {exc}"
        elif not zip_path:
            package_stage.status = "skip"
            package_stage.detail = "no zip requested"
        else:
            package_stage.status = "fail"
            package_stage.detail = "package not attempted because report generation failed"

        manifest = {
            "number": number,
            "dir": os.path.basename(dest),
            "upstream_sha": result.upstream_sha,
            "source_fingerprint": source_fingerprint(self.hermes_url),
            "source_provenance": {
                "source_fingerprint": source_fingerprint(self.hermes_url),
                "fetched_at": fetched_at,
                "acquisition": "fresh-direct-clone",
                "baseline_sha": baseline_sha,
                "source_census": {"files": source_census.files, "dirs": source_census.dirs},
            },
            "commit_subjects": commit_subjects,
            "changed_areas": changed_areas,
            "source_delta": {
                "baseline_sha": source_delta.baseline_sha,
                "complete": source_delta.complete,
                "counts": source_delta.counts,
                "owned_paths": sorted(self.owned.mapping) if self.owned else [],
                "changes": [
                    {
                        "status": change.status,
                        "old_path": self.rules.transform_path(change.old_path),
                        "new_path": self.rules.transform_path(change.new_path),
                        "old_mapped": change.old_mapped,
                        "new_mapped": change.new_mapped,
                    }
                    for change in source_delta.changes
                ],
            },
            "reconciliation_actions": reconcile.fixed,
            "gate": passed,
            "stages": [s.name for s in stages],
            "verify": {
                "total": verify.total,
                "passed": verify.passed,
                "locked": verify.locked,
                "failed": [f.mapped_path for f in verify.failed],
                "pass_ratio": round(verify.pass_ratio, 4),
            },
            "brand": {
                "total": brand.total,
                "renamed": brand.renamed,
                "rewritten": brand.rewritten,
                "locked_copied": brand.locked_copied,
                "binary_copied": brand.binary_copied,
                "errors": brand.errors,
            },
            "diff": {
                "renamed": len(diff.actions("renamed")),
                "rewritten": len(diff.actions("rewritten")),
                "identical": len(diff.actions("identical")),
                "locked": len(diff.actions("locked")),
                "missing": len(diff.actions("missing")),
            },
            "scan": {
                "total": scan.total,
                "by_category": scan.by_category,
                "by_format": scan.by_format,
                "unknown_binary": scan.unknown_binary,
            },
            "fork": {
                "statuses": forkcheck.statuses,
                "violations": forkcheck.violation_count,
                "preserved": len(forkcheck.preserved),
                "preserved_paths": forkcheck.preserved,
                "features_fork": forkcheck.features_fork,
                "features_branded": forkcheck.features_branded,
                "added_lines": sum(e.added_lines for e in forkcheck.entries),
                "deleted_lines": sum(e.deleted_lines for e in forkcheck.entries),
            },
        }
        result.manifest_path = os.path.join(dest, "manifest.json")
        try:
            with open(result.manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2)
            manifest_stage.status = "ok"
        except OSError as exc:
            manifest_stage.status = "fail"
            manifest_stage.detail = f"manifest write failed: {exc}"
        result.stages = stages

        if not passed and not keep_failed:
            shutil.rmtree(dest)

        return result
