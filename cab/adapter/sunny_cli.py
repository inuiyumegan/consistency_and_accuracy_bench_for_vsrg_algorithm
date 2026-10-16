#!/usr/bin/env python3
"""CLI bridge for Star-Rating-Rebirth (Sunny Rework).

Two modes:
  1) One-shot:  python3 sunny_cli.py <osu_file>          -> prints a float
  2) Server:    python3 sunny_cli.py --server
     Reads newline-delimited JSON requests from stdin:
       {"id": 1, "file": "/path/to/map.osu"}
     and writes one JSON response line per request to stdout:
       {"id": 1, "difficulty": 12.34, "status": "ok"}
     Algorithm modules are imported once and reused across requests.
"""

import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(_ROOT, "test_algorithms/Star-Rating-Rebirth"))

import algorithm


def compute(file_path: str) -> float:
    sr = algorithm.calculate(file_path, "NM")
    return float(sr or 0.0)


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
        sys.exit(0)

    file_path = sys.argv[1]
    try:
        print(f"{compute(file_path):.6f}")
    except Exception as e:
        sys.stderr.write(f"Star-Rating-Rebirth warning: {e}\n")
        print("0.0")
        sys.exit(0)


if __name__ == "__main__":
    main()
