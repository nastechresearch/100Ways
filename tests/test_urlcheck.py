"""URL checks against a real server, a real brand map, and no invented verdicts.

The engine used to ship a fork whose links nobody had looked at.  These tests
drive the checker against a stdlib ``http.server`` bound to an ephemeral port,
so ``BROKEN`` is a 404 the test made happen, not a fixture the test imagined.
The other half of the honesty is the *absence* of a claim: a link the run did
not probe is ``UNREACHABLE`` with the reason it was not probed, never ``OK``.
"""

from __future__ import annotations

import http.server
import json
import re
import threading
from pathlib import Path

import pytest

from hundredways.urlcheck import (
    BRAND_VIOLATION,
    BROKEN,
    OK,
    PLACEHOLDER,
    SEVERITY_ORDER,
    STATUSES,
    UNREACHABLE,
    URL_POLICY_PATH,
    Link,
    Probe,
    UrlPolicy,
    classify,
    extract_links,
    findings_to_json,
    host_of,
    probe_tally,
    probe_urls,
    render,
    scan_tree,
)

REPO = Path(__file__).resolve().parents[1]


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's method name
        if self.path == "/ok":
            self._text(200, "here")
        elif self.path == "/moved":
            self.send_response(301)
            self.send_header("Location", "/ok")
            self.end_headers()
        elif self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
        elif self.path == "/slow":
            import time

            time.sleep(1.5)
            self._text(200, "late")
        else:
            self._text(404, "nope")

    def _text(self, code: int, body: str) -> None:
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:  # keep pytest output clean
        pass


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def policy(**overrides) -> UrlPolicy:
    """The shipped policy, with only the knob a test needs to move."""
    return UrlPolicy.load(str(REPO / "config" / "url-policy.yml")).replaced(**overrides)


def live_policy() -> UrlPolicy:
    """The shipped policy with localhost freed from the placeholder list.

    The test server is on 127.0.0.1, which the real policy does call a
    placeholder -- and the offline tests below rely on that being true.  The
    probe tests need the socket verdicts, not the host list, so only that one
    field moves.
    """
    return policy(placeholder_hosts=[])


def make_link(url: str, path: str = "README.md", line: int = 1) -> Link:
    return Link(url=url, path=path, line=line, column=0, context=url)


def linked(url: str, path: str = "README.md") -> Link:
    """The first link a text really yields -- extraction is part of the test."""
    return extract_links(url + "\n", path)[0]


# -- extraction -------------------------------------------------------------


def test_finds_bare_markdown_autolink_and_html_urls():
    text = (
        "see https://nastechresearch.com/docs now\n"
        "a [link](https://github.com/nastechresearch/nastech-agent) here\n"
        "an <https://pypi.org/project/nastech-agent/> autolink\n"
        'and <a href="https://nastech.github.io/console">html</a>\n'
    )
    found = extract_links(text, "README.md")
    assert [item.url for item in found] == [
        "https://nastechresearch.com/docs",
        "https://github.com/nastechresearch/nastech-agent",
        "https://pypi.org/project/nastech-agent/",
        "https://nastech.github.io/console",
    ]
    assert [item.line for item in found] == [1, 2, 3, 4]
    assert found[0].column == text.splitlines()[0].index("https")
    assert found[0].path == "README.md"


def test_strips_trailing_punctuation_keeps_a_quoted_slash():
    found = extract_links(
        "go to https://nastechresearch.com/docs. or read https://nastechresearch.com/a_(b)\n",
        "a.md",
    )
    assert [item.url for item in found] == [
        "https://nastechresearch.com/docs",
        "https://nastechresearch.com/a_(b)",
    ]


def test_records_the_line_it_found_the_link_on():
    text = "line one\nno links here\nthird: https://nastechresearch.com/x\n"
    found = extract_links(text, "docs/a.md")
    assert (found[0].line, found[0].context) == (3, "third: https://nastechresearch.com/x")


def test_context_never_carries_a_credential_shape():
    text = "token ghp_abcdefghijklmnopqrstuvwxyz123456 https://nastechresearch.com/x\n"
    found = extract_links(text, "a.md")
    assert "ghp_" not in found[0].context
    assert "[REDACTED]" in found[0].context


def test_host_of_matches_a_subdomain_suffix():
    assert host_of("https://docs.nastechresearch.com/a") == "docs.nastechresearch.com"
    assert policy().matches("allowed_hosts", "docs.nastechresearch.com") is True
    assert policy().matches("allowed_hosts", "evil-nastechresearch.com") is False
    assert policy().matches("allowed_hosts", "nastechresearch.com") is True


