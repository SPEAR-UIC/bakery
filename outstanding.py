"""bakery/outstanding.py - what is still unanswered about a file, and what can be offered

Offers come nearest first: seeded, sibling dataset, other machine, respelled.
Offers are shown, never applied; a person confirms them.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from dataclasses import dataclass

from sqlalchemy import func, select

from models import (
    DatasetAnswer,
    FieldDoc,
    SourceFile,
    STATE_COMPLETE,
    STATE_IMPORTED,
    STATE_IN_PROGRESS,
    now,
)
from queries import (
    dataset_answer,
    dataset_answers,
    field_doc,
    field_docs,
    find_or_make_dataset,
    find_or_make_machine,
)
from questions import DATASET_QUESTIONS, describe_measurements


@dataclass
class Offer:
    """An answer from elsewhere, and how near it is."""

    doc: FieldDoc
    kind: str               # seeded | sibling | other machine | respelled

    @property
    def where(self):
        """Return the name of the dataset the offer comes from."""
        return self.doc.dataset.name if self.doc.dataset else "another dataset"

    def describe(self):
        """Return where the offer comes from, as one line."""
        if self.kind == "seeded":
            return f"already recorded here, unconfirmed ({self.doc.recorded_by})"
        if self.kind == "sibling":
            return f"{self.where} on this machine describes the same column"
        if self.kind == "respelled":
            return f"{self.where} describes {self.doc.field_name}, the same name in another case"
        return f"{self.where} describes the same column"


@dataclass
class FieldWork:
    """One column of one file, and what is needed to ask about it."""

    name: str
    position: int
    measurements: dict | None
    doc: FieldDoc | None = None
    offer: Offer | None = None

    @property
    def evidence(self):
        """Return the column's measurements as text."""
        return describe_measurements(self.measurements)

    def answered(self, part):
        """Return this dataset's own answer for the part, else the offer's."""
        own = getattr(self.doc, part, None) if self.doc else None
        if own:
            return own
        return getattr(self.offer.doc, part) if self.offer else None

    @property
    def description(self):
        """Return the description, own or offered."""
        return self.answered("description")

    @property
    def data_type(self):
        """Return the data type, own or offered."""
        return self.answered("data_type")

    @property
    def unit(self):
        """Return the unit, own or offered."""
        return self.answered("unit")

    @property
    def note(self):
        """Return this dataset's note; notes are never offered."""
        # A note is about this export's work, not the column.
        return self.doc.note if self.doc else None

    @property
    def is_settled(self):
        """Return whether this dataset's answer is settled."""
        return bool(self.doc and self.doc.is_settled)

    @property
    def state(self):
        """Return "confirmed", "offered" or "unanswered"."""
        if self.is_settled:
            return "confirmed"
        if self.description or self.data_type:
            return "offered"
        return "unanswered"


@dataclass
class Coverage:
    """How much of a file is answered, for the file list."""

    columns: int = 0
    confirmed: int = 0
    offered: int = 0

    @property
    def outstanding(self):
        """Return the number of columns not confirmed."""
        return self.columns - self.confirmed

    @property
    def share(self):
        """Return the confirmed fraction, 0.0 for no columns."""
        return self.confirmed / self.columns if self.columns else 0.0

    def describe(self):
        """Return the counts as one line."""
        if not self.columns:
            return "no columns"
        if not self.outstanding:
            return f"all {self.columns} confirmed"
        if self.offered:
            return f"{self.confirmed}/{self.columns} confirmed, {self.offered} to accept"
        return f"{self.confirmed}/{self.columns} confirmed"


# --- offers ---

def best_answer(rows):
    """Return the best row with an answer: confirmed first, then most recent."""
    return max((row for row in rows if row.has_answer),
               key=lambda row: (row.is_confirmed, row.recorded_at), default=None)


def offer_for(session, dataset, field_name, existing=None):
    """Return the nearest Offer for an unsettled column, or None.
    Order: own seeded row, sibling dataset, other machine, same name in another case."""
    if existing is not None and existing.has_answer:
        return Offer(existing, "seeded")

    elsewhere = session.scalars(select(FieldDoc).where(
        FieldDoc.field_name == field_name, FieldDoc.dataset_id != dataset.id)).all()
    siblings = [row for row in elsewhere if row.dataset.machine_id == dataset.machine_id]
    for rows, kind in ((siblings, "sibling"), (elsewhere, "other machine")):
        found = best_answer(rows)
        if found:
            return Offer(found, kind)

    # FIXME: no dataset filter, so a column of this same dataset can be offered as a respelling.
    respelled = best_answer(session.scalars(select(FieldDoc).where(
        func.lower(FieldDoc.field_name) == field_name.lower(),
        FieldDoc.field_name != field_name)).all())
    return Offer(respelled, "respelled") if respelled else None


