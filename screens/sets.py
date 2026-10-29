"""bakery/screens/sets.py - the file sets: the screen the app opens on

A file set is a dataset: files of one format, answered once, baked to one
Croissant whose data is a cr:FileSet.
SetListScreen: one row per set, least finished first, then files in no set.
    enter works on the set, v lists files for ticking and moving.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from sqlalchemy import select
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from baking import bake_dataset
from database import describe
from models import Dataset, SourceFile
from outstanding import coverage_of, outstanding_dataset_questions
from questions import REQUIRED_KEYS
from screens.columns import FieldScreen, ReviewScreen
from screens.common import database_banner, row_key
from screens.dataset import DatasetScreen
from screens.files import FileListScreen
from screens.machine import MachinesScreen, SetNameScreen
from screens.notes import NotesScreen

UNSET = "unset"


def representative(dataset):
    """Return the set's file with the most columns, to answer the set's columns on."""
    return max(dataset.files, key=lambda each: (len(each.fields), each.name))


class SetListScreen(Screen):
    """Every file set with its progress, and the files in no set."""

    BINDINGS = [
        Binding("enter", "work", "Work on this set", priority=True),
        Binding("a", "review", "Review the set's columns"),
        Binding("d", "dataset_questions", "Set questions"),
        Binding("b", "bake", "Write it out"),
        Binding("R", "rename", "Rename the set"),
        Binding("v", "files", "Files"),
        Binding("n", "notes", "Notes"),
        Binding("m", "machines", "Machines"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        """Lay out header, database line, sets table, hint, footer."""
        yield Header()
        yield database_banner(self.app)
        yield Static(id="where")
        yield DataTable(id="sets", cursor_type="row", zebra_stripes=True)
        yield Static(id="hint")
        yield Footer()

    def on_mount(self):
        """Add the table columns and fill the table."""
        self.query_one("#sets", DataTable).add_columns(
            "set", "machine", "files", "columns", "set questions")
        self.query_one("#where", Static).update(f"  {describe(self.app.database.url)}")
        self.action_refresh()

    def action_refresh(self):
        """Reload the sets, least finished first, then the files in no set."""
        session = self.app.session
        table = self.query_one("#sets", DataTable)
        was_on = row_key(table)
        table.clear()
        rows = []
        for dataset in session.scalars(select(Dataset)):
            if dataset.files:
                coverage = coverage_of(session, representative(dataset))
                rows.append((coverage.share, dataset, coverage))
        for _, dataset, coverage in sorted(rows, key=lambda row: (row[0], row[1].name)):
            left = sum(1 for spec, _ in outstanding_dataset_questions(session, dataset)
                       if spec["key"] in REQUIRED_KEYS)
            table.add_row(dataset.name, dataset.machine.name, str(len(dataset.files)),
                          coverage.describe(), f"{left} needed, d to answer" if left else "done",
                          key=str(dataset.id))
        unset = len(session.scalars(select(SourceFile.id).where(SourceFile.dataset_id.is_(None))).all())
        if unset:
            table.add_row("(not in a set)", "", str(unset), "v to list them", "", key=UNSET)
        keys = [str(dataset.id) for _, dataset, _ in rows] + ([UNSET] if unset else [])
        if was_on in keys:
            table.move_cursor(row=keys.index(was_on))
        self.query_one("#hint", Static).update(
            "  Nothing imported yet. Start with:  bakery --import <parbake output>" if not keys
            else f"  {len(rows)} set(s). A set's files share its machine and answers and bake "
                 "to one Croissant.\n  v lists the files: tick them, then s puts them in a set.")

    def selected_set(self):
        """Return the dataset under the cursor, or None for no set or the unset row."""
        key = row_key(self.query_one("#sets", DataTable))
        if key in (None, UNSET):
            return None
        return self.app.session.get(Dataset, int(key))

    @on(DataTable.RowSelected)
    def row_chosen(self, event):
        """Work on the selected set, or list the files in no set."""
        self.action_work()

    def action_work(self):
        """Answer the set's columns; on the no-set row, list the files."""
        dataset = self.selected_set()
        if dataset is None:
            self.action_files()
            return
        self.app.push_screen(FieldScreen(representative(dataset)), lambda _: self.action_refresh())

    def action_review(self):
        """Review every column of the set."""
        dataset = self.selected_set()
        if dataset is not None:
            self.app.push_screen(ReviewScreen(representative(dataset)),
                                 lambda _: self.action_refresh())

    def action_dataset_questions(self):
        """Answer the set's licence, citation and RAI questions."""
        dataset = self.selected_set()
        if dataset is not None:
            self.app.push_screen(DatasetScreen(dataset), lambda _: self.action_refresh())

    def action_bake(self):
        """Write the set's Croissant; an unfinished one stays par-baked."""
        dataset = self.selected_set()
        if dataset is None:
            return
        out = self.app.output_directory
        report = bake_dataset(self.app.session, dataset, out)
        self.action_refresh()
        if report.finished:
            self.app.notify(f"{dataset.name}: finished Croissant written to {out}.")
        else:
            outstanding = max(map(len, report.unfinished.values()), default=0)
            self.app.notify(f"{dataset.name} written to {out}, still par-baked: "
                            f"{outstanding} thing(s) outstanding.", severity="warning")

    def action_rename(self):
        """Rename the set under the cursor."""
        dataset = self.selected_set()
        if dataset is not None:
            self.app.push_screen(SetNameScreen(dataset), lambda _: self.action_refresh())

    def action_files(self):
        """List every file, for ticking and moving between sets."""
        self.app.push_screen(FileListScreen(), lambda _: self.action_refresh())

    def action_notes(self):
        """Open the notes screen."""
        self.app.push_screen(NotesScreen())

    def action_machines(self):
        """Open the machines screen."""
        self.app.push_screen(MachinesScreen())
