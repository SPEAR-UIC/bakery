"""bakery/tests/test_bakery.py - tests for the Textual interface (bakery.py, screens/)

Runs headless with run_test(); bodies run under asyncio.run, so no pytest plugin.
Run: pytest tests/test_bakery.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import asyncio

import pytest
from sqlalchemy import select

from database import Database
from models import Dataset, FieldDoc, FileField, Machine, SourceFile
from outstanding import confirm_machine, record_field_answer
from bakery import BakeryApp
from screens import (
    FieldScreen,
    FileListScreen,
    MachineScreen,
    MachinesScreen,
    NotesScreen,
    ReviewScreen,
)


def shown(widget):
    """Return what a Static is displaying."""
    return str(widget.render())


@pytest.fixture
def database(tmp_path):
    """Yield an open database in a temporary directory."""
    with Database(directory=tmp_path) as opened:
        yield opened


def a_file(session, name="ANL-ALCF-DJC-POLARIS_20220809_20221231", kind="DJC",
           columns=("JOB_NAME", "EXIT_CODE"), suggestion="polaris", rows=39432):
    """Return an imported file with measured columns."""
    stored = SourceFile(name=name, kind=kind, machine_suggestion=suggestion, rows=rows,
                        parbaked_path=f"/out/{name}.parbaked.json",
                        parbaked_sha256="0" * 64, column_count=len(columns))
    session.add(stored)
    session.flush()
    for position, column in enumerate(columns):
        session.add(FileField(file_id=stored.id, name=column, position=position,
                              measurements={"rows_seen": rows, "distinct_count": 42,
                                            "most_common_values": [
                                                {"value": "0", "count": rows}]}))
    session.commit()
    return stored


def drive(database, body, start_on_files=True):
    """Run one scripted session against a headless app, on the file list by default."""
    async def run():
        """Start the app headless, open the file list, and run body against it."""
        app = BakeryApp(database)
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            if start_on_files:
                await pilot.press("v")
                await pilot.pause()
            await body(app, pilot)
    asyncio.run(run())


# --- the queue -------------------------------------------------------------

def test_an_empty_database_says_what_to_do_rather_than_showing_a_blank_table(database):
    async def body(app, pilot):
        """Check the empty queue and its hint."""
        assert isinstance(app.screen, FileListScreen)
        assert app.screen.query_one("#files").row_count == 0
        assert "--import" in shown(app.screen.query_one("#hint"))
    drive(database, body)


def test_the_queue_lists_files_and_marks_an_unconfirmed_machine_as_a_guess(database):
    with database.session() as session:
        a_file(session)

    async def body(app, pilot):
        """Check the one row and its guess marker."""
        table = app.screen.query_one("#files")
        assert table.row_count == 1
        row = table.get_row_at(0)
        assert row[1] == "ANL-ALCF-DJC-POLARIS_20220809_20221231"
        assert row[2] == "DJC"
        assert row[3] == "? polaris"        # a suggestion nobody has confirmed
        assert row[6] == "imported"
        assert "suggestion nobody has confirmed" in shown(app.screen.query_one("#hint"))
    drive(database, body)


# --- the machine -----------------------------------------------------------

def test_the_machine_screen_shows_the_evidence_not_just_the_suggestion(database):
    """A filename saying POLARIS over data saying thetagpu is what someone must see."""
    with database.session() as session:
        from datetime import date
        stored = a_file(session)
        stored.covers_from, stored.covers_to = date(2022, 8, 9), date(2022, 12, 31)
        session.add(Machine("aurora"))
        session.commit()

    async def body(app, pilot):
        """Open the machine screen and check its evidence."""
        app.screen.action_work()
        await pilot.pause()

        assert isinstance(app.screen, MachineScreen)
        evidence = shown(app.screen.query_one("#evidence"))
        assert "2 columns" in evidence and "39,432 rows" in evidence
        assert "polaris" in evidence
        assert "2022-08-09" in evidence
        # The machines already named are offered, and the guess is pre-typed.
        assert [each.name for each in app.screen.known] == ["aurora"]
        assert app.screen.query_one("#typed").value == "polaris"
    drive(database, body)


def test_confirming_the_machine_goes_straight_on_to_the_columns(database):
    """Somebody who just said "this is Polaris" was asking to get on with it."""
    with database.session() as session:
        a_file(session)

    async def body(app, pilot):
        """Confirm the machine and check the columns screen opens."""
        app.screen.action_work()
        await pilot.pause()
        app.screen.action_confirm()
        await pilot.pause()
        await pilot.pause()

        assert isinstance(app.screen, FieldScreen)
        assert app.screen.current.name == "JOB_NAME"
    drive(database, body)

    with database.session() as session:
        stored = session.scalars(select(SourceFile)).one()
        assert stored.machine.name == "polaris"
        assert stored.dataset.name == "POLARIS_DJC"


def test_a_machine_screen_with_no_name_typed_does_nothing(database):
    with database.session() as session:
        a_file(session, suggestion=None)

    async def body(app, pilot):
        """Confirm with no name typed."""
        app.screen.action_work()
        await pilot.pause()
        assert app.screen.query_one("#typed").value == ""
        app.screen.action_confirm()
        await pilot.pause()
        assert isinstance(app.screen, MachineScreen)      # still asking
    drive(database, body)

    with database.session() as session:
        assert session.scalars(select(Machine)).all() == []


# --- the columns -----------------------------------------------------------

def test_a_column_is_shown_with_what_parbake_measured(database):
    with database.session() as session:
        stored = a_file(session)
        confirm_machine(session, stored, "polaris")

    async def body(app, pilot):
        """Open the first column and check its evidence."""
        app.screen.action_work()
        await pilot.pause()

        assert isinstance(app.screen, FieldScreen)
        assert "JOB_NAME" in shown(app.screen.query_one("#heading"))
        assert "1/2 outstanding" in shown(app.screen.query_one("#heading"))
        evidence = shown(app.screen.query_one("#evidence"))
        assert "39,432 rows" in evidence and "42 distinct values" in evidence
        assert "nothing on offer" in shown(app.screen.query_one("#offer"))
    drive(database, body)


def test_an_answer_is_written_when_it_is_given_and_the_next_column_comes_up(database):
    """Nobody answers 266 questions in one sitting, so nothing waits for the end."""
    with database.session() as session:
        stored = a_file(session)
        confirm_machine(session, stored, "polaris")

    async def body(app, pilot):
        """Answer the first column and check the next one comes up."""
        app.screen.action_work()
        await pilot.pause()
        field = app.screen
        field.query_one("#description").value = "The PBS job identifier."
        field.query_one("#data_type").value = "sc:Text"
        field.action_save()
        await pilot.pause()

        assert field.current.name == "EXIT_CODE"
        assert "1/1 outstanding" in shown(field.query_one("#heading"))
        assert field.query_one("#description").value == ""      # cleared for the next
    drive(database, body)

    with database.session() as session:
        recorded = session.scalars(select(FieldDoc)).one()
        assert recorded.field_name == "JOB_NAME"
        assert recorded.description == "The PBS job identifier."
        assert recorded.is_confirmed is True


def test_an_offer_arrives_filled_in_and_accepting_it_is_one_keystroke(database):
    """The difference between 66 questions and 66 confirmations."""
    with database.session() as session:
        source = a_file(session, name="ANL-ALCF-DJC-MIRA_20130409_20131231",
                        columns=("COBALT_JOBID",), suggestion="mira")
        _, dataset = confirm_machine(session, source, "mira")
        record_field_answer(session, dataset, "COBALT_JOBID",
                            description="The Cobalt job id.", data_type="sc:Integer")
        asking = a_file(session, name="ANL-ALCF-TH-MIRA_20130409_20131231", kind="TH",
                        columns=("COBALT_JOBID",), suggestion="mira")
        confirm_machine(session, asking, "mira")

    async def body(app, pilot):
        """Open the Mira file and accept the offer."""
        table = app.screen.query_one("#files")
        row = next(index for index in range(table.row_count)
                   if "TH-MIRA" in str(table.get_row_at(index)[1]))
        table.move_cursor(row=row)
        await pilot.pause()
        app.screen.action_work()
        await pilot.pause()

        field = app.screen
        assert "MIRA_DJC on this machine" in shown(field.query_one("#offer"))
        assert field.query_one("#description").value == "The Cobalt job id."
        assert field.query_one("#data_type").value == "sc:Integer"

        field.action_accept()
        await pilot.pause()
    drive(database, body)

    with database.session() as session:
        dataset = session.scalar(select(Dataset).where(Dataset.name == "MIRA_TH"))
        recorded = session.scalar(select(FieldDoc).where(
            FieldDoc.dataset_id == dataset.id))
        assert recorded.description == "The Cobalt job id."
        assert recorded.is_confirmed is True        # accepted here, on purpose


def test_typing_over_an_offer_records_what_was_typed(database):
    """START_TIMESTAMP is a job starting in one dataset and an outage in another."""
    with database.session() as session:
        jobs = a_file(session, columns=("START_TIMESTAMP",))
        _, dataset = confirm_machine(session, jobs, "polaris")
        record_field_answer(session, dataset, "START_TIMESTAMP",
                            description="When the job began execution.",
                            data_type="sc:DateTime")
        a_file(session, name="ANL-ALCF-MACHINESTATUS-POLARIS_20220809_20221231",
               kind="MACHINESTATUS", columns=("START_TIMESTAMP",))

    async def body(app, pilot):
        """Open the machine-status file and type over the offer."""
        table = app.screen.query_one("#files")
        row = next(index for index in range(table.row_count)
                   if "MACHINESTATUS" in str(table.get_row_at(index)[1]))
        table.move_cursor(row=row)
        await pilot.pause()
        app.screen.action_work()
        await pilot.pause()
        app.screen.action_confirm()
        await pilot.pause()
        await pilot.pause()

        field = app.screen
        assert field.query_one("#description").value == "When the job began execution."
        field.query_one("#description").value = "When the outage began."
        field.action_save()
        await pilot.pause()
    drive(database, body)

    with database.session() as session:
        wording = {session.get(Dataset, each.dataset_id).name: each.description
                   for each in session.scalars(select(FieldDoc))}
        assert wording["POLARIS_DJC"] == "When the job began execution."
        assert wording["POLARIS_MACHINESTATUS"] == "When the outage began."


def test_skipping_moves_on_without_recording_anything(database):
    with database.session() as session:
        stored = a_file(session)
        confirm_machine(session, stored, "polaris")

    async def body(app, pilot):
        """Skip through the columns and wrap around."""
        app.screen.action_work()
        await pilot.pause()
        field = app.screen
        assert field.current.name == "JOB_NAME"
        field.action_skip()
        await pilot.pause()
        assert field.current.name == "EXIT_CODE"
        # And round again, rather than falling off the end.
        field.action_skip()
        await pilot.pause()
        assert field.current.name == "JOB_NAME"
    drive(database, body)

    with database.session() as session:
        assert session.scalars(select(FieldDoc)).all() == []


def test_answering_the_last_column_returns_to_the_queue(database):
    with database.session() as session:
        stored = a_file(session, columns=("JOB_NAME",))
        confirm_machine(session, stored, "polaris")

    async def body(app, pilot):
        """Answer the last column."""
        app.screen.action_work()
        await pilot.pause()
        field = app.screen
        field.query_one("#description").value = "The PBS job identifier."
        field.query_one("#data_type").value = "sc:Text"
        field.action_save()
        await pilot.pause()
        await pilot.pause()

        assert isinstance(app.screen, FileListScreen)
        assert app.screen.query_one("#files").get_row_at(0)[6] == "complete"
    drive(database, body)


def test_the_cursor_holds_its_file_while_the_queue_reorders(database):
    """Finishing one file moves it down the list. The next keystroke must not open another."""
    with database.session() as session:
        a_file(session, name="AAA_20220101_20221231", kind="AAA", columns=("A", "B", "C"))
        working = a_file(session, name="ZZZ_20220101_20221231", kind="ZZZ", columns=("D",))
        confirm_machine(session, working, "polaris")

    async def body(app, pilot):
        """Answer the ZZZ file's column and check the cursor stays on it."""
        table = app.screen.query_one("#files")
        row = next(index for index in range(table.row_count)
                   if str(table.get_row_at(index)[1]).startswith("ZZZ"))
        table.move_cursor(row=row)
        await pilot.pause()
        assert app.screen.selected_file().name.startswith("ZZZ")

        app.screen.action_work()
        await pilot.pause()
        field = app.screen
        field.query_one("#description").value = "something"
        field.query_one("#data_type").value = "sc:Text"
        field.action_save()
        await pilot.pause()
        await pilot.pause()

        # ZZZ is now finished and has sunk below AAA, and the cursor went with it.
        assert isinstance(app.screen, FileListScreen)
        assert app.screen.selected_file().name.startswith("ZZZ")
    drive(database, body)


