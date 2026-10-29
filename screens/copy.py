"""bakery/screens/copy.py - copying another dataset's answers in

CopySourceScreen: pick the source. CopyPlanScreen: review the plan.
DifferenceScreen: decide each difference.
Nothing is written until every difference is decided.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from screens.common import indented
from transfer import apply_copy, copy_candidates, describe_values, plan_copy


class CopySourceScreen(Screen):
    """Every other dataset, most useful first."""

    BINDINGS = [
        Binding("enter", "choose", "Copy from this dataset", priority=True),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, source_file):
        """Keep the file to copy into."""
        super().__init__()
        self.source_file = source_file
        self.candidates = []

    def compose(self) -> ComposeResult:
        """Lay out header, title, sources table, footer."""
        yield Header()
        yield Static(id="title")
        yield DataTable(id="sources", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self):
        """Fill the title and the table of candidate sources."""
        target = self.source_file.dataset
        self.query_one("#title", Static).update(
            f"  Copy answers into {target.name}, for the columns whose names match.\n"
            "  Gaps are filled; where this dataset already has a different confirmed\n"
            "  answer, you choose. Only answers confirmed in the source are copied.")
        table = self.query_one("#sources", DataTable)
        table.add_columns("dataset", "machine", "your columns it answers", "confirmed answers")
        self.candidates = copy_candidates(self.app.session, target)
        for dataset, matching, confirmed in self.candidates:
            table.add_row(dataset.name, dataset.machine.name, str(matching), str(confirmed),
                          key=str(dataset.id))

    @on(DataTable.RowSelected)
    def row_chosen(self, event):
        """Choose the selected row."""
        self.action_choose()

    def action_choose(self):
        """Plan a copy from the dataset under the cursor and show the plan."""
        table = self.query_one("#sources", DataTable)
        if not table.row_count:
            return
        source = self.candidates[table.cursor_row][0]
        plan = plan_copy(self.app.session, source, self.source_file.dataset)
        self.app.push_screen(CopyPlanScreen(plan), self.after_plan)

    def after_plan(self, report):
        """Close with the copy's report if it was written; stay open if not."""
        if report:
            self.dismiss(report)

    def action_back(self):
        """Close the screen."""
        self.dismiss(None)


class CopyPlanScreen(Screen):
    """What the copy would do, before anything is written."""

    BINDINGS = [
        Binding("ctrl+s", "copy", "Copy", priority=True),
        Binding("escape", "back", "Back, copy nothing"),
    ]

    def __init__(self, plan):
        """Keep the plan to show."""
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        """Lay out header, plan, footer."""
        yield Header()
        with VerticalScroll():
            yield Static(id="plan")
        yield Footer()

    def on_mount(self):
        """Show the plan lines."""
        count = len(self.plan.differences)
        last = (f"  ctrl+s to copy; the {count} difference(s) are shown one at a time first."
                if count else "  ctrl+s to copy.")
        self.query_one("#plan", Static).update(indented(self.plan.lines() + ["", last]))

    def action_copy(self):
        """Decide the differences if any, else apply the copy."""
        if self.plan.differences:
            self.app.push_screen(DifferenceScreen(self.plan), self.after_choosing)
        else:
            self.dismiss(apply_copy(self.app.session, self.plan))

    def after_choosing(self, finished):
        """Apply the copy if every difference was decided."""
        if finished:
            self.dismiss(apply_copy(self.app.session, self.plan))

    def action_back(self):
        """Close the screen, copying nothing."""
        self.dismiss(None)


class DifferenceScreen(Screen):
    """One difference at a time; leaving abandons the whole copy."""

    BINDINGS = [
        Binding("u", "decide(True)", "Use the copy"),
        Binding("k", "decide(False)", "Keep current"),
        Binding("a", "decide_rest(True)", "Use the copy for all remaining"),
        Binding("x", "decide_rest(False)", "Keep all remaining"),
        Binding("escape", "back", "Abandon the copy"),
    ]

    def __init__(self, plan):
        """Keep the plan; start at the first difference."""
        super().__init__()
        self.plan = plan
        self.at = 0

    def compose(self) -> ComposeResult:
        """Lay out header, difference, footer."""
        yield Header()
        with VerticalScroll():
            yield Static(id="difference")
        yield Footer()

    def on_mount(self):
        """Show the first difference."""
        self.show()

    def show(self):
        """Show the current difference: the value now and the incoming value."""
        each = self.plan.differences[self.at]
        where = "column" if each.kind == "column" else "dataset-level answer"
        lines = [f"[{self.at + 1}/{len(self.plan.differences)}] {where} {each.name}",
                 "", f"{self.plan.target.name} has now:"]
        lines += [f"    {line}" for line in describe_values(each.current)]
        lines += ["", f"copy from {self.plan.source.name} would set:"]
        lines += [f"    {line}" for line in describe_values(each.incoming)]
        lines += ["", "u use the copy    k keep current    a use the copy for all remaining"
                      "    x keep all remaining"]
        self.query_one("#difference", Static).update(indented(lines))

    def action_decide(self, take):
        """Decide the current difference; close after the last."""
        self.plan.differences[self.at].take_incoming = take
        self.at += 1
        if self.at >= len(self.plan.differences):
            self.dismiss(True)
        else:
            self.show()

    def action_decide_rest(self, take):
        """Decide this and every remaining difference the same way."""
        for each in self.plan.differences[self.at:]:
            each.take_incoming = take
        self.dismiss(True)

    def action_back(self):
        """Clear every decision and abandon the copy."""
        for each in self.plan.differences:
            each.take_incoming = None
        self.dismiss(False)
