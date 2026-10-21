"""Batched git plumbing must decide exactly what the per-file subprocesses decided.

These tests hold a real fixture repository spanning a multi-commit range and pin
the two performance claims of this slice:

* ``read_blobs`` answers with the same bytes (and the same ``None``) a per-path
  ``git show`` returned, for every shape a name can take: spaces, non-ASCII,
  deletions, renames, binaries, duplicate requests;
* one ``git diff --numstat --no-renames -z`` reproduces every per-path numstat.

Subprocess counts are asserted as well - fewer processes is the whole point, so
each test records the git commands the engine starts and counts the sessions.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

import pytest

from hundredways import analyzer, port, verify
from hundredways.analyzer import analyze
from hundredways.rules import BrandingRules
from hundredways.verify import (
    CAT_FILE_TIMEOUT_SECONDS,
    NUMSTAT_TIMEOUT_SECONDS,
    BlobError,
    _numstat,
    gate_passes,
    numstat_for,
    read_blobs,
    tree_numstat,
    verify_rebrand,
)
from tests.conftest import commit, git

BLOB_REV = re.compile(r"^[0-9a-f]{40}:")


@dataclass
class GitCall:
    cmd: list[str]
    kwargs: dict


@dataclass
class Range:
    """Two commits plus every path shape the engine has to read between them."""

    repo: str
    old: str
    new: str
    paths: list[str]


@pytest.fixture
def git_calls(monkeypatch):
    """Every git subprocess started after this fixture is requested."""
    recorded: list[GitCall] = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        recorded.append(GitCall([str(part) for part in cmd], kwargs))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    return recorded


def write(repo: str, path: str, data: bytes) -> None:
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as handle:
        handle.write(data)


def commit_worktree(repo: str, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def git_show(repo: str, rev: str, path: str) -> bytes | None:
    """The read this slice replaces: raw ``git show`` bytes, ``None`` when git failed."""
    proc = subprocess.run(
        ["git", "-C", repo, "show", f"{rev}:{path}"], capture_output=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else None


def tree_paths(repo: str, sha: str) -> list[str]:
    return git(repo, "ls-tree", "-r", "--name-only", sha).splitlines()


@pytest.fixture
def change_range(git_repo) -> Range:
    write(git_repo, "plain.txt", b"one\ntwo\nthree\n")
    write(git_repo, "sp ace.txt", b"first line\n")
    write(git_repo, "docs/报告.md", b"upstream report\n")
    write(git_repo, "gone.txt", b"removed soon\nsecond removed line\n")
    write(git_repo, "tools/old_name.txt", b"stable body\nsecond stable line\nthird line\n")
    write(git_repo, "bin.dat", b"\x89PNG\r\n\x1a\n\x00\x01\x02")
    write(git_repo, "docs/glob?.md", b"literal question mark\n")
    write(git_repo, "docs/globA.md", b"matched by the question mark\n")
    old = commit_worktree(git_repo, "base")

    write(git_repo, "plain.txt", b"one\ntwo\nthree\nfour\nfive\n")
    write(git_repo, "sp ace.txt", b"first line\nsecond line\n")
    write(git_repo, "docs/报告.md", b"upstream report\nrebranded line\n")
    os.remove(os.path.join(git_repo, "gone.txt"))
    os.rename(
        os.path.join(git_repo, "tools/old_name.txt"),
        os.path.join(git_repo, "tools/new_name.txt"),
    )
    write(git_repo, "bin.dat", b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03\x04")
    write(git_repo, "docs/glob?.md", b"literal question mark\ntwo more\nthree more\n")
    write(git_repo, "docs/globA.md", b"matched by the question mark\nplus one\n")
    write(git_repo, "docs/new_file.md", b"brand new\n")
    new = commit_worktree(git_repo, "a sync window")

    paths = [
        "plain.txt",
        "sp ace.txt",
        "docs/报告.md",
        "gone.txt",
        "tools/old_name.txt",
        "tools/new_name.txt",
        "bin.dat",
        "docs/globA.md",
        "docs/new_file.md",
        "absent.txt",
    ]
    return Range(repo=git_repo, old=old, new=new, paths=paths)


@pytest.fixture
def brand_range(git_repo) -> Range:
    """An upstream tree and its imperfectly branded twin, as verify and analyze see them."""
    write(git_repo, "tools/hermes_runner.py", b"def run_hermes(): pass\n")
    write(git_repo, "sp ace.txt", b"keep me\nsecond line\n")
    write(git_repo, "docs/报告.md", b"unicode name\n")
    write(git_repo, "gone.txt", b"deleted by the rebrand\n")
    write(git_repo, "bin.dat", b"\x89PNG\r\n\x1a\n\x00\x01")
    up = commit_worktree(git_repo, "upstream tree")

    git(git_repo, "checkout", "-q", "-b", "nastech/main", up)
    os.remove(os.path.join(git_repo, "tools/hermes_runner.py"))
    os.remove(os.path.join(git_repo, "gone.txt"))
    write(git_repo, "tools/nastech_runner.py", b"def run_nastech(): pass\nleft unbranded\n")
    na = commit_worktree(git_repo, "rebrand with a gap")
    git(git_repo, "checkout", "-q", "master")

    paths = sorted(set(tree_paths(git_repo, up)) | set(tree_paths(git_repo, na)))
    return Range(repo=git_repo, old=up, new=na, paths=paths)


def _blob_reads(calls: list[GitCall]) -> list[GitCall]:
    """``git show <sha>:<path>`` calls - the per-file reads this slice removes."""
    return [
        call for call in calls
        if call.cmd[3:4] == ["show"] and any(BLOB_REV.match(arg) for arg in call.cmd[4:])
    ]


def _cat_file_sessions(calls: list[GitCall]) -> list[GitCall]:
    return [call for call in calls if call.cmd[3:4] == ["cat-file"]]


def _tree_numstat_sessions(calls: list[GitCall]) -> list[GitCall]:
    return [call for call in calls if "--no-renames" in call.cmd]


def _path_numstat_sessions(calls: list[GitCall]) -> list[GitCall]:
    return [
        call for call in calls
        if "--numstat" in call.cmd and "--no-renames" not in call.cmd
    ]


# --- blob reads ---------------------------------------------------------------

def test_read_blobs_equals_git_show_for_every_path(change_range, git_calls):
    specs = [(rev, path) for rev in (change_range.old, change_range.new)
             for path in change_range.paths]
    specs.insert(3, (change_range.old, "plain.txt"))
    expected = [git_show(change_range.repo, rev, path) for rev, path in specs]

    got = read_blobs(change_range.repo, specs)

    assert got == expected
    assert got[3] == b"one\ntwo\nthree\n", "a repeated request must be answered again"
    assert any(value is None for value in got), "the fixture must span unreadable paths"


def test_read_blobs_uses_one_process_where_git_show_used_one_per_file(change_range, git_calls):
    specs = [(rev, path) for rev in (change_range.old, change_range.new)
             for path in change_range.paths]
    baseline = len(git_calls)

    read_blobs(change_range.repo, specs)

    assert len(specs) == 20, "one session must replace twenty per-file reads"
    assert len(_cat_file_sessions(git_calls[baseline:])) == 1
    assert len(_blob_reads(git_calls[baseline:])) == 0


def test_read_blobs_declares_a_timeout_and_checks_the_exit_code(change_range, git_calls):
    baseline = len(git_calls)

    read_blobs(change_range.repo, [(change_range.new, "plain.txt")])

    session = _cat_file_sessions(git_calls[baseline:])[0]
    assert session.kwargs["timeout"] == CAT_FILE_TIMEOUT_SECONDS
    assert session.kwargs["check"] is False

    with pytest.raises(BlobError):
        read_blobs(os.path.join(change_range.repo, "not-a-repo"), [("HEAD", "plain.txt")])


def test_read_blobs_surfaces_a_hung_git(monkeypatch, change_range):
    def hang(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)

    with pytest.raises(subprocess.TimeoutExpired):
        read_blobs(change_range.repo, [(change_range.new, "plain.txt")])


def test_read_blobs_without_requests_starts_nothing(change_range, git_calls):
    baseline = len(git_calls)

    assert read_blobs(change_range.repo, []) == []
    assert _cat_file_sessions(git_calls[baseline:]) == []


def test_read_blobs_keeps_empty_and_binary_blobs_exact(change_range):
    write(change_range.repo, "empty.txt", b"")
    sha = commit_worktree(change_range.repo, "add an empty file")

    assert read_blobs(change_range.repo, [(sha, "empty.txt")]) == [b""]
    assert read_blobs(change_range.repo, [(sha, "bin.dat")]) == [
        git_show(change_range.repo, sha, "bin.dat")
    ]


# --- numstat ------------------------------------------------------------------

def test_tree_numstat_equals_every_per_path_call(change_range):
    stats = tree_numstat(change_range.repo, change_range.old, change_range.new)

    for path in change_range.paths:
        if path == "bin.dat":
            # git prints "-" for a binary file, so both readings raise; that parity
            # is what test_binary_paths_still_raise... pins down.
            continue
        expected = _numstat(change_range.repo, change_range.old, change_range.new, path)
        assert numstat_for(stats, change_range.repo, change_range.old, change_range.new, path) \
            == expected, path


def test_tree_numstat_costs_one_call_for_a_whole_range(change_range, git_calls):
    baseline = len(git_calls)

    tree_numstat(change_range.repo, change_range.old, change_range.new)

    assert len(_tree_numstat_sessions(git_calls[baseline:])) == 1
    assert len(change_range.paths) > 5, "one call must replace several per-path ones"


def test_tree_numstat_resolves_the_rename_and_its_old_side(change_range):
    renamed = git(change_range.repo, "diff", "--name-status", change_range.old, change_range.new)
    assert any(line.startswith("R") for line in renamed.splitlines()), "fixture needs a rename"

    stats = tree_numstat(change_range.repo, change_range.old, change_range.new)

    assert stats["tools/new_name.txt"] == ("3", "0")
    assert stats["tools/old_name.txt"] == ("0", "3")
    assert stats["gone.txt"] == ("0", "2")
    assert stats["docs/报告.md"] == ("1", "0")
    assert stats["sp ace.txt"] == ("1", "0")


def test_numstat_for_falls_back_for_glob_magic_paths(change_range, git_calls):
    stats = tree_numstat(change_range.repo, change_range.old, change_range.new)
    expected = _numstat(change_range.repo, change_range.old, change_range.new, "docs/glob?.md")
    baseline = len(git_calls)

    # git matched this pathspec as a glob and answered two files, so the only way
    # to keep the old number is to keep making the old call.
    assert numstat_for(stats, change_range.repo, change_range.old, change_range.new,
                       "docs/glob?.md") == expected

    assert len(_path_numstat_sessions(git_calls[baseline:])) == 1


def test_binary_paths_still_raise_the_way_the_per_path_call_did(change_range):
    stats = tree_numstat(change_range.repo, change_range.old, change_range.new)

    with pytest.raises(ValueError):
        numstat_for(stats, change_range.repo, change_range.old, change_range.new, "bin.dat")
    with pytest.raises(ValueError):
        _numstat(change_range.repo, change_range.old, change_range.new, "bin.dat")


def test_tree_numstat_declares_a_timeout(change_range, git_calls):
    baseline = len(git_calls)

    tree_numstat(change_range.repo, change_range.old, change_range.new)

    session = _tree_numstat_sessions(git_calls[baseline:])[0]
    assert session.kwargs["timeout"] == NUMSTAT_TIMEOUT_SECONDS

    with pytest.raises(RuntimeError):
        tree_numstat(os.path.join(change_range.repo, "not-a-repo"), "HEAD~1", "HEAD")


# --- verify_rebrand -----------------------------------------------------------

def test_verify_rebrand_deltas_match_per_path_numstat(brand_range, git_calls):
    baseline = len(git_calls)

    report = verify_rebrand(brand_range.repo, brand_range.old, brand_range.new,
                            BrandingRules(), compute_deltas=True)

    failed = [result for result in report.results if not result.pass_]
    assert failed
    assert len(_tree_numstat_sessions(git_calls[baseline:])) == 1
    assert len(_path_numstat_sessions(git_calls[baseline:])) == 0
    for result in failed:
        assert (result.added_lines, result.deleted_lines) == _numstat(
            brand_range.repo, brand_range.old, brand_range.new, result.mapped_path
        ), result.mapped_path


def test_verify_rebrand_reads_one_blob_session_and_no_per_file_shows(brand_range, git_calls):
    report = verify_rebrand(brand_range.repo, brand_range.old, brand_range.new, BrandingRules())

    sessions = _cat_file_sessions(git_calls)
    assert len(sessions) == 1
    assert len(_blob_reads(git_calls)) == 0
    assert sessions[0].kwargs["timeout"] == CAT_FILE_TIMEOUT_SECONDS
    assert report.total == len(tree_paths(brand_range.repo, brand_range.old))
    assert report.failed and not gate_passes(report, 1.0)


def test_verify_rebrand_batches_one_session_per_path_group(brand_range, git_calls, monkeypatch):
    monkeypatch.setattr(verify, "_PATHS_PER_BATCH", 1)

    verify_rebrand(brand_range.repo, brand_range.old, brand_range.new, BrandingRules())

    assert len(_cat_file_sessions(git_calls)) == len(tree_paths(brand_range.repo,
                                                               brand_range.old))


def test_verify_rebrand_verdicts_survive_the_batched_read(brand_range):
    report = verify_rebrand(brand_range.repo, brand_range.old, brand_range.new,
                            BrandingRules(), compute_deltas=True)

    checked = {result.path: result.pass_ for result in report.results}
    assert checked["tools/hermes_runner.py"] is False
    assert checked["sp ace.txt"] is True
    assert checked["gone.txt"] is False
    assert [result.locked for result in report.results if result.path == "bin.dat"] == [True]


# --- analyzer -----------------------------------------------------------------

def test_analyze_line_counts_match_per_path_numstat(brand_range, git_calls):
    rules = BrandingRules()
    baseline = len(git_calls)

    report = analyze(brand_range.old, brand_range.new, brand_range.repo, rules)

    changed = report.changed()
    assert changed
    assert len(_tree_numstat_sessions(git_calls[baseline:])) == 1
    assert len(_path_numstat_sessions(git_calls[baseline:])) == 0
    for entry in changed:
        assert (entry.added_lines, entry.deleted_lines) == _numstat(
            brand_range.repo, brand_range.old, brand_range.new, rules.transform_path(entry.path)
        ), entry.path


def test_analyze_reads_each_tree_in_one_path_group(brand_range, git_calls):
    report = analyze(brand_range.old, brand_range.new, brand_range.repo, BrandingRules())

    sessions = _cat_file_sessions(git_calls)
    # One group per tree: a whole-tree request has to stay bounded, or a 5000-file
    # tree would hold both its trees' bytes twice (the batch buffer and the dicts).
    assert len(sessions) == 2
    assert len(_blob_reads(git_calls)) == 0
    assert all(session.kwargs["timeout"] == CAT_FILE_TIMEOUT_SECONDS for session in sessions)
    assert len(report.entries) >= len(brand_range.paths)


def test_analyze_batches_a_tree_one_path_group_at_a_time(brand_range, git_calls, monkeypatch):
    monkeypatch.setattr(analyzer, "_PATHS_PER_BATCH", 1)

    report = analyze(brand_range.old, brand_range.new, brand_range.repo, BrandingRules())

    assert len(_cat_file_sessions(git_calls)) == len(
        tree_paths(brand_range.repo, brand_range.old)
    ) + len(tree_paths(brand_range.repo, brand_range.new))
    assert report.entries


# --- port ---------------------------------------------------------------------

def upstream_lead(repo: str, files: dict[str, bytes], rename: tuple[str, str]) -> str:
    """One non-merge upstream commit ahead of ``sync-upstream``, carrying a real rename."""
    base = commit(repo, {"seed.txt": "seed\n", rename[0]: "renamed body\nsecond line\n"}, "base")
    git(repo, "branch", "upstream/main", base)
    git(repo, "checkout", "-q", "upstream/main")
    os.rename(os.path.join(repo, rename[0]), os.path.join(repo, rename[1]))
    for path, data in files.items():
        write(repo, path, data)
    sha = commit_worktree(repo, "upstream work")
    git(repo, "checkout", "-q", "master")
    git(repo, "branch", "sync-upstream", base)
    return sha


def changed_entries(repo: str, sha: str) -> list[str]:
    return git(repo, "diff", "--name-status", "--find-renames", f"{sha}^1", sha).splitlines()


def test_port_ships_a_whole_commit_with_one_cat_file_session(git_repo, git_calls):
    sha = upstream_lead(
        git_repo,
        {
            "tools/hermes_runner.py": b"def run_hermes(): pass\n",
            "docs/report file.md": b"# report named with a space\n",
            "assets/mark.png": b"\x89PNG\r\n\x1a\n\x00\x01 binary asset\n",
        },
        ("tools/hermes_old.py", "tools/hermes_new.py"),
    )
    entries = changed_entries(git_repo, sha)
    assert any(entry.startswith("R") for entry in entries), "fixture needs a real rename"
    baseline = len(git_calls)

    results = port.port_commits(git_repo, "upstream/main", "sync-upstream", BrandingRules())

    assert len(results) == 1
    assert len(entries) == 4, "one session must replace four per-file git show reads"
    # Two sessions per commit: _port_one's blob read and the parity gate's own.
    assert len(_cat_file_sessions(git_calls[baseline:])) == 2
    assert len(_blob_reads(git_calls[baseline:])) == 0
    assert results[0].status == "ported" and gate_passes(results[0].report, 1.0)


def test_port_output_is_the_branded_blob_byte_for_byte(git_repo):
    rules = BrandingRules()
    sha = upstream_lead(
        git_repo,
        {"tools/hermes_runner.py": b"def run_hermes(): pass\n", "sp ace.md": b"# spaced name\n"},
        ("tools/hermes_old.py", "tools/hermes_new.py"),
    )

    ported = port.port_commits(git_repo, "upstream/main", "sync-upstream", rules)
    port_sha = ported[0].port_sha

    for entry in changed_entries(git_repo, sha):
        parts = entry.split("\t")
        if parts[0][0] == "D":
            continue
        source = parts[-1]
        shown = git_show(git_repo, sha, source)
        assert shown is not None
        expected = rules.transform_text(shown.decode("utf-8")).encode("utf-8")
        assert git_show(git_repo, port_sha, rules.transform_path(source)) == expected, source


def test_port_aborts_a_quoted_path_the_old_git_show_aborted_too(git_repo):
    """git quotes non-ASCII paths in ``--name-status``, and neither read form resolves
    the quoted result: batching must not turn that abort into a silent empty file."""
    sha = upstream_lead(git_repo, {"docs/报告.md": b"unicode file name\n"},
                        ("tools/hermes_old.py", "tools/hermes_new.py"))
    quoted = '"docs/\\346\\212\\245\\345\\221\\212.md"'
    assert any(quoted in entry for entry in changed_entries(git_repo, sha)), \
        "git must quote the non-ASCII path"
    assert git_show(git_repo, sha, "docs/报告.md") == b"unicode file name\n"
    assert git_show(git_repo, sha, quoted) is None, "the quoted form is unreadable, as before"

    with pytest.raises(BlobError):
        port.port_commits(git_repo, "upstream/main", "sync-upstream", BrandingRules())


def test_port_refuses_a_blob_git_cannot_show(git_repo, monkeypatch):
    sha = upstream_lead(git_repo, {"tools/hermes_runner.py": b"def run_hermes(): pass\n"},
                        ("tools/hermes_old.py", "tools/hermes_new.py"))
    worktree = port._worktree(git_repo, "sync-upstream")
    res = port.PortResult(upstream_sha=sha, subject="upstream work")
    monkeypatch.setattr(port, "read_blobs", lambda repo, requests: [None] * len(requests))

    # The old loop raised out of the run when git show failed; a missing batch
    # record has to do the same instead of quietly shipping an empty file.
    with pytest.raises(BlobError):
        port._port_one(worktree, git_repo, sha, BrandingRules(), 0.99, res,
                       git(git_repo, "rev-parse", "HEAD"))
    assert not os.path.exists(os.path.join(worktree, "tools/nastech_runner.py"))
    assert not os.path.exists(os.path.join(worktree, "tools/nastech_new.py"))


def test_rebrand_blob_passes_binary_through_and_rewrites_text():
    rules = BrandingRules()

    assert port._rebrand_blob(b"def run_hermes(): pass\n", rules) == b"def run_nastech(): pass\n"
    assert port._rebrand_blob(b"\x00\xff\xfe binary hermes", rules) == b"\x00\xff\xfe binary hermes"
