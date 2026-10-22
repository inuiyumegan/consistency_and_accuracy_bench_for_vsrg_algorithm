#!/usr/bin/env python3
"""CLI bridge for Dan-Overlay algorithm.

Two modes:
  1) One-shot:  python3 dan_overlay_cli.py <osu_file>    -> prints a float
  2) Server:    python3 dan_overlay_cli.py --server
     Reads newline-delimited JSON requests from stdin:
       {"id": 1, "file": "/path/to/map.osu"}
     and writes one JSON response line per request to stdout:
       {"id": 1, "difficulty": 12.34, "status": "ok"}
     Algorithm modules are imported once and reused across requests.
"""

import importlib
import json
import math
import os
import shutil
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(_ROOT, "test_algorithms/Dan-Overlay/src"))

#: How msd.exe is being executed ("msd: wine ..." / "onnx-fallback").  Exported
#: with every server response so a result can be marked as degraded.
_MSD_RUNTIME: str = "unknown"


def _ensure_linux_wine_msd() -> None:
    """Ensure Dan-Overlay's minacalc_bridge invokes msd.exe via Wine on non-Windows."""
    if os.name == "nt":
        return
    try:
        mb = importlib.import_module("07_model.minacalc_bridge")
    except Exception:
        return
    cli_path = getattr(mb, "_CLI_PATH", None)
    if not cli_path or not str(cli_path).lower().endswith(".exe"):
        return
    wine = shutil.which("wine64") or shutil.which("wine")
    if not wine:
        return
    if hasattr(mb, "_CLI_CMD"):
        mb._CLI_CMD = [wine, cli_path]


def _report_msd_runtime() -> None:
    """Surface how the MSD binary will be executed, on stderr.

    Dan-Overlay ships ``tools/bin/msd.exe`` only.  On Linux it must go through
    Wine; without Wine the bridge returns ``None`` and ``compute()`` silently
    falls back to the pure-ONNX ``sr``, so the algorithm's readings change with
    no error anywhere.  A batch log that shows this line is the difference
    between "comparable" and "quietly degraded".
    """
    try:
        mb = importlib.import_module("07_model.minacalc_bridge")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"[cab] Dan-Overlay: MSD bridge import failed ({exc}); "
            f"readings fall back to the pure-ONNX sr\n"
        )
        return

    cmd = getattr(mb, "_CLI_CMD", None)
    global _MSD_RUNTIME
    if cmd is None:
        _MSD_RUNTIME = "onnx-fallback"
        if os.environ.get("CAB_DAN_OVERLAY_REQUIRE_MSD", "").strip() in ("1", "true", "yes"):
            # Opt-in hard failure: an ONNX-only reading is NOT comparable with a
            # Wine-enabled run, so a published batch must not silently mix them.
            raise RuntimeError(
                "CAB_DAN_OVERLAY_REQUIRE_MSD is set but msd.exe cannot run "
                "(install wine or provide a native msd). Refusing to publish "
                "ONNX-fallback readings."
            )
        sys.stderr.write(
            "[cab] WARNING: Dan-Overlay cannot execute msd.exe (no wine/wine64 "
            "and no native msd). It falls back to the pure-ONNX sr, so these "
            "readings are NOT comparable with a Wine-enabled run.\n"
        )
    else:
        _MSD_RUNTIME = "msd:" + " ".join(cmd)
        sys.stderr.write(f"[cab] Dan-Overlay MSD runtime: {' '.join(cmd)}\n")


#: Near-empty 4K map used to isolate the bridge's fixed per-call cost.  Two
#: notes mean the MinaCalc work itself is on the order of milliseconds, so what
#: the timing captures is process start-up plus PE loading.
_PROBE_MAP = """osu file format v14

[General]
AudioFilename: probe.mp3
Mode: 3

[Difficulty]
CircleSize:4
OverallDifficulty:8
ApproachRate:5

[TimingPoints]
0,500,4,2,0,100,1,0

[HitObjects]
64,192,1000,1,0,0:0:0:0:
192,192,1500,1,0,0:0:0:0:
"""


