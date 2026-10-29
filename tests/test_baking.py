"""bakery/tests/test_baking.py - tests for writing recorded answers out as Croissants (baking.py)

A finished file must pass mlcroissant; an unfinished one must not.
Run: pytest tests/test_baking.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from baking import (
    answers_for,
    bake_dataset,
    bake_everything,
    enumeration_record_set,
    field_entry,
    filename_glob_for,
    finish,
    keywords_with_hardware,
    machine_block,
    reasons_unfinished,
    with_unit,
)
from database import Database
from importing import import_target
from models import (
    Dataset,
    DatasetAnswer,
    Enumeration,
    EnumerationValue,
    FieldDoc,
    Machine,
    MachineIteration,
    MachinePartition,
    SourceFile,
)
from outstanding import confirm_machine, record_dataset_answer, record_field_answer
from questions import DATASET_QUESTIONS


@pytest.fixture
def database(tmp_path):
    """Yield an open database in a temporary directory."""
    with Database(directory=tmp_path) as opened:
        yield opened


def a_parbaked_file(directory, name="ANL-ALCF-DJC-POLARIS_20220809_20221231",
                    columns=("JOB_NAME", "EXIT_CODE"), csv_path=None):
    """Write a par-baked Croissant to disk; baking reads the @context back off it."""
    directory = Path(directory) / "parbaked_croissants"
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        # mlcroissant reports a context without @language as an error; parbake writes it.
        "@context": {"@language": "en",
                     "@vocab": "https://schema.org/", "cr": "http://mlcommons.org/croissant/",
                     "rai": "http://mlcommons.org/croissant/RAI/", "sc": "https://schema.org/",
                     "dct": "http://purl.org/dc/terms/", "conformsTo": "dct:conformsTo",
                     "citeAs": "cr:citeAs", "field": "cr:field", "recordSet": "cr:recordSet",
                     "fileObject": "cr:fileObject", "fileSet": "cr:fileSet",
                     "includes": "cr:includes", "extract": "cr:extract",
                     "column": "cr:column", "references": "cr:references",
                     "dataType": {"@id": "cr:dataType", "@type": "@vocab"},
                     "key": "cr:key", "md5": "cr:md5", "source": "cr:source",
                     "data": {"@id": "cr:data", "@type": "@json"}},
        "@type": "sc:Dataset",
        "conformsTo": "http://mlcommons.org/croissant/PARBAKED-DO-NOT-SUBMIT",
        "name": name,
        "distribution": [{"@type": "cr:FileObject", "name": f"{name}.csv"}],
        "recordSet": [{"@type": "cr:RecordSet", "name": "records",
                       "field": [{"name": column} for column in columns]}],
        "_parbake": {"source_file": str(csv_path) if csv_path else f"/data/{name}.csv",
                     "source_size_in_bytes": 27825794, "rows_read": 39432},
        "_parbake_measurements": {
            column: {"position": index, "rows_seen": 39432, "distinct_count": 42}
            for index, column in enumerate(columns)},
    }
    path = directory / f"{name}.parbaked.json"
    path.write_text(json.dumps(document))
    return path


def an_answered_file(session, root, columns=("JOB_NAME", "EXIT_CODE"), csv_path=None,
                     name="ANL-ALCF-DJC-POLARIS_20220809_20221231"):
    """Return a file imported, given a machine, and with every question answered."""
    a_parbaked_file(root, name=name, columns=columns, csv_path=csv_path)
    import_target(session, root)
    stored = session.scalar(select(SourceFile).where(SourceFile.name == name))
    machine, dataset = confirm_machine(session, stored, "polaris")
    plausible = {"version": "1.0.0", "datePublished": "2026-09-01",
                 "url": "https://example.invalid/polaris"}
    for spec in DATASET_QUESTIONS:
        record_dataset_answer(session, dataset, spec["key"],
                              plausible.get(spec["key"], f"answer for {spec['key']}"))
    for column in columns:
        record_field_answer(session, dataset, column, description=f"What {column} holds.",
                            data_type="sc:Text", source_file=stored)
    return stored, machine, dataset


def a_profiled_machine(session, name="polaris"):
    """Return a machine and one profiled iteration of it."""
    machine = session.scalar(select(Machine).where(Machine.key == name.lower()))
    if machine is None:
        machine = Machine(name)
        session.add(machine)
        session.commit()
    machine.organization = "Argonne National Laboratory"
    iteration = MachineIteration(
        machine_id=machine.id, label="as deployed", starts_on=date(2022, 8, 1),
        dates_as_written="August 2022 - present", vendor="Hewlett Packard Enterprise",
        architecture="HPE Apollo Gen10+", scheduler="PBS Professional",
        partition_summary="Homogeneous (identical) nodes")
    iteration.partitions = [MachinePartition(
        name="default", cpu="1x AMD EPYC Milan 7543P per node",
        gpu="4x NVIDIA A100 per node", node_count=595)]
    session.add(iteration)
    session.commit()
    return machine, iteration


# --- units -----------------------------------------------------------------

def test_the_unit_is_appended_to_the_description_as_djc_v4_writes_it():
    assert with_unit("Core hours consumed by the job", "Core Hours") == (
        "Core hours consumed by the job. Unit: Core Hours.")
    # A sentence that already ends in a full stop does not gain a second one.
    assert with_unit("Core hours consumed.", "Core Hours") == (
        "Core hours consumed. Unit: Core Hours.")


def test_a_unit_already_in_the_text_is_not_appended_twice():
    """Anything seeded out of djc_v4 already has it: it wrote them in by hand."""
    already = "Refers to the size category of the job. Unit: Core Hours."
    assert with_unit(already, "Core Hours") == already


def test_no_unit_and_no_description_are_both_survivable():
    assert with_unit("Just a description.", None) == "Just a description."
    assert with_unit(None, "Watts") == "Unit: Watts."
    assert with_unit(None, None) == ""


# --- one field -------------------------------------------------------------

def test_a_field_carries_its_description_type_and_source(database):
    doc = FieldDoc(dataset_id=1, field_name="EXIT_CODE", data_type="sc:Integer",
                   description="The scheduler's exit status", unit=None)
    entry = field_entry("EXIT_CODE", doc, "records",
                        lambda name: {"fileObject": {"@id": "x.csv"},
                                      "extract": {"column": name}})

    assert entry["@id"] == "records/EXIT_CODE"
    assert entry["dataType"] == "sc:Integer"
    assert entry["source"]["extract"]["column"] == "EXIT_CODE"
    assert "references" not in entry        # no decoding table linked


def test_a_column_with_no_description_yet_still_emits_a_field(database):
    """A gap is written as a gap. The conformsTo is what says the file is unfinished."""
    entry = field_entry("NOTE", None, "records", lambda name: {})
    assert entry["name"] == "NOTE"
    assert entry["description"] == ""
    assert "dataType" not in entry


def test_a_column_with_a_decoding_table_points_at_it(database):
    """The link djc_v4 leaves out, so its decodings sit in the file unreferenced."""
    with database.session() as session:
        machine, _ = a_profiled_machine(session)
        enumeration = Enumeration(machine_id=machine.id, name="exit_code_enum")
        session.add(enumeration)
        session.commit()
        dataset = Dataset(machine_id=machine.id, name="POLARIS_DJC", kind="DJC")
        session.add(dataset)
        session.commit()
        doc = FieldDoc(dataset_id=dataset.id, field_name="EXIT_CODE",
                       data_type="sc:Integer", description="The exit code.",
                       enumeration_id=enumeration.id)
        session.add(doc)
        session.commit()

        entry = field_entry("EXIT_CODE", doc, "records", lambda name: {})
        assert entry["references"] == {"field": {"@id": "exit_code_enum/code"}}


# --- decoding tables -------------------------------------------------------

def test_a_decoding_table_becomes_an_enumeration_record_set(database):
    with database.session() as session:
        machine, _ = a_profiled_machine(session)
        enumeration = Enumeration(machine_id=machine.id, name="exit_code_enum",
                                  description="Exit-code decodings.")
        enumeration.values = [
            EnumerationValue(code="0", meaning="Success.", position=0),
            EnumerationValue(code="143", meaning="SIGTERM. Did not run to walltime.",
                             is_provisional=True, position=1)]
        session.add(enumeration)
        session.commit()

        written = enumeration_record_set(enumeration)
        assert written["cr:isEnumeration"] is True
        assert written["key"] == {"@id": "exit_code_enum/code"}
        assert [field["name"] for field in written["field"]] == ["code", "meaning"]
        assert written["data"][0] == {"exit_code_enum/code": "0",
                                      "exit_code_enum/meaning": "Success."}
        # A guess says so in the file, not only in the database.
        assert written["data"][1]["exit_code_enum/meaning"].startswith("PROVISIONAL: ")


def test_a_meaning_that_already_says_provisional_is_not_labelled_twice(database):
    with database.session() as session:
        machine, _ = a_profiled_machine(session)
        enumeration = Enumeration(machine_id=machine.id, name="exit_code_enum")
        enumeration.values = [EnumerationValue(
            code="143", meaning="SIGTERM. PROVISIONAL: open question.",
            is_provisional=True, position=0)]
        session.add(enumeration)
        session.commit()

        meaning = enumeration_record_set(enumeration)["data"][0]["exit_code_enum/meaning"]
        assert meaning.count("PROVISIONAL") == 1


# --- the machine -----------------------------------------------------------

def test_the_machine_profile_is_written_out_where_croissant_has_no_slot(database):
    with database.session() as session:
        machine, iteration = a_profiled_machine(session)
        block = machine_block(machine, iteration)

        assert block["name"] == "polaris"
        assert block["organization"] == "Argonne National Laboratory"
        assert block["as_of"]["from"] == "2022-08-01"
        assert block["as_of"]["to"] == "present"
        assert block["as_of"]["as_written"] == "August 2022 - present"
        assert block["scheduler"] == "PBS Professional"
        assert block["partitions"][0]["node_count"] == 595
        # Nothing empty is written: a blank rack count is not "rack_count": null.
        assert "rack_count" not in block["partitions"][0]


def test_a_machine_with_no_hardware_says_so_rather_than_looking_complete(database):
    with database.session() as session:
        machine = Machine("polaris")
        session.add(machine)
        session.commit()
        assert "No hardware recorded" in machine_block(machine)["note"]


def test_the_hardware_joins_the_keywords_without_repeating_them(database):
    """djc_v4 lists "HPE Apollo Gen10+" and "NVIDIA A100" by hand. One keyword per fact."""
    with database.session() as session:
        machine, iteration = a_profiled_machine(session)
        keywords = keywords_with_hardware(
            ["Polaris", "ALCF", "HPE Apollo Gen10+", "NVIDIA A100"], machine, iteration)

        assert "PBS Professional" in keywords
        assert "Hewlett Packard Enterprise" in keywords
        # Already present in another spelling, so not added again.
        assert keywords.count("HPE Apollo Gen10+") == 1
        assert not any("per node" in each for each in keywords)


# --- the series glob -------------------------------------------------------

def test_the_glob_comes_from_the_filenames_not_from_the_dataset_name(database, tmp_path):
    """POLARIS_DJC's files are called ANL-ALCF-DJC-POLARIS_*. Two conventions."""
    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out")
        assert filename_glob_for(dataset) == "ANL-ALCF-DJC-POLARIS_*.csv"


