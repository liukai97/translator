"""Small collection helpers shared by command-line scripts."""

from __future__ import annotations

from collections import Counter


def duplicate_values(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)
