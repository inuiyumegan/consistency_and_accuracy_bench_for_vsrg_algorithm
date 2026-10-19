"""CLI subprocess adapter conforming to Section 6 of CAB-VA technical specification.

Supports two execution modes:

- **One-shot** (default): a fresh subprocess is spawned for every beatmap.
- **Persistent server mode**: when ``server_cmd`` is provided, a long-lived child
  process is kept alive *per worker process* and driven over a newline-delimited
  JSON protocol (Section 6.2 of the technical spec):

      benchmark → server:  {"id": 1, "file": "/path/to/map.osu"}
      server → benchmark:  {"id": 1, "difficulty": 14.28, "status": "ok"}

  This eliminates per-sample process startup cost (node/python cold start +
  module/model loading), typically the dominant overhead for fast algorithms.
  The adapter automatically falls back to one-shot mode if the server keeps
  failing, so scoring correctness is never compromised.
"""

from __future__ import annotations

import json
import select
import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional, Union

from cab.adapter.base import BaseAdapter
from cab.adapter.in_memory import materialize_beatmap_path
from cab.parser.osu_parser import Beatmap

# Consecutive persistent-server failures tolerated before giving up on it
# (per adapter instance / worker process) and using one-shot mode only.
_SERVER_MAX_FAILURES = 3


