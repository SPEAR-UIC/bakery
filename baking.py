"""bakery/baking.py - writing recorded answers back out as Croissants

One <DATASET>.baked.json per dataset; its data is one cr:FileSet matching every file.
FileSets need no checksum, so the data need not be reachable from here.
The par-baked marker comes off only when nothing is outstanding; the rest is listed by reason.
Units and notes are appended to descriptions.
The Markdown beside each file comes from parbake's renderer; the .txt from pandoc.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import json
import re
from dataclasses import dataclass, field as dataclass_field
from datetime import date
from pathlib import Path

from sqlalchemy import select

from croissant_files import (BAKED_MARKDOWN_SUFFIX, BAKED_SUFFIX, BAKED_TEXT_SUFFIX,
                              conforms_to, read_json)
from enumerations import codes_in, coverage
from importing import split_off_dates
from models import PARTITION_FIELDS, SourceFile
from outstanding import field_work_for, outstanding_dataset_questions
from parbake_link import render_markdown
from plain_text import has_pandoc, render_text
from queries import dataset_answer, dataset_answers, field_docs
from questions import DATASET_QUESTIONS, DEFAULT_VERSION, REQUIRED_KEYS, WRAPPED_KEYS, wrap_answer

# A validator rejects a version that is not MAJOR.MINOR.PATCH; it counts as unfinished.
SEMANTIC_VERSION = re.compile(r"^\d+(\.\d+){1,2}$")

UNFINISHED_WARNING = ("NOT FINISHED. Questions remain unanswered; this file "
                      "deliberately fails validation and must not be cited, "
                      "submitted or published.")


@dataclass
class BakeReport:
    """What a bake wrote, left unfinished, superseded and skipped."""
    written: list = dataclass_field(default_factory=list)       # the Croissants
    undecoded: list = dataclass_field(default_factory=list)     # warnings, not reasons
    readable: list = dataclass_field(default_factory=list)      # .md and .txt beside them
    unfinished: dict = dataclass_field(default_factory=dict)    # path -> reasons
    superseded: list = dataclass_field(default_factory=list)
    skipped: list = dataclass_field(default_factory=list)       # (what, why)

    @property
    def finished(self):
        """Return the written paths with nothing outstanding."""
        return [path for path in self.written if str(path) not in self.unfinished]

    def lines(self):
        """Return the report as lines."""
        out = [f"{len(self.written)} Croissant(s) written, "
               f"{len(self.finished)} of them finished, "
               f"{len(self.readable)} .md/.txt rendering(s) beside them"]
        for path in self.written:
            reasons = self.unfinished.get(str(path))
            if not reasons:
                out.append(f"  {Path(path).name}: finished, conformsTo names a real "
                           "Croissant version")
                continue
            out.append(f"  {Path(path).name}: STILL PAR-BAKED, "
                       f"{len(reasons)} thing(s) outstanding")
            out += [f"      {reason}" for reason in reasons[:6]]
            if len(reasons) > 6:
                out.append(f"      ... and {len(reasons) - 6} more")
        out += [f"  warning: {line}" for line in self.undecoded]
        out += [f"  superseded (par-baked, now stale): {Path(path).name}"
                for path in self.superseded]
        out += [f"  skipped {what}: {why}" for what, why in self.skipped]
        return out


# --- descriptions ---

def append_sentence(text, addition):
    """Return text with addition after it, unless it is already there."""
    text = (text or "").strip()
    if addition.lower() in text.lower():
        return text
    if not text:
        return addition
    separator = " " if text.endswith((".", "!", "?")) else ". "
    return f"{text}{separator}{addition}"


def with_unit(description, unit):
    """Return the description with "Unit: <unit>." appended, unless already there."""
    if not unit:
        return description or ""
    if f"unit: {unit.lower()}" in (description or "").lower():
        return (description or "").strip()
    return append_sentence(description, f"Unit: {unit}.")


def with_note(description, note):
    """Return the description with "NOTE: <note>" last, for notes.py to find."""
    if not note:
        return description or ""
    return append_sentence(description, f"NOTE: {note.strip()}")


def with_undecoded(description, codes, table):
    """Append "UNDECODED: ..." for codes the table does not decode."""
    if not codes:
        return description
    return append_sentence(description, f"UNDECODED: {', '.join(codes)} "
                                        f"(no meaning in {table.name}).")


def described(doc):
    """Return the column's description with its unit and note, or ""."""
    return with_note(with_unit(doc.description, doc.unit), doc.note) if doc else ""