def field_work_for(session, source_file, only_outstanding=True):
    """Return the file's columns as FieldWork, unsettled only by default.
    Empty until the machine is confirmed."""
    dataset = source_file.dataset
    if dataset is None:
        return []
    recorded = field_docs(session, dataset)
    work = []
    for column in source_file.fields:
        item = FieldWork(column.name, column.position, column.measurements,
                         recorded.get(column.name))
        if item.is_settled:
            if only_outstanding:
                continue
        else:
            item.offer = offer_for(session, dataset, column.name, item.doc)
        work.append(item)
    return work


def coverage_of(session, source_file):
    """Return the file's Coverage."""
    found = Coverage(columns=len(source_file.fields))
    if source_file.dataset is None:
        return found
    recorded = field_docs(session, source_file.dataset)
    for column in source_file.fields:
        doc = recorded.get(column.name)
        if doc and doc.is_settled:
            found.confirmed += 1
        elif doc and doc.has_answer:
            found.offered += 1
    return found


# --- answering ---

def dataset_for(session, source_file, machine_name, dataset_name=None):
    """Return (machine, dataset) for the file under this machine name, made if new.
    The dataset is <MACHINE>_<KIND> unless named."""
    machine, _ = find_or_make_machine(session, machine_name)
    kind = source_file.kind
    name = dataset_name or (f"{machine.name.upper()}_{kind}" if kind else machine.name.upper())
    dataset, _ = find_or_make_dataset(session, machine, name, kind)
    return machine, dataset


def confirm_machine(session, source_file, machine_name, dataset_name=None):
    """Set the file's machine and dataset; return (machine, dataset)."""
    machine, dataset = dataset_for(session, source_file, machine_name, dataset_name)
    source_file.dataset = dataset
    source_file.iteration = source_file.suggested_iteration
    source_file.state = STATE_IN_PROGRESS
    source_file.updated_at = now()
    session.commit()
    return machine, dataset


def record_field_answer(session, dataset, field_name, description=None,
                        data_type=None, unit=None, note=None, source_file=None,
                        recorded_by=None):
    """Record a confirmed answer for a column; return the FieldDoc.
    None leaves a part alone; "" clears it."""
    doc = field_doc(session, dataset, field_name, create=True)
    for part, value in (("description", description), ("data_type", data_type),
                        ("unit", unit), ("note", note)):
        if value is not None:
            setattr(doc, part, value or None)
    doc.confirm(recorded_by, source_file=source_file)
    if source_file is not None:
        refresh_state(session, source_file)
    session.commit()
    return doc


def refresh_state(session, source_file):
    """Set and return the file's state from its coverage."""
    found = coverage_of(session, source_file)
    if source_file.machine is None:
        source_file.state = STATE_IMPORTED
    elif found.columns and not found.outstanding:
        source_file.state = STATE_COMPLETE
    else:
        source_file.state = STATE_IN_PROGRESS
    source_file.updated_at = now()
    return source_file.state


# --- dataset-level questions ---

def outstanding_dataset_questions(session, dataset):
    """Return [(question, answer row or None)] for dataset questions not confirmed."""
    answered = dataset_answers(session, dataset)
    return [(spec, answered.get(spec["key"])) for spec in DATASET_QUESTIONS
            if not (answered.get(spec["key"]) and answered[spec["key"]].is_confirmed)]


def record_dataset_answer(session, dataset, key, answer, recorded_by=None,
                          source_file=None):
    """Record a confirmed dataset-level answer; return the row."""
    row = dataset_answer(session, dataset, key)
    if row is None:
        row = DatasetAnswer(dataset_id=dataset.id, key=key, answer=answer)
        session.add(row)
    row.answer = answer
    row.confirm(recorded_by, source_file=source_file)
    session.commit()
    return row


def clear_dataset_answer(session, dataset, key):
    """Remove a dataset-level answer, if there is one."""
    row = dataset_answer(session, dataset, key)
    if row is not None:
        session.delete(row)
        session.commit()


def machine_needs_profile(machine):
    """Return whether the machine has no hardware recorded."""
    return not machine.iterations


def files_for_listing(session):
    """Return [(file, coverage)], least finished first."""
    rows = [(source_file, coverage_of(session, source_file))
            for source_file in session.scalars(select(SourceFile).order_by(SourceFile.name))]
    rows.sort(key=lambda row: (row[1].share, row[0].name))
    return rows
