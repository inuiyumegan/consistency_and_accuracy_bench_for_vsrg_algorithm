"""Small I/O helpers shared by the report writers.

Artefacts used to be written in place with ``open(..., "w")``; an interrupted
export (Ctrl-C, full disk, an exception in a later section) left a truncated
CSV/JSON that downstream tooling happily parsed as "fewer rows".  Writing to a
sibling ``.tmp`` and atomically replacing avoids that class of corruption.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``path`` atomically.

    The temporary file lives in the destination directory so ``os.replace``
    stays on one filesystem and is therefore atomic.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(out.parent), prefix=f".{out.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fp:
            fp.write(text)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_name, out)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return out