# --- the pieces ---

def field_entry(column_name, doc, record_set_id, source_reference, undecoded=()):
    """Return one cr:Field, with a references link to its decoding table if any."""
    entry = {
        "@type": "cr:Field",
        "@id": f"{record_set_id}/{column_name}",
        "name": column_name,
        "description": with_undecoded(described(doc), undecoded, doc and doc.enumeration),
        "source": source_reference(column_name),
    }
    if doc and doc.data_type:
        entry["dataType"] = doc.data_type
    if doc and doc.enumeration is not None:
        entry["references"] = {"field": {"@id": f"{doc.enumeration.name}/code"}}
    return entry


def enumeration_record_set(enumeration):
    """Return a decoding table as a cr:RecordSet, values inline, provisional ones marked."""
    name = enumeration.name
    return {
        "@type": "cr:RecordSet",
        "@id": name,
        "name": name,
        "description": enumeration.description or "Value decodings for the codes in this dataset.",
        "cr:isEnumeration": True,
        "key": {"@id": f"{name}/code"},
        "field": [
            {"@type": "cr:Field", "@id": f"{name}/code", "name": "code",
             "description": enumeration.code_description or "The code as it appears in the data.",
             "dataType": "sc:Text"},
            {"@type": "cr:Field", "@id": f"{name}/meaning", "name": "meaning",
             "description": (enumeration.meaning_description
                             or "What the code means. Anything provisional says so."),
             "dataType": "sc:Text"},
        ],
        "data": [
            {f"{name}/code": value.code,
             f"{name}/meaning": (f"PROVISIONAL: {value.meaning}"
                                 if value.is_provisional and "provisional" not in value.meaning.lower()
                                 else value.meaning)}
            for value in enumeration.values
        ],
    }


def filename_glob_for(dataset):
    """Return a glob matching the dataset's files; a recorded pattern wins.
    Otherwise the longest prefix shared by the file stems."""
    if dataset.filename_pattern:
        return dataset.filename_pattern
    stems = {split_off_dates(each.name)[0] for each in dataset.files}
    if len(stems) == 1:
        return f"{stems.pop()}_*.csv"
    if not stems:
        return f"{dataset.name}_*.csv"
    shortest = min(stems, key=len)
    shared = next((shortest[:position] for position, character in enumerate(shortest)
                   if any(stem[position] != character for stem in stems)), shortest)
    return f"{shared}*.csv" if shared else "*.csv"


def machine_block(machine, iteration=None):
    """Return the machine's hardware as a non-standard block, or None."""
    if machine is None:
        return None
    iteration = iteration or machine.current_iteration
    block = {"name": machine.name}
    if machine.organization:
        block["organization"] = machine.organization
    if iteration is None:
        block["note"] = "No hardware recorded for this machine."
        return block

    block["as_of"] = {
        "label": iteration.label,
        "from": str(iteration.starts_on) if iteration.starts_on else None,
        "to": str(iteration.ends_on) if iteration.ends_on else "present",
        "as_written": iteration.dates_as_written,
    }
    for name in ("vendor", "architecture", "scheduler", "partition_summary"):
        if getattr(iteration, name):
            block[name] = getattr(iteration, name)
    block["partitions"] = [
        {name: getattr(partition, name) for name in PARTITION_FIELDS
         if name != "position" and getattr(partition, name) is not None}
        for partition in iteration.partitions]
    return block


def keywords_with_hardware(keywords, machine, iteration=None):
    """Return the keywords plus the machine and hardware facts not already covered."""
    found = list(keywords) if isinstance(keywords, list) else ([keywords] if keywords else [])
    if machine is None:
        return found
    iteration = iteration or machine.current_iteration
    extra = [machine.name]
    if iteration is not None:
        extra += [iteration.vendor, iteration.architecture, iteration.scheduler]
    for value in filter(None, extra):
        folded = value.lower()
        if not any(folded in each.lower() or each.lower() in folded for each in found):
            found.append(value)
    return found


