"""bakery/parbake_link.py - the one module that imports from parbake

Adds ../parbake to sys.path; re-exports its markers, directory names and Markdown renderer.
Imported, not copied, so the two cannot drift.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import sys
from pathlib import Path

PARBAKE_DIRECTORY = Path(__file__).resolve().parent.parent / "parbake"

if str(PARBAKE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PARBAKE_DIRECTORY))

try:
    from settings import CROISSANT_SUBDIRECTORY
    from settings import MARKDOWN_SUBDIRECTORY, TEXT_SUBDIRECTORY
    from parbaked_croissant import BANNER, FIELD_PLACEHOLDER, PARBAKED_CONFORMS_TO
    from croissant_to_md import render_markdown
except ImportError as problem:      # pragma: no cover - depends on the layout
    raise SystemExit(
        f"Could not import parbake from {PARBAKE_DIRECTORY}: {problem}\n"
        "The bakery expects to sit alongside the parbake directory."
    )

__all__ = [
    "BANNER",
    "CROISSANT_SUBDIRECTORY",
    "FIELD_PLACEHOLDER",
    "MARKDOWN_SUBDIRECTORY",
    "PARBAKED_CONFORMS_TO",
    "PARBAKE_DIRECTORY",
    "render_markdown",
    "TEXT_SUBDIRECTORY",
]