# --- the machines ----------------------------------------------------------

def test_the_machines_screen_says_when_no_hardware_is_recorded(database):
    with database.session() as session:
        stored = a_file(session)
        confirm_machine(session, stored, "polaris")

    async def body(app, pilot):
        """Open the machines screen."""
        app.screen.action_machines()
        await pilot.pause()

        assert isinstance(app.screen, MachinesScreen)
        text = shown(app.screen.query_one("#machines"))
        assert "polaris" in text
        assert "no hardware recorded" in text
        assert "dataset POLARIS_DJC" in text
    drive(database, body)


def test_the_machines_screen_shows_each_iteration_and_its_dates(database):
    from datetime import date
    from models import MachineIteration, MachinePartition
    with database.session() as session:
        machine = Machine("polaris", organization="Argonne National Laboratory")
        session.add(machine)
        session.commit()
        first = MachineIteration(machine_id=machine.id, label="as deployed",
                                 starts_on=date(2022, 8, 1), vendor="HPE",
                                 architecture="Apollo Gen10+", scheduler="PBS Professional")
        first.partitions = [MachinePartition(name="default", node_count=595,
                                             gpu="4x NVIDIA A100 per node")]
        session.add(first)
        session.commit()
        second = first.next_iteration(date(2024, 6, 1), "after the 2024 refresh")
        second.partitions[0].gpu = "4x NVIDIA H100 per node"
        session.add(second)
        session.commit()

    async def body(app, pilot):
        """Open the machines screen."""
        app.screen.action_machines()
        await pilot.pause()
        text = shown(app.screen.query_one("#machines"))

        assert "Argonne National Laboratory" in text
        assert "as deployed: 2022-08-01 to 2024-05-31" in text
        assert "after the 2024 refresh: 2024-06-01 to present" in text
        assert "A100" in text and "H100" in text
        assert "595 nodes" in text
    drive(database, body)