def answers_for(session, dataset):
    """Return dataset-level answers in catalogue order, with defaults for version and
    datePublished; a bare string is wrapped where needed."""
    recorded = dataset_answers(session, dataset)
    defaults = {"version": DEFAULT_VERSION, "datePublished": date.today().isoformat()}
    written = {}
    for spec in DATASET_QUESTIONS:
        found = recorded.get(spec["key"])
        if found is None and spec["key"] not in defaults:
            continue
        value = found.answer if found is not None else defaults[spec["key"]]
        if spec["key"] in WRAPPED_KEYS and not isinstance(value, dict):
            value = wrap_answer(spec["key"], value)
        written[spec["key"]] = value
    return written


# --- is it finished? ---

def malformed_answers(answers):
    """Return problems with version and datePublished that a validator rejects."""
    problems = []
    version = answers.get("version")
    if isinstance(version, str) and version.strip() and not SEMANTIC_VERSION.match(version.strip()):
        problems.append(f"version is {version!r}, which is not MAJOR.MINOR.PATCH; "
                        "a validator objects to it")
    published = answers.get("datePublished")
    if isinstance(published, str) and published.strip():
        try:
            date.fromisoformat(published.strip())
        except ValueError:
            problems.append(f"datePublished is {published!r}, which is not a YYYY-MM-DD "
                            "date; a validator rejects it outright")
    return problems


def reasons_unfinished(session, dataset):
    """Return every reason the dataset's Croissant is not finished, each column once."""
    reasons = [f"{spec['key']} is " + ("unanswered" if found is None else "seeded but not confirmed")
               for spec, found in outstanding_dataset_questions(session, dataset)
               if spec["key"] in REQUIRED_KEYS]
    reasons += malformed_answers(answers_for(session, dataset))
    columns = {}
    for each in dataset.files:
        for work in field_work_for(session, each):
            columns.setdefault(work.name, work)
    for work in columns.values():
        missing = [what for what, have in (("a description", work.description),
                                           ("a type", work.data_type)) if not have]
        reasons.append(f"{work.name} needs {' and '.join(missing or ['confirming'])}")
    return reasons


# --- the Croissant ---

def context_from(source_file):
    """Return the @context from the file's par-baked Croissant, or None."""
    document, _ = read_json(source_file.parbaked_path)
    return document.get("@context") if document else None


def columns_of(files):
    """Return column names in order, across all the files."""
    return list({column.name: None for each in files for column in each.fields})


def undecoded_codes(session, dataset, files):
    """Return {column: (table, undecoded codes)} for linked columns in these files."""
    found = {}
    for name, doc in field_docs(session, dataset).items():
        if doc.enumeration is None:
            continue
        columns = [column for each in files for column in each.fields if column.name == name]
        codes = coverage(doc.enumeration, codes_in(columns)).undecoded
        if codes:
            found[name] = (doc.enumeration, codes)
    return found


def record_set_for(session, dataset, files, undecoded):
    """Return the dataset's records cr:RecordSet, every field read from the FileSet."""
    recorded = field_docs(session, dataset)
    ordered = columns_of(files)
    answer = dataset_answer(session, dataset, "recordSet")
    return {
        "@type": "cr:RecordSet", "@id": "records", "name": "records",
        "description": answer.answer if answer else f"One record per row. {len(ordered)} fields.",
        "field": [field_entry(name, recorded.get(name), "records",
                              lambda column: {"fileSet": {"@id": "data_files"},
                                              "extract": {"column": column}},
                              undecoded.get(name, (None, ()))[1])
                  for name in ordered],
    }


def used_enumerations(session, dataset):
    """Return the decoding tables this dataset's columns use, by name."""
    return sorted({doc.enumeration.id: doc.enumeration for doc in field_docs(session, dataset).values()
                   if doc.enumeration}.values(), key=lambda table: table.name)


