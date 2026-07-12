"""Print a compact human-readable translation progress report."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from translation_core.paths import VALIDATE_TRANSLATION_PROGRESS_SCRIPT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show compact translation progress.")
    parser.add_argument("--list-pending", default=10, type=int)
    parser.add_argument("--json", action="store_true", help="Print raw validator JSON.")
    parser.add_argument("--validator", default=VALIDATE_TRANSLATION_PROGRESS_SCRIPT, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    completed = subprocess.run(
        [sys.executable, str(args.validator), "--list-pending", str(args.list_pending)],
        check=True,
        capture_output=True,
        text=True,
    )
    raw = completed.stdout.strip()
    if args.json:
        print(raw)
        return

    report: dict[str, Any] = json.loads(raw)
    translated = int(report["translated_source_segments"])
    total = int(report["segments"])
    pending = int(report["pending_source_segments"])
    percent = (translated / total * 100) if total else 0.0

    print(f"Progress: {translated}/{total} translated ({percent:.1f}%), pending {pending}")
    next_missing = report.get("next_missing_batch")
    if next_missing:
        print(
            "Next: "
            f"{next_missing['batch_id']} "
            f"({next_missing['first_missing_segment_id']}..{next_missing['last_missing_segment_id']}, "
            f"{next_missing['missing_count']} missing)"
        )
    else:
        print("Next: none")

    issues = report.get("issues", {})
    issue_keys = [key for key, value in issues.items() if value]
    print("Issues: " + ("none" if not issue_keys else ", ".join(issue_keys)))

    pending_batches = report.get("pending_batches", [])
    if pending_batches:
        print("Pending batches:")
        for batch in pending_batches:
            print(
                f"- {batch['batch_id']}: {batch['missing_count']} "
                f"({batch['first_missing_segment_id']}..{batch['last_missing_segment_id']})"
            )


if __name__ == "__main__":
    main()