class CliAdapter(BaseAdapter):
    """Executes external algorithm binary or script via CLI subprocess."""

    def __init__(
        self,
        name: str,
        cmd_template: str,
        scale_type: str = "auto",
        timeout_seconds: float = 30.0,
        server_cmd: Optional[str] = None,
        overhead_probe_cmd: Optional[str] = None,
    ):
        """
        Args:
            name: Human-readable algorithm name.
            cmd_template: Command string with `{input}` placeholder, or executable path.
                          E.g. "./algo --input {input} --output-format json"
            scale_type: 'nd', 'non-nd', or 'auto'.
            timeout_seconds: Subprocess timeout in seconds (per evaluation).
            server_cmd: Optional persistent-server command (no {input} placeholder).
                        When set, a long-lived process is reused across samples.
            overhead_probe_cmd: Optional one-shot command that prints a fixed
                        per-invocation cost in milliseconds.  Used to subtract
                        compatibility-layer overhead (e.g. Wine start-up) from
                        the computation-speed samples.  Must print 0.0 when the
                        binary runs natively.
        """
        super().__init__(name=name, scale_type=scale_type)
        self.cmd_template = cmd_template
        self.timeout_seconds = timeout_seconds
        self.server_cmd = server_cmd
        self.overhead_probe_cmd = overhead_probe_cmd

        # Persistent-server state (lives for the lifetime of this adapter
        # instance, i.e. the lifetime of one pool worker process).
        self._server_proc: Optional[subprocess.Popen] = None
        self._server_id = 0
        self._server_failures = 0

    # ------------------------------------------------------------------
    # One-shot execution
    # ------------------------------------------------------------------
    def _calculate_oneshot(self, map_path: str) -> float:
        quoted_path = shlex.quote(map_path)
        if "{input}" in self.cmd_template:
            cmd = self.cmd_template.format(input=quoted_path)
        else:
            cmd = f"{self.cmd_template} --input {quoted_path} --output-format json"

        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )

        if proc.returncode != 0:
            raise RuntimeError(
                f"Algorithm process exited with code {proc.returncode}: {proc.stderr.strip()}"
            )

        return self._parse_numeric_output(proc.stdout)

    @staticmethod
    def _parse_numeric_output(stdout: str) -> float:
        """Parse a float from subprocess stdout (JSON object/float or last line)."""
        stdout = stdout.strip()
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                for key in ["difficulty", "sr", "star_rating", "rating", "val"]:
                    if key in data:
                        return float(data[key])
                raise KeyError(f"No difficulty key in JSON response: {data}")
            return float(data)
        except json.JSONDecodeError:
            last_line = stdout.splitlines()[-1].strip()
            return float(last_line)

    # ------------------------------------------------------------------
    # Compatibility-layer overhead probe
    # ------------------------------------------------------------------
    def measure_platform_overhead_ms(self) -> Optional[float]:
        """Fixed per-invocation cost of a compatibility layer, in ms.

        Returns None when this adapter has no probe configured (the common
        case), and 0.0 when the probe reports that the underlying binary runs
        natively -- in which case there is nothing to subtract.  Any failure
        degrades to None so a broken probe can never take down a run.
        """
        if not self.overhead_probe_cmd:
            return None

        # Preferred path: ask the persistent server.  The baseline has to be
        # measured in the same process state as the samples it will later be
        # subtracted from; a fresh one-shot interpreter reports ~80 ms more
        # (5087 ms vs ~4987 ms steady) and would over-subtract from every chart,
        # making the algorithm look faster than it is.
        if self.server_cmd:
            try:
                proc = self._ensure_server()
                self._server_id += 1
                req_id = self._server_id
                proc.stdin.write(json.dumps({"id": req_id, "op": "overhead"}) + "\n")
                proc.stdin.flush()
                while True:
                    line = self._read_server_line(proc)
                    if line is None:
                        raise RuntimeError("overhead query timed out")
                    try:
                        resp = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if resp.get("id") != req_id:
                        raise RuntimeError("overhead protocol desync")
                    if resp.get("status") != "ok":
                        raise RuntimeError(str(resp.get("error")))
                    return max(0.0, float(resp.get("overhead_ms", 0.0)))
            except Exception:  # noqa: BLE001
                pass  # fall back to the one-shot probe below

        try:
            proc = subprocess.run(
                self.overhead_probe_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=max(60.0, self.timeout_seconds * 4.0),
            )
            if proc.returncode != 0:
                return None
            return max(0.0, float(self._parse_numeric_output(proc.stdout)))
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Persistent server execution
    # ------------------------------------------------------------------
    def _ensure_server(self) -> subprocess.Popen:
        """Return a running persistent server process, (re)starting if needed."""
        proc = self._server_proc
        if proc is not None and proc.poll() is None:
            return proc
        proc = subprocess.Popen(
            self.server_cmd,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._server_proc = proc
        return proc

    def _read_server_line(self, proc: subprocess.Popen) -> Optional[str]:
        """Read one non-empty line from the server within the timeout budget.

        Returns None on timeout or EOF (server exited).
        """
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([proc.stdout], [], [], remaining)
            if not ready:
                return None
            line = proc.stdout.readline()
            if not line:
                return None  # EOF: server died
            stripped = line.strip()
            if stripped:
                return stripped

    def _calculate_via_server(self, map_path: str) -> float:
        proc = self._ensure_server()
        self._server_id += 1
        req_id = self._server_id

        try:
            proc.stdin.write(json.dumps({"id": req_id, "file": map_path}) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(f"Persistent server stdin closed: {exc}")

        while True:
            line = self._read_server_line(proc)
            if line is None:
                raise RuntimeError(
                    f"Persistent server timed out or exited (timeout={self.timeout_seconds}s)"
                )
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                # Tolerate stray non-protocol output lines on stdout.
                continue
            if resp.get("id") != req_id:
                raise RuntimeError(
                    f"Persistent server protocol desync: got id={resp.get('id')!r}, expected {req_id}"
                )
            if resp.get("status") != "ok":
                raise RuntimeError(f"Persistent server error: {resp.get('error')}")
            return float(resp["difficulty"])

    def _teardown_server(self) -> None:
        """Terminate the persistent server process (if any)."""
        proc = self._server_proc
        self._server_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def calculate(self, target: Union[Beatmap, str, Path]) -> float:
        with materialize_beatmap_path(target) as map_path:
            path_str = str(map_path)
            if self.server_cmd and self._server_failures < _SERVER_MAX_FAILURES:
                try:
                    result = self._calculate_via_server(path_str)
                    self._server_failures = 0
                    return result
                except Exception:
                    # Count the failure, drop the (possibly desynced) server and
                    # retry this sample with a fresh one-shot process.
                    self._server_failures += 1
                    self._teardown_server()
            return self._calculate_oneshot(path_str)
