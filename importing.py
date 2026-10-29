"""bakery/importing.py - reading a parbake output directory into the database

Only the par-baked Croissant is read; the .txt and .md beside it are recorded, never opened.
The machine is only suggested; a person confirms it.
Re-import matches files by name and refreshes measurements, never answers.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import calendar
import re
from dataclasses import dataclass, field as dataclass_field
from datetime import date
from pathlib import Path

from sqlalchemy import select

from croissant_files import find_parbaked_files, is_parbaked, parbaked_name, read_json, sha256_of
from models import DerivedFile, FileField, SourceFile, now
from parbake_link import MARKDOWN_SUBDIRECTORY, TEXT_SUBDIRECTORY
from queries import machine_names

DATE_RANGE = re.compile(r"_(\d{8})_(\d{8})$")          # NAME_20220809_20221231
SINGLE_DAY = re.compile(r"_(\d{4})-(\d{2})-(\d{2})$")   # NAME_2024-05-02
SINGLE_MONTH = re.compile(r"_(\d{4})-(\d{2})$")          # NAME_2026-01


# --- what a filename says ---

@dataclass
class NameParts:
    """Guesses read off a filename."""

    stem: str
    kind: str | None = None
    machine: str | None = None
    covers_from: date | None = None
    covers_to: date | None = None


def _last_day_of(year, month):
    """Return the last day of the month."""
    return date(year, month, calendar.monthrange(year, month)[1])


def split_off_dates(stem):
    """Return (name without its date suffix, start, end); dates None if absent or invalid."""
    found = DATE_RANGE.search(stem)
    if found:
        try:
            starts = date(int(found[1][:4]), int(found[1][4:6]), int(found[1][6:]))
            ends = date(int(found[2][:4]), int(found[2][4:6]), int(found[2][6:]))
        except ValueError:
            return stem, None, None     # eight digits, not a date
        return stem[: found.start()], starts, ends

    found = SINGLE_DAY.search(stem)
    if found:
        try:
            day = date(int(found[1]), int(found[2]), int(found[3]))
        except ValueError:
            return stem, None, None
        return stem[: found.start()], day, day

    found = SINGLE_MONTH.search(stem)
    if found:
        try:
            first = date(int(found[1]), int(found[2]), 1)
        except ValueError:
            return stem, None, None
        return stem[: found.start()], first, _last_day_of(first.year, first.month)

    return stem, None, None


def shared_prefix_of(stems):
    """Return the hyphenated segments every name starts with, such as ANL-ALCF.
    Never includes the last segment, which is the machine."""
    hyphenated = [stem.split("-") for stem in stems if "-" in stem]
    if len(hyphenated) < 2:
        return []

    shared = []
    for position in range(min(len(parts) for parts in hyphenated) - 2):
        segment = hyphenated[0][position]
        if all(parts[position] == segment for parts in hyphenated):
            shared.append(segment)
        else:
            break
    return shared


def parse_name(stem, known_machines=(), shared_prefix=()):
    """Return NameParts for a filename; a known machine wins over position.
    Hyphens put the machine last (ANL-ALCF-GPU-NODE-POLARIS), underscores first (aurora_crayex)."""
    body, covers_from, covers_to = split_off_dates(stem)
    parts = NameParts(stem=stem, covers_from=covers_from, covers_to=covers_to)

    folded = {name.lower(): name for name in known_machines}
    segments = re.split(r"[-_]", body)
    recognised = [segment for segment in segments if segment.lower() in folded]

    if "-" in body:
        hyphenated = body.split("-")
        parts.machine = recognised[-1] if recognised else hyphenated[-1]
        remaining = [segment for segment in hyphenated if segment != parts.machine]
        if shared_prefix and remaining[: len(shared_prefix)] == list(shared_prefix):
            remaining = remaining[len(shared_prefix):]
        elif len(remaining) >= 3:
            # No batch to learn from: organisation and facility take two.
            remaining = remaining[2:]
        elif len(remaining) == 2:
            remaining = remaining[1:]
        parts.kind = "-".join(remaining) or None
    else:
        parts.machine = recognised[-1] if recognised else None
        remaining = [segment for segment in segments if segment != parts.machine]
        parts.kind = "_".join(remaining) or None

    return parts


def machine_from_column(document):
    """Return the single value of MACHINE_NAME, or None if it has more than one."""
    measured = (document.get("_parbake_measurements") or {}).get("MACHINE_NAME")
    if not measured:
        return None
    common = measured.get("most_common_values") or []
    if len(common) != 1 or measured.get("distinct_count") != 1:
        return None
    value = str(common[0].get("value", "")).strip()
    return value or None


@dataclass
class Suggestion:
    """A guess at the machine, with the evidence for it."""

    machine: str | None
    evidence: list = dataclass_field(default_factory=list)
    disagrees: bool = False

    def describe(self):
        """Return the machine and its evidence as one line."""
        if not self.machine:
            return "no machine suggested"
        lead = f"{self.machine}: {'; '.join(self.evidence)}"
        return lead + ("  (THE TWO DISAGREE)" if self.disagrees else "")


def suggest_machine(document, parts):
    """Return a Suggestion; MACHINE_NAME wins over the filename and a disagreement is flagged."""
    from_column = machine_from_column(document)
    from_name = parts.machine

    if from_column and from_name:
        agree = from_column.lower() == from_name.lower()
        return Suggestion(
            machine=from_column,
            evidence=[f"every row of MACHINE_NAME says {from_column!r}",
                      f"the filename says {from_name!r}"],
            disagrees=not agree)
    if from_column:
        return Suggestion(from_column, [f"every row of MACHINE_NAME says {from_column!r}"])
    if from_name:
        return Suggestion(from_name, [f"read off the filename {parts.stem!r}"])
    return Suggestion(None)


# --- reading one par-baked file ---

def columns_in(document):
    """Return [(name, position, measurements)] from the first recordSet."""
    record_sets = document.get("recordSet") or []
    if not record_sets:
        return []
    measurements = document.get("_parbake_measurements") or {}
    found = []
    for position, field in enumerate(record_sets[0].get("field") or []):
        name = field.get("name")
        if name:
            found.append((name, position, measurements.get(name)))
    return found


def derived_files_for(croissant_path, stem):
    """Return [(path, kind)] for the .txt and .md beside this Croissant that exist."""
    output_root = croissant_path.parent.parent
    candidates = [
        (output_root / TEXT_SUBDIRECTORY / f"{stem}.txt", "txt"),
        (output_root / MARKDOWN_SUBDIRECTORY / f"{stem}.parbaked.md", "md"),
    ]
    return [(path, kind) for path, kind in candidates if path.is_file()]


# --- putting it in the database ---

@dataclass
class ImportReport:
    """What an import added, re-measured, skipped and found changed."""

    added: list = dataclass_field(default_factory=list)
    remeasured: list = dataclass_field(default_factory=list)
    unchanged: list = dataclass_field(default_factory=list)
    skipped: list = dataclass_field(default_factory=list)       # (name, why)
    columns_added: dict = dataclass_field(default_factory=dict)
    columns_removed: dict = dataclass_field(default_factory=dict)
    disagreements: list = dataclass_field(default_factory=list)  # (name, description)
    derived_recorded: int = 0

    @property
    def total(self):
        """Return the number of files in the database from this import."""
        return len(self.added) + len(self.remeasured) + len(self.unchanged)

    def lines(self):
        """Return the report as lines."""
        out = [f"{self.total} file(s) in the database: "
               f"{len(self.added)} new, {len(self.remeasured)} re-measured, "
               f"{len(self.unchanged)} unchanged"]
        if self.derived_recorded:
            out.append(f"{self.derived_recorded} text and markdown sibling(s) recorded, "
                       "not read")
        for name, columns in self.columns_added.items():
            out.append(f"{name}: {len(columns)} new column(s): {', '.join(sorted(columns))}")
        for name, columns in self.columns_removed.items():
            out.append(f"{name}: {len(columns)} column(s) gone: {', '.join(sorted(columns))}")
        for name, why in self.disagreements:
            out.append(f"{name}: {why}")
        for name, why in self.skipped:
            out.append(f"skipped {name}: {why}")
        return out


def sync_columns(session, stored, document, report, is_new):
    """Update the file's columns and measurements from the document.
    Added and removed columns are reported only for a file seen before."""
    measured = {name: (position, values) for name, position, values in columns_in(document)}
    existing = {column.name: column for column in stored.fields}

    appeared = set(measured) - set(existing)
    departed = set(existing) - set(measured)

    for name in departed:
        session.delete(existing[name])
    for name, (position, values) in measured.items():
        if name in existing:
            existing[name].position = position
            existing[name].measurements = values
        else:
            session.add(FileField(file_id=stored.id, name=name,
                                  position=position, measurements=values))

    stored.column_count = len(measured)
    if is_new:
        return
    if appeared:
        report.columns_added[stored.name] = sorted(appeared)
    if departed:
        report.columns_removed[stored.name] = sorted(departed)


def sync_derived(session, stored, croissant_path, report):
    """Record any .txt and .md beside the Croissant not already recorded."""
    already = {each.path for each in stored.derived}
    for path, kind in derived_files_for(croissant_path, stored.name):
        if str(path) not in already:
            session.add(DerivedFile(file_id=stored.id, path=str(path), format=kind))
            report.derived_recorded += 1


def import_one(session, croissant_path, report, known_machines=(), shared_prefix=()):
    """Import one par-baked Croissant; return the SourceFile, or None if skipped."""
    croissant_path = Path(croissant_path)
    document, problem = read_json(croissant_path)
    if document is None:
        report.skipped.append((croissant_path.name, f"could not be read: {problem}"))
        return None
    if not is_parbaked(document):
        report.skipped.append(
            (croissant_path.name,
             "not par-baked. A finished Croissant is seeded, not imported."))
        return None

    name = document.get("name") or parbaked_name(croissant_path)
    provenance = document.get("_parbake") or {}
    parts = parse_name(name, known_machines, shared_prefix)
    suggestion = suggest_machine(document, parts)
    checksum = sha256_of(croissant_path)

    stored = session.scalar(select(SourceFile).where(SourceFile.name == name))
    is_new = stored is None
    if is_new:
        stored = SourceFile(name=name, parbaked_path=str(croissant_path),
                            parbaked_sha256=checksum)
        session.add(stored)
        session.flush()      # the columns need its id

    changed = is_new or stored.parbaked_sha256 != checksum

    # Refreshed every time, so a moved directory does not go stale.
    stored.parbaked_path = str(croissant_path)
    stored.parbaked_sha256 = checksum
    stored.kind = parts.kind
    stored.covers_from = parts.covers_from
    stored.covers_to = parts.covers_to
    stored.machine_suggestion = suggestion.machine
    stored.source_csv_path = provenance.get("source_file")
    stored.source_size_bytes = provenance.get("source_size_in_bytes")
    stored.rows = provenance.get("rows_read")
    stored.rows_are_whole_file = provenance.get("rows_read_is_the_whole_file")

    if changed:
        sync_columns(session, stored, document, report, is_new)
        stored.updated_at = now()
    sync_derived(session, stored, croissant_path, report)

    if suggestion.disagrees:
        report.disagreements.append((name, suggestion.describe()))

    if is_new:
        report.added.append(name)
    elif changed:
        report.remeasured.append(name)
    else:
        report.unchanged.append(name)
    return stored


def import_target(session, target):
    """Import a parbake output directory, its croissants folder, or one file."""
    report = ImportReport()
    croissant_files = find_parbaked_files(target)
    if not croissant_files:
        return report

    known = machine_names(session)
    shared_prefix = shared_prefix_of([parbaked_name(path) for path in croissant_files])

    for path in croissant_files:
        import_one(session, path, report, known, shared_prefix)

    session.commit()
    return report