# --- writing it out --------------------------------------------------------

def test_writing_out_a_file_with_no_machine_asks_for_one_instead(database):
    with database.session() as session:
        a_file(session)

    async def body(app, pilot):
        """Try to write out the file."""
        app.screen.action_bake()
        await pilot.pause()
        assert isinstance(app.screen, FileListScreen)       # nothing pushed, nothing written
    drive(database, body)


def test_writing_out_an_unfinished_file_says_what_is_left(database, tmp_path):
    """Half-done is the normal state, so it is written and marked, not refused."""
    import json
    with database.session() as session:
        stored = a_file(session)
        # baking reads the @context back off the par-baked file, so it has to exist.
        parbaked = tmp_path / f"{stored.name}.parbaked.json"
        parbaked.write_text(json.dumps({
            "@context": {"@language": "en", "sc": "https://schema.org/",
                         "cr": "http://mlcommons.org/croissant/"},
            "name": stored.name,
            "conformsTo": "http://mlcommons.org/croissant/PARBAKED-DO-NOT-SUBMIT"}))
        stored.parbaked_path = str(parbaked)
        session.commit()
        confirm_machine(session, stored, "polaris")

    written = tmp_path / "written"

    async def body(app, pilot):
        """Write out the file."""
        app.output_directory = written
        app.screen.action_bake()
        await pilot.pause()
    drive(database, body)

    document = json.loads((written / "POLARIS_DJC.baked.json").read_text())
    assert "PARBAKED" in document["conformsTo"]
    assert document["_unfinished"]["outstanding"]
    assert sorted(path.name for path in written.iterdir()) == [
        "POLARIS_DJC.baked.json", "POLARIS_DJC.baked.md", "POLARIS_DJC.baked.txt"]


# --- notes, and looking back -----------------------------------------------

