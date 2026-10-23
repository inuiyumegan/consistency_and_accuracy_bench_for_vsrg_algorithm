"""Formatting helpers for optional metrics.

Every dimension can be *not evaluated* (``None``), which is deliberately
different from a score of 0 or 100 (see ``cab.metrics.scoring``).  Rendering
must therefore never assume a float: ``f"{None:.1f}"`` raises TypeError and
killed a whole benchmark at report-printing time.
"""

from __future__ import annotations

from typing import Optional


def fmt(value: Optional[float], digits: int = 1, missing: str = "—") -> str:
    """Format a possibly-missing number; ``missing`` for ``None``/NaN."""
    if value is None:
        return missing
    try:
        number = float(value)
    except (TypeError, ValueError):
        return missing
    if number != number:  # NaN
        return missing
    return f"{number:.{digits}f}"


def fmt_ratio(value: Optional[float], digits: int = 1, missing: str = "—") -> str:
    """Same as :func:`fmt` but appends ``x`` (realtime factor style)."""
    rendered = fmt(value, digits=digits, missing=missing)
    return rendered if rendered == missing else f"{rendered}x"