def test_a_recorded_filename_pattern_wins(database, tmp_path):
    """Somebody who wrote one knows about the date ranges nobody has imported."""
    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out")
        dataset.filename_pattern = "ANL-ALCF-DJC-POLARIS_YYYYMMDD_YYYYMMDD.csv"
        session.commit()
        assert filename_glob_for(dataset).startswith("ANL-ALCF-DJC-POLARIS_YYYY")


def test_files_of_two_shapes_glob_to_what_they_share(database, tmp_path):
    with database.session() as session:
        stored, machine, dataset = an_answered_file(session, tmp_path / "out")
        a_parbaked_file(tmp_path / "other", name="ANL-ALCF-DJC-POLARIS-V2_20230101_20231231")
        import_target(session, tmp_path / "other")
        second = session.scalar(select(SourceFile).where(
            SourceFile.name.like("%V2%")))
        second.dataset_id = dataset.id
        session.commit()

        assert filename_glob_for(dataset) == "ANL-ALCF-DJC-POLARIS*.csv"


# --- the dataset-level answers ---------------------------------------------

def test_a_bare_string_licence_is_wrapped_and_an_object_one_is_left_alone(
        database, tmp_path):
    licence = {"@type": "sc:CreativeWork", "name": "ALCF attribution",
               "text": "Cite the ALCF.", "url": "https://example.invalid"}
    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out")
        record_dataset_answer(session, dataset, "license", licence)
        record_dataset_answer(session, dataset, "creator", "Argonne National Laboratory")

        written = answers_for(session, dataset)
        # The seeded object keeps its name and url, which wrapping cannot reconstruct.
        assert written["license"] == licence
        assert written["creator"] == {"@type": "sc:Organization",
                                      "name": "Argonne National Laboratory"}


