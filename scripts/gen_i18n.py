#!/usr/bin/env python3
"""Generate i18n/<lang>/README.md for every supported language."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hundredways.i18n import locales, render_readme  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "i18n")


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    for lang in locales():
        out_dir = os.path.join(ROOT, lang)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "README.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render_readme(lang))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
