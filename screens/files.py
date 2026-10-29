"""bakery/screens/files.py - the file list: every imported file, least finished first

The screen the app opens. Rows are keyed by file id; ticks are kept by id.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from baking import bake_dataset
from database import describe
from models import SourceFile
from outstanding import files_for_listing
from screens.actions import FileActions
from screens.columns import FieldScreen, ReviewScreen
from screens.common import database_banner, has_machine, row_key
from screens.dataset import DatasetScreen
from screens.machine import GroupScreen, MachineScreen, MachinesScreen, SameAsScreen
from screens.notes import NotesScreen


class FileListScreen(FileActions, Screen):
    """Every imported file, least finished first."""

    BINDINGS = [
        Binding("enter", "work", "Work on this file", priority=True),
        Binding("a", "review", "Review everything set"),
        Binding("M", "change_machine", "Change machine"),
        Binding("c", "copy_answers", "Copy answers in"),
        Binding("b", "bake", "Write it out"),
        Binding("n", "notes", "Notes"),
        Binding("r", "refresh", "Refresh"),
        Binding("m", "machines", "Machines"),
        Binding("space", "tick", "Tick", priority=True),
        Binding("T", "tick_range", "Tick up to here"),
        Binding("U", "untick_all", "Untick all"),
        Binding("g", "group", "Group ticked files"),
        Binding("s", "same_as", "Same as a file"),
        Binding("d", "dataset_questions", "Dataset questions"),
        Binding("escape", "back", "Sets"),
        Binding("q", "quit", "Quit"),
    ]
    TICK = "x"

    def __init__(self):
        """Start with no rows and nothing ticked."""
        super().__init__()
        self.rows = []
        # By id, so a tick survives the list reordering.
        self.ticked = set()
        self.last_ticked = None
        # Held across the machine screen, since the list reorders meanwhile.
        self.working_on = None

    def compose(self) -> ComposeResult:
        """Lay out header, database, files table, hint, footer."""
        yield Header()
        yield database_banner(self.app)
        yield Static(id="where")
        yield DataTable(id="files", cursor_type="row", zebra_stripes=True)
        yield Static(id="hint")
        yield Footer()

    def on_mount(self):
        """Add the table columns, show the database, fill the table."""
        table = self.query_one("#files", DataTable)
        self.tick_column = table.add_columns(
            "", "file", "kind", "machine", "columns", "progress", "state", "set")[0]
        self.query_one("#where", Static).update(f"  {describe(self.app.database.url)}")
        self.action_refresh()

    def action_refresh(self):
        """Reload the files, keeping the cursor on the same file."""
        table = self.query_one("#files", DataTable)
        was_on = self.selected_file()
        table.clear()
        self.rows = files_for_listing(self.app.session)
        for source_file, coverage in self.rows:
            machine = source_file.machine
            table.add_row(
                self.TICK if source_file.id in self.ticked else "",
                source_file.name, source_file.kind or "",
                machine.name if machine else f"? {source_file.machine_suggestion or ''}",
                str(coverage.columns), coverage.describe(), source_file.state,
                source_file.dataset.name if source_file.dataset else "",
                key=str(source_file.id))
        if was_on is not None:
            position = next((at for at, (each, _) in enumerate(self.rows)
                             if each.id == was_on.id), None)
            if position is not None:
                table.move_cursor(row=position)
        self.show_hint()

    def show_hint(self):
        """Show the file counts and the ticked count."""
        if not self.rows:
            hint = "  Nothing imported yet. Start with:  bakery --import <parbake output>"
        else:
            outstanding = sum(1 for _, coverage in self.rows if coverage.outstanding)
            hint = (f"  {len(self.rows)} file(s), {outstanding} with something outstanding. "
                    "A '?' machine is a suggestion nobody has confirmed.")
            if self.ticked:
                hint += (f"\n  {len(self.ticked)} ticked; s to give them another file's answers, "
                         "g to group them under a new name.")
        self.query_one("#hint", Static).update(hint)

    def selected_file(self):
        """Return the file under the cursor, or None."""
        key = row_key(self.query_one("#files", DataTable))
        return self.app.session.get(SourceFile, int(key)) if key else None

    # --- working on a file ---

    def action_work(self):
        """Ask for the machine if the file has none, else open its columns."""
        source_file = self.selected_file()
        if source_file is None:
            return
        self.working_on = source_file
        if source_file.machine is None:
            self.app.push_screen(MachineScreen(source_file), self.after_machine)
        else:
            self.open_columns(source_file)

    def open_columns(self, source_file):
        """Open the file's columns one at a time."""
        self.app.push_screen(FieldScreen(source_file), lambda _: self.action_refresh())

    def after_machine(self, confirmed):
        """Refresh, then open the columns if the machine was confirmed."""
        self.action_refresh()
        source_file = self.working_on
        if confirmed and source_file is not None and source_file.machine is not None:
            self.open_columns(source_file)

    @on(DataTable.RowSelected)
    def row_chosen(self, event):
        """Work on the selected file."""
        self.action_work()

    def action_review(self):
        """Open the review of every column of the file."""
        source_file = self.selected_file()
        if source_file and has_machine(self.app, source_file):
            self.app.push_screen(ReviewScreen(source_file), lambda _: self.action_refresh())

    def action_change_machine(self):
        """Change the file's machine, or ask for one if it has none."""
        source_file = self.selected_file()
        if source_file is None:
            return
        if source_file.dataset is None:
            self.action_work()
        else:
            self.change_machine_of(source_file)

    def action_copy_answers(self):
        """Copy another dataset's answers into the file."""
        source_file = self.selected_file()
        if source_file and has_machine(self.app, source_file):
            self.copy_answers_into(source_file)

    def action_bake(self):
        """Write out the file's dataset; an unfinished one stays par-baked."""
        source_file = self.selected_file()
        if not (source_file and has_machine(self.app, source_file)):
            return
        out = self.app.output_directory
        report = bake_dataset(self.app.session, source_file.dataset, out)
        self.action_refresh()
        if report.finished:
            self.app.notify(f"{source_file.dataset.name}: finished Croissant written to {out}.")
        else:
            outstanding = max(map(len, report.unfinished.values()), default=0)
            self.app.notify(f"{source_file.dataset.name} written to {out}, still par-baked: "
                            f"{outstanding} thing(s) outstanding.", severity="warning")

    def action_dataset_questions(self):
        """Answer the licence, citation and RAI questions for the file's dataset."""
        source_file = self.selected_file()
        if source_file and has_machine(self.app, source_file):
            self.app.push_screen(DatasetScreen(source_file.dataset), lambda _: self.action_refresh())

    def action_back(self):
        """Go back to the sets."""
        self.dismiss(None)

    def action_notes(self):
        """Show the notes."""
        self.app.push_screen(NotesScreen())

    def action_machines(self):
        """Show the machines."""
        self.app.push_screen(MachinesScreen())

    # --- ticking files to group them ---

    def set_tick(self, source_file, ticked):
        """Tick or untick one file."""
        (self.ticked.add if ticked else self.ticked.discard)(source_file.id)
        self.query_one("#files", DataTable).update_cell(
            str(source_file.id), self.tick_column, self.TICK if ticked else "")

    def action_tick(self):
        """Toggle the tick on the file under the cursor and step down."""
        source_file = self.selected_file()
        if source_file is None:
            return
        self.set_tick(source_file, source_file.id not in self.ticked)
        self.last_ticked = source_file.id
        self.query_one("#files", DataTable).action_cursor_down()
        self.show_hint()

    def action_tick_range(self):
        """Tick every file from the last ticked one to this one."""
        source_file = self.selected_file()
        if source_file is None:
            return
        positions = {each.id: at for at, (each, _) in enumerate(self.rows)}
        if self.last_ticked not in positions:
            self.app.notify("Tick the first file with space, then move here and press T.",
                            severity="warning")
            return
        start, end = sorted((positions[self.last_ticked], positions[source_file.id]))
        for each, _ in self.rows[start:end + 1]:
            self.set_tick(each, True)
        self.last_ticked = source_file.id
        self.show_hint()

    def action_untick_all(self):
        """Untick every file."""
        for each, _ in self.rows:
            if each.id in self.ticked:
                self.set_tick(each, False)
        self.ticked.clear()
        self.last_ticked = None
        self.show_hint()

    def action_group(self):
        """Open the group screen for the ticked files."""
        if not self.ticked:
            self.app.notify("Tick the files first: space for one, T for a range.",
                            severity="warning")
            return
        files = [each for each, _ in self.rows if each.id in self.ticked]
        self.app.push_screen(GroupScreen(files), self.after_group)

    def action_same_as(self):
        """Give the ticked files, or the cursor's file, another file's machine and answers."""
        files = [each for each, _ in self.rows if each.id in self.ticked]
        if not files and self.selected_file():
            files = [self.selected_file()]
        if files:
            self.app.push_screen(SameAsScreen(files), self.after_group)

    def after_group(self, report):
        """Clear the ticks if the files were grouped, then refresh."""
        if report:
            self.ticked.clear()
            self.last_ticked = None
        self.after_change(report)
