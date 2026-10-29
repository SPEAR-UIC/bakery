"""bakery/transfer.py - moving files between datasets, and copying answers between them

    change_machine         move a file to another machine's dataset, answers and all
    group_files            put several files of one format in one dataset
    plan_copy, apply_copy  plan copying another dataset's answers in, then write the chosen ones
Only answers confirmed in the source are copied, since copying confirms them.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy import select

from models import Dataset, DatasetAnswer, FieldDoc, now
from outstanding import dataset_for, refresh_state
from queries import copy_row, table_named

PARTS = ("description", "data_type", "unit", "note")
PART_LABELS = {"description": "description", "data_type": "type", "unit": "unit",
               "note": "note", "table": "decoding table", "answer": "answer"}


def table_of(doc):
    """Return the name of the column's decoding table, or None."""
    return doc.enumeration.name if doc.enumeration else None


def values_of(doc, table_name):
    """Return a column's answer as a dict of comparable values."""
    values = {part: getattr(doc, part) or None for part in PARTS}
    values["table"] = table_name
    return values


def columns_of(dataset):
    """Return the column names across the dataset's files, in order.
    For a dataset with no files (seeded only), return its recorded field names."""
    names = {column.name: None for source_file in dataset.files
             for column in source_file.fields}
    return list(names or {doc.field_name: None for doc in dataset.field_docs})


def describe_values(values):
    """Return the values as "label: value" lines."""
    lines = []
    for part in PARTS + ("table", "answer"):
        if part not in values:
            continue
        value = values[part]
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"{PART_LABELS[part]}: {value if value not in (None, '') else '(none)'}")
    return lines


# --- copying ---

@dataclass
class Difference:
    """An answer the target has already confirmed differently."""

    kind: str                           # "column" or "answer"
    name: str
    current: dict
    incoming: dict
    take_incoming: Optional[bool] = None


@dataclass
class CopyPlan:
    """What copying source's answers into target would change."""
    source: Dataset
    target: Dataset
    fills: List[str] = field(default_factory=list)
    same: List[str] = field(default_factory=list)
    differences: List[Difference] = field(default_factory=list)
    not_confirmed: List[str] = field(default_factory=list)
    unanswered_in_source: int = 0
    answer_fills: List[str] = field(default_factory=list)
    answer_same: List[str] = field(default_factory=list)
    links_left_out: List[str] = field(default_factory=list)
    links_kept: List[str] = field(default_factory=list)        # "column (table)"
    incoming: Dict[str, dict] = field(default_factory=dict)
    tables: Dict[str, Optional[int]] = field(default_factory=dict)

    @property
    def column_differences(self):
        """Return the differences in columns."""
        return [each for each in self.differences if each.kind == "column"]

    @property
    def answer_differences(self):
        """Return the differences in dataset-level answers."""
        return [each for each in self.differences if each.kind == "answer"]

    def lines(self):
        """Return the plan as lines."""
        columns = len(self.fills) + len(self.same) + len(self.column_differences)
        out = [f"From {self.source.name} into {self.target.name}",
               "",
               f"  columns answered in both      {columns}",
               f"    will be filled              {len(self.fills)}",
               f"    already the same            {len(self.same)}",
               f"    differ, you choose          {len(self.column_differences)}"]
        if self.not_confirmed:
            out.append(f"  not copied, unconfirmed in {self.source.name}: "
                       f"{len(self.not_confirmed)}")
        if self.unanswered_in_source:
            out.append(f"  not answered in {self.source.name}: {self.unanswered_in_source}")
        out += ["",
                f"  dataset-level answers: {len(self.answer_fills)} to fill, "
                f"{len(self.answer_same)} the same, "
                f"{len(self.answer_differences)} differ"]
        if self.links_left_out:
            out.append(f"  decoding-table links left out, no table of that name on "
                       f"{self.target.machine.name}: {', '.join(self.links_left_out)}")
        if self.links_kept:
            out.append(f"  keep their own decoding table, none incoming: "
                       f"{', '.join(self.links_kept)}")
        return out


