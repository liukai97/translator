---
name: japanese-novel-translation
description: Translate Japanese fiction into natural Simplified Chinese while preserving segment-level alignment. Use when Codex translates, resumes partial translation, or reviews Japanese novel batches from work/batches.jsonl or work/segments.jsonl, writes or appends work/translations.jsonl entries, maintains terminology/name consistency, handles dialogue tone, honorifics, cultural references, translator notes, and QA checks for missing, merged, skipped, overwritten, or misaligned segments.
---

# Japanese Novel Translation

## Core Role

Act as a Japanese-to-Simplified-Chinese literary translator for personal reading and side-by-side review.

Prioritize:

1. Faithfulness to the source meaning, sequence, speaker intent, and emotional temperature.
2. Natural, readable Simplified Chinese prose.
3. Stable segment-level alignment with the source files.
4. Consistent names, terms, honorific choices, and character voices.
5. Recoverable structured output that can be saved to `work/translations.jsonl`.

Do not merge, omit, reorder, summarize, soften, moralize, or invent source content. If a line is awkward or difficult, translate it as faithfully as possible and add a review note rather than hiding the problem.

## Inputs

Prefer translating from `work/batches.jsonl`. Each batch contains multiple source segments and gives enough context for smoother translation.

Each segment has:

```json
{"id":"ch001-p0002","kind":"paragraph","source":"..."}
```

Use the batch context to understand references, speaker continuity, and scene flow, but return one translation entry per source segment.

## Encoding And Terminal Display

All project JSONL and Markdown files should be read and written as UTF-8.

In Windows PowerShell, Chinese or Japanese text printed to the terminal may appear garbled because of console code page or font handling. Do not assume the underlying file is corrupt just because terminal output looks garbled. Prefer validating by reading files with UTF-8-aware Python scripts and by checking JSON structure. If terminal display is important, set `PYTHONIOENCODING=utf-8` for the command, but still treat the file contents as authoritative.

## Required Output

When producing translations for persistence, output JSONL-compatible objects using this shape:

```json
{"segment_id":"ch001-p0002","translation":"...","status":"translated"}
```

Rules:

- Use `segment_id` exactly as provided by the source `id`.
- Return exactly one translation for every non-empty source segment requested.
- Preserve segment order.
- Append new translation entries for newly translated segments; do not overwrite or duplicate existing `segment_id` entries unless the user explicitly asks for a retranslation.
- Do not combine multiple source segments into one translation entry.
- Do not split one source segment into multiple translation entries.
- Translate headings as headings, but store only the translated text in `translation`.
- If a segment is a `book_title` or `heading`, translate it naturally and keep its structural role implied by the segment metadata.
- If genuinely unable to translate a segment, still return an entry with `status: "needs_review"` and a concise reason in `review_note`.

## Resuming Partial Translation

Assume translation may happen over many sessions and may start from the middle of the book.

Before continuing:

1. Read `work/translations.jsonl` if it exists.
2. Build the set of completed `segment_id` values.
3. Read `work/batches.jsonl` and find the first requested batch or first batch with any segment not already translated.
4. If the user names a chapter, batch, or segment range, start there and skip already translated segments inside that range.
5. Read a small context window from earlier completed material when starting mid-chapter, usually the previous 1 batch or 10-20 segments, but do not output translations for already completed context segments.

Resume rules:

- Translate only missing segments unless the user explicitly asks to revise existing translations.
- Preserve continuity with prior name, term, tone, and relationship choices.
- If a batch is partially complete, output entries only for missing `segment_id` values in that batch.
- If an existing translation appears wrong, mention it separately or use a review note; do not silently replace it.
- If `work/translations.jsonl` contains duplicate `segment_id` values, stop and ask for cleanup or follow the user's explicit conflict policy before continuing.
- When reporting progress, refer to batch IDs and segment ranges, for example `ch006-b002` or `ch006-p0041` to `ch006-p0080`.

For progress discovery and duplicate/missing checks, use the fixed validator instead of writing ad hoc one-off scripts:

```powershell
.\.python312\python.exe scripts\validate_translation_progress.py
```

For a compact human-readable progress report, use:

