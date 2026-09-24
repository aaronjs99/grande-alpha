"""Terminal table rendering shared by the command-line views."""

from __future__ import annotations

import shutil
import textwrap
from typing import Any

CLI_WIDTHS: dict[str, int] = {
    "Gate": 22,
    "Status": 11,
    "Observed": 31,
    "Requirement": 43,
    "Time": 25,
    "Severity": 9,
    "Category": 20,
    "Summary": 55,
    "Run": 12,
    "Source": 32,
    "Metric": 24,
    "Condition": 25,
    "Owner": 16,
    "Current result": 28,
    "Exact next action": 58,
    "Value": 28,
}


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    return (
        str(value)
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("—", "-")
        .replace("–", "-")
        .replace("…", "...")
        .replace("•", " / ")
        .replace("·", " / ")
        .replace("×", "x")
        .replace("≥", ">=")
        .replace("≤", "<=")
    )


def format_table(headers: list[str], rows: list[list[Any]], width: int | None = None) -> str:
    """Render a wrapping terminal table within the requested display width."""

    if not headers:
        return ""
    available = width or shutil.get_terminal_size((120, 30)).columns
    available = max(54, available)
    string_rows = [[_cell(value) for value in row] for row in rows]
    minimums = [max(5, len(header)) for header in headers]
    preferred = []
    for column, header in enumerate(headers):
        content = max([len(header), *(len(row[column]) for row in string_rows)] or [len(header)])
        preferred.append(max(minimums[column], min(CLI_WIDTHS.get(header, 28), content)))
    separators = 3 * (len(headers) - 1)
    while sum(preferred) + separators > available:
        candidates = [index for index, value in enumerate(preferred) if value > minimums[index]]
        if not candidates:
            break
        largest = max(candidates, key=lambda index: preferred[index] - minimums[index])
        preferred[largest] -= 1

    def rule(character: str = "-") -> str:
        return "+".join(character * value for value in preferred)

    def wrapped(values: list[str]) -> list[str]:
        cells = [
            textwrap.wrap(value, width=preferred[index], break_long_words=True, break_on_hyphens=False)
            or [""]
            for index, value in enumerate(values)
        ]
        height = max(len(value) for value in cells)
        return [
            " | ".join(
                cells[column][line].ljust(preferred[column])
                if line < len(cells[column])
                else " " * preferred[column]
                for column in range(len(headers))
            ).rstrip()
            for line in range(height)
        ]

    lines = [*wrapped(headers), rule("=")]
    for index, row in enumerate(string_rows):
        lines.extend(wrapped(row))
        if index != len(string_rows) - 1:
            lines.append(rule())
    return "\n".join(lines)
