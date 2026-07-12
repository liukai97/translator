"""Submit the current batch output and prepare the next translation request.

The helper is intentionally stateless. ``work/translations.jsonl`` determines
the first pending batch. If that batch already has model output, the helper
validates and appends it before recalculating progress. By default it then
prints the next request; with ``--assemble`` it validates current progress and
rebuilds the parallel HTML instead.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from translation_core.cli import emit_json_report
from translation_core.jsonl import read_jsonl as _read_jsonl
from translation_core.paths import (
    APPEND_TRANSLATIONS_SCRIPT,
    ASSEMBLE_PARALLEL_SCRIPT,
    BATCHES_PATH,
    CHECKPOINTS_DIR,
    MERGE_MODEL_OUTPUT_SCRIPT,
    PARALLEL_HTML_PATH,
    PREPARE_BATCH_REQUEST_SCRIPT,
    SEGMENTS_PATH,
    TRANSLATIONS_PATH,
    VALIDATE_TRANSLATION_PROGRESS_SCRIPT,
)
from translation_core.progress import completed_translation_ids, first_pending_batch
from translation_core.validation import validate_batches, validate_translations


REQUEST_BEGIN = "--- TRANSLATION REQUEST BEGIN ---"
REQUEST_END = "--- TRANSLATION REQUEST END ---"


def read_jsonl(path: Path, required: bool = True) -> list[dict[str, Any]]:
    return _read_jsonl(path, required, encoding="utf-8-sig")


def run_json_command(command: list[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"{label} failed: {details}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"{label} produced no JSON report")
    try:
        report = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} produced invalid JSON: {lines[-1]}") from exc
    if not isinstance(report, dict) or not report.get("ok", True):
        raise RuntimeError(f"{label} reported failure: {report}")
    return report


def validate_progress(
    args: argparse.Namespace, expected_batch_id: str | None = None
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(args.validator),
        "--segments",
        str(args.segments),
        "--batches",
        str(args.batches),
        "--translations",
        str(args.translations),
    ]
    if expected_batch_id:
        command.extend(["--expect-batches", expected_batch_id])
    command.extend(["--list-pending", "1"])
    return run_json_command(command, "translation validation")


def prepare_request(args: argparse.Namespace, batch_id: str) -> tuple[dict[str, Any], str]:
    report = run_json_command(
        [
            sys.executable,
            str(args.prepare_script),
            "--batches",
            str(args.batches),
            "--translations",
            str(args.translations),
            "--batch",
            batch_id,
            "--context-segments",
            str(args.context_segments),
            "--output-dir",
            str(args.output_dir),
            "--next-command",
            ".\\.python312\\python.exe scripts\\translation_task_loop.py",
        ],
        "request preparation",
    )
    request_path = Path(str(report["request_path"]))
    return report, request_path.read_text(encoding="utf-8")


def ensure_output_is_current(request_path: Path, model_output_path: Path) -> None:
    if not request_path.exists():
        return
    if model_output_path.stat().st_mtime < request_path.stat().st_mtime:
        raise ValueError(
            f"refuse stale model output older than its request: {model_output_path}"
        )


def merge_current_output(
    args: argparse.Namespace,
    batch_id: str,
    model_output_path: Path,
) -> dict[str, Any]:
    return run_json_command(
        [
            sys.executable,
            str(args.merge_script),
            str(model_output_path),
            "--batch",
            batch_id,
            "--batches",
            str(args.batches),
            "--segments",
            str(args.segments),
            "--translations",
            str(args.translations),
            "--append-script",
            str(args.append_script),
            "--append",
        ],
        f"merge for {batch_id}",
    )


def assemble(args: argparse.Namespace) -> dict[str, Any]:
    return run_json_command(
        [
            sys.executable,
            str(args.assembler),
            "--segments",
            str(args.segments),
            "--translations",
            str(args.translations),
            "--batches",
            str(args.batches),
            "--output",
            str(args.parallel_output),
        ],
        "parallel HTML assembly",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit current pending-batch output and prepare the next request."
    )
    parser.add_argument(
        "--assemble",
        action="store_true",
        help="After submitting current output, validate progress and rebuild HTML instead of preparing another request.",
    )
    parser.add_argument("--output-dir", default=CHECKPOINTS_DIR, type=Path)
    parser.add_argument("--segments", default=SEGMENTS_PATH, type=Path)
    parser.add_argument("--batches", default=BATCHES_PATH, type=Path)
    parser.add_argument("--translations", default=TRANSLATIONS_PATH, type=Path)
    parser.add_argument("--parallel-output", default=PARALLEL_HTML_PATH, type=Path)
    parser.add_argument("--context-segments", default=8, type=int)
    parser.add_argument("--prepare-script", default=PREPARE_BATCH_REQUEST_SCRIPT, type=Path)
    parser.add_argument("--merge-script", default=MERGE_MODEL_OUTPUT_SCRIPT, type=Path)
    parser.add_argument("--append-script", default=APPEND_TRANSLATIONS_SCRIPT, type=Path)
    parser.add_argument("--validator", default=VALIDATE_TRANSLATION_PROGRESS_SCRIPT, type=Path)
    parser.add_argument("--assembler", default=ASSEMBLE_PARALLEL_SCRIPT, type=Path)
    parser.add_argument(
        "--no-print-request",
        action="store_true",
        help="Print only the JSON transition report, not the prepared Markdown request.",
    )
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    batches = read_jsonl(args.batches)
    _, batch_segment_ids = validate_batches(batches)
    translations = read_jsonl(args.translations, required=False)
    validate_translations(translations, known_segment_ids=set(batch_segment_ids))
    completed_ids = completed_translation_ids(translations)
    latest_validation_report = validate_progress(args)
    performed = ["validate_progress"]
    submitted_batch_id: str | None = None

    pending = first_pending_batch(batches, completed_ids)
    if pending is not None:
        batch_id = str(pending["batch_id"])
        request_path = args.output_dir / f"{batch_id}_request.md"
        model_output_path = args.output_dir / f"{batch_id}_output.jsonl"
        if model_output_path.exists() and model_output_path.stat().st_size > 0:
            ensure_output_is_current(request_path, model_output_path)
            merge_current_output(args, batch_id, model_output_path)
            latest_validation_report = validate_progress(args, batch_id)
            performed.extend([f"merge_output:{batch_id}", f"validate_batch:{batch_id}"])
            submitted_batch_id = batch_id
            translations = read_jsonl(args.translations, required=False)
            validate_translations(translations, known_segment_ids=set(batch_segment_ids))
            completed_ids = completed_translation_ids(translations)
            pending = first_pending_batch(batches, completed_ids)

    if args.assemble:
        assembly_report = assemble(args)
        performed.append("assemble_parallel")
        report = {
            "ok": True,
            "action": "assembled",
            "performed": performed,
            "submitted_batch_id": submitted_batch_id,
            "assembled": True,
            "parallel_output": str(args.parallel_output),
            "next_pending_batch": latest_validation_report.get("next_missing_batch"),
            "validation": latest_validation_report,
            "assembly": assembly_report,
            "message": "current progress validated and parallel HTML rebuilt",
        }
        emit_json_report(report)
        return

    if pending is None:
        report = {
            "ok": True,
            "action": "no_pending",
            "performed": performed,
            "submitted_batch_id": submitted_batch_id,
            "assembled": False,
            "message": "no pending batches; run with --assemble to rebuild HTML",
        }
        emit_json_report(report)
        return

    current_batch_id = str(pending["batch_id"])
    request_report, request = prepare_request(args, current_batch_id)
    performed.append(f"prepare_request:{current_batch_id}")
    report = {
        "ok": True,
        "action": "translate",
        "performed": performed,
        "submitted_batch_id": submitted_batch_id,
        "batch_id": current_batch_id,
        "request_path": request_report["request_path"],
        "model_output_path": request_report["model_output_path"],
        "target_segment_count": request_report["target_segment_count"],
        "assembled": False,
        "next_step": "Translate the request below, write model_output_path, then run this helper again.",
    }
    emit_json_report(report)
    if not args.no_print_request:
        print(REQUEST_BEGIN)
        print(request, end="" if request.endswith("\n") else "\n")
        print(REQUEST_END)


if __name__ == "__main__":
    main()
