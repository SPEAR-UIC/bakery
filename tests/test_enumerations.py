"""bakery/tests/test_enumerations.py - tests for decoding tables and their columns (enumerations.py)

Run: pytest tests/test_enumerations.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import pytest
from sqlalchemy import select

from database import Database
from enumerations import (
    TableRow,
    copy_table_to,
    coverage,
    link_column,
    observed_codes,
    offered_table,
    parse_table_text,
    save_table,
    table_text,
    tables_for,
)
from models import Enumeration, FieldDoc, FileField, SourceFile
from outstanding import confirm_machine, field_work_for, record_field_answer


@pytest.fixture
def database(tmp_path):
    """Yield an open database in a temporary directory."""
    with Database(directory=tmp_path) as opened:
        yield opened


def a_file(session, name, machine, kind="SYSLOG", columns=None):
    """Return an imported file on machine; columns maps a name to {value: count}."""
    columns = columns or {"facility": ({"3": 900, "0": 100}, 2)}
    stored = SourceFile(name=name, kind=kind, machine_suggestion=machine.lower(),
                        parbaked_path=f"/out/{name}.parbaked.json",
                        parbaked_sha256="0" * 64, column_count=len(columns))
    session.add(stored)
    session.flush()
    for position, (column, (values, distinct)) in enumerate(columns.items()):
        session.add(FileField(file_id=stored.id, name=column, position=position, measurements={
            "distinct_count": distinct,
            "distinct_count_is_at_least": False,
            "most_common_values": [{"value": value, "count": count}
                                   for value, count in values.items()],
        }))
    session.commit()
    confirm_machine(session, stored, machine)
    return stored


# --- the editor's text box -------------------------------------------------

def test_each_line_is_a_code_and_its_meaning():
    rows, problems = parse_table_text("0 = kern: kernel messages\n3 = daemon: system daemons\n")
    assert rows == [TableRow("0", "kern: kernel messages"),
                    TableRow("3", "daemon: system daemons")]
    assert problems == []


def test_only_the_first_equals_sign_splits():
    rows, _ = parse_table_text("-29 = walltime kill (exit = -29)")
    assert rows == [TableRow("-29", "walltime kill (exit = -29)")]


def test_a_code_with_no_meaning_yet_is_left_out_not_stored_empty():
    rows, problems = parse_table_text("3 = daemon\n16 = \n\n")
    assert rows == [TableRow("3", "daemon")]
    assert problems == []


def test_mistakes_are_named_by_line():
    _, problems = parse_table_text("3 = daemon\nno equals here\n = orphan meaning\n3 = again")
    assert len(problems) == 3
    assert problems[0].startswith("line 2:")
    assert problems[1].startswith("line 3:")
    assert problems[2].startswith("line 4:") and "twice" in problems[2]


def test_the_text_offers_a_line_for_each_undecoded_code(database):
    with database.session() as session:
        stored = a_file(session, "POLARIS.log_syslog_1", "Polaris")
        machine = stored.dataset.machine
        table = save_table(session, machine, "syslog_facility", "", [TableRow("3", "daemon")])
        text = table_text(table, undecoded=["0"])
        assert text == "3 = daemon\n0 = "
        assert parse_table_text(text)[0] == [TableRow("3", "daemon")]


# --- what is in the data ---------------------------------------------------

def test_codes_are_gathered_across_the_datasets_files_most_common_first(database):
    with database.session() as session:
        first = a_file(session, "POLARIS.log_syslog_1", "Polaris",
                       columns={"facility": ({"3": 900, "0": 100}, 2)})
        a_file(session, "POLARIS.log_syslog_2", "Polaris",
               columns={"facility": ({"0": 950, "16": 5}, 2)})
        observed = observed_codes(session, first.dataset, "facility")
        assert observed.codes == ["0", "3", "16"]
        assert observed.is_complete


def test_a_file_with_more_values_than_parbake_listed_makes_the_list_incomplete(database):
    with database.session() as session:
        first = a_file(session, "POLARIS.log_syslog_1", "Polaris",
                       columns={"facility": ({"3": 900, "0": 100}, 11)})
        observed = observed_codes(session, first.dataset, "facility")
        assert not observed.is_complete


def test_coverage_splits_what_the_table_decodes_from_what_it_does_not(database):
    with database.session() as session:
        stored = a_file(session, "POLARIS.log_syslog_1", "Polaris",
                        columns={"facility": ({"3": 900, "0": 100}, 11)})
        table = save_table(session, stored.dataset.machine, "syslog_facility", "",
                           [TableRow("3", "daemon")])
        result = coverage(table, observed_codes(session, stored.dataset, "facility"))
        assert result.decoded == ["3"]
        assert result.undecoded == ["0"]
        assert "decodes 1 of 2" in result.describe()
        assert "there are more" in result.describe()


# --- making and finding tables ---------------------------------------------

def test_a_table_is_made_on_a_machine_with_its_hedges_flagged(database):
    with database.session() as session:
        machine = a_file(session, "POLARIS.log_syslog_1", "Polaris").dataset.machine
        table = save_table(session, machine, "exit_code", "PBS exit codes",
                           [TableRow("0", "success"),
                            TableRow("143", "PROVISIONAL: probably qdel")])
        assert [value.code for value in table.values] == ["0", "143"]
        assert [value.is_provisional for value in table.values] == [False, True]


def test_two_tables_of_one_name_on_a_machine_are_refused(database):
    with database.session() as session:
        machine = a_file(session, "POLARIS.log_syslog_1", "Polaris").dataset.machine
        save_table(session, machine, "syslog_facility", "", [TableRow("3", "daemon")])
        with pytest.raises(ValueError, match="already has"):
            save_table(session, machine, "syslog_facility", "", [])


def test_editing_a_table_replaces_its_rows_and_keeps_it_the_same_table(database):
    with database.session() as session:
        machine = a_file(session, "POLARIS.log_syslog_1", "Polaris").dataset.machine
        table = save_table(session, machine, "syslog_facility", "", [TableRow("3", "daemon")])
        edited = save_table(session, machine, "syslog_facility", "RFC 5424",
                            [TableRow("0", "kern"), TableRow("3", "daemon")], table=table)
        assert edited.id == table.id
        assert [value.code for value in edited.values] == ["0", "3"]
        assert edited.description == "RFC 5424"


def test_other_machines_tables_are_listed_only_when_asked_for(database):
    with database.session() as session:
        polaris = a_file(session, "POLARIS.log_syslog_1", "Polaris").dataset.machine
        aurora = a_file(session, "AURORA.log_syslog_1", "Aurora").dataset.machine
        own = save_table(session, polaris, "syslog_facility", "", [])
        other = save_table(session, aurora, "exit_code", "", [])
        assert tables_for(session, polaris) == [own]
        assert tables_for(session, polaris, include_other_machines=True) == [own, other]


def test_another_machines_table_is_copied_not_shared(database):
    with database.session() as session:
        polaris = a_file(session, "POLARIS.log_syslog_1", "Polaris").dataset.machine
        aurora = a_file(session, "AURORA.log_syslog_1", "Aurora").dataset.machine
        original = save_table(session, aurora, "syslog_facility", "RFC 5424",
                              [TableRow("3", "daemon")])
        copy = copy_table_to(session, original, polaris)
        assert copy.id != original.id and copy.machine_id == polaris.id
        assert [(value.code, value.meaning) for value in copy.values] == [("3", "daemon")]

        save_table(session, polaris, "syslog_facility", "", [TableRow("3", "changed")],
                   table=copy)
        assert original.values[0].meaning == "daemon"
        assert copy_table_to(session, copy, polaris) is copy


# --- columns and tables ----------------------------------------------------

def test_a_column_is_linked_and_unlinked(database):
    with database.session() as session:
        stored = a_file(session, "POLARIS.log_syslog_1", "Polaris")
        table = save_table(session, stored.dataset.machine, "syslog_facility", "", [])
        doc = link_column(session, stored.dataset, "facility", table)
        assert doc.enumeration is table
        assert link_column(session, stored.dataset, "facility", None).enumeration is None


def test_linking_a_column_nobody_has_described_does_not_settle_it(database):
    with database.session() as session:
        stored = a_file(session, "POLARIS.log_syslog_1", "Polaris")
        table = save_table(session, stored.dataset.machine, "syslog_facility", "", [])
        link_column(session, stored.dataset, "facility", table)
        assert [item.name for item in field_work_for(session, stored)] == ["facility"]


def test_linking_keeps_a_confirmed_answer_confirmed(database):
    with database.session() as session:
        stored = a_file(session, "POLARIS.log_syslog_1", "Polaris")
        record_field_answer(session, stored.dataset, "facility",
                            description="Syslog facility", data_type="sc:Integer")
        table = save_table(session, stored.dataset.machine, "syslog_facility", "", [])
        doc = link_column(session, stored.dataset, "facility", table)
        assert doc.is_confirmed and doc.description == "Syslog facility"


def test_a_column_cannot_point_at_another_machines_table(database):
    with database.session() as session:
        stored = a_file(session, "POLARIS.log_syslog_1", "Polaris")
        aurora = a_file(session, "AURORA.log_syslog_1", "Aurora").dataset.machine
        foreign = save_table(session, aurora, "syslog_facility", "", [])
        with pytest.raises(ValueError, match="copy it here first"):
            link_column(session, stored.dataset, "facility", foreign)


def test_an_offer_from_this_machine_brings_its_table(database):
    with database.session() as session:
        first = a_file(session, "POLARIS.log_syslog_1", "Polaris", kind="SYSLOG")
        record_field_answer(session, first.dataset, "facility",
                            description="Syslog facility", data_type="sc:Integer")
        table = save_table(session, first.dataset.machine, "syslog_facility", "", [])
        link_column(session, first.dataset, "facility", table)

        sibling = a_file(session, "POLARIS.log_console_1", "Polaris", kind="CONSOLE")
        offer = field_work_for(session, sibling)[0].offer
        assert offer.kind == "sibling"
        assert offered_table(sibling.dataset, offer) is table


def test_an_offer_from_another_machine_leaves_its_table_behind(database):
    with database.session() as session:
        aurora_file = a_file(session, "AURORA.log_syslog_1", "Aurora")
        record_field_answer(session, aurora_file.dataset, "facility",
                            description="Syslog facility", data_type="sc:Integer")
        table = save_table(session, aurora_file.dataset.machine, "syslog_facility", "", [])
        link_column(session, aurora_file.dataset, "facility", table)

        polaris_file = a_file(session, "POLARIS.log_syslog_1", "Polaris")
        offer = field_work_for(session, polaris_file)[0].offer
        assert offer.kind == "other machine"
        assert offered_table(polaris_file.dataset, offer) is None
