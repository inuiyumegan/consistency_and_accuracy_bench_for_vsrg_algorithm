#!/usr/bin/env python3
"""CLI bridge for Star-Rating-Rebirth (Sunny Rework).

Two modes:
  1) One-shot:  python3 sunny_cli.py <osu_file>          -> prints a float
  2) Server:    python3 sunny_cli.py --server
     Reads newline-delimited JSON requests from stdin:
       {"id": 1, "file": "/path/to/map.osu"}
     and writes one JSON response line per request to stdout:
       {"id": 1, "difficulty": 12.34, "status": "ok"}

Failure policy: a failed calculation MUST exit non-zero (or answer
``status: error``) and must never print a number.  This bridge used to print
``0.0`` and exit 0 on error, which the benchmark accepted as a legitimate
difficulty of zero and scored as "perfectly stable" (CODE_REVIEW P0-03).
"""

import json
import math
import os
import sys

__version__ = "1.1.0"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(_ROOT, "test_algorithms/Star-Rating-Rebirth"))

import algorithm  # noqa: E402


def compute(file_path: str) -> float:
    sr = algorithm.calculate(file_path, "NM")
    if sr is None:
        raise ValueError("Star-Rating-Rebirth returned no star rating")
    value = float(sr)
    if not math.isfinite(value):
        raise ValueError(f"Star-Rating-Rebirth returned a non-finite rating: {value!r}")
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
        try:
            resp.update({"difficulty": compute(req["file"]), "status": "ok"})
        except Exception as e:  # noqa: BLE001 - reported to the parent, not swallowed
            resp.update({"status": "error", "error": f"{type(e).__name__}: {e}"})
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--server":
        run_server()
        return

    if len(sys.argv) < 2:
        sys.stderr.write("usage: sunny_cli.py <osu_file> | --server\n")
        sys.exit(2)

    file_path = sys.argv[1]
    try:
        print(f"{compute(file_path):.6f}")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"Star-Rating-Rebirth error: {type(e).__name__}: {e}\n")
        sys.exit(3)


if __name__ == "__main__":
    main()