def test_a_note_typed_on_a_column_is_recorded_with_it(database):
    with database.session() as session:
        stored = a_file(session, columns=("EXIT_STATUS",))
        confirm_machine(session, stored, "polaris")

    async def body(app, pilot):
        """Answer a column with a note."""
        app.screen.action_work()
        await pilot.pause()
        field = app.screen
        field.query_one("#description").value = "The exit status."
        field.query_one("#data_type").value = "sc:Integer"
        field.query_one("#note").value = "Disagrees with EXIT_CODE on 122 rows."
        field.action_save()
        await pilot.pause()
    drive(database, body)

    with database.session() as session:
        recorded = session.scalars(select(FieldDoc)).one()
        assert recorded.note == "Disagrees with EXIT_CODE on 122 rows."
        assert recorded.description == "The exit status."     # kept apart from it


def test_the_columns_screen_goes_backwards_as_well_as_forwards(database):
    """Which it did not: action_skip wrapped forwards and nothing went back."""
    with database.session() as session:
        stored = a_file(session, columns=("A", "B", "C"))
        confirm_machine(session, stored, "polaris")

    async def body(app, pilot):
        """Step forwards and backwards through the columns."""
        app.screen.action_work()
        await pilot.pause()
        field = app.screen
        assert field.current.name == "A"

        field.action_skip()
        await pilot.pause()
        assert field.current.name == "B"

        field.action_previous()
        await pilot.pause()
        assert field.current.name == "A"

        # And back from the first wraps to the last rather than sticking.
        field.action_previous()
        await pilot.pause()
        assert field.current.name == "C"
    drive(database, body)


def test_the_review_screen_lists_every_column_answered_or_not(database):
    with database.session() as session:
        stored = a_file(session, columns=("JOB_NAME", "EXIT_CODE"))
        _, dataset = confirm_machine(session, stored, "polaris")
        record_field_answer(session, dataset, "JOB_NAME", description="The PBS id.",
                            data_type="sc:Text", unit=None,
                            note="Strip .polaris to join.", source_file=stored)

    async def body(app, pilot):
        """Open the review screen and check its rows."""
        app.screen.action_review()
        await pilot.pause()

        assert isinstance(app.screen, ReviewScreen)
        table = app.screen.query_one("#columns")
        rows = {table.get_row_at(index)[0]: table.get_row_at(index)
                for index in range(table.row_count)}

        # An answered column stays listed.
        assert rows["JOB_NAME"][1] == "set"
        assert rows["JOB_NAME"][2] == "Text"
        assert rows["JOB_NAME"][4] == "yes"          # it has a note
        assert rows["EXIT_CODE"][1] == "-"
        assert "1 with a note" in shown(app.screen.query_one("#title"))
    drive(database, body)


def test_picking_a_confirmed_column_from_the_review_screen_reopens_it(database):
    """Previously unreachable: an answered column left the queue for good."""
    with database.session() as session:
        stored = a_file(session, columns=("JOB_NAME", "EXIT_CODE"))
        _, dataset = confirm_machine(session, stored, "polaris")
        record_field_answer(session, dataset, "JOB_NAME", description="The PBS id.",
                            data_type="sc:Text", source_file=stored)

    async def body(app, pilot):
        """Reopen a confirmed column from the review screen and edit it."""
        app.screen.action_review()
        await pilot.pause()
        table = app.screen.query_one("#columns")
        row = next(index for index in range(table.row_count)
                   if table.get_row_at(index)[0] == "JOB_NAME")
        table.move_cursor(row=row)
        await pilot.pause()
        app.screen.action_edit()
        await pilot.pause()
        await pilot.pause()

        field = app.screen
        assert isinstance(field, FieldScreen)
        assert field.current.name == "JOB_NAME"           # opened at the one picked
        assert field.include_settled is True
        assert "CONFIRMED, editing" in shown(field.query_one("#heading"))
        assert field.query_one("#description").value == "The PBS id."

        field.query_one("#description").value = "The PBS job identifier; strip .polaris."
        field.action_save()
        await pilot.pause()
    drive(database, body)

    with database.session() as session:
        recorded = session.scalar(select(FieldDoc).where(FieldDoc.field_name == "JOB_NAME"))
        assert recorded.description == "The PBS job identifier; strip .polaris."


def test_reviewing_a_file_with_no_machine_asks_for_one_first(database):
    with database.session() as session:
        a_file(session)

    async def body(app, pilot):
        """Try to open the review screen."""
        app.screen.action_review()
        await pilot.pause()
        assert isinstance(app.screen, FileListScreen)     # nothing opened
    drive(database, body)


def test_the_notes_screen_lists_what_has_been_flagged(database):
    with database.session() as session:
        stored = a_file(session, columns=("EXIT_STATUS", "JOB_NAME"))
        _, dataset = confirm_machine(session, stored, "polaris")
        record_field_answer(session, dataset, "EXIT_STATUS", description="The status.",
                            data_type="sc:Integer",
                            note="Disagrees with EXIT_CODE on 122 rows.",
                            source_file=stored)

    async def body(app, pilot):
        """Open the notes screen."""
        app.screen.action_notes()
        await pilot.pause()

        assert isinstance(app.screen, NotesScreen)
        text = shown(app.screen.query_one("#notes"))
        assert "1 note(s) recorded" in text
        assert "POLARIS_DJC" in text
        assert "records / EXIT_STATUS" in text
        assert "Disagrees with EXIT_CODE on 122 rows." in text
        assert "JOB_NAME" not in text        # no note on it
    drive(database, body)