# --- is it finished? -------------------------------------------------------

def test_the_marker_stays_while_anything_is_outstanding(database, tmp_path):
    """The one thing stopping unreviewed work from looking reviewed."""
    document = {}
    assert finish(document, ["EXIT_CODE needs a description"]) is False
    assert "PARBAKED" in document["conformsTo"]
    assert "must not be cited" in document["_unfinished"]["warning"]
    assert document["_unfinished"]["outstanding"] == ["EXIT_CODE needs a description"]


def test_the_marker_goes_only_when_nothing_is_left(database):
    document = {}
    assert finish(document, []) is True
    assert document["conformsTo"] == ["http://mlcommons.org/croissant/1.0",
                                      "http://mlcommons.org/croissant/RAI/1.0"]
    assert "_unfinished" not in document


def test_what_is_outstanding_is_named_rather_than_counted(database, tmp_path):
    """"17 outstanding" tells somebody nothing about what to go and do."""
    with database.session() as session:
        a_parbaked_file(tmp_path / "out", columns=("JOB_NAME", "EXIT_CODE"))
        import_target(session, tmp_path / "out")
        stored = session.scalars(select(SourceFile)).one()
        machine, dataset = confirm_machine(session, stored, "polaris")
        record_field_answer(session, dataset, "JOB_NAME",
                            description="The PBS id.", data_type="sc:Text")

        reasons = reasons_unfinished(session, dataset)
        assert "EXIT_CODE needs a description and a type" in reasons
        assert "JOB_NAME needs a description and a type" not in reasons
        assert "citeAs is unanswered" in reasons
        assert not any(key in each for each in reasons
                       for key in ("license", "url", "creator", "keywords", "version", "datePublished"))


