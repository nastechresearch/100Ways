from pathlib import Path

from hundredways.brandscan import (
    _allowed,
    propose_token_overrides,
    scan_brand,
    scan_paths,
)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_sees_glued_run_ons_without_flagging_english_words(tmp_path):
    _write(tmp_path, "a.py", "HERMESCLOUD = 1\nhermesagent = 2\nhermesbot-cron = 3\n")
    _write(tmp_path, "b.md", "synchronous and asynchronous work; a venous pump\n")

    runs = sorted(item.run for item in scan_brand(str(tmp_path)).violations)

    assert "HERMESCLOUD" in runs
    assert "hermesagent" in runs
    assert "hermesbot-cron" in runs
    assert not any(run.lower().startswith("sync") for run in runs)
    assert not any("venous" in run for run in runs)


def test_nous_run_on_is_seen_but_english_suffix_is_not(tmp_path):
    _write(tmp_path, "n.ts", "const nousnet = 1;\nawait runAnonymous();\n")

    runs = sorted(item.run for item in scan_brand(str(tmp_path)).violations)

    assert "nousnet" in runs
    assert not any("anonymous" in run.lower() for run in runs)


def test_camelcase_words_that_start_like_nous_are_not_brand_runs(tmp_path):
    _write(tmp_path, "i18n.ts", "noUsage: 1,\nnoUser: 2,\nconst nousnet = 3;\n")

    runs = sorted(item.run for item in scan_brand(str(tmp_path)).violations)

    assert runs == ["nousnet"]


def test_scan_paths_catch_a_branded_filename_that_text_scan_misses(tmp_path):
    _write(tmp_path, "src/hermesagent.ts", "export const x = 1;\n")

    hits = scan_paths(str(tmp_path))

    assert any(hit.run == "hermesagent" and hit.kind == "path" for hit in hits)


def test_allow_list_mirrors_the_weekly_gate(tmp_path):
    from hundredways.weekly_sync import _allowed_brand_occurrence

    cases = [
        ("contributors/names/c.md", "hermes", "hermesagent"),
        ("package-lock.json", "hermes", '"hermes-parser": "1.0.0"'),
        ("package-lock.json", "hermes", '"other-hermes": 1'),
        ("website/package-lock.json", "nous", '"@nous-research/image-size"'),
        ("reports/SYNC-SUMMARY.md", "nous", "> powered by nousresearch"),
        ("reports/SYNC-SUMMARY.md", "nous", "> Powered by Nastech"),
        ("README.md", "hermes", "hermes_cli"),
    ]
    for path, word, line in cases:
        assert _allowed(path, word, line) == _allowed_brand_occurrence(word, line, path)


def test_proposals_are_only_additive_for_runs_no_rule_covers(tmp_path):
    _write(tmp_path, "x.txt", "HERMESCLOUD\nhermes_cli\n")

    proposals = propose_token_overrides(scan_brand(str(tmp_path)))

    matches = {item["match"] for item in proposals}
    assert "HERMESCLOUD" in matches
    assert "hermes" not in matches
    replaced = {item["match"]: item["replace"] for item in proposals}
    assert replaced["HERMESCLOUD"] == "NASTECHCLOUD"
    assert all(item["anchored"] is False for item in proposals)
