"""Tests for CliAdapter persistent server mode (protocol + process reuse + recovery)."""

import os
import sys
import time
import unittest

from cab.adapter.cli import CliAdapter

_SAMPLE = os.path.abspath("samples/4k/jack/air's gravity(signicial dan).osu")
_MOCK_CMD = f"{sys.executable} {os.path.abspath('tests/mock_persistent_server.py')}"
_EXPECTED = float(len(os.path.basename(_SAMPLE)))


class TestCliAdapterServerMode(unittest.TestCase):
    def test_server_mode_roundtrip_and_reuse(self):
        adapter = CliAdapter(
            name="Mock",
            cmd_template="echo 0.0",
            scale_type="non-ng",
            server_cmd=_MOCK_CMD,
        )
        try:
            v1 = adapter.calculate(_SAMPLE)
            self.assertAlmostEqual(v1, _EXPECTED, places=6)

            pid1 = adapter._server_proc.pid
            v2 = adapter.calculate(_SAMPLE)
            self.assertAlmostEqual(v2, _EXPECTED, places=6)
            # Persistent process must be reused across calls
            self.assertEqual(adapter._server_proc.pid, pid1)
        finally:
            adapter._teardown_server()

    def test_server_mode_restarts_after_death(self):
        adapter = CliAdapter(
            name="Mock",
            cmd_template="echo 0.0",
            scale_type="non-ng",
            server_cmd=_MOCK_CMD,
        )
        try:
            adapter.calculate(_SAMPLE)
            pid1 = adapter._server_proc.pid
            adapter._server_proc.terminate()
            time.sleep(0.3)
            v = adapter.calculate(_SAMPLE)
            self.assertAlmostEqual(v, _EXPECTED, places=6)
            self.assertIsNotNone(adapter._server_proc)
            self.assertNotEqual(adapter._server_proc.pid, pid1)
        finally:
            adapter._teardown_server()

    def test_oneshot_mode_still_works(self):
        # No server_cmd -> legacy one-shot path via cmd_template
        adapter = CliAdapter(
            name="Mock",
            cmd_template=f"{sys.executable} -c \"print('12.5')\"",
            scale_type="non-ng",
        )
        self.assertIsNone(adapter.server_cmd)
        self.assertAlmostEqual(adapter.calculate(_SAMPLE), 12.5, places=6)


if __name__ == "__main__":
    unittest.main()
