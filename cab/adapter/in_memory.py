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
    """Return path to high-speed in-memory directory (/dev/shm on Linux)."""
    shm_cand = Path("/dev/shm/cab_pipeline")
    if os.path.exists("/dev/shm") and os.access("/dev/shm", os.W_OK):
        shm_cand.mkdir(parents=True, exist_ok=True)
        return shm_cand
    fallback = Path(tempfile.gettempdir()) / "cab_pipeline"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


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

    if isinstance(target, Beatmap) and target.file_path and Path(target.file_path).is_file():
        # Check if unmodified: if it has never been mutated, can yield original file
        # However, to be completely safe against in-memory modifications, we check:
        pass

    # Serialize to shm
    shm_dir = get_shm_dir()
    unique_name = f"bm_{uuid.uuid4().hex}.osu"
    tmp_path = shm_dir / unique_name

    content = target.serialize() if isinstance(target, Beatmap) else str(target)
    with open(tmp_path, "w", encoding="utf-8") as fp:
        fp.write(content)

    try:
        yield tmp_path
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
