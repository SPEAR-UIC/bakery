"""bakery/croissant_files.py - Croissant files on disk: names, checksums, par-baked marks

Reads NAME.parbaked.json; writes NAME.baked.json, NAME.baked.md and NAME.baked.txt.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import hashlib
import json
from pathlib import Path

from parbake_link import CROISSANT_SUBDIRECTORY, PARBAKED_CONFORMS_TO

PARBAKED_SUFFIX = ".parbaked.json"
BAKED_SUFFIX = ".baked.json"
BAKED_MARKDOWN_SUFFIX = ".baked.md"
BAKED_TEXT_SUFFIX = ".baked.txt"

# Written only once nothing is left unanswered.
FINISHED_CONFORMS_TO = [
    "http://mlcommons.org/croissant/1.0",
    "http://mlcommons.org/croissant/RAI/1.0",
]


def conforms_to(finished):
    """Return the finished conformsTo, or the par-baked one if not finished."""
    # The par-baked conformsTo stops an unfinished file validating.
    return list(FINISHED_CONFORMS_TO) if finished else PARBAKED_CONFORMS_TO


def is_parbaked(document):
    """Return whether conformsTo marks the document as par-baked."""
    conforms = document.get("conformsTo")
    conforms = conforms if isinstance(conforms, list) else [conforms]
    return any(isinstance(each, str) and "PARBAKED" in each for each in conforms)


def sha256_of(path):
    """Return the hex SHA-256 of a file."""
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path):
    """Return (document, None), or (None, problem) if it cannot be read."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except (OSError, ValueError) as problem:
        return None, problem


def parbaked_name(path):
    """Return NAME from NAME.parbaked.json."""
    name = Path(path).name
    return name[: -len(PARBAKED_SUFFIX)] if name.endswith(PARBAKED_SUFFIX) else Path(path).stem


def find_parbaked_files(target):
    """Return par-baked files in a parbake output directory, its croissants
    folder, or the one file given."""
    target = Path(target)
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    here = sorted(target.glob(f"*{PARBAKED_SUFFIX}"))
    if here:
        return here
    return sorted((target / CROISSANT_SUBDIRECTORY).glob(f"*{PARBAKED_SUFFIX}"))