def test_a_seeded_answer_is_reported_as_unconfirmed_not_as_missing(database, tmp_path):
    with database.session() as session:
        a_parbaked_file(tmp_path / "out")
        import_target(session, tmp_path / "out")
        stored = session.scalars(select(SourceFile)).one()
        machine, dataset = confirm_machine(session, stored, "polaris")
        session.add(DatasetAnswer(dataset_id=dataset.id, key="citeAs",
                                  answer="Cite the ALCF.", is_confirmed=False))
        session.commit()

        reasons = reasons_unfinished(session, dataset)
        assert "citeAs is seeded but not confirmed" in reasons


# --- writing ---

def test_a_finished_dataset_is_written_with_markdown_and_text_beside_it(database, tmp_path):
    with database.session() as session:
        stored, machine, dataset = an_answered_file(session, tmp_path / "out")
        a_profiled_machine(session)
        report = bake_dataset(session, dataset, tmp_path / "baked")

        assert len(report.written) == 1 and len(report.readable) == 2
        assert report.finished == report.written
        assert report.written[0].name == "POLARIS_DJC.baked.json"
        assert [path.name for path in report.readable] == [
            "POLARIS_DJC.baked.md", "POLARIS_DJC.baked.txt"]
        assert stored.baked_path == str(report.written[0])

    document = json.loads(report.written[0].read_text())
    assert document["conformsTo"] == ["http://mlcommons.org/croissant/1.0",
                                      "http://mlcommons.org/croissant/RAI/1.0"]
    assert "_unfinished" not in document
    assert "_machine" in document
    text = report.readable[1].read_text()
    assert "POLARIS_DJC" in text and "|" not in text and "**" not in text