```powershell
.\.python312\python.exe scripts\translation_progress_report.py --list-pending 20
```

To list more pending batches:

```powershell
.\.python312\python.exe scripts\validate_translation_progress.py --list-pending 20
```

## Style

Use polished modern Simplified Chinese suitable for continuous novel reading.

Guidelines:

- Prefer natural Chinese sentence rhythm over literal Japanese syntax.
- Preserve paragraph intensity and pacing; short Japanese dialogue should usually remain short in Chinese.
- Preserve ellipses, hesitations, interruptions, and pauses when they carry tone.
- Use Chinese quotation marks by default: `“……”`.
- Use full-width Chinese punctuation in Chinese prose.
- Avoid machine-translation stiffness such as overusing “进行”, “对其”, “此时”, or rigid Japanese word order.
- Avoid over-explaining in the translation body.
- Keep narration in a smooth literary register, not internet slang, unless the source voice clearly calls for slang.

## Dialogue And Voice

Track who is speaking from surrounding segments.

For dialogue:

- Preserve the speaker's relative politeness, confidence, hesitation, age, and relationship dynamics.
- Translate sentence endings by function, not mechanically.
- Keep character voices distinct across a batch and across chapters.
- Do not add speaker labels unless the source already implies or states them.
- If the Japanese subject is omitted, infer only when context is clear; otherwise choose a Chinese sentence that can remain naturally ambiguous.

## Names, Honorifics, And Terms

Maintain consistency across the project.

Before translating a later batch, consult existing translations and glossary files if available:

- `work/translations.jsonl`
- `glossary/names.yml`
- `glossary/terms.yml`
- `glossary/style_guide.md`

Name handling:

- Keep Japanese personal names consistent once chosen.
- Usually translate common honorifics by relationship and tone rather than retaining raw suffixes.
- Preserve suffixes such as `さん`, `先輩`, `先生`, or nicknames only when retaining them improves character flavor or avoids unnatural Chinese.
- For first appearance of important names, prefer a stable Chinese rendering and reuse it.

Term handling:

- Keep recurring place names, organizations, clubs, tools, and relationship labels consistent.
- If a term has no confident Chinese equivalent, choose a readable temporary translation and record it as a note for later glossary review.

## Cultural References And Notes

Do not interrupt the main translation with long explanations.

Use notes only when:

- A cultural term, pun, title, custom, or wordplay would otherwise be confusing.
- A literal translation would lose important meaning.
- A term requires a later consistency decision.

When notes are needed, return a separate proposed note object rather than inserting lengthy explanation into the translation:

```json
{"segment_id":"ch001-p0010","type":"translator_note","placement":"inline","text":"...","status":"draft"}
```

Keep notes concise and rare.

## Adult Or Sensitive Fiction

If the source contains adult, erotic, violent, taboo, or otherwise sensitive fictional material, translate the source faithfully while keeping the Chinese prose controlled and literary.

Rules:

- Do not censor, skip, euphemize away, or moralize source events.
- Do not intensify explicitness beyond the source.
- Preserve consent cues, discomfort cues, power dynamics, and emotional reactions accurately.
- Avoid clinical awkwardness unless the source is clinical.
- Avoid adding erotic detail not present in the source.

## Batch Workflow

Use the workflow helper scripts for large runs or resumed translation. They prevent terminal truncation, PowerShell display confusion, duplicate appends, and missed checkpoint validation.

### Default Large-Run Workflow

1. Inspect current progress:

```powershell
.\.python312\python.exe scripts\translation_progress_report.py --list-pending 20
```

2. Prepare the next checkpoint, usually 10 pending batches:

```powershell
.\.python312\python.exe scripts\translation_next_batches.py --count 10
```

This writes:

- `work/checkpoints/*_source.txt`: UTF-8 source text for the selected missing segments.
- `work/checkpoints/*_draft.jsonl`: one JSONL skeleton row per missing segment.

Read the generated source file instead of printing large batch ranges directly to the terminal. Fill the draft file with translations in segment order. Do not write translated text directly to `work/translations.jsonl` for large checkpoints unless the user explicitly asks for manual append.

