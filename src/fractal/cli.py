"""Command-line entrypoint for Fractal."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from fractal import SYSTEM_VERSION


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="fractal",
        description="Operate a Fractal continuous-improvement workspace.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("version", help="Show the active Fractal system version.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Fractal command-line interface."""
    args = build_parser().parse_args(argv)
    if args.action == "version":
        print(SYSTEM_VERSION)
        return 0
    raise AssertionError(f"Unhandled action: {args.action}")