def _probe_overhead(repeats: int = 3) -> float:
    """Measure the fixed per-invocation cost of the msd.exe bridge, in ms.

    Returns 0.0 in two cases where there is nothing to subtract:

    * msd.exe is launched natively, so the target platform pays the same
      process start-up (this is the case on Windows);
    * the bridge is unavailable and ``calc`` returns immediately.

    When the binary is reached through a compatibility layer (Wine on Linux),
    that layer's start-up cost exists only here and would otherwise be charged
    to the algorithm by the computation-speed dimension.  A median of a few
    repeats is used because a single sample varies by tens of milliseconds.
    """
    import statistics
    import tempfile
    import time

    sys.path.insert(0, os.path.join(_ROOT, "test_algorithms/Dan-Overlay/src/07_model"))
    try:
        # NOTE: the pipeline reaches the bridge as top-level "minacalc_bridge"
        # (07_model/ is on sys.path), which is a *different* module object from
        # "07_model.minacalc_bridge".  Read state only -- patch nothing here.
        import minacalc_bridge as mb
    except Exception:  # noqa: BLE001
        return 0.0

    cmd = getattr(mb, "_CLI_CMD", None)
    cli_path = getattr(mb, "_CLI_PATH", None)
    if not cmd:
        return 0.0
    if cli_path is not None and cmd[0] == cli_path:
        # Native launch: no compatibility layer in the path, nothing to subtract.
        return 0.0

    fd, probe_path = tempfile.mkstemp(suffix=".osu", prefix="cab_overhead_probe_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_PROBE_MAP)
        # Discard one call first: the very first launch in a fresh interpreter
        # pays extra start-up, and including it inflates the baseline by ~100 ms
        # (measured 5087 ms cold vs ~4987 ms steady), which would over-subtract
        # from every sample and make the algorithm look faster than it is.
        mb._cache.clear()
        mb.calc(probe_path, rate=1.0)
        samples = []
        for _ in range(max(1, int(repeats))):
            mb._cache.clear()          # force the subprocess to actually run
            t0 = time.perf_counter()
            mb.calc(probe_path, rate=1.0)
            samples.append((time.perf_counter() - t0) * 1000.0)
        return float(statistics.median(samples))
    finally:
        try:
            os.unlink(probe_path)
        except OSError:
            pass


_ensure_linux_wine_msd()
_report_msd_runtime()
from pipeline import analyze_map


def compute(file_path: str) -> float:
    """Return Dan-Overlay's ``dp`` reading, or raise.

    ``dp is None`` means the pipeline rejected the chart (parse error,
    non-mania, out-of-domain, or the primary SR path failed).  It used to fall
    back to ``res["sr"]`` and then to ``0.0``: a star rating of 5-11 would be
    read as dan 5-11 (ND 4.5-10.5), i.e. a whole unit system off, and a failure
    became ND -0.5 in the accuracy pool.
    """
    res = analyze_map(file_path)
    dp = res.get("dp")
    if dp is None:
        reason = res.get("status") or res.get("error") or "pipeline returned dp=None"
        raise ValueError(f"Dan-Overlay produced no dan reading ({reason})")
    value = float(dp)
    if not math.isfinite(value):
        raise ValueError(f"Dan-Overlay returned a non-finite dp: {value!r}")
    return value


def run_server() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = {"id": req.get("id")}

        # Overhead query: answered from this long-lived process so the baseline
        # is measured in exactly the state the real samples will be measured in.
        # A one-shot probe reports ~80 ms more (5087 vs ~4987 ms) and would
        # over-subtract from every chart.
        if req.get("op") == "overhead":
            try:
                resp.update(
                    {
                        "overhead_ms": _probe_overhead(int(req.get("repeats", 3))),
                        "status": "ok",
                    }
                )
            except Exception as e:  # noqa: BLE001
                resp.update({"status": "error", "error": str(e)})
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        try:
            resp.update(
                {"difficulty": compute(req["file"]), "status": "ok", "msd_runtime": _MSD_RUNTIME}
            )
        except Exception as e:
            resp.update({"status": "error", "error": str(e), "msd_runtime": _MSD_RUNTIME})
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--server":
        run_server()
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "--probe-overhead":
        # Prints the fixed per-invocation cost in milliseconds (0.0 when msd.exe
        # runs natively).  Consumed by CliAdapter.measure_platform_overhead_ms()
        # and subtracted from every speed-probe sample.
        print(f"{_probe_overhead():.3f}")
        return

    if len(sys.argv) < 2:
        sys.stderr.write("usage: dan_overlay_cli.py <osu_file> | --server | --probe-overhead\n")
        sys.exit(2)

    file_path = sys.argv[1]
    try:
        print(f"{compute(file_path):.6f}")
    except Exception as e:
        sys.stderr.write(f"Dan-Overlay failed: {e}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
