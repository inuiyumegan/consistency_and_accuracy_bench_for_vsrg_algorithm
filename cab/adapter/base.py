"""Base algorithm adapter abstraction for CAB-VA."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence, Union

from cab.parser.osu_parser import Beatmap


class BaseAdapter(ABC):
    """Abstract adapter defining the contract between CAB-VA and candidate rating algorithms."""

    def __init__(self, name: str, scale_type: str = "auto"):
        """
        Args:
            name: Human-readable algorithm name.
            scale_type: 'nd', 'non-nd', or 'auto' (determined dynamically).
        """
        self.name = name
        self.scale_type = scale_type

    @abstractmethod
    def calculate(self, target: Union[Beatmap, str, Path]) -> float:
        """Calculate and return scalar difficulty value for a single beatmap.
        
        Args:
            target: Either a parsed Beatmap instance or a file path string/Path.
            
        Returns:
            Scalar difficulty value (float).
        """
        pass

    def calculate_batch(
        self,
        targets: Sequence[Union[Beatmap, str, Path]],
    ) -> list[float]:
        """Calculate difficulty values for a sequence of beatmaps.
        
        Default implementation performs sequential evaluation; subclasses may override
        for batch streaming.
        """
        return [self.calculate(t) for t in targets]