def plan_columns(session, plan, source, target):
    """Add each target column to the plan as a fill, same, difference or skip."""
    source_docs = {doc.field_name: doc for doc in source.field_docs}
    target_docs = {doc.field_name: doc for doc in target.field_docs}
    for name in columns_of(target):
        incoming_doc = source_docs.get(name)
        if not (incoming_doc and incoming_doc.has_answer):
            plan.unanswered_in_source += 1
            continue
        if not incoming_doc.is_confirmed:
            plan.not_confirmed.append(name)
            continue

        # A link survives only if the target machine has a table of that name.
        table = table_named(session, target.machine_id, table_of(incoming_doc))
        if incoming_doc.enumeration and table is None:
            plan.links_left_out.append(name)
        plan.tables[name] = table.id if table else None
        incoming = plan.incoming[name] = values_of(incoming_doc, table.name if table else None)

        current_doc = target_docs.get(name)
        current = current_doc and values_of(current_doc, table_of(current_doc))
        if current_doc is None or not current_doc.is_settled:
            plan.fills.append(name)
            if table is None and current_doc is not None and current_doc.enumeration:
                plan.links_kept.append(f"{name} ({table_of(current_doc)})")
        elif current == incoming:
            plan.same.append(name)
        else:
            plan.differences.append(Difference("column", name, current, incoming))


def plan_answers(plan, source, target):
    """Add each confirmed source dataset-level answer to the plan."""
    target_answers = {row.key: row for row in target.answers}
    for row in source.answers:
        if not row.is_confirmed:
            continue
        current = target_answers.get(row.key)
        if current is None or not current.is_confirmed:
            plan.answer_fills.append(row.key)
        elif current.answer == row.answer:
            plan.answer_same.append(row.key)
        else:
            plan.differences.append(Difference(
                "answer", row.key, {"answer": current.answer}, {"answer": row.answer}))


def plan_copy(session, source: Dataset, target: Dataset) -> CopyPlan:
    """Return the CopyPlan for copying source into target; write nothing."""
    plan = CopyPlan(source=source, target=target)
    plan_columns(session, plan, source, target)
    plan_answers(plan, source, target)
    return plan


def write_column(session, plan, target_docs, name, replace, recorded_by, stamp):
    """Write one column: fill keeps what the source leaves empty, replace does not."""
    doc = target_docs.get(name)
    if doc is None:
        doc = target_docs[name] = FieldDoc(dataset_id=plan.target.id, field_name=name)
        session.add(doc)
    incoming = plan.incoming[name]
    for part in PARTS:
        if replace or incoming[part] is not None:
            setattr(doc, part, incoming[part])
    # A fill keeps the target's own link when none comes in; the plan lists it.
    if replace or plan.tables[name] is not None:
        doc.enumeration_id = plan.tables[name]
    doc.confirm(recorded_by, stamp)


def apply_copy(session, plan: CopyPlan) -> List[str]:
    """Write the plan; take a difference only where take_incoming is True.
    Return lines saying what was done."""
    recorded_by = f"copied from {plan.source.name}"
    stamp = now()
    target_docs = {doc.field_name: doc for doc in plan.target.field_docs}
    taken_columns = [each.name for each in plan.column_differences if each.take_incoming]
    for name in plan.fills:
        write_column(session, plan, target_docs, name, False, recorded_by, stamp)
    for name in taken_columns:
        write_column(session, plan, target_docs, name, True, recorded_by, stamp)

    source_answers = {row.key: row for row in plan.source.answers}
    target_answers = {row.key: row for row in plan.target.answers}
    taken_answers = [each.name for each in plan.answer_differences if each.take_incoming]
    for key in plan.answer_fills + taken_answers:
        row = target_answers.get(key)
        if row is None:
            row = DatasetAnswer(dataset_id=plan.target.id, key=key)
            session.add(row)
        row.answer = source_answers[key].answer
        row.confirm(recorded_by, stamp)

    session.flush()
    for source_file in plan.target.files:
        refresh_state(session, source_file)
    session.commit()

    kept_columns = len(plan.column_differences) - len(taken_columns)
    kept_answers = len(plan.answer_differences) - len(taken_answers)
    lines = [f"Copied from {plan.source.name} into {plan.target.name}",
             "",
             f"  columns filled                {len(plan.fills)}",
             f"  columns replaced, you chose   {len(taken_columns)}",
             f"  columns kept as they were     {kept_columns + len(plan.same)}",
             f"  dataset-level answers written {len(plan.answer_fills) + len(taken_answers)}",
             f"  dataset-level answers kept    {kept_answers + len(plan.answer_same)}"]
    if plan.links_left_out:
        lines.append(f"  decoding-table links left out: {', '.join(plan.links_left_out)}")
    if plan.links_kept:
        lines.append(f"  kept their own decoding table: {', '.join(plan.links_kept)}")
    return lines


