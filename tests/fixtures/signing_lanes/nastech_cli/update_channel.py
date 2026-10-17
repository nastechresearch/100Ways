"""Stub of the fork module: only the symbol the fixture's ``stable.py`` imports."""
import re

STABLE_TAG_RE = re.compile(r"^v(?:0|[1-9]\d{0,2})\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
