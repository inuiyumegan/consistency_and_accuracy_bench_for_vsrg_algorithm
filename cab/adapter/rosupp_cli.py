#!/usr/bin/env python3
"""CLI bridge for the ppy-sb/rosu-pp mania star-rating runners.

Two variants are exposed via ``--variant``:

  * ``sunny-rs`` (default) -> ``rosu-pp-old`` (commit f0663cf "implement
    sunnyxxy formula"): a faithful Rust port of Star-Rating-Rebirth.
  * ``arily``             -> ``rosu-pp`` (main).

Modes:
  1) One-shot:  python3 rosupp_cli.py [--variant NAME] <osu_file>   -> prints a float
  2) Server:    python3 rosupp_cli.py [--variant NAME] --server
     Reads newline-delimited JSON requests from stdin and answers one JSON line
     per request; the runner process is spawned once and reused.

Failure policy:
  * A failing runner exits non-zero and prints nothing (never ``0.0``).
  * The runner's response ``id`` is **verified**, not overwritten: rewriting it
    destroyed the only cross-request desynchronisation check.
  * Runner reads happen on a thread with a timeout, so a hung runner is killed
    instead of blocking the whole benchmark.
"""

import json
import math
import os
import queue
import signal
import subprocess
import sys
import threading

__version__ = "1.2.0"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

# variant -> relative path of the prebuilt runner binary
VARIANTS = {
    "sunny-rs": "test_algorithms/rosu-pp-runner-old/target/release/rosu-pp-runner-old",
    "arily": "test_algorithms/rosu-pp-runner/target/release/rosu-pp-runner",
}

#: Upper bound for a single runner answer (a 700 s marathon at 1x can take a
#: while, but a hang must not be indefinite).
_REQUEST_TIMEOUT_S = 300.0

_EOF = object()


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


def _finite(value, what: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} is not numeric: {value!r}") from None
    if not math.isfinite(f):
        raise ValueError(f"{what} is non-finite: {f!r}")
    return f


class _Runner:
    """A reusable runner process with a timeout-guarded line reader."""

    def __init__(self, binary: str):
        self.binary = binary
        self.proc = None
        self.queue = None

    def _pump(self, stream, sink):
        try:
            for line in stream:
                if line and line.strip():
                    sink.put(line.strip())
        except (ValueError, OSError):
            pass
        finally:
            sink.put(_EOF)

    def ensure(self):
        if self.proc is not None and self.proc.poll() is None:
            return self.proc
        self.proc = subprocess.Popen(
            [self.binary, "--server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self.queue = queue.Queue()
        threading.Thread(
            target=self._pump, args=(self.proc.stdout, self.queue), daemon=True
        ).start()
        return self.proc

    def request(self, file_path: str):
        proc = self.ensure()
        proc.stdin.write(json.dumps({"file": file_path}) + "\n")
        proc.stdin.flush()
        try:
            item = self.queue.get(timeout=_REQUEST_TIMEOUT_S)
        except queue.Empty:
            self.kill()
            raise TimeoutError(
                f"rosu-pp runner did not answer within {_REQUEST_TIMEOUT_S:.0f}s"
            )
        if item is _EOF:
            self.kill()
            raise RuntimeError("rosu-pp runner exited unexpectedly")
        return json.loads(item)

    def kill(self):
        proc, self.proc = self.proc, None
        self.queue = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            for stream in (proc.stdin, proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass


def run_server(binary):
    runner = _Runner(binary)
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
                resp = runner.request(req["file"])
                if resp.get("id") not in (None, req_id):
                    # Never rewrite the id: a mismatch means the answer belongs
                    # to a previous request (stale line after a timeout).
                    runner.kill()
                    raise RuntimeError(
                        f"runner response id {resp.get('id')!r} != request {req_id!r}"
                    )
                resp["id"] = req_id
                if resp.get("status", "ok") == "ok":
                    resp["difficulty"] = _finite(resp.get("difficulty"), "difficulty")
            except Exception as e:  # noqa: BLE001 - reported, not swallowed
                resp = {"id": req_id, "status": "error", "error": f"{type(e).__name__}: {e}"}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    finally:
        runner.kill()


def main():
    variant, argv = _parse_variant(sys.argv[1:])
    binary = _binary_for(variant)

    if argv and argv[0] == "--server":
        run_server(binary)
        return

    if len(argv) < 1:
        sys.stderr.write("usage: rosupp_cli.py [--variant NAME] <osu_file> | --server\n")
        sys.exit(2)

    file_path = argv[0]
    try:
        proc = subprocess.run(
            [binary, file_path], capture_output=True, text=True, timeout=_REQUEST_TIMEOUT_S
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"runner exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
            )
        out = (proc.stdout or "").strip()
        if not out:
            raise RuntimeError("runner printed nothing")
        value = _finite(out.splitlines()[-1].strip(), "runner output")
        print(f"{value:.6f}")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"rosu-pp ({variant}) error: {type(e).__name__}: {e}\n")
        sys.exit(3)


if __name__ == "__main__":
    main()