def copy_candidates(session, target: Dataset):
    """Return [(dataset, target columns it has confirmed, all it has confirmed)].
    Most useful first."""
    wanted = set(columns_of(target))
    found = []
    for dataset in session.scalars(select(Dataset).where(Dataset.id != target.id)):
        confirmed = [doc for doc in dataset.field_docs if doc.is_confirmed and doc.has_answer]
        matching = sum(1 for doc in confirmed if doc.field_name in wanted)
        found.append((dataset, matching, len(confirmed)))
    found.sort(key=lambda row: (-row[1], -row[2], row[0].name))
    return found


# --- changing the machine ---

@dataclass
class MachineChange:
    """What change_machine did to one file."""
    file_name: str
    old_dataset: Optional[str]
    new_dataset: str
    moved: bool = False                 # moved rather than copied
    columns: int = 0
    answers: int = 0
    kept_differences: List[str] = field(default_factory=list)
    links_dropped: List[str] = field(default_factory=list)
    removed_dataset: Optional[str] = None

    def lines(self):
        """Return the change as lines."""
        if self.old_dataset == self.new_dataset:
            return [f"{self.file_name} is already in {self.new_dataset}. Nothing changed."]
        verb = "moved" if self.moved else "copied"
        out = [f"{self.file_name}",
               f"  {self.old_dataset or 'no machine'} -> {self.new_dataset}",
               "",
               f"  column answers {verb}         {self.columns}",
               f"  dataset-level answers {verb}  {self.answers}"]
        if self.kept_differences:
            out.append(f"  kept {self.new_dataset}'s own confirmed answer, which "
                       f"differs, for: {', '.join(self.kept_differences)}")
        if self.links_dropped:
            out.append(f"  decoding-table links dropped, no table of that name on the "
                       f"new machine: {', '.join(self.links_dropped)}")
        if self.removed_dataset:
            out.append(f"  {self.removed_dataset} had no other files and was removed")
        elif self.old_dataset and not self.moved:
            out.append(f"  {self.old_dataset} still has other files and keeps its answers")
        return out


def carry_columns(session, old, new, machine, change):
    """Move or copy the old dataset's columns to the new; keep settled answers in the new."""
    new_docs = {doc.field_name: doc for doc in new.field_docs}
    for doc in list(old.field_docs):
        existing = new_docs.get(doc.field_name)
        old_table = table_of(doc)
        if existing is not None and existing.is_settled:
            if values_of(existing, table_of(existing)) != values_of(doc, old_table):
                change.kept_differences.append(doc.field_name)
            continue
        if existing is not None:
            session.delete(existing)
            session.flush()
        table = table_named(session, machine.id, old_table)
        if old_table and table is None:
            change.links_dropped.append(doc.field_name)
        if change.moved:
            doc.dataset = new
            doc.enumeration = table
        else:
            session.add(copy_row(doc, dataset_id=new.id,
                                 enumeration_id=table.id if table else None))
        change.columns += 1


def carry_answers(session, old, new, change):
    """Move or copy the old dataset's answers to the new; keep confirmed answers in the new."""
    new_answers = {row.key: row for row in new.answers}
    for row in list(old.answers):
        existing = new_answers.get(row.key)
        # Confirmed is settled here: an answer is one value, and blank deletes it.
        if existing is not None and existing.is_confirmed:
            if existing.answer != row.answer:
                change.kept_differences.append(row.key)
            continue
        if existing is not None:
            session.delete(existing)
            session.flush()
        if change.moved:
            row.dataset = new
        else:
            session.add(copy_row(row, dataset_id=new.id))
        change.answers += 1


