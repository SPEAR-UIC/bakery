"""bakery/plain_text.py - the Markdown rendering as plain text, by pandoc

Table separators are resized first: each column as wide as its longest cell,
the widest cut to fit the page, so only it wraps; pandoc splits evenly otherwise.
Smart quotes, TeX and sub/superscripts are off: Croissant strings pass unchanged.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import re
import shutil
import subprocess

PANDOC_READER = "markdown-smart-tex_math_dollars-raw_tex-subscript-superscript"
TEXT_COLUMNS = 94          # pandoc's table rules run up to 6 past it: lines stay within 100
NARROWEST_WRAP = 30         # the widest column is never cut below this
SEPARATOR = re.compile(r"^\|(?:-{3,}\|)+$")


def cells(line):
    """Return a table row's cells."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def sized_tables(markdown):
    """Return markdown with each table separator sized to its columns' longest cells."""
    lines = markdown.split("\n")
    for at, line in enumerate(lines):
        if not (at and SEPARATOR.match(line)):
            continue
        rows = [lines[at - 1]]
        rows += _table_rows(lines, at + 1)
        widths = fitted([max(3, *map(len, column)) for column in zip(*map(cells, rows))])
        lines[at] = "|" + "|".join("-" * width for width in widths) + "|"
    return "\n".join(lines)


def fitted(widths):
    """Return widths with the widest cut so the table, gaps and indent fit the page."""
    widest = widths.index(max(widths))
    room = TEXT_COLUMNS - 2 * len(widths) - (sum(widths) - widths[widest])
    widths[widest] = min(widths[widest], max(NARROWEST_WRAP, room))
    return widths


def _table_rows(lines, start):
    """Yield the table rows from start until the first non-row line."""
    for line in lines[start:]:
        if not line.startswith("|"):
            return
        yield line


def has_pandoc():
    """Return whether pandoc is on PATH."""
    return shutil.which("pandoc") is not None


def render_text(markdown):
    """Return markdown as wrapped plain text."""
    return subprocess.run(
        ["pandoc", "-f", PANDOC_READER, "-t", "plain", f"--columns={TEXT_COLUMNS}"],
        input=sized_tables(markdown), capture_output=True, text=True, check=True).stdout