def test_without_pandoc_the_text_is_skipped_with_a_warning(database, tmp_path, monkeypatch):
    monkeypatch.setattr("baking.has_pandoc", lambda: False)
    with database.session() as session:
        _, _, dataset = an_answered_file(session, tmp_path / "out")
        report = bake_dataset(session, dataset, tmp_path / "baked")
    assert [path.name for path in report.readable] == ["POLARIS_DJC.baked.md"]
    assert report.skipped == [("POLARIS_DJC.baked.txt", "pandoc is not installed")]


def test_the_data_need_not_be_reachable_to_finish(database, tmp_path):
    with database.session() as session:
        _, _, dataset = an_answered_file(session, tmp_path / "out",
                                         csv_path=tmp_path / "elsewhere" / "gone.csv")
        report = bake_dataset(session, dataset, tmp_path / "baked")
    assert report.finished == report.written


def test_the_local_path_of_the_data_is_never_written(database, tmp_path):
    data = tmp_path / "ANL-ALCF-DJC-POLARIS_20220809_20221231.csv"
    data.write_text("a,b\n1,2\n")
    with database.session() as session:
        _, _, dataset = an_answered_file(session, tmp_path / "out", csv_path=data)
        report = bake_dataset(session, dataset, tmp_path / "baked")
    assert str(data) not in report.written[0].read_text()


def test_files_with_no_machine_are_not_baked(database, tmp_path):
    with database.session() as session:
        a_parbaked_file(tmp_path / "out")
        import_target(session, tmp_path / "out")
        report = bake_everything(session, tmp_path / "baked")
        assert report.written == []


def test_a_dataset_whose_parbaked_files_vanished_is_skipped(database, tmp_path):
    """The @context is read back off them, so without them there is nothing to write."""
    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out")
        Path(stored.parbaked_path).unlink()
        report = bake_dataset(session, dataset, tmp_path / "baked")
        assert report.written == []
        assert "@context cannot be read back" in report.skipped[0][1]


def test_each_unanswered_column_is_named_once_however_many_files(database, tmp_path):
    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out", columns=("JOB_NAME",))
        for number in (1, 2):
            a_parbaked_file(tmp_path / f"more{number}", columns=("JOB_NAME", "GPUS"),
                            name=f"ANL-ALCF-DJC-POLARIS_2023010{number}_20231231")
            import_target(session, tmp_path / f"more{number}")
        for each in session.scalars(select(SourceFile)):
            each.dataset_id = dataset.id
        session.commit()
        reasons = reasons_unfinished(session, dataset)
    assert reasons.count("GPUS needs a description and a type") == 1


def test_a_series_is_a_fileset_with_no_checksum_rather_than_a_fake_one(
        database, tmp_path):
    """djc_v4 writes "sha256": "unknown-not-yet-published" in the one verifiable field."""
    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out")
        report = bake_dataset(session, dataset, tmp_path / "baked")

    document = json.loads(report.written[0].read_text())
    distribution = document["distribution"][0]
    assert distribution["@type"] == "cr:FileSet"
    assert distribution["includes"] == "ANL-ALCF-DJC-POLARIS_*.csv"
    assert "sha256" not in distribution and "md5" not in distribution
    # And it is still finished: a FileSet needs no checksum to validate.
    assert document["conformsTo"][0].endswith("croissant/1.0")


def test_the_series_fields_point_at_the_fileset(database, tmp_path):
    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out")
        report = bake_dataset(session, dataset, tmp_path / "baked")

    document = json.loads(report.written[0].read_text())
    field = document["recordSet"][-1]["field"][0]
    assert field["source"]["fileSet"] == {"@id": "data_files"}
    assert "fileObject" not in field["source"]


def test_the_series_takes_the_union_of_its_files_columns(database, tmp_path):
    """A later export gaining a column does not unsay what the earlier ones hold."""
    with database.session() as session:
        stored, machine, dataset = an_answered_file(
            session, tmp_path / "out", columns=("JOB_NAME", "EXIT_CODE"))
        a_parbaked_file(tmp_path / "later",
                        name="ANL-ALCF-DJC-POLARIS_20230101_20231231",
                        columns=("JOB_NAME", "EXIT_CODE", "GPUS_REQUESTED"))
        import_target(session, tmp_path / "later")
        later = session.scalar(select(SourceFile).where(SourceFile.name.like("%2023%")))
        later.dataset_id = dataset.id
        session.commit()

        report = bake_dataset(session, dataset, tmp_path / "baked")

    names = [field["name"] for field in
             json.loads(report.written[0].read_text())["recordSet"][-1]["field"]]
    assert names == ["JOB_NAME", "EXIT_CODE", "GPUS_REQUESTED"]


