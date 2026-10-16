#!/usr/bin/env python3
"""CLI bridge for the ppy-sb/rosu-pp mania star-rating runners.

Two variants are exposed via ``--variant``:

  * ``sunny-rs`` (default) -> ``rosu-pp-old`` (commit f0663cf "implement
    sunnyxxy formula"): a faithful Rust port of Star-Rating-Rebirth. Its
    numbers match the original Python algorithm to ~1e-6 while running
    ~40x faster, so it is used as a drop-in replacement for the slow
    Python ``sunny`` adapter.

  * ``arily``             -> ``rosu-pp`` (main). Arily's (owner of the
    ppy-sb server) modified formula; a distinct algorithm, benchmarked
    separately.

Modes:
  1) One-shot:  python3 rosupp_cli.py [--variant NAME] <osu_file>   -> prints a float
  2) Server:    python3 rosupp_cli.py [--variant NAME] --server
     Reads newline-delimited JSON requests from stdin:
       {"id": 1, "file": "/path/to/map.osu"}
     and writes one JSON response line per request to stdout:
       {"id": 1, "difficulty": 12.34, "status": "ok"}
     The runner process is spawned once and reused across requests.
"""

import json
import os
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

# variant -> relative path of the prebuilt runner binary
VARIANTS = {
    "sunny-rs": "test_algorithms/rosu-pp-runner-old/target/release/rosu-pp-runner-old",
    "arily": "test_algorithms/rosu-pp-runner/target/release/rosu-pp-runner",
}


def _parse_variant(argv):
    variant = "sunny-rs"
    if len(argv) >= 2 and argv[0] == "--variant":
        variant = argv[1]
        argv = argv[2:]
    return variant, argv


def _binary_for(variant):
    rel = VARIANTS.get(variant)
    if rel is None:
        raise ValueError(f"Unknown rosupp variant: {variant!r} (known: {', '.join(VARIANTS)})")
    path = os.path.join(_ROOT, rel)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"rosu-pp runner binary not found: {path}\n"
            f"Build it with: cargo build --release "
            f"(in the corresponding rosu-pp-runner directory)"
        )
    return path


def run_server(binary):
    proc = subprocess.Popen(
        [binary, "--server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            req_id = req.get("id")
            try:
                payload = json.dumps({"id": req_id, "file": req["file"]})
                proc.stdin.write(payload + "\n")
                proc.stdin.flush()
                resp_line = proc.stdout.readline()
                resp = json.loads(resp_line) if resp_line else {}
                resp["id"] = req_id
            except Exception as e:  # noqa: BLE001
                resp = {"id": req_id, "status": "error", "error": str(e)}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    finally:
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        proc.wait()


def main():
    variant, argv = _parse_variant(sys.argv[1:])
    binary = _binary_for(variant)

    if argv and argv[0] == "--server":
        run_server(binary)
        return

    if len(argv) < 1:
        print("0.0")
        sys.exit(0)

    file_path = argv[0]
    try:
        proc = subprocess.run(
            [binary, file_path], capture_output=True, text=True, timeout=120
        )
        out = proc.stdout.strip()
        val = float(out) if out else 0.0
        print(f"{val:.6f}")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"rosu-pp ({variant}) warning: {e}\n")
        print("0.0")
        sys.exit(0)


if __name__ == "__main__":
    main()
