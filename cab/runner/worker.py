"""Worker process initialization and task execution for parallel multiprocessing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cab.adapter.base import BaseAdapter
from cab.adapter.cli import CliAdapter
from cab.adapter.daniel import DanielAdapter
from cab.parser.osu_parser import Beatmap
from cab.mutator.rate import scale_rate

_GLOBAL_ADAPTER: Optional[BaseAdapter] = None


def init_worker(adapter_type: str, adapter_kwargs: Dict[str, Any]) -> None:
    """Initialize worker process with its dedicated algorithm adapter instance."""
    global _GLOBAL_ADAPTER
    if adapter_type.lower() == "daniel":
        _GLOBAL_ADAPTER = DanielAdapter(**adapter_kwargs)
    elif adapter_type.lower() == "cli":
        _GLOBAL_ADAPTER = CliAdapter(**adapter_kwargs)
    else:
        raise ValueError(f"Unknown adapter type for worker: {adapter_type}")


def run_calc_path(path: str) -> Optional[float]:
    """Calculate difficulty for an existing file path."""
    global _GLOBAL_ADAPTER
    if _GLOBAL_ADAPTER is None:
        raise RuntimeError("Worker adapter not initialized")
    try:
        return _GLOBAL_ADAPTER.calculate(path)
    except Exception:
        return None


def run_calc_path_timed(path: str) -> Tuple[Optional[float], float, Optional[str]]:
    """Calculate difficulty and execution time in ms. Returns (difficulty_or_none, elapsed_ms, error_msg)."""
    global _GLOBAL_ADAPTER
    if _GLOBAL_ADAPTER is None:
        raise RuntimeError("Worker adapter not initialized")
    import time
    t0 = time.perf_counter()
    try:
        diff = _GLOBAL_ADAPTER.calculate(path)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        return (diff, dt_ms, None)
    except Exception as e:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        err_msg = str(e).strip().splitlines()[-1] if str(e).strip() else "Unknown error"
        return (None, dt_ms, err_msg)


def run_calc_content(content: str) -> Optional[float]:
    """Calculate difficulty for in-memory .osu content string."""
    global _GLOBAL_ADAPTER
    if _GLOBAL_ADAPTER is None:
        raise RuntimeError("Worker adapter not initialized")
    try:
        return _GLOBAL_ADAPTER.calculate(content)
    except Exception:
        return None


def run_calc_rate_job(args: Tuple[str, float, str]) -> Tuple[str, float, Optional[float]]:
    """Calculate difficulty for a rate variant.
    
    args: (map_name, rate, base_content_str)
    Mutates beatmap at rate r in worker, evaluates, and returns (map_name, rate, difficulty_or_none).
    """
    map_name, rate, content_str = args
    try:
        bm = Beatmap.parse(content_str)
        mutated = scale_rate(bm, rate)
        mutated_content = mutated.serialize()
        diff = run_calc_content(mutated_content)
        return (map_name, rate, diff)
    except Exception:
        return (map_name, rate, None)


def run_calc_jitter_job(args: Tuple[str, str, int]) -> Tuple[str, Optional[float]]:
    """args: (map_name, content_str, seed)"""
    from cab.mutator.jitter import apply_jitter
    map_name, content_str, seed = args
    try:
        bm = Beatmap.parse(content_str)
        mutated = apply_jitter(bm, seed=seed)
        diff = run_calc_content(mutated.serialize())
        return (map_name, diff)
    except Exception:
        return (map_name, None)


def run_calc_short_ln_job(args: Tuple[str, str, int]) -> Tuple[str, Optional[float]]:
    """args: (map_name, content_str, seed)"""
    from cab.mutator.short_ln import inject_short_ln
    map_name, content_str, seed = args
    try:
        bm = Beatmap.parse(content_str)
        mutated = inject_short_ln(bm, seed=seed)
        diff = run_calc_content(mutated.serialize())
        return (map_name, diff)
    except Exception:
        return (map_name, None)


def run_calc_ho_probe_job(args: Tuple[str, str]) -> Tuple[float, float]:
    """args: (file_path, ho_content_str) -> returns (diff_orig, diff_ho)"""
    file_path, ho_content = args
    diff_orig = run_calc_path(file_path)
    diff_ho = run_calc_content(ho_content)
    return (diff_orig, diff_ho)
