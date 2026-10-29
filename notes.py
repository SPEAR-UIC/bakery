"""bakery/notes.py - find every note left for later across a directory of Croissants

A note is "NOTE: ..." inside any description: dataset, file, record set, field.
Reads the files, not the database: finds hand-written notes and files never imported.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from croissant_files import is_parbaked, read_json
from models import FieldDoc

# Upper case only: parbake's banner says "responsible-AI notes".
NOTE_MARKER = re.compile(r"\bNOTES?:\s*")

# Keys that say a JSON file is a Croissant.
CROISSANT_HINTS = ("recordSet", "distribution", "conformsTo")


@dataclass
class Note:
    """One note and where it was found."""

    text: str
    where: str              # "dataset", "file X", "records / EXIT_CODE"
    dataset: str
    path: Path
    unfinished: bool = False    # its file is still par-baked

    def __str__(self):
        """Return "dataset / where: text"."""
        return f"{self.dataset} / {self.where}: {self.text}"


def notes_in(description):
    """Return the text of each NOTE: in a description."""
    if not isinstance(description, str) or not description:
        return []
    return [each.strip() for each in NOTE_MARKER.split(description)[1:] if each.strip()]


def label(node, default="?"):
    """Return the node's name, else its @id, else default."""
    return node.get("name") or node.get("@id") or default


def descriptions_in(document):
    """Yield (description, where) for every place a Croissant has one."""
    yield document.get("description"), "dataset"
    for entry in document.get("distribution") or []:
        if isinstance(entry, dict):
            yield entry.get("description"), f"file {label(entry)}"
    for record_set in document.get("recordSet") or []:
        if not isinstance(record_set, dict):
            continue
        set_name = label(record_set, "records")
        yield record_set.get("description"), f"record set {set_name}"
        for field in record_set.get("field") or []:
            if isinstance(field, dict):
                yield field.get("description"), f"{set_name} / {label(field)}"


def notes_in_document(document, path):
    """Return a Note for each note in a Croissant document."""
    name = document.get("name") or Path(path).name
    unfinished = is_parbaked(document) or "_unfinished" in document
    return [Note(text, where, name, Path(path), unfinished)
            for description, where in descriptions_in(document)
            for text in notes_in(description)]


def find_notes(*targets):
    """Return (notes, Croissants read, [(path, why)] unreadable); skip non-Croissant JSON."""
    found, scanned, skipped = [], 0, []
    for target in map(Path, targets):
        for path in [target] if target.is_file() else sorted(target.rglob("*.json")):
            document, problem = read_json(path)
            if problem is not None:
                skipped.append((path, str(problem)))
                continue
            if not isinstance(document, dict) or not any(hint in document for hint in CROISSANT_HINTS):
                continue
            scanned += 1
            found += notes_in_document(document, path)
    return found, scanned, skipped


def by_dataset(notes):
    """Return {dataset: [notes]}, sorted by dataset."""
    grouped = {}
    for note in notes:
        grouped.setdefault(note.dataset, []).append(note)
    return dict(sorted(grouped.items()))


def report(notes, scanned, skipped):
    """Return the printed report lines for find_notes() output."""
    if not scanned:
        return ["No Croissant files found. Point at a directory of them, or at one file."]
    if not notes:
        return [f"{scanned} Croissant(s) read. No notes in any of them."]

    grouped = by_dataset(notes)
    lines = [f"{len(notes)} note(s) across {len(grouped)} dataset(s), "
             f"from {scanned} Croissant(s) read."]
    for dataset, found in grouped.items():
        marker = "  (still par-baked)" if any(note.unfinished for note in found) else ""
        lines += ["", f"{dataset}{marker}"]
        for note in found:
            lines += [f"    {note.where}", f"        {note.text}"]
    if skipped:
        lines.append("")
        lines += [f"could not read {Path(path).name}: {why}" for path, why in skipped]
    return lines


def notes_recorded_in(session):
    """Return the notes recorded in the database, written out or not."""
    # FIXME: path is the dataset name here, and "records" is assumed.
    found = [Note(text=doc.note, where=f"records / {doc.field_name}",
                  dataset=doc.dataset.name, path=Path(doc.dataset.name),
                  unfinished=not doc.is_complete)
             for doc in session.scalars(select(FieldDoc).where(FieldDoc.note.isnot(None)))]
    return sorted(found, key=lambda note: (note.dataset, note.where))
