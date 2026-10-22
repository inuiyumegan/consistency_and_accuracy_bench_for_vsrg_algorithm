"""CLI subprocess adapter conforming to Section 6 of CAB-VA technical specification.

Supports two execution modes:

- **One-shot** (default): a fresh subprocess is spawned for every beatmap.
- **Persistent server mode**: when ``server_cmd`` is provided, a long-lived child
  process is kept alive *per worker process* and driven over a newline-delimited
  JSON protocol (Section 6.2 of the technical spec):

      benchmark -> server:  {"id": 1, "file": "/path/to/map.osu"}
      server -> benchmark:  {"id": 1, "difficulty": 14.28, "status": "ok"}

Robustness rules:

* **Never invent a float.**  A process that fails must raise; a stdout that is
  empty, non-numeric, ``NaN`` or ``inf`` must raise.  Returning ``0.0`` turned
  every crash into a legitimate-looking difficulty of zero.
* **Never leak a process.**  The server runs in its own process group and is
  torn down with SIGTERM -> wait -> SIGKILL -> wait, so Wine/msd.exe and other
  grandchildren cannot survive a timeout.
* **Read with a thread, not ``select``.**  ``select`` on a ``TextIOWrapper``
  reports "not ready" while complete lines sit in Python's buffer, and Windows
  cannot select on pipes at all.  A dedicated reader thread feeding a queue has
  neither problem.
* Server ``stderr`` is retained (tail buffer) and surfaced in errors instead of
  being sent to ``DEVNULL``.
"""

from __future__ import annotations

import json
import math
import os
import queue
import shlex
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, List, Optional, Tuple, Union

from cab.adapter.base import BaseAdapter
from cab.adapter.in_memory import materialize_beatmap_path
from cab.parser.osu_parser import Beatmap

# Consecutive persistent-server failures tolerated before giving up on it
# (per adapter instance / worker process) and using one-shot mode only.
_SERVER_MAX_FAILURES = 3

#: Shell metacharacters that force a ``shell=True`` invocation.
_SHELL_META = set("|&;<>()$`*?[]{}~!#\n")

#: How many server stderr lines to keep for diagnostics.
_STDERR_TAIL = 200

#: Sentinel pushed by the reader thread on EOF.
_EOF = object()


