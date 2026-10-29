"""bakery/screens/columns.py - answering columns one at a time, and reviewing a file

FieldScreen: one column at a time, measurements beside it; ctrl+e picks its decoding table.
ReviewScreen: every column of a file with its state; enter edits one; d the dataset questions.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Label, Select, Static

from enumerations import link_column, offered_table
from outstanding import coverage_of, field_work_for, record_field_answer
from questions import DATA_TYPE_CHOICES
from screens.actions import FileActions
from screens.common import row_key
from screens.dataset import DatasetScreen
from screens.tables import TablePickerScreen


def table_line(item, dataset):
    """Return the decoding-table line for a column: linked, offered, or none."""
    table = item.doc.enumeration if item.doc else None
    if table is not None:
        return f"  decoding table: {table.name} ({len(table.values)} codes); ctrl+e to change"
    offered = offered_table(dataset, item.offer)
    if offered is not None:
        return f"  decoding table offered: {offered.name}; ctrl+a takes it with the offer"
    return "  decoding table: none; ctrl+e to pick one"


class FieldScreen(Screen):
    """One column at a time; include_settled also shows answered columns."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save and next", priority=True),
        Binding("ctrl+k", "skip", "Next", priority=True),
        Binding("ctrl+j", "previous", "Previous", priority=True),
        Binding("ctrl+a", "accept", "Accept the offer", priority=True),
        Binding("ctrl+e", "pick_table", "Decoding table", priority=True),
        Binding("escape", "back", "Back", priority=True),
    ]

    def __init__(self, source_file, include_settled=False, start_at=None):
        """Keep the file, whether to include answered columns, and the column to start at."""
        super().__init__()
        self.source_file = source_file
        self.include_settled = include_settled
        self.start_at = start_at
        self.work = []
        self.at = 0

    def compose(self) -> ComposeResult:
        """Lay out heading, evidence, offer, answer inputs, footer."""
        yield Header()
        with VerticalScroll():
            yield Static(id="heading")
            yield Static(id="evidence")
            yield Static(id="offer")
            yield Static(id="table")
            yield Label("  What does this column hold?")
            yield Input(id="description")
            with Horizontal():
                yield Select([(each, each) for each in DATA_TYPE_CHOICES],
                             prompt="type", id="data_type", allow_blank=True)
                yield Input(placeholder="unit, e.g. Seconds (optional)", id="unit")
            yield Label("  Anything a person should come back to? (optional)")
            yield Input(placeholder="e.g. disagrees with the accounting table by ~2%", id="note")
        yield Footer()

    def on_mount(self):
        """Load the columns and show the starting one, or finish if none."""
        self.work = self.load()
        if not self.work:
            self.all_done()
            return
        names = [item.name for item in self.work]
        self.at = names.index(self.start_at) if self.start_at in names else 0
        self.show()

    def load(self):
        """Return the columns to work on."""
        return field_work_for(self.app.session, self.source_file,
                              only_outstanding=not self.include_settled)

    def all_done(self):
        """Report every column confirmed and close."""
        self.app.notify(f"{self.source_file.name}: every column is confirmed.")
        self.dismiss(True)

    @property
    def current(self):
        """Return the column being shown, or None."""
        return self.work[self.at] if self.work else None

    def show(self):
        """Show the current column, its measurements, offer and answer."""
        item = self.current
        coverage = coverage_of(self.app.session, self.source_file)
        counted = "column" if self.include_settled else "outstanding"
        self.query_one("#heading", Static).update(
            f"  {self.source_file.name}\n"
            f"  [{self.at + 1}/{len(self.work)} {counted}, {coverage.describe()}]"
            + ("  CONFIRMED, editing" if item.is_settled else "")
            + f"\n\n  {item.name}")
        self.query_one("#evidence", Static).update(
            "\n".join(f"      {line}" for line in item.evidence.splitlines())
            or "      no measurements recorded for this column")
        self.query_one("#offer", Static).update(
            f"  offered: {item.offer.describe()}" if item.offer
            else "  nothing on offer; this one is new")
        self.query_one("#table", Static).update(table_line(item, self.source_file.dataset))
        self.fill(item)
        chooser = self.query_one("#data_type", Select)
        # clear(), not Select.BLANK: that is a stale alias here and raises.
        if item.data_type in DATA_TYPE_CHOICES:
            chooser.value = item.data_type
        else:
            chooser.clear()
        self.set_focus(self.query_one("#description", Input))

    def fill(self, item):
        """Fill the inputs from the item."""
        for part in ("description", "unit", "note"):
            self.query_one(f"#{part}", Input).value = getattr(item, part) or ""

    def action_save(self):
        """Record the answer in the inputs and move on."""
        item = self.current
        if item is None:
            return
        chooser = self.query_one("#data_type", Select)
        record_field_answer(
            self.app.session, self.source_file.dataset, item.name,
            description=self.query_one("#description", Input).value.strip(),
            data_type="" if chooser.is_blank() else chooser.value,
            unit=self.query_one("#unit", Input).value.strip(),
            note=self.query_one("#note", Input).value.strip(),
            source_file=self.source_file)
        self.advance()

    def action_accept(self):
        """Take the offer as it stands, with its table if on this machine, and save."""
        item = self.current
        if item is None or item.offer is None:
            return
        table = offered_table(self.source_file.dataset, item.offer)
        if table is not None and not (item.doc and item.doc.enumeration):
            link_column(self.app.session, self.source_file.dataset, item.name, table)
        self.fill(item)
        self.action_save()

    def action_pick_table(self):
        """Open the decoding-table picker for the current column."""
        item = self.current
        if item is not None:
            self.app.push_screen(TablePickerScreen(self.source_file.dataset, item.name),
                                 self.after_pick)

    def after_pick(self, line):
        """Reload the current column to show its new table, and say what changed."""
        if not line:
            return
        name = self.current.name
        self.work = self.load()
        names = [each.name for each in self.work]
        self.at = names.index(name) if name in names else min(self.at, len(self.work) - 1)
        self.app.notify(line)
        self.show()

    def action_skip(self):
        """Show the next column."""
        self.at = (self.at + 1) % len(self.work)
        self.show()

    def action_previous(self):
        """Show the previous column."""
        self.at = (self.at - 1) % len(self.work)
        self.show()

    def advance(self):
        """Reload the columns and show the next one, or finish if none are left."""
        remaining = self.load()
        if not remaining:
            self.all_done()
            return
        self.work = remaining
        if self.include_settled:
            self.at = (self.at + 1) % len(self.work)
        else:
            self.at = min(self.at, len(self.work) - 1)
        self.show()

    def action_back(self):
        """Close the screen."""
        self.dismiss(True)