def test_the_series_keeps_no_measurements_because_they_are_true_of_one_file(
        database, tmp_path):
    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out")
        report = bake_dataset(session, dataset, tmp_path / "baked")

    assert "_parbake_measurements" not in json.loads(report.written[0].read_text())


def test_a_dataset_with_no_files_is_skipped(database, tmp_path):
    with database.session() as session:
        machine, _ = a_profiled_machine(session)
        dataset = Dataset(machine_id=machine.id, name="POLARIS_NOTHING", kind="NOTHING")
        session.add(dataset)
        session.commit()
        report = bake_dataset(session, dataset, tmp_path / "baked")

        assert report.written == []
        assert "no files belong to it yet" in report.skipped[0][1]


def test_only_the_decoding_tables_a_dataset_actually_uses_are_written(
        database, tmp_path):
    """Otherwise task-history decodings land in the machine-status document."""
    with database.session() as session:
        stored, machine, dataset = an_answered_file(session, tmp_path / "out")
        used = Enumeration(machine_id=machine.id, name="exit_code_enum")
        used.values = [EnumerationValue(code="0", meaning="Success.", position=0)]
        unused = Enumeration(machine_id=machine.id, name="hardware_error_enum")
        unused.values = [EnumerationValue(code="E1", meaning="A fault.", position=0)]
        session.add_all([used, unused])
        session.commit()

        doc = session.scalar(select(FieldDoc).where(
            FieldDoc.dataset_id == dataset.id, FieldDoc.field_name == "EXIT_CODE"))
        doc.enumeration_id = used.id
        session.commit()

        report = bake_dataset(session, dataset, tmp_path / "baked")

    written = json.loads(report.written[0].read_text())
    ids = [record_set["@id"] for record_set in written["recordSet"]]
    assert "exit_code_enum" in ids
    assert "hardware_error_enum" not in ids


# --- the promise of the pair ----------------------------------------------

def _validated(path):
    """Return mlcroissant's errors for a file, or None if it refused the file."""
    import warnings
    warnings.filterwarnings("ignore")
    import mlcroissant
    try:
        dataset = mlcroissant.Dataset(jsonld=str(path))
        return list(dataset.metadata.issues.errors)
    except Exception as problem:
        return [str(problem)]


def test_answering_everything_produces_a_file_that_validates(database, tmp_path):
    """The promise of the pair, checked with the real validator rather than asserted."""
    pytest.importorskip("mlcroissant")

    data = tmp_path / "ANL-ALCF-DJC-POLARIS_20220809_20221231.csv"
    data.write_text("JOB_NAME,EXIT_CODE\n314424.polaris,0\n")

    with database.session() as session:
        stored, machine, dataset = an_answered_file(session, tmp_path / "out",
                                                    csv_path=data)
        a_profiled_machine(session)
        report = bake_dataset(session, dataset, tmp_path / "baked")

    assert report.finished == report.written
    assert _validated(report.written[0]) == []


def test_an_unfinished_file_still_does_not_validate(database, tmp_path):
    """The other half of the promise, and the more important half."""
    pytest.importorskip("mlcroissant")

    with database.session() as session:
        a_parbaked_file(tmp_path / "out")
        import_target(session, tmp_path / "out")
        stored = session.scalars(select(SourceFile)).one()
        confirm_machine(session, stored, "polaris")     # and nothing else answered
        report = bake_dataset(session, stored.dataset, tmp_path / "baked")

    assert report.finished == []
    problems = _validated(report.written[0])
    assert problems
    assert any("PARBAKED" in str(each) for each in problems)


