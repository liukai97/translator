"""Canonical project-relative paths used by translation commands."""

from __future__ import annotations

from pathlib import Path


INPUT_DIR = Path("input")
WORK_DIR = Path("work")
OUTPUT_DIR = Path("output")
SCRIPTS_DIR = Path("scripts")
GLOSSARY_DIR = Path("glossary")

INPUT_BOOK_PATH = INPUT_DIR / "book.md"
CLEAN_BOOK_PATH = WORK_DIR / "book.no_pages.md"
SEGMENTS_PATH = WORK_DIR / "segments.jsonl"
BATCHES_PATH = WORK_DIR / "batches.jsonl"
TRANSLATIONS_PATH = WORK_DIR / "translations.jsonl"
CHECKPOINTS_DIR = WORK_DIR / "checkpoints"
PARALLEL_HTML_PATH = OUTPUT_DIR / "parallel.html"
STYLE_GUIDE_PATH = GLOSSARY_DIR / "style_guide.md"

APPEND_TRANSLATIONS_SCRIPT = SCRIPTS_DIR / "append_translations.py"
ASSEMBLE_PARALLEL_SCRIPT = SCRIPTS_DIR / "assemble_parallel.py"
MERGE_MODEL_OUTPUT_SCRIPT = SCRIPTS_DIR / "merge_model_output.py"
PREPARE_BATCH_REQUEST_SCRIPT = SCRIPTS_DIR / "prepare_batch_request.py"
VALIDATE_TRANSLATION_PROGRESS_SCRIPT = SCRIPTS_DIR / "validate_translation_progress.py"