class ReviewScreen(FileActions, Screen):
    """Every column of a file, in order, with its state. enter edits one."""

    BINDINGS = [
        Binding("enter", "edit", "Edit this column", priority=True),
        Binding("m", "change_machine", "Change machine"),
        Binding("c", "copy_answers", "Copy answers in"),
        Binding("d", "dataset_questions", "Dataset questions"),
        Binding("escape", "back", "Back"),
    ]
    STATES = {"confirmed": "set", "offered": "offered", "unanswered": "-"}

    def __init__(self, source_file):
        """Keep the file to review."""
        super().__init__()
        self.source_file = source_file
        self.work = []

    def compose(self) -> ComposeResult:
        """Lay out header, title, columns table, detail, footer."""
        yield Header()
        yield Static(id="title")
        yield DataTable(id="columns", cursor_type="row", zebra_stripes=True)
        yield Static(id="detail")
        yield Footer()

    def on_mount(self):
        """Add the table columns and fill the table."""
        self.query_one("#columns", DataTable).add_columns(
            "column", "state", "type", "unit", "note", "table", "description")
        self.action_refresh()

    def action_refresh(self):
        """Reload the columns and the title, keeping the cursor row."""
        table = self.query_one("#columns", DataTable)
        was_on = table.cursor_row if table.row_count else 0
        table.clear()
        self.work = field_work_for(self.app.session, self.source_file, only_outstanding=False)
        for item in self.work:
            table.add_row(item.name, self.STATES[item.state],
                          (item.data_type or "").replace("sc:", ""), item.unit or "",
                          "yes" if item.note else "",
                          item.doc.enumeration.name if item.doc and item.doc.enumeration else "",
                          (item.description or "")[:64],
                          key=item.name)
        if table.row_count:
            table.move_cursor(row=min(was_on, table.row_count - 1))

        coverage = coverage_of(self.app.session, self.source_file)
        with_notes = sum(1 for item in self.work if item.note)
        dataset = self.source_file.dataset
        self.query_one("#title", Static).update(
            f"  {self.source_file.name}   {dataset.machine.name} / {dataset.name}\n"
            f"  {coverage.describe()}"
            + (f", {with_notes} with a note" if with_notes else "")
            + ";  enter to edit any of them")

    @on(DataTable.RowHighlighted)
    def show_detail(self, event):
        """Show the full description and note, which the table truncates."""
        name = row_key(self.query_one("#columns", DataTable))
        item = next((each for each in self.work if each.name == name), None)
        if item is None:
            return
        lines = [f"  {item.description or '(nothing recorded)'}"]
        if item.note:
            lines.append(f"  NOTE: {item.note}")
        if item.offer and not item.is_settled:
            lines.append(f"  offered: {item.offer.describe()}")
        self.query_one("#detail", Static).update("\n".join(lines))

    @on(DataTable.RowSelected)
    def row_chosen(self, event):
        """Edit the selected column."""
        self.action_edit()

    def action_edit(self):
        """Open the column under the cursor for editing."""
        name = row_key(self.query_one("#columns", DataTable))
        if name is not None:
            self.app.push_screen(
                FieldScreen(self.source_file, include_settled=True, start_at=name),
                lambda _: self.action_refresh())

    def action_change_machine(self):
        """Change the file's machine."""
        self.change_machine_of(self.source_file)

    def action_copy_answers(self):
        """Copy another dataset's answers into the file."""
        self.copy_answers_into(self.source_file)

    def action_dataset_questions(self):
        """Answer the licence, citation and RAI questions for the file's dataset."""
        self.app.push_screen(DatasetScreen(self.source_file.dataset), lambda _: self.action_refresh())

    def action_back(self):
        """Close the screen."""
        self.dismiss(None)