def test_the_notes_screen_says_how_to_make_one_when_there_are_none(database):
    with database.session() as session:
        a_file(session)

    async def body(app, pilot):
        """Open the notes screen."""
        app.screen.action_notes()
        await pilot.pause()
        text = shown(app.screen.query_one("#notes"))
        assert "No notes recorded yet" in text
        assert "--notes" in text            # and where the other listing lives
    drive(database, body)


# --- changing the machine, and copying answers in ---------------------------

def test_the_machine_can_be_changed_from_the_review_screen(database):
    from screens import ReportScreen
    with database.session() as session:
        stored = a_file(session, name="METIS.logs_20260814_1", kind="logs", columns=("pod",))
        _, wrong = confirm_machine(session, stored, "ThetaGPU")
        record_field_answer(session, wrong, "pod", description="The pod.", data_type="sc:Text",
                            source_file=stored)

    async def body(app, pilot):
        """Change the machine from the review screen."""
        app.screen.action_review()
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        machine = app.screen
        assert isinstance(machine, MachineScreen) and machine.changing
        assert machine.query_one("#typed").value == "ThetaGPU"     # starts at the current one
        assert "Now confirmed as: ThetaGPU" in shown(machine.query_one("#evidence"))

        machine.query_one("#typed").value = "Metis"
        machine.action_confirm()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ReportScreen)
        report = shown(app.screen.query_one("#report"))
        assert "THETAGPU_logs -> METIS_logs" in report
        assert "column answers moved         1" in report
    drive(database, body)

    with database.session() as session:
        stored = session.scalars(select(SourceFile)).one()
        assert stored.dataset.name == "METIS_logs"
        assert session.scalars(select(FieldDoc)).one().description == "The pod."


def test_shift_m_on_the_file_list_changes_the_machine(database):
    with database.session() as session:
        stored = a_file(session)
        confirm_machine(session, stored, "aurora")

    async def body(app, pilot):
        """Open and close the machine change screen."""
        await pilot.press("M")
        await pilot.pause()
        assert isinstance(app.screen, MachineScreen) and app.screen.changing
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, FileListScreen)
    drive(database, body)

    with database.session() as session:
        assert session.scalars(select(SourceFile)).one().dataset.name == "AURORA_DJC"


def _two_datasets_to_copy_between(session):
    """Answer every column on Polaris and one on Aurora; return the Aurora file."""
    columns = ("MACHINE_NAME", "START_TIMESTAMP", "NOTE")
    source_file = a_file(session, name="ANL-ALCF-MACHINESTATUS-POLARIS_20220809_20221231",
                         kind="MACHINESTATUS", columns=columns)
    _, source = confirm_machine(session, source_file, "polaris")
    for name in columns:
        record_field_answer(session, source, name, description=f"{name} from Polaris.",
                            data_type="sc:Text")
    target_file = a_file(session, name="ANL-ALCF-MACHINESTATUS-AURORA_20250127_20251231",
                         kind="MACHINESTATUS", columns=columns)
    _, target = confirm_machine(session, target_file, "aurora")
    record_field_answer(session, target, "NOTE", description="Aurora's own note column.",
                        data_type="sc:Text")
    return target_file


def _open_copy_for_aurora(app):
    """Move the file list cursor to the Aurora file."""
    table = app.screen.query_one("#files")
    row = next(index for index in range(table.row_count)
               if "AURORA" in str(table.get_row_at(index)[1]))
    table.move_cursor(row=row)


def test_copying_answers_in_fills_gaps_and_asks_about_differences(database):
    from screens import CopyPlanScreen, CopySourceScreen, DifferenceScreen, ReportScreen
    with database.session() as session:
        _two_datasets_to_copy_between(session)

    async def body(app, pilot):
        """Copy Polaris answers into Aurora, keeping Aurora's own note."""
        _open_copy_for_aurora(app)
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CopySourceScreen)
        first = app.screen.query_one("#sources").get_row_at(0)
        assert first[0] == "POLARIS_MACHINESTATUS" and first[2] == "3"

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, CopyPlanScreen)
        plan = shown(app.screen.query_one("#plan"))
        assert "will be filled              2" in plan
        assert "differ, you choose          1" in plan

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, DifferenceScreen)
        difference = shown(app.screen.query_one("#difference"))
        assert "column NOTE" in difference
        assert "Aurora's own note column." in difference and "NOTE from Polaris." in difference

        await pilot.press("k")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ReportScreen)
        assert "columns filled                2" in shown(app.screen.query_one("#report"))
    drive(database, body)

    with database.session() as session:
        aurora = session.scalar(select(Dataset).where(Dataset.name == "AURORA_MACHINESTATUS"))
        by_name = {doc.field_name: doc for doc in aurora.field_docs}
        assert by_name["MACHINE_NAME"].description == "MACHINE_NAME from Polaris."
        assert by_name["MACHINE_NAME"].recorded_by == "copied from POLARIS_MACHINESTATUS"
        assert by_name["NOTE"].description == "Aurora's own note column."     # kept


def test_abandoning_a_copy_part_way_writes_nothing(database):
    from screens import DifferenceScreen
    with database.session() as session:
        _two_datasets_to_copy_between(session)

    async def body(app, pilot):
        """Start a copy and abandon it at the first difference."""
        _open_copy_for_aurora(app)
        await pilot.pause()
        for key in ("c", "enter", "ctrl+s"):
            await pilot.press(key)
            await pilot.pause()
        assert isinstance(app.screen, DifferenceScreen)
        await pilot.press("escape")
        await pilot.pause()
    drive(database, body)

    with database.session() as session:
        aurora = session.scalar(select(Dataset).where(Dataset.name == "AURORA_MACHINESTATUS"))
        assert [doc.field_name for doc in aurora.field_docs] == ["NOTE"]


