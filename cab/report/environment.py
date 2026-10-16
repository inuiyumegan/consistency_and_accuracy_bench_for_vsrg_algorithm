"""Evaluation environment metadata collection.

Records the machine environment in which a benchmark run was performed.

Rationale: computation-speed (SP) values are absolute measurements (ms of
compute per second of music), so they are only directly comparable between
runs performed on the same machine. Attaching environment metadata to every
report makes this constraint auditable — the same "environment disclosure"
approach used by Blender Benchmark / 3DMark.
"""

from __future__ import annotations

import os
import platform
from typing import Dict


def _cpu_model() -> str:
    """Best-effort CPU model name (Linux /proc/cpuinfo, then platform fallbacks)."""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as fp:
            for line in fp:
                low = line.lower()
                if low.startswith("model name") or low.startswith("hardware"):
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip():
                        return parts[1].strip()
    except OSError:
        pass
    name = platform.processor() or platform.machine()
    return (name or "unknown").strip()


def _memory_total_gb() -> float:
    """Total physical memory in GiB (best effort; 0.0 when undetectable)."""
    try:
        with open("/proc/meminfo", "r", encoding="utf-8", errors="ignore") as fp:
            for line in fp:
                if line.startswith("MemTotal:"):
                    kb = float(line.split()[1])
                    return round(kb / (1024.0 * 1024.0), 1)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def collect_environment_info() -> Dict[str, object]:
    """Collect evaluation environment metadata for reports.

    Returns a dict with CPU model, core count, memory, OS and Python version.
    Never raises: every field degrades gracefully to a safe default.
    """
    try:
        cores = os.cpu_count() or 0
    except Exception:
        cores = 0
    return {
        "cpu_model": _cpu_model(),
        "cpu_cores": cores,
        "memory_gb": _memory_total_gb(),
        "os": f"{platform.system()} {platform.release()}".strip(),
        "python_version": platform.python_version(),
        "note": (
            "Computation-speed (SP) values are machine-dependent; "
            "compare only within the same evaluation environment."
        ),
    }
