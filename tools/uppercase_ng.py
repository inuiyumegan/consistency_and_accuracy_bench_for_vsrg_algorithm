#!/usr/bin/env python3
"""Uppercase the NG (Numeric Greek) abbreviation in the CAB-VA docs.

Only the *abbreviation* is affected.  A literal "ng" that is part of an English
word (scrolling, benchmarking, algorithms, rating, timing, long, ...) must stay
untouched, so we require both neighbouring characters to be non-letters (or
string boundaries).  Chinese characters, spaces, `$`, backticks, asterisks,
parentheses and hyphens all qualify as boundaries; ASCII letters do not.
"""

import re
import sys
from pathlib import Path

# "ng" whose neighbours are both non-letters (or string edges).
_PATTERN = re.compile(r"(?<![A-Za-z])ng(?![A-Za-z])")

# Code/CLI literals that MUST stay lowercase to match the implementation
# (`--scale` choices are auto | ng | non-ng | daniel in cab/cli.py).
# Backtick-delimited spans are protected verbatim; these bare tokens are
# protected too because they always refer to the literal, not the concept.
_PROTECTED_TOKENS = (
    "non-ng",
)

# A backtick span (`...`) is treated as code and left untouched.
_CODE_SPAN = re.compile(r"`[^`]*`")

_PLACEHOLDER = "\x00{}\x00"


def convert(text: str) -> tuple[str, int]:
    """Uppercase the NG abbreviation (concept), preserving code literals."""
    # 1. Stash backtick spans so their contents are never rewritten.
    spans: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        spans.append(m.group(0))
        return _PLACEHOLDER.format(len(spans) - 1)

    protected = _CODE_SPAN.sub(_stash, text)

    # 2. Protect bare literal tokens (e.g. non-ng) that appear outside code spans.
    for token in _PROTECTED_TOKENS:
        protected = protected.replace(token, _PLACEHOLDER.format(len(spans)))
        spans.append(token)

    # 3. Uppercase remaining standalone abbreviations.
    protected, count = _PATTERN.subn("NG", protected)

    # 4. Restore protected spans.
    for i, span in enumerate(spans):
        protected = protected.replace(_PLACEHOLDER.format(i), span)
    return protected, count


def main() -> int:
    docs = sys.argv[1:] or [
        "README.md",
        "README_EN.md",
        "CAB_VA_USAGE_GUIDE.md",
        "CAB_VA_USAGE_GUIDE_EN.md",
    ]
    total = 0
    for name in docs:
        path = Path(name)
        if not path.exists():
            print(f"skip (missing): {name}")
            continue
        original = path.read_text(encoding="utf-8")
        updated, count = convert(original)
        if count:
            path.write_text(updated, encoding="utf-8")
        print(f"{name}: {count} replacement(s)")
        total += count
    print(f"TOTAL: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
