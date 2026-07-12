"""Run checkpoint validation and rebuild the parallel HTML output."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from translation_core.paths import ASSEMBLE_PARALLEL_SCRIPT, VALIDATE_TRANSLATION_PROGRESS_SCRIPT


def run_command(args: list[str]) -> None:
    print("RUN " + " ".join(args), flush=True)
    subprocess.run(args, check=True, text=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a translation checkpoint and rebuild output.")
    parser.add_argument("--from-batch", help="First batch ID in a consecutive checkpoint.")
    parser.add_argument("--count", type=int, help="Number of consecutive batches in the checkpoint.")
    parser.add_argument("--batches", nargs="*", default=[], help="Explicit batch IDs in the checkpoint.")
    parser.add_argument("--list-pending", default=12, type=int)
    parser.add_argument("--skip-assemble", action="store_true")
    parser.add_argument("--validator", default=VALIDATE_TRANSLATION_PROGRESS_SCRIPT, type=Path)
    parser.add_argument("--assembler", default=ASSEMBLE_PARALLEL_SCRIPT, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batches and (args.from_batch or args.count is not None):
        raise SystemExit("use either --batches or --from-batch/--count, not both")
    if bool(args.from_batch) != (args.count is not None):
        raise SystemExit("--from-batch and --count must be used together")

    validate_args = [sys.executable, str(args.validator)]
    if args.batches:
        validate_args.extend(["--expect-batches", *args.batches])
    elif args.from_batch:
        validate_args.extend(["--expect-from", args.from_batch, "--expect-count", str(args.count)])
    validate_args.extend(["--list-pending", str(args.list_pending)])
    run_command(validate_args)

    if not args.skip_assemble:
        run_command([sys.executable, str(args.assembler)])

    run_command([sys.executable, str(args.validator), "--list-pending", str(args.list_pending)])


if __name__ == "__main__":
    main()