# --- grouping files as one format ------------------------------------------

def test_ticked_files_are_grouped_as_one_format(database):
    """Tick one, tick a range up to another, group: one dataset for all of them."""
    with database.session() as session:
        a_file(session)                                   # listed first, left alone
        for number in (1, 2, 3):
            a_file(session, name=f"POLARIS.log_syslog_20251016_{number}",
                   kind=f"POLARIS.log_syslog_20251016_{number}", columns=("facility",))

    async def body(app, pilot):
        """Tick three files and group them."""
        await pilot.press("down", "space", "down", "T")
        screen = app.screen
        assert len(screen.ticked) == 3
        await pilot.press("g")
        await pilot.pause()
        assert "Group 3 file(s)" in shown(app.screen.query_one("#which"))
        assert app.screen.query_one("#machine").value == "polaris"
        app.screen.query_one("#name").value = "POLARIS_SYSLOG"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "3 file(s) joined POLARIS_SYSLOG" in shown(app.screen.query_one("#report"))
        await pilot.press("escape")
        await pilot.pause()
        assert screen.ticked == set()

    drive(database, body)
    with database.session() as session:
        grouped = {each.name: (each.dataset.name if each.dataset else None)
                   for each in session.scalars(select(SourceFile))}
        assert grouped.pop("ANL-ALCF-DJC-POLARIS_20220809_20221231") is None
        assert set(grouped.values()) == {"POLARIS_SYSLOG"}


# --- decoding tables ---

def a_polaris_file_with_a_table(session, other_machine_table=False):
    """Return a Polaris file with a syslog_facility table on Polaris, or on Aurora."""
    from enumerations import TableRow, save_table
    from outstanding import dataset_for
    stored = a_file(session)
    confirm_machine(session, stored, "polaris")
    machine = (dataset_for(session, stored, "aurora")[0] if other_machine_table
               else stored.dataset.machine)
    save_table(session, machine, "syslog_facility", "",
               [TableRow("0", "kern"), TableRow("3", "daemon")])
    return stored


async def pick(app, pilot, position):
    """Open the picker on the current column and choose the option at position."""
    await pilot.press("ctrl+e")
    await pilot.pause()
    app.screen.query_one("#tables").highlighted = position
    await pilot.press("enter")
    await pilot.pause()


def linked_table(database, column="EXIT_CODE"):
    """Return the name of the table a column links to, or None."""
    from queries import field_doc
    with database.session() as session:
        stored = session.scalars(select(SourceFile)).first()
        doc = field_doc(session, stored.dataset, column)
        return doc.enumeration.name if doc and doc.enumeration else None


def test_a_column_is_linked_to_a_table_from_the_picker(database):
    with database.session() as session:
        stored_id = a_polaris_file_with_a_table(session).id

    async def body(app, pilot):
        """Pick the machine's table for EXIT_CODE and check the column shows it."""
        stored = app.session.get(SourceFile, stored_id)
        app.push_screen(FieldScreen(stored, start_at="EXIT_CODE"))
        await pilot.pause()
        assert "decoding table: none" in shown(app.screen.query_one("#table"))
        await pick(app, pilot, 1)
        assert isinstance(app.screen, FieldScreen)
        assert "syslog_facility" in shown(app.screen.query_one("#table"))
    drive(database, body)
    assert linked_table(database) == "syslog_facility"


def test_choosing_no_table_unlinks_the_column(database):
    with database.session() as session:
        stored_id = a_polaris_file_with_a_table(session).id

    async def body(app, pilot):
        """Link, then pick the first option to unlink."""
        stored = app.session.get(SourceFile, stored_id)
        app.push_screen(FieldScreen(stored, start_at="EXIT_CODE"))
        await pilot.pause()
        await pick(app, pilot, 1)
        assert "syslog_facility" in shown(app.screen.query_one("#table"))
        await pick(app, pilot, 0)
        assert "decoding table: none" in shown(app.screen.query_one("#table"))
    drive(database, body)
    assert linked_table(database) is None


def test_another_machines_table_is_listed_only_after_o_and_copied_when_chosen(database):
    with database.session() as session:
        stored_id = a_polaris_file_with_a_table(session, other_machine_table=True).id

    async def body(app, pilot):
        """Check the Aurora table is hidden, shown with o, and copied when chosen."""
        stored = app.session.get(SourceFile, stored_id)
        app.push_screen(FieldScreen(stored, start_at="EXIT_CODE"))
        await pilot.pause()
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert app.screen.query_one("#tables").option_count == 2      # no table, new
        await pilot.press("o")
        await pilot.pause()
        options = app.screen.query_one("#tables")
        assert options.option_count == 3
        assert "copied to polaris" in str(options.get_option_at_index(1).prompt)
        options.highlighted = 1
        await pilot.press("enter")
        await pilot.pause()
    drive(database, body)
    assert linked_table(database) == "syslog_facility"
    with database.session() as session:
        from models import Enumeration
        owners = sorted(table.machine.name for table in session.scalars(select(Enumeration)))
        assert owners == ["aurora", "polaris"]


