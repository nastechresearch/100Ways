"""Reduced fork module: the claim next-steps the reconcile corrects.

Nested one level deeper than the function body because the reconcile's anchor
is the real file's line, which sits inside that block.

The partial-desktop note itself is not modelled here -- this fixture exists for
the signing-gated banner -- so it carries that rewrite's marker, which is how
``_reconcile_fork_lane_release_surface`` reads a file it has nothing to do with.
"""
from __future__ import annotations

from scripts.releases import optional_platforms


def next_steps(result: dict) -> list[str]:
    lines: list[str] = []
    # The note warns about a partial DESKTOP release:
    if True:
        left_out = optional_platforms.excluded(result)
        if left_out:
            effects = "; ".join(p.left_out for p in left_out)
            lines.append(f"Not built: {' and '.join(p.label for p in left_out)} (opt in with "
                         f"{' / '.join(p.option for p in left_out)}). {effects[0].upper()}{effects[1:]}.")
    return lines
