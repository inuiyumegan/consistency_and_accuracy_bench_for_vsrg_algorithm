"""Adapter for Daniel (VSRG Rice Difficulty Calculator).

Repository: https://github.com/TheBagelOfMan/Daniel

Daniel computes internal Star Rating (SR) and maps it to the Daniel Difficulty Scale
where:
  Daniel 11.5 = Alpha Mid (ND 11.0)
  Daniel 15.5 = Epsilon Mid (ND 15.0)
  Daniel - 0.5 = ND (strict theoretical conversion standard).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Union

from cab.adapter.base import BaseAdapter
from cab.adapter.in_memory import materialize_beatmap_path
from cab.parser.osu_parser import Beatmap

DAN_MEANS = {
    "Alpha":   6.562,
    "Beta":    6.957,
    "Gamma":   7.459,
    "Delta":   7.939,
    "Epsilon": 9.095,
    "Zeta":    9.473,
    "Eta":     10.162,
    "Theta":   10.782,
}
ORDER = list(DAN_MEANS.keys())
DAN_ORDER_START = 11


def _precompute_dan_boundaries():
    means = [DAN_MEANS[d] for d in ORDER]
    boundaries = []
    for i in range(len(ORDER)):
        mean = means[i]
        lower = (means[i - 1] + mean) / 2 if i > 0 else mean - ((means[1] + mean) / 2 - mean)
        upper = (mean + means[i + 1]) / 2 if i < len(means) - 1 else mean + (mean - means[i - 1]) / 2
        boundaries.append((lower, upper))
    return boundaries


_DAN_BOUNDARIES = _precompute_dan_boundaries()


def sr_to_daniel_scale(sr: float) -> float:
    """Map internal Daniel SR to the Daniel Difficulty Scale (Daniel - 0.5 = ND).

    Continuous, strictly monotonic piecewise-linear function aligning with official
    Daniel Dan ticks:
      Alpha Mid:   11.5 <=> ND 11.0
      Epsilon Mid: 15.5 <=> ND 15.0
      Theta Mid:   18.5 <=> ND 18.0
    """
    first_lower, first_upper = _DAN_BOUNDARIES[0]
    if sr < first_lower:
        slope = 1.0 / (first_upper - first_lower)
        return float(DAN_ORDER_START + (sr - first_lower) * slope)

    last_lower, last_upper = _DAN_BOUNDARIES[-1]
    if sr >= last_upper:
        slope = 1.0 / (last_upper - last_lower)
        return float((DAN_ORDER_START + len(ORDER)) + (sr - last_upper) * slope)

    for i in range(len(ORDER)):
        lower, upper = _DAN_BOUNDARIES[i]
        if lower <= sr < upper:
            t = (sr - lower) / (upper - lower)
            return float(DAN_ORDER_START + i + t)

    return float(DAN_ORDER_START + len(ORDER))


class DanielAdapter(BaseAdapter):
    """Direct high-performance Python in-process adapter for Daniel algorithm."""

    def __init__(
        self,
        daniel_root: Optional[Union[str, Path]] = None,
        scale_type: str = "daniel",
        return_raw_sr: bool = False,
    ):
        """
        Args:
            daniel_root: Path to Daniel repository or Daniel/src directory.
            scale_type: 'daniel' (where daniel - 0.5 = ND) or 'non-nd'.
            return_raw_sr: If True, returns raw SR; if False (default), returns Daniel scale.
        """
        super().__init__(name="Daniel", scale_type=scale_type)
        self.return_raw_sr = return_raw_sr
        if daniel_root is None:
            cand = Path("test_algorithms/Daniel").resolve()
            if not cand.exists():
                cand = Path(__file__).resolve().parent.parent.parent / "test_algorithms" / "Daniel"
            daniel_root = cand
        else:
            daniel_root = Path(daniel_root).resolve()

        src_dir = daniel_root / "src" if (daniel_root / "src").exists() else daniel_root
        if not (src_dir / "algorithm.py").exists():
            raise FileNotFoundError(f"Could not find algorithm.py in {src_dir}")

        self.src_dir = str(src_dir)
        self._load_daniel_module()

    def _load_daniel_module(self):
        """Dynamically import algorithm.py from Daniel source directory."""
        if self.src_dir not in sys.path:
            sys.path.insert(0, self.src_dir)
        import algorithm  # type: ignore
        self.algo = algorithm

    def calculate(self, target: Union[Beatmap, str, Path]) -> float:
        """Calculate Daniel difficulty value.
        
        Returns difficulty on the Daniel scale (e.g. 15.5 for ND 15.0)
        unless return_raw_sr is explicitly True.
        """
        with materialize_beatmap_path(target) as path:
            sr, _, _, _ = self.algo.calculate(str(path), "NM")
            raw_sr = float(sr)
            if self.return_raw_sr:
                return raw_sr
            return sr_to_daniel_scale(raw_sr)