def test_a_malformed_version_or_date_keeps_the_marker_on(database, tmp_path):
    """Otherwise the marker comes off a file that still fails to validate."""
    data = tmp_path / "ANL-ALCF-DJC-POLARIS_20220809_20221231.csv"
    data.write_text("a,b\n1,2\n")

    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out", csv_path=data)
        record_dataset_answer(session, dataset, "version", "v1.0-final")
        record_dataset_answer(session, dataset, "datePublished", "September 2026")

        report = bake_dataset(session, stored.dataset, tmp_path / "baked")
        reasons = report.unfinished[str(report.written[0])]

        assert any("not MAJOR.MINOR.PATCH" in each for each in reasons)
        assert any("not a YYYY-MM-DD date" in each for each in reasons)
        assert report.finished == []


def test_a_version_of_two_parts_is_accepted_because_djc_v4_uses_one(database, tmp_path):
    """The finished file in the corpus says 1.0, and the validator takes it."""
    from baking import malformed_answers
    assert malformed_answers({"version": "1.0"}) == []
    assert malformed_answers({"version": "1.0.0"}) == []
    assert malformed_answers({"version": ""}) == []          # unanswered is a separate reason
    assert malformed_answers({"datePublished": "2026-09-01"}) == []
    assert len(malformed_answers({"version": "one point oh"})) == 1


# --- notes -----------------------------------------------------------------

def test_the_note_is_appended_to_the_description_marked_so_it_can_be_found():
    from baking import with_note

    assert with_note("Core hours consumed", "Looks low for Q4.") == (
        "Core hours consumed. NOTE: Looks low for Q4.")
    # An already-ended sentence does not gain a second full stop.
    assert with_note("Core hours consumed.", "Looks low.") == (
        "Core hours consumed. NOTE: Looks low.")
    assert with_note("Nothing to add", None) == "Nothing to add"
    # A note with nothing described yet is still worth writing out.
    assert with_note(None, "Nobody knows what this is.") == (
        "NOTE: Nobody knows what this is.")


def test_a_note_already_in_the_description_is_not_appended_twice():
    from baking import with_note
    already = "Core hours consumed. NOTE: Looks low for Q4."
    assert with_note(already, "Looks low for Q4.") == already


def test_the_unit_comes_before_the_note(database):
    """The unit is part of what the column means; the note is an aside about it."""
    from baking import described

    class Recorded:
        """Stand in for a recorded FieldDoc."""
        description = "Core hours actually consumed"
        unit = "Core Hours"
        note = "Disagrees with the fact table by ~2%."

    assert described(Recorded()) == (
        "Core hours actually consumed. Unit: Core Hours. "
        "NOTE: Disagrees with the fact table by ~2%.")


def test_a_note_reaches_the_written_croissant_and_can_be_found_again(
        database, tmp_path):
    """It travels with the published file, and notes.py finds it."""
    from notes import find_notes

    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out",
                                              columns=("JOB_NAME", "EXIT_STATUS"))
        record_field_answer(
            session, dataset, "EXIT_STATUS", description="The exit status.",
            data_type="sc:Integer",
            note="Disagrees with EXIT_CODE on 122 of 39,432 rows.",
            source_file=stored)
        report = bake_dataset(session, stored.dataset, tmp_path / "baked")

    written = json.loads(report.written[0].read_text())
    fields = {each["name"]: each for each in written["recordSet"][-1]["field"]}
    assert fields["EXIT_STATUS"]["description"].endswith(
        "NOTE: Disagrees with EXIT_CODE on 122 of 39,432 rows.")
    assert "NOTE:" not in fields["JOB_NAME"]["description"]

    found, scanned, _ = find_notes(tmp_path / "baked")
    assert scanned >= 1
    assert [note.where for note in found] == ["records / EXIT_STATUS"]
    assert found[0].text == "Disagrees with EXIT_CODE on 122 of 39,432 rows."


# --- decoding tables ---

def a_linked_table(session, dataset, codes=(("0", "success"), ("1", "error"))):
    """Return a table on the dataset's machine, linked from EXIT_CODE."""
    from enumerations import TableRow, link_column, save_table
    table = save_table(session, dataset.machine, "exit_codes", "Exit codes.",
                       [TableRow(code, meaning) for code, meaning in codes])
    link_column(session, dataset, "EXIT_CODE", table)
    return table


