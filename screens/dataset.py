"""bakery/screens/dataset.py - answering a dataset's own questions: licence, citation, RAI

DatasetScreen: every question, marked * if a finished file needs it, with its state.
AnswerScreen: one answer. Lists one item per line; objects as JSON.
    Saving confirms it; saving blank removes it.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static, TextArea

from outstanding import clear_dataset_answer, record_dataset_answer
from queries import dataset_answers
from questions import DATASET_QUESTIONS, DEFAULT_VERSION, REQUIRED_KEYS, answer_text, parse_answer
from screens.common import row_key

DEFAULTS = {"version": f"{DEFAULT_VERSION} (default)", "datePublished": "day of baking (default)"}


class DatasetScreen(Screen):
    """Every dataset-level question with its state; enter answers one."""

    BINDINGS = [
        Binding("enter", "edit", "Answer this", priority=True),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, dataset):
        """Keep the dataset."""
        super().__init__()
        self.dataset = dataset

    def compose(self) -> ComposeResult:
        """Lay out header, title, the questions table, help, footer."""
        yield Header()
        yield Static(id="title")
        yield DataTable(id="questions", cursor_type="row", zebra_stripes=True)
        yield Static(id="help")
        yield Footer()

    def on_mount(self):
        """Add the table columns and fill the table."""
        self.query_one("#questions", DataTable).add_columns("question", "needed", "state", "answer")
        self.action_refresh()
        self.query_one("#questions", DataTable).focus()

    def action_refresh(self):
        """Reload every question's state, keeping the cursor row."""
        table = self.query_one("#questions", DataTable)
        was_on = table.cursor_row if table.row_count else 0
        table.clear()
        answers = dataset_answers(self.app.session, self.dataset)
        missing = 0
        for spec in DATASET_QUESTIONS:
            key, row = spec["key"], answers.get(spec["key"])
            if row is None:
                state, shown = "-", DEFAULTS.get(key, "")
            else:
                state = "set" if row.is_confirmed else "seeded"
                shown = answer_text(row.answer).replace("\n", " / ")[:70]
            if key in REQUIRED_KEYS and not (row and row.is_confirmed):
                missing += 1
            table.add_row(key, "*" if key in REQUIRED_KEYS else "", state, shown, key=key)
        table.move_cursor(row=min(was_on, table.row_count - 1))
        self.query_one("#title", Static).update(
            f"  {self.dataset.name}: answered once for every file in the dataset.\n"
            f"  * needed for a finished file; {missing} still to answer or confirm. "
            "Seeded answers need confirming.\n")

    @on(DataTable.RowHighlighted)
    def show_help(self, event):
        """Show the highlighted question's prompt and help."""
        spec = self.spec(row_key(self.query_one("#questions", DataTable)))
        if spec:
            self.query_one("#help", Static).update(
                f"\n  {spec['prompt']}\n  {spec.get('help_text', '')}")

    @staticmethod
    def spec(key):
        """Return the catalogue entry for a key, or None."""
        return next((spec for spec in DATASET_QUESTIONS if spec["key"] == key), None)

    @on(DataTable.RowSelected)
    def row_chosen(self, event):
        """Answer the selected question."""
        self.action_edit()

    def action_edit(self):
        """Open the highlighted question's answer."""
        spec = self.spec(row_key(self.query_one("#questions", DataTable)))
        if spec:
            self.app.push_screen(AnswerScreen(self.dataset, spec), lambda _: self.action_refresh())

    def action_back(self):
        """Close the screen."""
        self.dismiss(None)


class AnswerScreen(Screen):
    """Edit one dataset-level answer; ctrl+s saves it confirmed."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("escape", "cancel", "Back, save nothing"),
    ]

    def __init__(self, dataset, spec):
        """Keep the dataset and the question."""
        super().__init__()
        self.dataset = dataset
        self.spec = spec

    def compose(self) -> ComposeResult:
        """Lay out header, question, answer box, footer."""
        yield Header()
        with VerticalScroll():
            yield Static(id="question")
            yield TextArea(id="answer")
        yield Footer()

    def on_mount(self):
        """Show the question and fill the box with the current answer."""
        spec = self.spec
        row = dataset_answers(self.app.session, self.dataset).get(spec["key"])
        kind = "One per line." if spec.get("kind") == "lines" else ""
        seeded = " Seeded, not yet confirmed: ctrl+s confirms it." if row and not row.is_confirmed else ""
        self.query_one("#question", Static).update(
            f"  {self.dataset.name} / {spec['key']}"
            + ("  (needed for a finished file)" if spec["key"] in REQUIRED_KEYS else "") + "\n"
            f"  {spec['prompt']}\n  {spec.get('help_text', '')}\n  {kind}{seeded}"
            " Blank removes the answer.\n")
        area = self.query_one("#answer", TextArea)
        area.text = answer_text(row.answer) if row else ""
        area.focus()

    def action_save(self):
        """Save the answer confirmed, or remove it if blank."""
        value = parse_answer(self.query_one("#answer", TextArea).text, self.spec.get("kind"))
        if value is None:
            clear_dataset_answer(self.app.session, self.dataset, self.spec["key"])
        else:
            record_dataset_answer(self.app.session, self.dataset, self.spec["key"], value)
        self.dismiss(True)

    def action_cancel(self):
        """Close without saving."""
        self.dismiss(None)
