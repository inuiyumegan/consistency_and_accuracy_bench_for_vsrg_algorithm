#!/usr/bin/env python3
"""CLI bridge for the *pre-#24109* official osu!lazer mania difficulty calculator.

Wraps ``cab/adapter/csharp_runners/osu_mania_former/``, which is built against
ppy/osu tag ``2023.815.0`` — the last release cut before ppy/osu#24109
("Adjust LN value scaling to bring overrated LN maps more in line",
merged 2023-08-17).  That PR changed three things in the mania LN strain
handling (``release_threshold`` 24 -> 30, logistic slope 0.5 -> 0.27, and two
extra ``startTime > startTimes[i]`` clauses), which nerfed overrated LN maps
while leaving rice maps untouched.

Consequently this runner reports the **2023.815.0** mania star rating, not
today's. Use it to quantify what that LN fix changed; do not treat it as the
current official algorithm.

Modes (this script is a thin locator/exec passthrough):

  1) One-shot:  python3 osu_mania_former_cli.py <osu_file>   -> prints a float
  2) Server:    python3 osu_mania_former_cli.py --server
     Newline-delimited JSON protocol, see the C# runner's header comment.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_DLL_REL = Path(
    "cab/adapter/csharp_runners/osu_mania_former/bin/Release/net10.0/osu_mania_former.dll"
)


def _find_dotnet() -> str:
    """Locate a usable dotnet host (user-level SDK preferred over system)."""
    candidates = []
    dotnet_root = os.environ.get("DOTNET_ROOT")
    if dotnet_root:
        candidates.append(Path(dotnet_root) / "dotnet")
    candidates.append(Path.home() / ".dotnet" / "dotnet")
    on_path = shutil.which("dotnet")
    if on_path:
        candidates.append(Path(on_path))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    raise FileNotFoundError(
        "dotnet not found. Install the .NET 10 SDK, e.g.:\n"
        "  curl -sSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 10.0"
    )


def _find_dll() -> Path:
    dll = _ROOT / _DLL_REL
    if not dll.is_file():
        raise FileNotFoundError(
            f"osu_mania_former.dll not found at: {dll}\n"
            "Build it first — see "
            "cab/adapter/csharp_runners/osu_mania_former/README.md"
        )
    return dll


def main(argv: list[str]) -> None:
    dotnet = _find_dotnet()
    dll = _find_dll()
    # Replace this process with the .NET runner, so stdin/stdout flow straight
    # through in both one-shot and server modes.
    os.execv(dotnet, [dotnet, str(dll), *argv])


if __name__ == "__main__":
    main(sys.argv[1:])