# -- classification, offline -------------------------------------------------


def test_an_unprobed_link_is_never_reported_as_passing():
    finding = classify(make_link("https://nastechresearch.com/docs"), policy())
    assert finding.status == UNREACHABLE
    assert "not probed" in finding.detail


def test_a_placeholder_host_is_a_placeholder_with_a_suggestion():
    finding = classify(make_link("https://example.com/api/key"), policy())
    assert finding.status == PLACEHOLDER
    assert finding.suggestion


@pytest.mark.parametrize(
    "url",
    [
        "https://nousresearch.com/docs",
        "https://hermes-agent.nousresearch.com/docs",
        "https://github.com/NousResearch/hermes-agent",
    ],
)
def test_an_unbranded_link_is_named_with_the_branded_form(url):
    finding = classify(make_link(url), policy())
    assert finding.status == BRAND_VIOLATION
    assert "nous" not in finding.suggestion.lower()
    assert "hermes" not in finding.suggestion.lower()
    assert "nastech" in finding.suggestion.lower()


@pytest.mark.parametrize(
    "url", ["https://", "http://", "https://ex ample.com", "https:///only-path", "//x"]
)
def test_malformed_urls_are_broken_without_a_probe(url):
    finding = classify(make_link(url), policy())
    assert finding.status == BROKEN
    assert "malformed" in finding.detail.lower()


# -- classification, with a real probe --------------------------------------


def test_a_live_url_probes_ok(server):
    url = server + "/ok"
    finding = classify(make_link(url), live_policy(), probe_urls([url], timeout_seconds=4))
    assert finding.status == OK
    assert "200" in finding.detail


def test_a_dead_url_is_broken_with_the_status_the_server_gave(server):
    url = server + "/missing"
    finding = classify(make_link(url), live_policy(), probe_urls([url], timeout_seconds=4))
    assert finding.status == BROKEN
    assert "404" in finding.detail


def test_a_redirect_chain_is_a_pass_with_the_chain_recorded(server):
    url = server + "/moved"
    finding = classify(make_link(url), live_policy(), probe_urls([url], timeout_seconds=4))
    assert finding.status == OK
    assert "301" in finding.detail
    assert "/ok" in finding.detail


def test_a_redirect_loop_is_broken_not_infinite(server):
    url = server + "/loop"
    finding = classify(make_link(url), live_policy(), probe_urls([url], timeout_seconds=4))
    assert finding.status == BROKEN
    assert "redirect" in finding.detail.lower()


def test_a_slow_server_is_unreachable_and_says_so(server):
    url = server + "/slow"
    finding = classify(make_link(url), live_policy(), probe_urls([url], timeout_seconds=0.2))
    assert finding.status == UNREACHABLE
    assert "timeout" in finding.detail.lower()


def test_an_upstream_host_beats_a_healthy_probe(server):
    # The verdict the operator can act on wins: a host we told the policy is
    # upstream's is a brand finding even when it answers 200.
    url = server + "/ok"
    probing = probe_urls([url], timeout_seconds=4)
    finding = classify(make_link(url), policy(upstream_hosts=["127.0.0.1"]), probing)
    assert finding.status == BRAND_VIOLATION
    assert finding.suggestion


def test_a_link_the_budget_could_not_reach_is_not_probed(server):
    first, second = server + "/ok", server + "/moved"
    probing = probe_urls([first, second], timeout_seconds=4, max_urls=1)
    assert probing[first].probed is True
    assert probing[second].probed is False
    assert "budget" in probing[second].reason


def test_a_wall_budget_stops_before_the_first_request(server):
    url = server + "/ok"
    probing = probe_urls([url], timeout_seconds=4, wall_budget_seconds=0)
    assert probing[url].probed is False
    assert "wall budget" in probing[url].reason


def test_duplicate_urls_are_probed_once(server):
    url = server + "/ok"
    probing = probe_urls([url, url, url], timeout_seconds=4)
    assert len(probing) == 1
    assert probing[url].probed is True


# -- tree scanning -----------------------------------------------------------


