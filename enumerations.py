"""bakery/enumerations.py - decoding tables: make, link to columns, check coverage

A table belongs to one machine; a column links only to a table on its own machine.
Another machine's table is copied over first (copy_table_to).
A code in the data with no meaning in the table is not an error; baking reports it.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from dataclasses import dataclass, field
from typing import List

from sqlalchemy import select

from models import Enumeration, EnumerationValue, FileField, Machine, SourceFile
from queries import field_doc, table_named

# How existing decodings say they are unsure: "PROVISIONAL: ... Open question."
PROVISIONAL_MARKERS = ("provisional", "open question", "ambiguous",
                       "treat as unknown", "confirm before")


def looks_provisional(meaning):
    """Return whether a meaning contains a PROVISIONAL_MARKERS phrase."""
    lowered = (meaning or "").lower()
    return any(marker in lowered for marker in PROVISIONAL_MARKERS)


@dataclass
class TableRow:
    """One code and its meaning."""

    code: str
    meaning: str


# --- the editor's text box -------------------------------------------------

def parse_table_text(text):
    """Return (rows, problems) from `code = meaning` lines, split at the first "=".
    A code with no meaning is skipped as not yet decoded."""
    rows, problems, seen = [], [], set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if "=" not in line:
            problems.append(f"line {number}: no '=' between code and meaning: {line.strip()!r}")
            continue
        code, meaning = line.split("=", 1)
        code, meaning = code.strip(), meaning.strip()
        if not code:
            problems.append(f"line {number}: no code before the '='")
            continue
        if code in seen:
            problems.append(f"line {number}: code {code!r} appears twice")
            continue
        seen.add(code)
        if meaning:
            rows.append(TableRow(code, meaning))
    return rows, problems


def table_text(table=None, undecoded=()):
    """Return the editor text: the table's rows, then `code = ` for each undecoded code."""
    lines = [f"{value.code} = {value.meaning}" for value in (table.values if table else [])]
    lines += [f"{code} = " for code in undecoded]
    return "\n".join(lines)


# --- what is in the data ---------------------------------------------------

@dataclass
class ObservedCodes:
    """The values parbake recorded for a column, most common first."""
    # parbake lists only the most common values per file (--values-shown);
    # is_complete is true only when every file listed all its distinct values.
    codes: List[str] = field(default_factory=list)
    is_complete: bool = False


def observed_codes(session, dataset, field_name):
    """Return ObservedCodes for this column across the dataset's files."""
    return codes_in(session.scalars(
        select(FileField).join(SourceFile, FileField.file_id == SourceFile.id)
        .where(SourceFile.dataset_id == dataset.id, FileField.name == field_name)))


def codes_in(columns):
    """Return ObservedCodes from FileFields of one column, one per file."""
    counts = {}
    is_complete = True
    files_seen = 0
    for column in columns:
        files_seen += 1
        measurements = column.measurements or {}
        listed = measurements.get("most_common_values") or []
        for entry in listed:
            value = str(entry.get("value"))
            counts[value] = counts.get(value, 0) + (entry.get("count") or 0)
        distinct = measurements.get("distinct_count")
        if (distinct is None or measurements.get("distinct_count_is_at_least")
                or distinct > len(listed)):
            is_complete = False
    ordered = sorted(counts, key=lambda code: -counts[code])
    return ObservedCodes(codes=ordered, is_complete=is_complete and files_seen > 0)


@dataclass
class Coverage:
    """How well a table decodes what a column holds."""
    decoded: List[str] = field(default_factory=list)
    undecoded: List[str] = field(default_factory=list)
    observed_is_complete: bool = False

    def describe(self):
        """Return "decodes N of M code(s) seen", noting if more exist."""
        seen = len(self.decoded) + len(self.undecoded)
        text = f"decodes {len(self.decoded)} of {seen} code(s) seen"
        if not self.observed_is_complete:
            text += " (parbake listed only the most common; there are more)"
        return text


def coverage(table, observed):
    """Return Coverage: observed codes split into decoded and undecoded."""
    known = {value.code for value in table.values} if table else set()
    result = Coverage(observed_is_complete=observed.is_complete)
    for code in observed.codes:
        (result.decoded if code in known else result.undecoded).append(code)
    return result


# --- making and finding tables ---------------------------------------------

def save_table(session, machine, name, description, rows, table=None):
    """Make a table on the machine, or replace the rows of `table`; commit and return it.
    Raise ValueError if the name is empty or taken by another table on the machine."""
    name = name.strip()
    if not name:
        raise ValueError("a decoding table needs a name")
    clash = table_named(session, machine.id, name)
    if clash is not None and clash is not table:
        raise ValueError(f"{machine.name} already has a decoding table named {name!r}")

    if table is None:
        table = Enumeration(machine_id=machine.id, name=name)
        session.add(table)
    table.name = name
    table.description = (description or "").strip() or None
    table.values.clear()
    session.flush()
    for position, row in enumerate(rows):
        table.values.append(EnumerationValue(
            code=row.code, meaning=row.meaning, position=position,
            is_provisional=looks_provisional(row.meaning)))
    session.commit()
    return table


def tables_for(session, machine, include_other_machines=False):
    """Return the machine's tables by name; with the flag, every other machine's after."""
    own = list(session.scalars(select(Enumeration).where(
        Enumeration.machine_id == machine.id).order_by(Enumeration.name)))
    if not include_other_machines:
        return own
    others = list(session.scalars(
        select(Enumeration).join(Machine, Enumeration.machine_id == Machine.id)
        .where(Enumeration.machine_id != machine.id)
        .order_by(Machine.name, Enumeration.name)))
    return own + others


def copy_table_to(session, table, machine):
    """Return a copy of table on machine, or table itself if already there.
    Raise ValueError if the machine has another table of that name."""
    if table.machine_id == machine.id:
        return table
    rows = [TableRow(value.code, value.meaning) for value in table.values]
    copy = save_table(session, machine, table.name, table.description, rows)
    copy.code_description = table.code_description
    copy.meaning_description = table.meaning_description
    session.commit()
    return copy


# --- columns and tables ----------------------------------------------------

def link_column(session, dataset, field_name, table):
    """Link a column to a table on the dataset's machine, or to none; return the FieldDoc.
    A column with no answer yet gets an unconfirmed FieldDoc holding only the link."""
    if table is not None and table.machine_id != dataset.machine_id:
        raise ValueError(f"{table.name} belongs to another machine; copy it here first")
    doc = (field_doc(session, dataset, field_name)
           or field_doc(session, dataset, field_name, create=True, is_confirmed=False))
    doc.enumeration = table
    session.commit()
    return doc


def offered_table(dataset, offer):
    """Return the offer's table if it is on the dataset's machine, else None."""
    # An offer from another machine brings its description, not its table.
    if offer is None or offer.doc is None:
        return None
    table = offer.doc.enumeration
    if table is not None and table.machine_id == dataset.machine_id:
        return table
    return None