def test_a_file_with_a_linked_table_validates(database, tmp_path):
    pytest.importorskip("mlcroissant")
    data = tmp_path / "ANL-ALCF-DJC-POLARIS_20220809_20221231.csv"
    data.write_text("JOB_NAME,EXIT_CODE\n314424.polaris,0\n")
    with database.session() as session:
        stored, machine, dataset = an_answered_file(session, tmp_path / "out", csv_path=data)
        a_profiled_machine(session)
        a_linked_table(session, dataset)
        report = bake_dataset(session, dataset, tmp_path / "baked")
    written = json.loads(report.written[0].read_text())
    exit_code = next(field for field in written["recordSet"][-1]["field"]
                     if field["name"] == "EXIT_CODE")
    assert exit_code["references"] == {"field": {"@id": "exit_codes/code"}}
    assert report.finished == report.written
    assert _validated(report.written[0]) == []


def test_a_code_the_table_does_not_decode_is_a_warning_not_a_reason(database, tmp_path):
    pytest.importorskip("mlcroissant")
    data = tmp_path / "ANL-ALCF-DJC-POLARIS_20220809_20221231.csv"
    data.write_text("JOB_NAME,EXIT_CODE\n314424.polaris,0\n")
    with database.session() as session:
        stored, machine, dataset = an_answered_file(session, tmp_path / "out", csv_path=data)
        a_profiled_machine(session)
        a_linked_table(session, dataset)                     # decodes 0 and 1
        exit_code = next(column for column in stored.fields if column.name == "EXIT_CODE")
        exit_code.measurements = {**exit_code.measurements, "distinct_count": 3,
                                  "most_common_values": [{"value": "0", "count": 5},
                                                         {"value": "1", "count": 3},
                                                         {"value": "137", "count": 1}]}
        session.commit()
        report = bake_dataset(session, stored.dataset, tmp_path / "baked")
    written = json.loads(report.written[0].read_text())
    description = next(field["description"] for field in written["recordSet"][-1]["field"]
                       if field["name"] == "EXIT_CODE")
    assert description.endswith("UNDECODED: 137 (no meaning in exit_codes).")
    assert any("EXIT_CODE has 137 with no meaning in exit_codes" in line
               for line in report.lines())
    assert report.finished == report.written
    assert _validated(report.written[0]) == []


def test_a_seeded_tables_own_field_descriptions_are_baked(database, tmp_path):
    from seeding import seed_from
    with database.session() as session:
        stored, machine, dataset = an_answered_file(session, tmp_path / "out")
        source = json.loads(Path("../croissant_files/djc_v4.croissant.json").read_text())
        source["name"] = dataset.name
        path = tmp_path / "seed.croissant.json"
        path.write_text(json.dumps(source))
        seed_from(session, path)
        table = session.scalars(select(Enumeration)).one()
        from enumerations import link_column
        link_column(session, dataset, "EXIT_CODE", table)
        report = bake_dataset(session, stored.dataset, tmp_path / "baked")
    written = json.loads(report.written[0].read_text())
    fields = next(each for each in written["recordSet"] if each["@id"] == "exit_code_enum")["field"]
    assert fields[0]["description"] == "Exit code value as it appears in EXIT_STATUS/EXIT_CODE."
    assert fields[1]["description"].startswith("Interpretation per PBS_exit_code_reference.md")


def test_the_required_answers_alone_finish_a_file_with_version_and_date_defaulted(
        database, tmp_path):
    pytest.importorskip("mlcroissant")
    from questions import REQUIRED_KEYS
    data = tmp_path / "ANL-ALCF-DJC-POLARIS_20220809_20221231.csv"
    data.write_text("JOB_NAME,EXIT_CODE\n314424.polaris,0\n")
    with database.session() as session:
        stored, _, dataset = an_answered_file(session, tmp_path / "out", csv_path=data)
        for row in list(dataset.answers):
            if row.key not in REQUIRED_KEYS:
                session.delete(row)
        session.commit()
        report = bake_dataset(session, stored.dataset, tmp_path / "baked")
    written = json.loads(report.written[0].read_text())
    assert report.finished == report.written
    assert written["version"] == "1.0"
    assert written["datePublished"] == date.today().isoformat()
    assert "license" not in written and "url" not in written
    assert _validated(report.written[0]) == []