def build_tree(root: Path) -> None:
    (root / "README.md").write_text(
        "# p\n\n[upstream](https://nousresearch.com/docs)\n"
        "live: https://nastechresearch.com\nplaceholder: https://example.com/x\n",
        encoding="utf-8",
    )
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text(
        "see https://nastechresearch.com/docs and https://nastechresearch.com/docs\n",
        encoding="utf-8",
    )
    (root / "vendor").mkdir()
    (root / "vendor" / "thing.md").write_text("http://anything.invalid\n", encoding="utf-8")
    (root / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")


def test_scan_tree_walks_text_files_and_skips_ignored_paths(tmp_path):
    build_tree(tmp_path)
    found = scan_tree(str(tmp_path), policy(ignore_paths=["/vendor/"]), probe=False)
    assert {item.link.path for item in found} == {"README.md", "docs/guide.md"}


def test_scan_tree_is_deterministic_and_ordered(tmp_path):
    build_tree(tmp_path)
    once = scan_tree(str(tmp_path), policy(), probe=False)
    again = scan_tree(str(tmp_path), policy(), probe=False)
    assert [json.dumps(vars(f), default=str) for f in once] == [
        json.dumps(vars(f), default=str) for f in again
    ]
    keys = [(f.link.path, f.link.line, f.link.column, f.link.url) for f in once]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


def test_every_finding_carries_its_own_evidence(tmp_path):
    build_tree(tmp_path)
    found = scan_tree(str(tmp_path), policy(), probe=False)
    assert found
    for item in found:
        assert item.status in STATUSES
        assert item.detail
        if item.status != OK:
            assert item.suggestion or "not probed" in item.detail


def test_render_states_the_measurement_and_groups_by_status(tmp_path):
    build_tree(tmp_path)
    found = scan_tree(str(tmp_path), policy(), probe=False)
    text = render(found, probed=0, unprobed=len(found))
    assert "not probed" in text
    assert "nousresearch.com" in text
    headers = [
        line.split(" (")[0]
        for line in text.splitlines()
        if re.fullmatch(r"[A-Z_]+ \(\d+\)", line)
    ]
    assert headers == sorted(headers, key=SEVERITY_ORDER.index)
    assert headers[0] == BRAND_VIOLATION


def test_findings_to_json_is_complete_and_sorted(tmp_path):
    build_tree(tmp_path)
    payload = findings_to_json(scan_tree(str(tmp_path), policy(), probe=False))
    assert payload
    assert set(payload[0]) == {
        "url", "path", "line", "column", "status", "detail", "suggestion", "context", "probed",
    }
    assert not any(item["probed"] for item in payload)  # offline is not a pass
    assert [(i["path"], i["line"], i["column"]) for i in payload] == sorted(
        (i["path"], i["line"], i["column"]) for i in payload
    )


def test_the_shipped_policy_is_a_real_read_of_the_shipped_file():
    loaded = UrlPolicy.load(str(REPO / "config" / "url-policy.yml"))
    assert loaded.version == 1
    assert "nastechresearch.com" in loaded.allowed_hosts
    assert "nousresearch.com" in loaded.upstream_hosts
    assert "example.com" in loaded.placeholder_hosts
    assert loaded.timeout_seconds == 6
    assert loaded.max_urls == 200
    assert loaded.wall_budget_seconds >= loaded.timeout_seconds


def test_scanning_the_engine_repo_finds_its_own_documented_urls():
    found = scan_tree(str(REPO), policy(), probe=False)
    assert found
    assert {item.status for item in found} <= set(STATUSES)


def test_probe_tally_keeps_an_offline_pass_apart_from_a_live_one():
    links = extract_links(
        "docs: https://nastechresearch.com/docs\n", "README.md"
    )
    offline = [classify(link, policy()) for link in links]
    assert probe_tally(offline) == (0, len(offline))
    live = [
        classify(
            link,
            policy(),
            {link.url: Probe(url=link.url, kind="ok", probed=True, status_code=200)},
        )
        for link in links
    ]
    assert probe_tally(live) == (len(live), 0)


def test_the_engine_names_the_policy_file_it_ships():
    assert Path(URL_POLICY_PATH).is_file()
    assert UrlPolicy.load(URL_POLICY_PATH).allowed_hosts == policy().allowed_hosts


def test_a_policy_verdict_is_never_counted_as_an_answer_from_the_network():
    # The tally decides whether the report may say "a probe answered this link".  A
    # brand violation and a placeholder are found by reading the policy alone, so
    # counting them as probed would let an offline run claim network evidence.
    links = extract_links(
        "https://hermes-agent.nousresearch.com/docs and https://example.com/x\n", "README.md"
    )
    findings = [classify(link, policy()) for link in links]
    assert {item.status for item in findings} == {BRAND_VIOLATION, PLACEHOLDER}
    assert probe_tally(findings) == (0, len(findings))
