"""In-memory virtual file pipeline using tmpfs / shm for zero-disk-I/O execution."""

from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Union

from cab.parser.osu_parser import Beatmap


def get_shm_dir() -> Path:
    """Return path to high-speed in-memory directory (/dev/shm on Linux).

    Stale ``bm_*.osu`` files from a previous hard-killed run (SIGKILL / OOM)
    are swept on first use: /dev/shm counts towards RAM, so leaked scratch
    files directly reduce the memory available to the next benchmark.
    """
    if os.path.exists("/dev/shm") and os.access("/dev/shm", os.W_OK):
        target = Path("/dev/shm/cab_pipeline")
    else:
        target = Path(tempfile.gettempdir()) / "cab_pipeline"
    target.mkdir(parents=True, exist_ok=True)
    _sweep_stale(target)
    return target


_STALE_AFTER_S = 3600.0


def _sweep_stale(directory: Path) -> None:
    """Best-effort removal of scratch beatmaps older than one hour."""
    import time

    cutoff = time.time() - _STALE_AFTER_S
    try:
        entries = list(directory.glob("bm_*.osu"))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            continue


@contextmanager
def materialize_beatmap_path(
    target: Union[Beatmap, str, Path],
) -> Generator[Path, None, None]:
    """Context manager yielding a Path to the beatmap file.
    
    If target is already an existing file path, yields it directly with zero overhead.
    If target is a Beatmap object, serializes it to an in-memory tmpfs / shm file,
    yields the path, and guarantees immediate cleanup on exit.
    """
    if isinstance(target, (str, Path)):
        p = Path(target)
        if p.is_file():
            yield p
            return
        # A bare string that is not a file is NOT beatmap content: writing it
        # out produced a valid-looking .osu whose body was the file name, and
        # the algorithm then returned 0 (or crashed) for no visible reason.
        text = str(target)
        if "\n" not in text:
            raise FileNotFoundError(f"beatmap file not found: {p}")

    # Serialize to shm
    shm_dir = get_shm_dir()
    unique_name = f"bm_{uuid.uuid4().hex}.osu"
    tmp_path = shm_dir / unique_name

    if isinstance(target, Beatmap):
        content = target.serialize()
    elif isinstance(target, (str, Path)):
        content = str(target)
    else:
        raise TypeError(
            f"materialize_beatmap_path expects a Beatmap or path, got {type(target)!r}"
        )
    with open(tmp_path, "w", encoding="utf-8") as fp:
        fp.write(content)

    try:
        yield tmp_path
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