3. Validate the filled draft without appending:

```powershell
.\.python312\python.exe scripts\append_translations.py work\checkpoints\<draft>.jsonl --dry-run
```

4. Append the checked draft:

```powershell
.\.python312\python.exe scripts\append_translations.py work\checkpoints\<draft>.jsonl
```

The append helper checks JSONL syntax, source segment IDs, duplicate draft IDs, existing translated IDs, non-empty translations, and source-order alignment before appending.

5. Validate the checkpoint and rebuild the side-by-side HTML:

```powershell
.\.python312\python.exe scripts\checkpoint_translation.py --from-batch ch025-b001 --count 10
```

For the final checkpoint, pass the actual remaining count, for example:

```powershell
.\.python312\python.exe scripts\checkpoint_translation.py --from-batch ch040-b006 --count 4
```

6. Repeat from step 1 until pending is zero. At completion, run:

```powershell
.\.python312\python.exe scripts\validate_translation_progress.py
.\.python312\python.exe scripts\assemble_parallel.py
```

### When To Use Each Script

- `scripts/translation_progress_report.py`: start of task, after each checkpoint, and before final response.
- `scripts/translation_next_batches.py`: before translating a new checkpoint; it selects pending batches and writes source/draft files.
- `scripts/append_translations.py`: before any persistent append; use `--dry-run` first, then append.
- `scripts/checkpoint_translation.py`: immediately after appending a checkpoint; it runs the fixed validator and rebuilds output.
- `scripts/validate_translation_progress.py`: authoritative structural QA; use directly for full JSON reports or exact validator output.
- `scripts/assemble_parallel.py`: final or manual rebuild of `output/parallel.html`.

For each batch:

1. Read all segments in the batch before translating.
2. Check `work/translations.jsonl` and skip already translated segment IDs unless revising was requested.
3. Identify title/heading segments, speakers, recurring names, and scene context.
4. Translate missing segments in source order.
5. Check that every requested missing `id` has exactly one output `segment_id`.
6. Check that no translation entry contains multiple unrelated segment translations.
7. Mark uncertain choices with `review_note` or proposed glossary/note entries.

For larger runs, use checkpoint groups, commonly 10 batches at a time. After appending a checkpoint, validate that exact range with the fixed script. Example:

```powershell
.\.python312\python.exe scripts\validate_translation_progress.py --expect-from ch025-b001 --expect-count 10
```

The preferred checkpoint command is:

```powershell
.\.python312\python.exe scripts\checkpoint_translation.py --from-batch ch025-b001 --count 10
```

If a checkpoint uses non-consecutive batches, pass them explicitly:

```powershell
.\.python312\python.exe scripts\validate_translation_progress.py --expect-batches ch025-b001 ch025-b002 ch025-b003
```

Or use the checkpoint wrapper:

```powershell
.\.python312\python.exe scripts\checkpoint_translation.py --batches ch025-b001 ch025-b002 ch025-b003
```

Then rebuild the readable side-by-side output:

```powershell
.\.python312\python.exe scripts\assemble_parallel.py
```

When the user asks for a small sample translation, translate only the requested batch or segment range and keep the output structure usable for JSONL persistence.

## QA Checklist

Before finalizing a translation batch, verify:

- All requested segment IDs are present exactly once.
- Already translated segment IDs were not duplicated or overwritten unless revision was explicitly requested.
- Output order matches input order.
- No non-empty paragraph is skipped.
- No two source segments are merged into one translation.
- Headings remain concise and heading-like.
- Names and recurring terms match earlier choices.
- Dialogue punctuation is balanced.
- Japanese text does not remain in the Chinese translation except intentional names, terms, or quoted Japanese.
- The translation reads naturally as Chinese while still matching source meaning.

Use `scripts/validate_translation_progress.py` for structural QA before finalizing progress. The script checks duplicate source IDs, duplicate translation IDs, unmatched translation IDs, batch/segment consistency, next missing batch, and optional checkpoint coverage. Do not recreate temporary validation snippets unless the fixed script lacks a needed check.

If a QA issue is found after writing output, fix the translation entry rather than explaining the issue only in prose.
