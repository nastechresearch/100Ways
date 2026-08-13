from hundredways.rules import BrandingRules
from hundredways.verify import gate_passes, verify_port, verify_rebrand
from tests.conftest import commit, git, git_repo


def test_verify_rebrand_passes(git_repo):
    base = commit(git_repo, {"tools/hermes_runner.py": "def run_hermes(): pass"}, "base")
    rebrand = commit(
        git_repo,
        {"tools/nastech_runner.py": "def run_nastech(): pass"},
        "rebrand",
    )
    report = verify_rebrand(git_repo, base, rebrand, BrandingRules())
    assert report.passed == report.total
    assert not report.failed
    assert gate_passes(report, 1.0)


def test_verify_rebrand_reports_failure(git_repo):
    base = commit(git_repo, {"tools/hermes_runner.py": "def run_hermes(): pass"}, "base")
    rebrand = commit(
        git_repo,
        {"tools/nastech_runner.py": "def run_hermes(): pass"},
        "incomplete rebrand",
    )
    report = verify_rebrand(git_repo, base, rebrand, BrandingRules())
    assert report.failed
    assert not gate_passes(report, 1.0)


def test_verify_port_matches_changed_files(git_repo):
    up = commit(git_repo, {"src/mod.py": "x = 1\n"}, "upstream base")
    port = commit(
        git_repo,
        {"src/nastech_mod.py": "x = 1\n"},
        "port",
    )
    report = verify_port(git_repo, up, port, BrandingRules())
    assert not report.failed


def test_binary_locked_files_counted_not_failed(git_repo):
    png = "\x89PNG\r\n\x1a\n" + "bogus"
    base = commit(git_repo, {"assets/logo.png": png}, "base")
    rebrand = commit(git_repo, {"assets/logo.png": png}, "rebrand")
    report = verify_rebrand(git_repo, base, rebrand, BrandingRules())
    assert report.locked == 1
    assert not report.failed
