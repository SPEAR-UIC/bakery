"""bakery/queries.py - find-or-create lookups by name

find_or_make_* return (row, made); new rows are flushed, not committed.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from sqlalchemy import select

from models import Dataset, DatasetAnswer, Enumeration, FieldDoc, Machine, machine_key


def field_doc(session, dataset, name, create=False, **values):
    """Return the FieldDoc for a column; make it if create and missing."""
    doc = session.scalar(select(FieldDoc).where(
        FieldDoc.dataset_id == dataset.id, FieldDoc.field_name == name))
    if doc is None and create:
        doc = FieldDoc(dataset_id=dataset.id, field_name=name, **values)
        session.add(doc)
    return doc


def field_docs(session, dataset):
    """Return {column name: FieldDoc} for a dataset."""
    return {doc.field_name: doc for doc in session.scalars(
        select(FieldDoc).where(FieldDoc.dataset_id == dataset.id))}


def dataset_answer(session, dataset, key):
    """Return the DatasetAnswer for a key, or None."""
    return session.scalar(select(DatasetAnswer).where(
        DatasetAnswer.dataset_id == dataset.id, DatasetAnswer.key == key))


def dataset_answers(session, dataset):
    """Return {key: DatasetAnswer} for a dataset."""
    return {row.key: row for row in session.scalars(
        select(DatasetAnswer).where(DatasetAnswer.dataset_id == dataset.id))}


def table_named(session, machine_id, name):
    """Return the machine's Enumeration with this name, or None."""
    if not name:
        return None
    return session.scalar(select(Enumeration).where(
        Enumeration.machine_id == machine_id, Enumeration.name == name))


def machine_names(session):
    """Return every machine name."""
    return list(session.scalars(select(Machine.name)))


def find_or_make_machine(session, name):
    """Return (machine, made), matched on the folded name."""
    machine = session.scalar(select(Machine).where(Machine.key == machine_key(name)))
    if machine is not None:
        return machine, False
    machine = Machine(name)
    session.add(machine)
    session.flush()
    return machine, True


def find_or_make_dataset(session, machine, name, kind=None):
    """Return (dataset, made)."""
    dataset = session.scalar(select(Dataset).where(
        Dataset.machine_id == machine.id, Dataset.name == name))
    if dataset is not None:
        return dataset, False
    dataset = Dataset(machine_id=machine.id, name=name, kind=kind)
    session.add(dataset)
    session.flush()
    return dataset, True


def copy_row(row, **changes):
    """Return a new row with every column of row but its key, changes applied."""
    values = {column.key: getattr(row, column.key)
              for column in row.__table__.columns if not column.primary_key}
    values.update(changes)
    return type(row)(**values)
