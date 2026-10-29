"""bakery/seeding.py - reading finished work into the database

    *.croissant.json        field descriptions and types, dataset answers, decodings
    *_field_dictionary*.csv units, which Croissant has no slot for
    *_documentation*.md     the machine profile
Everything lands unconfirmed; a confirmed answer is never overwritten.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import csv
import re
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from croissant_files import PARBAKED_SUFFIX, is_parbaked, read_json
from documentation import organisation_in, parse_deployment_dates, parse_system_section, read_section
from enumerations import looks_provisional
from models import (
    DatasetAnswer,
    EnumerationValue,
    Enumeration,
    MachineIteration,
    MachinePartition,
    PARTITION_FIELDS,
    SEEDED_BY_PREFIX,
)
from queries import (
    dataset_answer,
    field_doc,
    find_or_make_dataset,
    find_or_make_machine,
    machine_names,
    table_named,
)
from questions import DATASET_KEYS, is_unanswered

# Matches "(no meaning supplied for X)"; seeded, such a column would never be asked.
PLACEHOLDER_SHAPE = re.compile(r"^\(\s*(no|none|not|tbd|todo)\b.*\)$",
                               re.IGNORECASE | re.DOTALL)

# The dictionaries disagree on a header; both spellings are read.
FIELD_COLUMN = "FIELD"
TYPE_COLUMNS = ("DTYPE", "DATATYPE")
UNIT_COLUMN = "UNIT"
MEANING_COLUMN = "MEANING"

DICTIONARY_SUFFIX = re.compile(r"_field_dictionary.*$", re.IGNORECASE)
DOCUMENTATION_SUFFIX = re.compile(r"_documentation.*$", re.IGNORECASE)


def croissant_type_for(written):
    """Return the Croissant type for a dictionary DTYPE, or None if unclear.
    Datetime is checked first: "string (ISO 8601 datetime)" is a datetime."""
    if not written:
        return None
    lowered = written.strip().lower()
    if "datetime" in lowered or "timestamp" in lowered:
        return "sc:DateTime"
    if lowered.startswith("date") or " date" in lowered:
        return "sc:Date"
    if lowered.startswith(("int", "long")):
        return "sc:Integer"
    if lowered.startswith(("float", "double", "decimal", "num")):
        return "sc:Float"
    if lowered.startswith("bool"):
        return "sc:Boolean"
    if lowered.startswith(("str", "text", "object")):
        return "sc:Text"
    return None


def looks_like_a_placeholder(text):
    """Return whether the text is empty or a placeholder such as "(no meaning)"."""
    stripped = (text or "").strip()
    return is_unanswered(stripped) or bool(PLACEHOLDER_SHAPE.match(stripped))


@dataclass
class SeedReport:
    """What a seed added, filled, kept, linked and skipped."""
    machines: list = dataclass_field(default_factory=list)
    iterations: list = dataclass_field(default_factory=list)
    datasets: list = dataclass_field(default_factory=list)
    fields_added: int = 0
    fields_filled: int = 0          # gaps in an unconfirmed row
    fields_kept: int = 0            # confirmed, left alone
    answers_added: int = 0
    enumerations: list = dataclass_field(default_factory=list)
    unlinked: list = dataclass_field(default_factory=list)      # (enum, why)
    links: list = dataclass_field(default_factory=list)         # (column, enum)
    conflicts: list = dataclass_field(default_factory=list)     # (what, why)
    notes: list = dataclass_field(default_factory=list)
    skipped: list = dataclass_field(default_factory=list)       # (file, why)
    sources: list = dataclass_field(default_factory=list)

    def lines(self):
        """Return the report as lines."""
        out = []
        if self.sources:
            out.append(f"read {len(self.sources)} file(s): "
                       f"{', '.join(Path(each).name for each in self.sources)}")
        out.append(f"{self.fields_added} field description(s) seeded, "
                   f"{self.fields_filled} gap(s) filled, "
                   f"{self.fields_kept} left alone as already confirmed")
        if self.answers_added:
            out.append(f"{self.answers_added} dataset-level answer(s) seeded")
        out += [f"machine {name} created" for name in self.machines]
        out += [f"machine profile recorded: {name}" for name in self.iterations]
        out += [f"dataset {name} created" for name in self.datasets]
        out += [f"decoding table {name} seeded" for name in self.enumerations]
        out += [f"{column} linked to decoding table {name}, as the file says"
                for column, name in self.links]
        out += self.notes
        out += [f"{name}: {why}" for name, why in self.unlinked]
        out += [f"DISAGREEMENT: {what}: {why}" for what, why in self.conflicts]
        out += [f"skipped {Path(name).name}: {why}" for name, why in self.skipped]
        out.append("Nothing seeded is confirmed. Each of these is offered at the "
                   "prompt as a default to accept, edit or reject.")
        return out


def seeded_by(source):
    """Return the recorded_by value for a seeded source file."""
    return f"{SEEDED_BY_PREFIX}{Path(source).name}"


# --- who and what a file is about ---

def machine_and_kind_from(label, known_machines=()):
    """Return (machine, kind) from a label: POLARIS_DJC -> ("POLARIS", "DJC").
    A known machine wins wherever it sits; otherwise the first segment."""
    segments = [segment for segment in label.split("_") if segment]
    if not segments:
        return None, None
    folded = {name.lower() for name in known_machines}
    for position, segment in enumerate(segments):
        if segment.lower() in folded:
            rest = segments[:position] + segments[position + 1:]
            return segment, "_".join(rest) or None
    return segments[0], "_".join(segments[1:]) or None


def machine_for(session, name, report):
    """Return the machine of that name, making it if needed."""
    # Made without asking: safe only because nothing seeded is confirmed.
    machine, made = find_or_make_machine(session, name)
    if made:
        report.machines.append(machine.name)
    return machine


def dataset_for_label(session, label, machine_name, report):
    """Return (machine, dataset) for a finished file named <MACHINE>_<KIND>."""
    from_label, kind = machine_and_kind_from(label, machine_names(session))
    machine = machine_for(session, machine_name or from_label, report)
    dataset, made = find_or_make_dataset(session, machine, label, kind)
    if made:
        report.datasets.append(dataset.name)
    return machine, dataset


# --- one field, never overwriting a person ---

def same_answer(one, other):
    """Return whether two answers differ only in case, spacing, periods or a suffix."""
    first = re.sub(r"[\s.]+", " ", (one or "").strip().lower())
    second = re.sub(r"[\s.]+", " ", (other or "").strip().lower())
    return bool(first and second) and (first in second or second in first)


def record_field(session, dataset, name, source, report,
                 description=None, data_type=None, unit=None):
    """Add a field row if none; fill gaps in an unconfirmed one, reporting conflicts.
    Leave a confirmed one alone. Return the existing row, or None."""
    if looks_like_a_placeholder(description):
        description = None
    values = {key: value for key, value in
              (("description", description), ("data_type", data_type), ("unit", unit)) if value}
    if not values:
        return None

    existing = field_doc(session, dataset, name)
    if existing is None:
        field_doc(session, dataset, name, create=True, is_confirmed=False,
                  recorded_by=seeded_by(source), **values)
        report.fields_added += 1
        return None
    if existing.is_confirmed:
        report.fields_kept += 1
        return existing

    filled = False
    for key, value in values.items():
        current = getattr(existing, key)
        if not current:
            setattr(existing, key, value)
            filled = True
        elif current != value and not same_answer(current, value):
            report.conflicts.append((
                f"{dataset.name} / {name} / {key}",
                f"{Path(source).name} says {value!r}; kept what was already "
                f"recorded ({existing.recorded_by or 'an earlier source'}) "
                f"saying {current!r}. Confirm which is right."))
    report.fields_filled += filled
    return existing


# --- a finished Croissant ---

def data_record_sets(document):
    """Return the record sets that are not decoding tables."""
    return [each for each in document.get("recordSet") or [] if not each.get("cr:isEnumeration")]


def candidate_columns(table, columns):
    """Return the columns a seeded table might decode, matched by name."""
    # FIXME: a name taken from "@id" keeps its path prefix and matches nothing.
    stem = re.sub(r"_enum$", "", table.name).lower()
    return sorted(column for column in columns if column and (
        stem in column.lower() or column.lower() in stem
        or column in (table.description or "")))


def table_from(record_set, machine, name):
    """Return an Enumeration built from a cr:isEnumeration record set."""
    fields = record_set.get("field") or []
    table = Enumeration(machine_id=machine.id, name=name,
                        description=record_set.get("description"),
                        code_description=fields[0].get("description") if fields else None,
                        meaning_description=fields[1].get("description") if len(fields) > 1 else None)
    # Rows are keyed by field id; code and meaning are taken by field order, not name.
    field_ids = [field.get("@id") for field in record_set.get("field") or []]
    if len(field_ids) < 2:
        return table
    for position, row in enumerate(record_set.get("data") or []):
        code, meaning = row.get(field_ids[0]), row.get(field_ids[1])
        if code is not None and meaning is not None:
            table.values.append(EnumerationValue(
                code=str(code), meaning=str(meaning), position=position,
                is_provisional=looks_provisional(meaning)))
    return table


def seed_enumerations(session, document, machine, report):
    """Seed each new cr:isEnumeration table; report candidate columns, never link them."""
    columns = {field.get("name") for record_set in data_record_sets(document)
               for field in record_set.get("field") or []}
    for record_set in document.get("recordSet") or []:
        name = record_set.get("name") or record_set.get("@id")
        if not record_set.get("cr:isEnumeration") or not name:
            continue
        if table_named(session, machine.id, name):
            continue
        table = table_from(record_set, machine, name)
        session.add(table)
        session.flush()
        report.enumerations.append(name)
        candidates = candidate_columns(table, columns)
        report.unlinked.append((
            name, "seeded but not linked to a column; "
            + (f"candidates: {', '.join(candidates)}" if candidates else "no obvious column")
            + ". djc_v4 has the same gap; link it when confirming."))


def link_referenced_tables(session, document, dataset, machine, source, report):
    """Link each field with `references` to its table; leave confirmed columns alone."""
    for record_set in data_record_sets(document):
        for field in record_set.get("field") or []:
            target = ((field.get("references") or {}).get("field") or {}).get("@id") or ""
            table_name, column = target.split("/")[0], field.get("name")
            if not table_name or not column:
                continue
            table = table_named(session, machine.id, table_name)
            if table is None:
                report.unlinked.append((table_name, f"{column} refers to it, but the "
                                        "file has no decoding table of that name"))
                continue
            doc = field_doc(session, dataset, column)
            if doc is None:
                doc = field_doc(session, dataset, column, create=True, is_confirmed=False,
                                recorded_by=seeded_by(source))
            elif doc.is_confirmed or doc.enumeration_id is not None:
                continue
            doc.enumeration = table
            report.links.append((column, table_name))
            report.unlinked = [(name, why) for name, why in report.unlinked
                               if name != table_name]


def seed_croissant(session, path, report, machine_name=None):
    """Seed dataset answers, fields and decoding tables from a finished Croissant."""
    path = Path(path)
    document, problem = read_json(path)
    if document is None:
        report.skipped.append((str(path), f"could not be read: {problem}"))
        return
    if is_parbaked(document):
        report.skipped.append(
            (str(path), "still par-baked. A par-baked file is imported, not seeded."))
        return

    machine, dataset = dataset_for_label(session, document.get("name") or path.stem,
                                         machine_name, report)
    report.sources.append(str(path))

    for key in DATASET_KEYS:
        # FIXME: an existing answer is skipped silently; a column clash is reported.
        if key not in document or dataset_answer(session, dataset, key):
            continue
        session.add(DatasetAnswer(dataset_id=dataset.id, key=key, answer=document[key],
                                  is_confirmed=False, recorded_by=seeded_by(path)))
        report.answers_added += 1

    for record_set in data_record_sets(document):
        for field in record_set.get("field") or []:
            if field.get("name"):
                record_field(session, dataset, field["name"], path, report,
                             description=field.get("description"),
                             data_type=field.get("dataType"))

    seed_enumerations(session, document, machine, report)
    link_referenced_tables(session, document, dataset, machine, path, report)


# --- a field dictionary ---

def seed_field_dictionary(session, path, report, machine_name=None):
    """Seed fields from a FIELD/DTYPE/UNIT/MEANING dictionary."""
    path = Path(path)
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error, UnicodeDecodeError) as problem:
        report.skipped.append((str(path), f"could not be read: {problem}"))
        return
    if not rows or FIELD_COLUMN not in rows[0]:
        report.skipped.append((str(path), f"no {FIELD_COLUMN} column; not a field dictionary"))
        return

    _, dataset = dataset_for_label(session, DICTIONARY_SUFFIX.sub("", path.stem),
                                   machine_name, report)
    report.sources.append(str(path))
    type_column = next((each for each in TYPE_COLUMNS if each in rows[0]), None)
    for row in rows:
        name = (row.get(FIELD_COLUMN) or "").strip()
        if name:
            record_field(
                session, dataset, name, path, report,
                description=(row.get(MEANING_COLUMN) or "").strip() or None,
                data_type=croissant_type_for(row.get(type_column) if type_column else None),
                unit=(row.get(UNIT_COLUMN) or "").strip() or None)


# --- a documentation file ---

def seed_documentation(session, path, report, machine_name=None):
    """Seed the System section as one open-ended iteration.
    A machine that already has a profile is left alone."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as problem:
        report.skipped.append((str(path), f"could not be read: {problem}"))
        return
    lines = read_section(text, "System")
    if not lines:
        report.skipped.append((str(path), "no System section; nothing here to seed"))
        return

    profile, partitions = parse_system_section(lines)
    from_label, _ = machine_and_kind_from(DOCUMENTATION_SUFFIX.sub("", path.stem),
                                          machine_names(session))
    # The document's own System name beats its filename.
    name = machine_name or profile["system_name"] or from_label
    if not name:
        report.skipped.append((str(path), "no machine named in the System section"))
        return

    machine = machine_for(session, name, report)
    report.sources.append(str(path))
    machine.organization = machine.organization or organisation_in(text)
    if machine.iterations:
        # Normal: every dataset's documentation repeats its machine's section.
        report.notes.append(f"{machine.name} already has a profile; {path.name} added nothing to it")
        return

    starts_on, ends_on = parse_deployment_dates(profile.get("deployment"))
    iteration = MachineIteration(
        machine_id=machine.id, label="as deployed",
        starts_on=starts_on, ends_on=ends_on,
        dates_as_written=profile.get("deployment"),
        vendor=profile.get("vendor"), architecture=profile.get("architecture"),
        scheduler=profile.get("scheduler"),
        partition_summary=profile.get("partition_summary"))
    iteration.partitions = [
        MachinePartition(**{key: entry.get(key) for key in PARTITION_FIELDS
                            if key not in ("name", "position")},
                         name=entry.get("name", "default"), position=position)
        for position, entry in enumerate(partitions)]
    session.add(iteration)
    session.flush()
    report.iterations.append(
        f"{machine.name}, {iteration.label}, "
        f"{starts_on or 'no start date'} to {ends_on or 'present'}, "
        f"{len(iteration.partitions)} partition(s)")


# --- everything at once ---

def seed_from(session, *targets, machine_name=None):
    """Seed files and directories: documentation, then Croissants, then dictionaries.
    The first unconfirmed answer wins, so the richer source goes first."""
    report = SeedReport()
    documentation, croissants, dictionaries = [], [], []
    for target in map(Path, targets):
        for path in sorted(target.rglob("*")) if target.is_dir() else [target]:
            name = path.name.lower()
            if not path.is_file() or name.endswith(PARBAKED_SUFFIX):
                continue
            if name.endswith(".json"):
                croissants.append(path)
            elif name.endswith(".csv") and "field_dictionary" in name:
                dictionaries.append(path)
            elif name.endswith(".md") and "documentation" in name:
                documentation.append(path)

    for path in documentation:
        seed_documentation(session, path, report, machine_name)
    for path in croissants:
        seed_croissant(session, path, report, machine_name)
    for path in dictionaries:
        seed_field_dictionary(session, path, report, machine_name)
    session.commit()
    return report
