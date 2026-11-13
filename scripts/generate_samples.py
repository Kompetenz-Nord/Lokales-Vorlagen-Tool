#!/usr/bin/env python3
"""Synthetische Musterdateien erzeugen („MUSTER – KEINE ECHTDATEN“).

Aufruf:
    python3 scripts/generate_samples.py                 # nach tests/fixtures/synthetic
    python3 scripts/generate_samples.py /pfad/zum/ordner
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from insurance_app.samples.generator import generate_sample_set  # noqa: E402

DEFAULT_OUT = ROOT / "tests" / "fixtures" / "synthetic"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetische Musterdokumente (Musterfall) erzeugen.")
    parser.add_argument(
        "out_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Zielordner (Standard: {DEFAULT_OUT.relative_to(ROOT)})",
    )
    args = parser.parse_args(argv)
    sample = generate_sample_set(args.out_dir)
    print(f"Musterdateien in {args.out_dir}:")
    for name, path in sample.files.items():
        size_kb = path.stat().st_size / 1024
        print(f"  {name:40s} {size_kb:8.1f} KB  ({len(sample.ground_truth[name])} Soll-Werte)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