def test_accepting_an_offer_brings_its_table_from_the_same_machine(database):
    with database.session() as session:
        first = a_polaris_file_with_a_table(session)
        from enumerations import link_column
        from models import Enumeration
        record_field_answer(session, first.dataset, "EXIT_CODE",
                            description="Exit code.", data_type="sc:Integer")
        link_column(session, first.dataset, "EXIT_CODE",
                    session.scalars(select(Enumeration)).one())
        sibling = a_file(session, name="ANL-ALCF-OTHER-POLARIS_20230101_20231231", kind="OTHER")
        confirm_machine(session, sibling, "polaris")
        sibling_id = sibling.id

    async def body(app, pilot):
        """Accept the sibling offer on EXIT_CODE."""
        stored = app.session.get(SourceFile, sibling_id)
        app.push_screen(FieldScreen(stored, start_at="EXIT_CODE"))
        await pilot.pause()
        assert "offered: syslog_facility" in shown(app.screen.query_one("#table"))
        await pilot.press("ctrl+a")
        await pilot.pause()
    drive(database, body)
    with database.session() as session:
        from queries import field_doc
        sibling = session.get(SourceFile, sibling_id)
        assert field_doc(session, sibling.dataset, "EXIT_CODE").enumeration.name == "syslog_facility"


def test_the_review_shows_each_columns_table(database):
    with database.session() as session:
        stored = a_polaris_file_with_a_table(session)
        from enumerations import link_column
        from models import Enumeration
        link_column(session, stored.dataset, "EXIT_CODE", session.scalars(select(Enumeration)).one())

    async def body(app, pilot):
        """Open the review and read the table column."""
        app.screen.action_review()
        await pilot.pause()
        table = app.screen.query_one("#columns")
        rows = {table.get_row_at(index)[0]: table.get_row_at(index)
                for index in range(table.row_count)}
        assert rows["EXIT_CODE"][5] == "syslog_facility"
        assert rows["JOB_NAME"][5] == ""
    drive(database, body)


async def open_editor(app, pilot, stored_id, position):
    """Open EXIT_CODE's picker and choose the option at position (new or edit)."""
    app.push_screen(FieldScreen(app.session.get(SourceFile, stored_id), start_at="EXIT_CODE"))
    await pilot.pause()
    await pick(app, pilot, position)


def test_a_new_table_is_made_from_the_picker_and_linked(database):
    with database.session() as session:
        stored = a_file(session)
        confirm_machine(session, stored, "polaris")
        stored_id = stored.id

    async def body(app, pilot):
        """Make exit_codes in the editor, starting from the codes seen."""
        await open_editor(app, pilot, stored_id, 1)          # no table, new
        assert app.screen.query_one("#rows").text == "0 = "  # the code parbake saw
        app.screen.query_one("#name").value = "exit_codes"
        app.screen.query_one("#rows").text = "0 = success\n1 = error\n2 = "
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, FieldScreen)
        assert "exit_codes (2 codes)" in shown(app.screen.query_one("#table"))
    drive(database, body)
    assert linked_table(database) == "exit_codes"


def test_the_editor_names_bad_lines_and_saves_nothing(database):
    with database.session() as session:
        stored = a_file(session)
        confirm_machine(session, stored, "polaris")
        stored_id = stored.id

    async def body(app, pilot):
        """Save rows with a line missing its = sign."""
        await open_editor(app, pilot, stored_id, 1)
        app.screen.query_one("#name").value = "exit_codes"
        app.screen.query_one("#rows").text = "0 = success\nno equals here"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "line 2" in shown(app.screen.query_one("#problems"))
    drive(database, body)
    assert linked_table(database) is None


def test_the_linked_table_is_edited_in_place(database):
    with database.session() as session:
        stored = a_polaris_file_with_a_table(session)
        from enumerations import link_column
        from models import Enumeration
        table = session.scalars(select(Enumeration)).one()
        link_column(session, stored.dataset, "EXIT_CODE", table)
        stored_id, table_id = stored.id, table.id

    async def body(app, pilot):
        """Open edit (no table, the table, new, edit), add a code, save."""
        await open_editor(app, pilot, stored_id, 3)
        assert app.screen.query_one("#name").value == "syslog_facility"
        assert app.screen.query_one("#rows").text.startswith("0 = kern\n3 = daemon")
        app.screen.query_one("#rows").text = "0 = kern\n3 = daemon\n4 = auth"
        await pilot.press("ctrl+s")
        await pilot.pause()
    drive(database, body)
    with database.session() as session:
        from models import Enumeration
        table = session.get(Enumeration, table_id)
        assert [value.code for value in table.values] == ["0", "3", "4"]
        assert len(session.scalars(select(Enumeration)).all()) == 1


def test_a_new_table_cannot_take_a_name_already_used_on_the_machine(database):
    with database.session() as session:
        stored_id = a_polaris_file_with_a_table(session).id

    async def body(app, pilot):
        """Name a new table syslog_facility, which Polaris already has."""
        await open_editor(app, pilot, stored_id, 2)          # no table, the table, new
        app.screen.query_one("#name").value = "syslog_facility"
        app.screen.query_one("#rows").text = "9 = clock"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "already has" in shown(app.screen.query_one("#problems"))
    drive(database, body)
    assert linked_table(database) is None


