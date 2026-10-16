#!/usr/bin/env python3
"""Standard CLI entry point for Daniel algorithm conforming to Section 6.1 of CAB-VA."""

import argparse
import json
import sys
from pathlib import Path

# Add Daniel src to sys.path
script_dir = Path(__file__).resolve().parent
daniel_src = script_dir.parent.parent / "test_algorithms" / "Daniel" / "src"
if daniel_src.exists():
    sys.path.insert(0, str(daniel_src))

from cab.adapter.daniel import sr_to_daniel_scale

try:
    import algorithm
except ImportError as e:
    sys.stderr.write(f"Failed to import Daniel algorithm: {e}\n")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Daniel VSRG Difficulty Calculator CLI")
    parser.add_argument("--input", "-i", required=True, help="Path to .osu beatmap file")
    parser.add_argument(
        "--output-format", "-f", default="json", choices=["json", "plain"], help="Output format"
    )
    parser.add_argument(
        "--raw-sr", action="store_true", help="Output raw internal SR instead of Daniel scale"
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        sys.stderr.write(f"Beatmap file not found: {input_path}\n")
        sys.exit(2)

    try:
        sr, _, _, _ = algorithm.calculate(str(input_path), "NM")
        raw_sr = float(sr)
        daniel_val = raw_sr if args.raw_sr else sr_to_daniel_scale(raw_sr)
        if args.output_format == "json":
            print(
                json.dumps(
                    {
                        "difficulty": round(daniel_val, 4),
                        "raw_sr": round(raw_sr, 4),
                        "scale": "sr" if args.raw_sr else "daniel",
                        "status": "ok",
                    }
                )
            )
        else:
            print(f"{daniel_val:.4f}")
    except Exception as exc:
        sys.stderr.write(f"Calculation failed: {exc}\n")
        sys.exit(3)


if __name__ == "__main__":
    main()
