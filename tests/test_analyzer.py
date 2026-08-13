from hundredways.analyzer import analyze
from hundredways.rules import BrandingRules
from tests.conftest import commit_bytes, commit_on, diverged, git_repo


def test_analyze_identical_after_branding(git_repo):
    diverged(git_repo)
    up = commit_on(git_repo, "upstream/main", {"tools/hermes_runner.py": "def run_hermes(): pass"}, "upstream add")
    na = commit_on(git_repo, "nastech/main", {"tools/nastech_runner.py": "def run_nastech(): pass"}, "nastech add")
    report = analyze(up, na, git_repo, BrandingRules())
    for e in report.entries:
        if e.path == "tools/hermes_runner.py":
            assert e.status == "identical"


def test_analyze_detects_upstream_only_file(git_repo):
    diverged(git_repo)
    up = commit_on(git_repo, "upstream/main", {"docs/new_file.md": "brand new content"}, "upstream add")
    na = commit_on(git_repo, "nastech/main", {}, "no-op")
    report = analyze(up, na, git_repo, BrandingRules())
    assert any(e.path == "docs/new_file.md" for e in report.upstream_only())


def test_analyze_detects_brand_violation(git_repo):
    diverged(git_repo)
    up = commit_on(git_repo, "upstream/main", {"src/hermes_plugin.py": "hermes imports here"}, "upstream add")
    na = commit_on(git_repo, "nastech/main", {"src/nastech_plugin.py": "NousResearch still left behind"}, "bad rebrand")
    report = analyze(up, na, git_repo, BrandingRules())
    viol = [e for e in report.violations() if e.path == "src/hermes_plugin.py"]
    assert viol and "NousResearch" in viol[0].brand_violations


def test_analyze_counts_changed_file(git_repo):
    diverged(git_repo)
    up = commit_on(git_repo, "upstream/main", {"src/hermes_app.py": "a = 1\n"}, "upstream add")
    na = commit_on(git_repo, "nastech/main", {"src/nastech_app.py": "a = 1\nb = 2\n"}, "changed")
    report = analyze(up, na, git_repo, BrandingRules())
    changed = [e for e in report.changed() if e.path == "src/hermes_app.py"]
    assert changed and changed[0].added_lines >= 1


def test_analyze_asset_detection(git_repo):
    png = b"\x89PNG\r\n\x1a\n" + b"bogus"
    diverged(git_repo)
    up = commit_bytes(git_repo, "upstream/main", "static/logo.png", png, "upstream asset")
    na = commit_bytes(git_repo, "nastech/main", "static/logo.png", png, "nastech asset")
    report = analyze(up, na, git_repo, BrandingRules())
    assert report.assets()