def test_ticked_files_take_the_machine_and_answers_of_a_picked_file(database):
    with database.session() as session:
        worked = a_file(session, name="POLARIS.log_syslog_1", kind="x1", columns=("facility",))
        _, dataset = confirm_machine(session, worked, "polaris")
        record_field_answer(session, dataset, "facility",
                            description="Syslog facility code.", data_type="sc:Integer",
                            source_file=worked)
        for number in (2, 3):
            a_file(session, name=f"POLARIS.log_syslog_{number}", kind=f"x{number}",
                   columns=("facility",))

    async def body(app, pilot):
        """Tick the two untouched files, press s, pick the worked file."""
        await pilot.press("space", "space", "s")
        await pilot.pause()
        sources = app.screen.query_one("#sources")
        assert sources.row_count == 1
        assert sources.get_row_at(0)[0] == "POLARIS.log_syslog_1"
        await pilot.press("enter")
        await pilot.pause()
        from screens import SetNameScreen
        assert isinstance(app.screen, SetNameScreen)      # a new set is named
        assert app.screen.query_one("#name").value == "POLARIS_"
        app.screen.query_one("#name").value = "POLARIS_SYSLOG"
        await pilot.press("enter")
        await pilot.pause()
        report = shown(app.screen.query_one("#report"))
        assert "2 file(s) joined POLARIS_x1" in report
        assert "The set is named POLARIS_SYSLOG." in report
    drive(database, body)
    with database.session() as session:
        files = session.scalars(select(SourceFile)).all()
        assert {each.dataset.name for each in files} == {"POLARIS_SYSLOG"}
        assert all(each.state == "complete" for each in files)


# --- dataset questions ---

def test_dataset_questions_are_answered_from_the_file_list(database):
    with database.session() as session:
        stored = a_file(session)
        _, dataset = confirm_machine(session, stored, "polaris")
        from models import DatasetAnswer
        session.add(DatasetAnswer(dataset_id=dataset.id, key="license",
                                  answer="CC-BY", is_confirmed=False))
        session.commit()

    async def body(app, pilot):
        """Answer citeAs and two list questions; confirm a seeded licence."""
        from screens import AnswerScreen, DatasetScreen
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, DatasetScreen)
        table = app.screen.query_one("#questions")
        rows = {table.get_row_at(index)[0]: table.get_row_at(index)
                for index in range(table.row_count)}
        assert len(rows) == 15
        assert rows["citeAs"][1] == "*" and rows["license"][1] == ""
        assert rows["license"][2] == "seeded"
        assert rows["version"][3] == "1.0 (default)"

        for key, typed in (("citeAs", "Cite the ALCF."), ("keywords", "Polaris\nPBS"),
                           ("rai:dataCollectionType", "Direct measurement\nSoftware Collection"),
                           ("license", None)):
            table.move_cursor(row=list(rows).index(key))
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, AnswerScreen)
            if typed is not None:
                app.screen.query_one("#answer").text = typed
            await pilot.press("ctrl+s")
            await pilot.pause()

    drive(database, body)

    with database.session() as session:
        from models import DatasetAnswer
        answers = {row.key: row for row in session.scalars(select(DatasetAnswer))}
        assert answers["citeAs"].answer == "Cite the ALCF." and answers["citeAs"].is_confirmed
        assert answers["keywords"].answer == ["Polaris", "PBS"]
        assert answers["rai:dataCollectionType"].answer == ["Direct measurement",
                                                            "Software Collection"]
        assert answers["license"].answer == "CC-BY" and answers["license"].is_confirmed



# --- file sets ---

def test_the_app_opens_on_the_sets_with_files_in_no_set_last(database):
    with database.session() as session:
        stored = a_file(session)
        confirm_machine(session, stored, "polaris")
        a_file(session, name="LOOSE_FILE", kind="LOOSE")

    async def body(app, pilot):
        """Read the sets table."""
        from screens import SetListScreen
        assert isinstance(app.screen, SetListScreen)
        table = app.screen.query_one("#sets")
        assert table.get_row_at(0)[:3] == ["POLARIS_DJC", "polaris", "1"]
        assert table.get_row_at(1)[0] == "(not in a set)"
        table.move_cursor(row=1)
        await pilot.press("enter")                         # the files in no set
        await pilot.pause()
        assert isinstance(app.screen, FileListScreen)
        rows = app.screen.query_one("#files")
        assert [rows.get_row_at(0)[7], rows.get_row_at(1)[7]] == ["POLARIS_DJC", ""]
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, SetListScreen)
    drive(database, body, start_on_files=False)


def test_a_set_is_renamed_and_a_taken_name_refused(database):
    with database.session() as session:
        first = a_file(session)
        confirm_machine(session, first, "polaris")
        other = a_file(session, name="ANL-ALCF-MACHINESTATUS-POLARIS_20220809_20221231",
                       kind="MACHINESTATUS")
        confirm_machine(session, other, "polaris")

    async def body(app, pilot):
        """Rename POLARIS_DJC to a taken name, then to a free one."""
        from screens import SetNameScreen
        table = app.screen.query_one("#sets")
        table.move_cursor(row=[table.get_row_at(i)[0] for i in range(table.row_count)]
                          .index("POLARIS_DJC"))
        await pilot.press("R")
        await pilot.pause()
        assert isinstance(app.screen, SetNameScreen)
        app.screen.query_one("#name").value = "POLARIS_MACHINESTATUS"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "already has a set" in shown(app.screen.query_one("#problems"))
        app.screen.query_one("#name").value = "POLARIS_JOBS"
        await pilot.press("ctrl+s")
        await pilot.pause()
    drive(database, body, start_on_files=False)
    with database.session() as session:
        names = sorted(each.name for each in session.scalars(select(Dataset)))
        assert names == ["POLARIS_JOBS", "POLARIS_MACHINESTATUS"]


def test_enter_on_a_set_answers_its_columns(database):
    with database.session() as session:
        stored = a_file(session)
        confirm_machine(session, stored, "polaris")

    async def body(app, pilot):
        """Open the set's columns."""
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, FieldScreen)
        assert app.screen.source_file.name == "ANL-ALCF-DJC-POLARIS_20220809_20221231"
    drive(database, body, start_on_files=False)
