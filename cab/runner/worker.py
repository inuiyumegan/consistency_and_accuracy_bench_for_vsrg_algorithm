"""Worker process initialization and task execution for parallel multiprocessing."""

from __future__ import annotations

import atexit
import os
import signal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cab.adapter.base import BaseAdapter
from cab.adapter.cli import CliAdapter
from cab.adapter.daniel import DanielAdapter
from cab.parser.osu_parser import Beatmap
from cab.mutator.rate import scale_rate

_GLOBAL_ADAPTER: Optional[BaseAdapter] = None


def _teardown_adapter() -> None:
    """Release the persistent algorithm server held by this worker."""
    global _GLOBAL_ADAPTER
    adapter = _GLOBAL_ADAPTER
    _GLOBAL_ADAPTER = None
    if adapter is None:
        return
    teardown = getattr(adapter, "_teardown_server", None)
    if callable(teardown):
        try:
            teardown()
        except Exception:  # noqa: BLE001 - never mask the real shutdown path
            pass


def _install_shutdown_hooks() -> None:
    """Kill the child server when the pool terminates this worker.

    ``Pool.terminate()`` sends SIGTERM, and a spawned worker dies immediately
    without running ``atexit`` handlers -- which used to leave Wine/msd.exe
    grandchildren alive at 250-300 MB each (CODE_REVIEW P0-06).
    """
    atexit.register(_teardown_adapter)

    def _handler(signum, _frame):  # pragma: no cover - signal path
        _teardown_adapter()
        os._exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


def init_worker(adapter_type: str, adapter_kwargs: Dict[str, Any]) -> None:
    """Initialize worker process with its dedicated algorithm adapter instance."""
    global _GLOBAL_ADAPTER
    if adapter_type.lower() == "daniel":
        _GLOBAL_ADAPTER = DanielAdapter(**adapter_kwargs)
    elif adapter_type.lower() == "cli":
        _GLOBAL_ADAPTER = CliAdapter(**adapter_kwargs)
    else:
        raise ValueError(f"Unknown adapter type for worker: {adapter_type}")
    _install_shutdown_hooks()


def run_ping(_ignored: Any = None) -> bool:
    """Health check used right after pool creation.

    If the initializer raised (missing Daniel checkout, unimportable adapter),
    multiprocessing keeps respawning workers forever and the first real task
    never returns.  Pinging once turns that into an immediate, readable error.
    """
    return _GLOBAL_ADAPTER is not None


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


def run_measure_overhead(_ignored: Any = None) -> Optional[float]:
    """Measure the adapter's compatibility-layer overhead inside this worker.

    Runs in the pool worker rather than the parent so the baseline comes from
    the same persistent server -- and therefore the same process state -- that
    the speed samples are measured in.  Returns None when the adapter exposes no
    probe (the common case).
    """
    global _GLOBAL_ADAPTER
    adapter = _GLOBAL_ADAPTER
    if adapter is None:
        return None
    fn = getattr(adapter, "measure_platform_overhead_ms", None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def run_calc_content(content: str) -> Optional[float]:
    """Calculate difficulty for in-memory .osu content string."""
    global _GLOBAL_ADAPTER
    if _GLOBAL_ADAPTER is None:
        raise RuntimeError("Worker adapter not initialized")
    try:
        return _GLOBAL_ADAPTER.calculate(content)
    except Exception:
        return None


def run_calc_rate_job(args: Tuple[str, float, str, str]) -> Tuple[str, float, Optional[float]]:
    """Calculate difficulty for a rate variant.
    
    args: (map_name, rate, base_content_str, rounding)
    Mutates beatmap at rate r in worker, evaluates, and returns (map_name, rate, difficulty_or_none).

    ``rounding`` defaults to ``"round"`` when omitted, so older three-element
    task tuples keep working.
    """
    map_name, rate, content_str = args[0], args[1], args[2]
    rounding = args[3] if len(args) > 3 else "round"
    try:
        bm = Beatmap.parse(content_str)
        mutated = scale_rate(bm, rate, rounding=rounding)
        mutated_content = mutated.serialize()
        diff = run_calc_content(mutated_content)
        return (map_name, rate, diff)
    except Exception:
        return (map_name, rate, None)


def run_calc_rate_batch(args: Tuple[str, Sequence[float], str, str]) -> List[Tuple[str, float, Optional[float]]]:
    """Evaluate one chart at many clock rates, parsing the chart once.

    args: (map_name, [rate, ...], base_content_str, rounding)

    ``run_calc_rate_job`` parses the chart on every call, so the standard
    two-phase sweep paid 12,140 parses for 300 charts (~3.9 ms each, 43% of the
    per-evaluation Python overhead).  ``scale_rate`` only reads its input (it
    copies each object before scaling and replaces ``hit_objects`` wholesale),
    so one parsed AST can serve every rate and produce byte-identical output --
    verified against the one-shot path on sampled charts.

    Each rate is isolated in its own try: a single failing rate must not cost
    the chart its other 56 readings.
    """
    map_name, rates, content_str = args[0], args[1], args[2]
    rounding = args[3] if len(args) > 3 else "round"
    try:
        bm = Beatmap.parse(content_str)
    except Exception:
        return [(map_name, float(r), None) for r in rates]

    out: List[Tuple[str, float, Optional[float]]] = []
    for rate in rates:
        try:
            mutated = scale_rate(bm, float(rate), rounding=rounding)
            out.append((map_name, float(rate), run_calc_content(mutated.serialize())))
        except Exception:
            out.append((map_name, float(rate), None))
    return out


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


def run_calc_arrangement_probe_job(args: Tuple[str, str]) -> Tuple[str, Optional[float]]:
    """args: (map_name, content_str) -> evaluates intra-window arrangement bunching probe."""
    from cab.mutator.jitter import apply_arrangement_probe
    map_name, content_str = args
    try:
        bm = Beatmap.parse(content_str)
        mutated = apply_arrangement_probe(bm)
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
