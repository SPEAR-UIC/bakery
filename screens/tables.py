"""bakery/screens/tables.py - picking, making and editing a column's decoding table

TablePickerScreen: no table, this machine's tables with coverage, new, edit.
    o adds other machines' tables; choosing one copies it here.
TableEditorScreen: name, description, `code = meaning` lines; undecoded codes prefilled.
Both dismiss with a report line, or None.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, OptionList, Static, TextArea

from enumerations import (
    copy_table_to,
    coverage,
    link_column,
    observed_codes,
    parse_table_text,
    save_table,
    table_text,
    tables_for,
)
from queries import field_doc

NO_TABLE = "no decoding table"
NEW = "new"
EDIT = "edit"


def linked_table(session, dataset, column):
    """Return the table the column links to, or None."""
    doc = field_doc(session, dataset, column)
    return doc.enumeration if doc else None


class TablePickerScreen(Screen):
    """Choose a decoding table for one column, or none."""

    BINDINGS = [
        Binding("o", "toggle_others", "Other machines"),
        Binding("escape", "cancel", "Back"),
    ]

    def __init__(self, dataset, column):
        """Keep the dataset and the column to link."""
        super().__init__()
        self.dataset = dataset
        self.column = column
        self.show_others = False
        self.choices = []

    def compose(self) -> ComposeResult:
        """Lay out header, title, the list of tables, footer."""
        yield Header()
        with VerticalScroll():
            yield Static(id="title")
            yield OptionList(id="tables")
        yield Footer()

    def on_mount(self):
        """Fill the list and focus it."""
        self.fill()
        self.query_one("#tables", OptionList).focus()

    def fill(self):
        """List no table, the tables with their coverage, then new and edit."""
        machine = self.dataset.machine
        observed = observed_codes(self.app.session, self.dataset, self.column)
        doc_table = linked_table(self.app.session, self.dataset, self.column)
        self.choices = ([None] + tables_for(self.app.session, machine, self.show_others)
                        + [NEW] + ([EDIT] if doc_table else []))
        options = self.query_one("#tables", OptionList)
        options.clear_options()
        for choice in self.choices:
            options.add_option(self.label(choice, machine, observed, doc_table))
        self.query_one("#title", Static).update(
            f"  Decoding table for {self.column} ({self.dataset.name})\n"
            f"  now: {doc_table.name if doc_table else 'none'}\n"
            f"  {len(observed.codes)} code(s) seen in the data. "
            + ("o hides" if self.show_others else "o shows") + " other machines' tables.\n")

    @staticmethod
    def label(table, machine, observed, doc_table):
        """Return the option text for a table, no table, new or edit."""
        if table is None:
            return f"({NO_TABLE})"
        if table == NEW:
            return "new table..."
        if table == EDIT:
            return f"edit {doc_table.name}..."
        text = (f"{table.name}  {len(table.values)} code(s), "
                f"{coverage(table, observed).describe()}")
        if table.machine_id != machine.id:
            text += f"  [{table.machine.name}; copied to {machine.name} if chosen]"
        return text

    def action_toggle_others(self):
        """Show or hide other machines' tables."""
        self.show_others = not self.show_others
        self.fill()

    @on(OptionList.OptionSelected)
    def chosen(self, event):
        """Link the chosen table, copying it here first if it is another machine's;
        or open the editor."""
        table = self.choices[event.option_index]
        session = self.app.session
        if table in (NEW, EDIT):
            existing = linked_table(session, self.dataset, self.column) if table == EDIT else None
            self.app.push_screen(TableEditorScreen(self.dataset, self.column, existing),
                                 self.after_editing)
            return
        if table is not None:
            try:
                table = copy_table_to(session, table, self.dataset.machine)
            except ValueError as problem:
                self.app.notify(str(problem), severity="error")
                return
        link_column(session, self.dataset, self.column, table)
        self.dismiss(f"{self.column}: {table.name if table else NO_TABLE}")

    def after_editing(self, line):
        """Close with the editor's line if it saved; stay open if not."""
        if line:
            self.dismiss(line)

    def action_cancel(self):
        """Close without changing anything."""
        self.dismiss(None)


class TableEditorScreen(Screen):
    """Make a decoding table, or edit one; save links it to the column."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save and link", priority=True),
        Binding("escape", "cancel", "Back, save nothing"),
    ]

    def __init__(self, dataset, column, table=None):
        """Keep the dataset, the column to link, and the table to edit, if any."""
        super().__init__()
        self.dataset = dataset
        self.column = column
        self.table = table

    def compose(self) -> ComposeResult:
        """Lay out header, title, name, description, rows, problems, footer."""
        yield Header()
        with VerticalScroll():
            yield Static(id="title")
            yield Label("  Name:")
            yield Input(placeholder="e.g. syslog_facility", id="name")
            yield Label("  Description (optional):")
            yield Input(placeholder="e.g. Syslog facility codes (RFC 5424).", id="description")
            yield Label("  One code per line: code = meaning. A line with no meaning is left out.")
            yield TextArea(id="rows")
            yield Static(id="problems")
        yield Footer()

    def on_mount(self):
        """Fill the inputs from the table and the codes seen."""
        observed = observed_codes(self.app.session, self.dataset, self.column)
        undecoded = coverage(self.table, observed).undecoded
        users = len(self.table.field_docs) if self.table else 0
        self.query_one("#title", Static).update(
            f"  {'Edit' if self.table else 'New'} decoding table for {self.column} "
            f"on {self.dataset.machine.name}\n"
            + (f"  Used by {users} column(s); changes apply to all of them.\n" if users else "")
            + f"  {len(undecoded)} code(s) seen in the data are not decoded yet"
            + ("" if observed.is_complete else "; parbake listed only the most common")
            + ".  ctrl+s saves.\n")
        self.query_one("#name", Input).value = self.table.name if self.table else ""
        self.query_one("#description", Input).value = (self.table.description or "") if self.table else ""
        self.query_one("#rows", TextArea).text = table_text(self.table, undecoded)
        self.query_one("#name", Input).focus()

    def action_save(self):
        """Save the table and link the column, or show what is wrong."""
        rows, problems = parse_table_text(self.query_one("#rows", TextArea).text)
        if problems:
            self.query_one("#problems", Static).update(
                "\n".join(f"  {problem}" for problem in problems))
            return
        session = self.app.session
        try:
            table = save_table(session, self.dataset.machine, self.query_one("#name", Input).value,
                               self.query_one("#description", Input).value, rows, self.table)
        except ValueError as problem:
            self.query_one("#problems", Static).update(f"  {problem}")
            return
        link_column(session, self.dataset, self.column, table)
        self.dismiss(f"{self.column}: {table.name} ({len(rows)} codes)")

    def action_cancel(self):
        """Close without saving."""
        self.dismiss(None)
