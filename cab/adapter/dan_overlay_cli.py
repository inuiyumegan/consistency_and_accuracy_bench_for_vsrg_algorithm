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
import os
import shutil
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(_ROOT, "test_algorithms/Dan-Overlay/src"))


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


_ensure_linux_wine_msd()
from pipeline import analyze_map


def compute(file_path: str) -> float:
    res = analyze_map(file_path)
    dp = res.get("dp")
    if dp is None:
        dp = res.get("sr", 0.0)
    return float(dp or 0.0)


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
        try:
            resp.update({"difficulty": compute(req["file"]), "status": "ok"})
        except Exception as e:
            resp.update({"status": "error", "error": str(e)})
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--server":
        run_server()
        return

    if len(sys.argv) < 2:
        print("0.0")
        sys.exit(1)

    file_path = sys.argv[1]
    try:
        print(f"{compute(file_path):.6f}")
    except Exception as e:
        sys.stderr.write(f"Dan-Overlay failed: {e}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