def change_machine(session, source_file, machine_name, dataset_name=None) -> MachineChange:
    """Move a file to another dataset; move its answers if it was alone, else copy them.
    A settled answer in the new dataset is kept and the difference listed."""
    old = source_file.dataset
    machine, new = dataset_for(session, source_file, machine_name, dataset_name)
    change = MachineChange(file_name=source_file.name,
                           old_dataset=old.name if old else None, new_dataset=new.name)
    if old is not None and old.id == new.id:
        return change

    # Through the relationship: setting the id would be undone by the loaded lists on flush.
    source_file.dataset = new
    source_file.iteration = source_file.suggested_iteration
    session.flush()

    if old is not None:
        change.moved = not [each for each in old.files if each.id != source_file.id]
        carry_columns(session, old, new, machine, change)
        carry_answers(session, old, new, change)
        session.flush()
        if change.moved:
            # Expired first, so the cascade does not act on the moved rows.
            session.expire(old)
            change.removed_dataset = old.name
            session.delete(old)
        session.flush()
        session.expire(new)

    refresh_state(session, source_file)
    session.commit()
    return change


# --- many files, one format ---

@dataclass
class GroupChange:
    """What group_files did."""
    dataset: str
    joined: int = 0
    already_in: int = 0
    answers_from: List[str] = field(default_factory=list)
    kept_differences: List[str] = field(default_factory=list)
    removed_datasets: List[str] = field(default_factory=list)
    other_columns: List[str] = field(default_factory=list)     # files unlike the source

    def lines(self):
        """Return the change as lines."""
        out = [f"{self.joined} file(s) joined {self.dataset}"]
        if self.already_in:
            out.append(f"{self.already_in} were already in it")
        if self.answers_from:
            out.append(f"answers brought in from: {', '.join(self.answers_from)}")
        if self.kept_differences:
            out.append(f"kept {self.dataset}'s own confirmed answer, which differs, "
                       f"for: {', '.join(self.kept_differences)}")
        if self.removed_datasets:
            out.append(f"{len(self.removed_datasets)} emptied dataset(s) removed")
        if self.other_columns:
            shown = ", ".join(self.other_columns[:3])
            more = len(self.other_columns) - 3
            out.append(f"WARNING: {len(self.other_columns)} file(s) have different columns "
                       f"from the source: {shown}" + (f" and {more} more" if more > 0 else ""))
        out.append(f"Every column answered in {self.dataset} now applies to all of them.")
        return out


def group_files(session, files, machine_name, dataset_name):
    """Put files of one format in one dataset, each moved as change_machine moves it."""
    change = GroupChange(dataset=dataset_name)
    for source_file in files:
        moved = change_machine(session, source_file, machine_name, dataset_name)
        if moved.old_dataset == moved.new_dataset:
            change.already_in += 1
            continue
        change.joined += 1
        if moved.columns or moved.answers:
            change.answers_from.append(source_file.name)
        change.kept_differences += [name for name in moved.kept_differences
                                    if name not in change.kept_differences]
        if moved.removed_dataset:
            change.removed_datasets.append(moved.removed_dataset)
    return change


def same_as(session, files, source):
    """Put files in source's dataset, sharing its machine and answers; return a GroupChange."""
    targets = [each for each in files if each.id != source.id]
    dataset = source.dataset
    change = group_files(session, targets, dataset.machine.name, dataset.name)
    wanted = {column.name for column in source.fields}
    change.other_columns = [each.name for each in targets
                            if {column.name for column in each.fields} != wanted]
    return change


def rename_set(session, dataset, name):
    """Rename a dataset (a file set); raise ValueError if empty or taken on its machine."""
    name = name.strip()
    if not name:
        raise ValueError("a set needs a name")
    clash = session.scalar(select(Dataset).where(
        Dataset.machine_id == dataset.machine_id, Dataset.name == name, Dataset.id != dataset.id))
    if clash is not None:
        raise ValueError(f"{dataset.machine.name} already has a set named {name!r}")
    dataset.name = name
    session.commit()