def file_set_for(dataset, files):
    """Return the cr:FileSet standing for every file of the dataset."""
    pattern = filename_glob_for(dataset)
    described_as = [f"Naming: {pattern}."]
    if dataset.format_notes:
        described_as.append(dataset.format_notes.rstrip(".") + ".")
    described_as.append(f"{len(files)} file(s) documented so far: "
                        + ", ".join(sorted(each.name for each in files)) + ".")
    return {"@type": "cr:FileSet", "@id": "data_files", "name": "data_files",
            "description": " ".join(described_as), "encodingFormat": "text/csv",
            "includes": pattern}


def build_document(session, dataset, context, files, undecoded):
    """Return the dataset's Croissant, before finishing."""
    machine = dataset.machine
    document = {"@context": context, "@type": "sc:Dataset", "name": dataset.name}
    answers = answers_for(session, dataset)
    document["description"] = answers.pop("description", dataset.description or "")
    document["conformsTo"] = None           # set by finish(); kept in position
    document.update(answers)
    document["keywords"] = keywords_with_hardware(answers.get("keywords"), machine)
    document["distribution"] = [file_set_for(dataset, files)]
    document["recordSet"] = ([enumeration_record_set(each) for each in used_enumerations(session, dataset)]
                             + [record_set_for(session, dataset, files, undecoded)])
    profile = machine_block(machine)
    if profile:
        document["_machine"] = profile
    return document


def bake_dataset(session, dataset, output_directory, report=None):
    """Write the dataset's Croissant and Markdown; return the report."""
    report = report or BakeReport()
    files = list(dataset.files)
    if not files:
        report.skipped.append((dataset.name, "no files belong to it yet"))
        return report
    context = next(filter(None, map(context_from, files)), None)
    if context is None:
        report.skipped.append((dataset.name, "none of its par-baked files are still on "
                                             "disk, so the @context cannot be read back"))
        return report

    undecoded = undecoded_codes(session, dataset, files)
    document = build_document(session, dataset, context, files, undecoded)
    path = Path(output_directory) / f"{dataset.name}{BAKED_SUFFIX}"
    finish_and_write(document, path, report, reasons_unfinished(session, dataset))
    note_undecoded(report, path, undecoded)
    for each in files:
        each.baked_path = str(path)
        # Rendered from the par-baked Croissant, so now stale.
        report.superseded += [derived.path for derived in each.derived]
    session.commit()
    return report


def note_undecoded(report, path, undecoded):
    """Add a report warning per column with codes its table does not decode."""
    report.undecoded += [f"{path.name}: {column} has {', '.join(codes)} with no meaning "
                         f"in {table.name}" for column, (table, codes) in undecoded.items()]


def finish(document, reasons):
    """Set conformsTo, real only when nothing is outstanding; return whether finished."""
    document["conformsTo"] = conforms_to(finished=not reasons)
    if reasons:
        document["_unfinished"] = {"warning": UNFINISHED_WARNING, "outstanding": reasons}
    return not reasons


def finish_and_write(document, path, report, reasons):
    """Finish the document; write the JSON, and the Markdown and text beside it."""
    finish(document, reasons)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    report.written.append(path)
    if reasons:
        report.unfinished[str(path)] = reasons
    stem = path.name[: -len(BAKED_SUFFIX)]
    markdown = render_markdown(document)
    readable = path.with_name(stem + BAKED_MARKDOWN_SUFFIX)
    readable.write_text(markdown, encoding="utf-8")
    report.readable.append(readable)
    if not has_pandoc():
        report.skipped.append((stem + BAKED_TEXT_SUFFIX, "pandoc is not installed"))
        return
    text = path.with_name(stem + BAKED_TEXT_SUFFIX)
    text.write_text(render_text(markdown), encoding="utf-8")
    report.readable.append(text)


def bake_everything(session, output_directory):
    """Write every dataset that has files; return the report."""
    report = BakeReport()
    datasets = {each.dataset.id: each.dataset
                for each in session.scalars(select(SourceFile).order_by(SourceFile.name))
                if each.dataset is not None}
    for dataset in sorted(datasets.values(), key=lambda dataset: dataset.name):
        bake_dataset(session, dataset, output_directory, report)
    return report
