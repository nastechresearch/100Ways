#!/usr/bin/env python3
"""Verify the pinned pure-Hermes-to-Nastech branding baseline."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from hundredways.assets import OwnedAssets
from hundredways.conformance import verify_final_candidate
from hundredways.rules import BrandingRules
from hundredways.updates import apply_owned_assets, brand_tree

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "config" / "pure-brand-baseline.json"

def _git_head(source: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _check_brand_rules(rules: BrandingRules) -> None:
    sample = "Hermes hermes HERMES; Nous nous NOUS"
    expected = "Nastech nastech NASTECH; Nastech nastech NASTECH"
    if rules.transform_text(sample) != expected:
        raise SystemExit("core Hermes/Nous name substitutions do not match policy")
    if rules.transform_text("⚕☤") != "𓄃𓄃":
        raise SystemExit("approved medical-glyph identity mapping is missing")
    if rules.transform_path("apps/desktop/public/nous-girl.jpg") != (
        "apps/desktop/public/nastech-bantu.jpg"
    ):
        raise SystemExit("approved Bantu mascot path mapping is missing")

    urls = (
        "https://github.com/NousResearch/hermes-agent.git?ref=main",
        "github.com/NousResearch/hermes-agent",
        "git@github.com:NousResearch/hermes-agent.git",
        "https://nousresearch.com/Hermes?brand=Nous",
    )
    for url in urls:
        if rules.transform_text(url) != url:
            raise SystemExit(f"URL was changed by branding rules: {url}")


def check_snapshot(source: Path, baseline_path: Path = BASELINE_PATH) -> dict[str, object]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    source_sha = _git_head(source)
    expected_sha = baseline["hermes_commit"]
    if source_sha != expected_sha:
        raise SystemExit(
            f"wrong Hermes source revision: expected {expected_sha}, got {source_sha}"
        )

    rules = BrandingRules()
    _check_brand_rules(rules)
    assets_root = ROOT / "config" / "owned-assets"
    owned = OwnedAssets(root=str(assets_root)).brand_visuals()
    expected_visuals = int(baseline["owned_visual_targets"])
    if len(owned.mapping) != expected_visuals:
        raise SystemExit(
            "registered visual target count changed: "
            f"expected {expected_visuals}, got {len(owned.mapping)}"
        )

    with tempfile.TemporaryDirectory(prefix="100ways-pure-brand-") as directory:
        candidate = Path(directory) / "candidate"
        brand_tree(str(source), str(candidate), rules, owned)
        apply_owned_assets(str(candidate), owned)
        report = verify_final_candidate(
            source,
            candidate,
            owned_assets_root=assets_root,
        )
        if not report.passed:
            raise SystemExit(
                "exact branded-tree conformance failed:\n"
                + json.dumps(report.to_dict(), indent=2)
            )
        expected_digest = baseline["tree_sha256"]
        if report.candidate_digest != expected_digest:
            raise SystemExit(
                "pinned branded-tree digest changed: "
                f"expected {expected_digest}, got {report.candidate_digest}"
            )
        return {
            "gate": "PASS",
            "hermes_commit": source_sha,
            "source_files_verified": report.verified_files,
            "owned_visual_targets": len(owned.mapping),
            "tree_sha256": report.candidate_digest,
            "issues": 0,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args(argv)
    result = check_snapshot(args.source)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