def _build_command(cmd: str) -> Tuple[Union[List[str], str], bool]:
    """Split a command into argv when it is a plain command line.

    Returns ``(argv_or_cmd, use_shell)``.  Plain command lines avoid the shell
    entirely, so paths containing spaces work on every platform; commands with
    shell syntax (pipes, ``&&``, redirections) keep ``shell=True``.
    """
    stripped = cmd.strip()
    if any(ch in _SHELL_META for ch in stripped):
        return stripped, True
    try:
        argv = shlex.split(stripped)
    except ValueError:
        return stripped, True
    if not argv:
        raise ValueError(f"empty command: {cmd!r}")
    return argv, False


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
            scale_type: 'nd', 'non-nd', or 'auto'.
            timeout_seconds: Subprocess timeout in seconds (per evaluation).
            server_cmd: Optional persistent-server command (no {input} placeholder).
            overhead_probe_cmd: Optional one-shot command that prints a fixed
                per-invocation cost in milliseconds.
        """
        super().__init__(name=name, scale_type=scale_type)
        if not timeout_seconds or float(timeout_seconds) <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds}")
        self.cmd_template = cmd_template
        self.timeout_seconds = float(timeout_seconds)
        self.server_cmd = server_cmd
        self.overhead_probe_cmd = overhead_probe_cmd

        # Persistent-server state (lives for the lifetime of this adapter
        # instance, i.e. the lifetime of one pool worker process).
        self._server_proc: Optional[subprocess.Popen] = None
        self._server_queue: Optional["queue.Queue"] = None
        self._server_id = 0
        self._server_failures = 0
        self._server_stderr: Deque[str] = deque(maxlen=_STDERR_TAIL)
        self._server_disabled = False

    # ------------------------------------------------------------------
    # One-shot execution
    # ------------------------------------------------------------------
    def _calculate_oneshot(self, map_path: str) -> float:
        quoted_path = shlex.quote(map_path)
        # str.replace, not str.format: a JSON payload inside the command
        # (e.g. --meta '{"k":1}') made .format() raise KeyError.
        if "{input}" in self.cmd_template:
            cmd = self.cmd_template.replace("{input}", quoted_path)
        else:
            cmd = f"{self.cmd_template} --input {quoted_path} --output-format json"

        argv, use_shell = _build_command(cmd)
        try:
            proc = subprocess.run(
                argv,
                shell=use_shell,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"{self.name}: one-shot calculation timed out after "
                f"{self.timeout_seconds:g}s for {map_path}"
            )

        if proc.returncode != 0:
            raise RuntimeError(
                f"{self.name}: algorithm process exited with code {proc.returncode}: "
                f"{(proc.stderr or '').strip()}"
            )

        return self._parse_numeric_output(proc.stdout, proc.stderr)

    def _parse_numeric_output(self, stdout: str, stderr: str = "") -> float:
        """Parse a finite float from stdout (JSON object/float or last line).

        Raises ``RuntimeError`` on empty, non-numeric or non-finite output: a
        failed measurement must never be reported as a difficulty.
        """
        text = (stdout or "").strip()
        if not text:
            raise RuntimeError(
                f"{self.name}: empty stdout from algorithm; "
                f"stderr tail: {(stderr or '').strip()[-500:]!r}"
            )

        value: Optional[float] = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            last_line = text.splitlines()[-1].strip()
            try:
                value = float(last_line)
            except ValueError:
                raise RuntimeError(
                    f"{self.name}: could not parse a number from stdout "
                    f"(last line {last_line[:120]!r}); "
                    f"stderr tail: {(stderr or '').strip()[-500:]!r}"
                ) from None
        else:
            if isinstance(data, dict):
                for key in ("difficulty", "sr", "star_rating", "rating", "val"):
                    if key in data:
                        try:
                            value = float(data[key])
                        except (TypeError, ValueError):
                            raise RuntimeError(
                                f"{self.name}: JSON field {key!r} is not numeric: "
                                f"{data[key]!r}"
                            ) from None
                        break
                else:
                    raise RuntimeError(
                        f"{self.name}: no difficulty key in JSON response: {data!r}"
                    )
            else:
                try:
                    value = float(data)
                except (TypeError, ValueError):
                    raise RuntimeError(
                        f"{self.name}: JSON response is not a number: {data!r}"
                    ) from None

        if value is None or not math.isfinite(value):
            raise RuntimeError(
                f"{self.name}: algorithm returned a non-finite difficulty ({value!r}); "
                "treating the sample as failed instead of scoring NaN/inf"
            )
        return value

    # ------------------------------------------------------------------
    # Compatibility-layer overhead probe
    # ------------------------------------------------------------------
    def measure_platform_overhead_ms(self) -> Optional[float]:
        """Fixed per-invocation cost of a compatibility layer, in ms.

        Returns None when this adapter has no probe configured (the common
        case), and 0.0 when the probe reports that the underlying binary runs
        natively.  Any failure degrades to None so a broken probe can never take
        down a run.
        """
        if not self.overhead_probe_cmd:
            return None

        # Preferred path: ask the persistent server.  The baseline has to be
        # measured in the same process state as the samples it will later be
        # subtracted from.
        if self.server_cmd and not self._server_disabled:
            try:
                proc = self._ensure_server()
                self._server_id += 1
                req_id = self._server_id
                proc.stdin.write(json.dumps({"id": req_id, "op": "overhead"}) + "\n")
                proc.stdin.flush()
                while True:
                    line = self._read_server_line()
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
                    value = float(resp.get("overhead_ms", 0.0))
                    if not math.isfinite(value):
                        raise RuntimeError("non-finite overhead")
                    return max(0.0, value)
            except Exception:  # noqa: BLE001
                # The server may now be desynced: drop it so the failure does
                # not leak into the next calculate() call.
                self._teardown_server()

        try:
            argv, use_shell = _build_command(self.overhead_probe_cmd)
            proc = subprocess.run(
                argv,
                shell=use_shell,
                capture_output=True,
                text=True,
                timeout=max(60.0, self.timeout_seconds * 4.0),
            )
            if proc.returncode != 0:
                return None
            return max(0.0, self._parse_numeric_output(proc.stdout, proc.stderr))
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Persistent server execution
    # ------------------------------------------------------------------
    @staticmethod
    def _pump_stdout(stream, sink: "queue.Queue") -> None:
        try:
            for line in stream:
                if line and line.strip():
                    sink.put(line.strip())
        except (ValueError, OSError):
            pass
        finally:
            sink.put(_EOF)

    def _pump_stderr(self, stream) -> None:
        try:
            for line in stream:
                if line:
                    self._server_stderr.append(line.rstrip("\n"))
        except (ValueError, OSError):
            pass

    def _ensure_server(self) -> subprocess.Popen:
        """Return a running persistent server process, (re)starting if needed."""
        proc = self._server_proc
        if proc is not None and proc.poll() is None:
            return proc
        if self._server_disabled:
            raise RuntimeError("persistent server disabled after repeated failures")
        if not self.server_cmd:
            raise RuntimeError("no server command configured")

        argv, use_shell = _build_command(self.server_cmd)
        proc = subprocess.Popen(
            argv,
            shell=use_shell,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            # Own process group: tear-down can then kill wine/msd.exe children.
            start_new_session=True,
        )
        self._server_proc = proc
        self._server_stderr.clear()
        self._server_queue = queue.Queue()
        threading.Thread(
            target=self._pump_stdout,
            args=(proc.stdout, self._server_queue),
            daemon=True,
            name=f"cab-server-out-{proc.pid}",
        ).start()
        threading.Thread(
            target=self._pump_stderr,
            args=(proc.stderr,),
            daemon=True,
            name=f"cab-server-err-{proc.pid}",
        ).start()
        return proc

    def _read_server_line(self) -> Optional[str]:
        """Read one protocol line, or ``None`` on timeout / EOF."""
        q = self._server_queue
        if q is None:
            return None
        try:
            item = q.get(timeout=self.timeout_seconds)
        except queue.Empty:
            return None
        if item is _EOF:
            # Re-queue so subsequent reads also see EOF instead of hanging.
            q.put(_EOF)
            return None
        return str(item)

    def _server_diagnostic(self) -> str:
        tail = "\n".join(list(self._server_stderr)[-15:])
        return f"\nserver stderr tail:\n{tail}" if tail else ""

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
            line = self._read_server_line()
            if line is None:
                raise RuntimeError(
                    f"Persistent server timed out or exited "
                    f"(timeout={self.timeout_seconds:g}s){self._server_diagnostic()}"
                )
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                # Tolerate stray non-protocol output lines on stdout.
                continue
            if resp.get("id") != req_id:
                raise RuntimeError(
                    f"Persistent server protocol desync: got id={resp.get('id')!r}, "
                    f"expected {req_id}"
                )
            if resp.get("status") != "ok":
                raise RuntimeError(f"Persistent server error: {resp.get('error')}")
            if "difficulty" not in resp:
                raise RuntimeError(
                    f"Persistent server response has no 'difficulty': {resp!r}"
                )
            value = float(resp["difficulty"])
            if not math.isfinite(value):
                raise RuntimeError(
                    f"Persistent server returned non-finite difficulty {value!r}"
                )
            return value

    def _teardown_server(self) -> None:
        """Terminate the persistent server and its whole process group."""
        proc, self._server_proc = self._server_proc, None
        self._server_queue = None
        if proc is None:
            return

        def _signal_group(sig) -> None:
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.send_signal(sig)
                except (ProcessLookupError, OSError):
                    pass
            except OSError:
                try:
                    proc.terminate()
                except OSError:
                    pass

        if proc.poll() is None:
            _signal_group(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_group(signal.SIGKILL)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        else:
            # Reap the zombie even if the child already exited.
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass

        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    def __del__(self):  # pragma: no cover - best-effort cleanup
        try:
            self._teardown_server()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def calculate(self, target: Union[Beatmap, str, Path]) -> float:
        with materialize_beatmap_path(target) as map_path:
            path_str = str(map_path)
            if self.server_cmd and not self._server_disabled and self._server_failures < _SERVER_MAX_FAILURES:
                try:
                    result = self._calculate_via_server(path_str)
                    self._server_failures = 0
                    return result
                except Exception:
                    # Count the failure, drop the (possibly desynced) server and
                    # retry this sample with a fresh one-shot process.
                    self._server_failures += 1
                    self._teardown_server()
                    if self._server_failures >= _SERVER_MAX_FAILURES:
                        self._server_disabled = True
                        import warnings

                        warnings.warn(
                            f"{self.name}: persistent server disabled after "
                            f"{self._server_failures} consecutive failures; "
                            f"falling back to one-shot for the rest of this worker"
                            f"{self._server_diagnostic()}",
                            RuntimeWarning,
                            stacklevel=2,
                        )
            return self._calculate_oneshot(path_str)
