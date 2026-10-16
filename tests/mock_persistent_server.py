#!/usr/bin/env python3
"""Tiny mock persistent server used by tests for CliAdapter server-mode protocol.

Protocol (see cab/adapter/cli.py):
  reads:  {"id": n, "file": "/path/to/map.osu"}
  writes: {"id": n, "difficulty": <basename length>, "status": "ok", "pid": os.getpid()}

The deterministic difficulty value (basename character count) and the pid field
let tests verify both round-trip correctness and persistent process reuse.
"""

import json
import os
import sys


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        val = float(len(os.path.basename(req.get("file", ""))))
        resp = {
            "id": req.get("id"),
            "difficulty": val,
            "status": "ok",
            "pid": os.getpid(),
        }
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
