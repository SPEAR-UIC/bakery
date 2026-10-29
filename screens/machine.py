"""bakery/screens/machine.py - a file's machine, grouping files as one format, the machines

MachineScreen: confirm or change a file's machine; with changing, answers follow the file.
GroupScreen: put ticked files in one dataset, by name.
SameAsScreen: put ticked files in a chosen file's dataset: its machine and answers.
SetNameScreen: name or rename a set; s asks for one when it makes a new set.
MachinesScreen: each machine and its datasets.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from sqlalchemy import select
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Label, OptionList, Static

from models import Dataset, Machine, SourceFile
from outstanding import confirm_machine, files_for_listing
from screens.common import TextScreen, row_key
from transfer import change_machine, group_files, rename_set, same_as


def rows_text(source_file):
    """Return the row count, marked as a lower bound after a preview."""
    rows, whole = source_file.rows, source_file.rows_are_whole_file
    if rows is None:
        return "an unrecorded number of rows"
    if whole is False:
        return f"at least {rows:,} rows (parbake read a preview)"
    if whole is None:
        return f"{rows:,} rows read (whether that is the whole file was not recorded)"
    return f"{rows:,} rows"


class MachineScreen(Screen):
    """Which machine a file came from, with the evidence for the suggestion."""

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("ctrl+s", "confirm", "Confirm", priority=True),
    ]

    def __init__(self, source_file, changing=False):
        """Keep the file and whether its machine is being changed."""
        super().__init__()
        self.source_file = source_file
        self.changing = changing

    def compose(self) -> ComposeResult:
        """Lay out title, evidence, known machines, name input, footer."""
        yield Header()
        with VerticalScroll():
            title = (f"  Change the machine for {self.source_file.name}"
                     if self.changing else f"  {self.source_file.name}")
            yield Static(title, id="title")
            yield Static(self.evidence(), id="evidence")
            yield Label("  Machines already named (enter to pick one):")
            yield OptionList(id="machines")
            yield Label("  ...or type a name for a machine not in the list:")
            yield Input(placeholder="polaris", id="typed")
        yield Footer()

    def evidence(self):
        """Return the file's size, suggested machine and dates as text."""
        source_file = self.source_file
        text = (f"  {source_file.column_count} columns, {rows_text(source_file)}.\n"
                f"  Suggested machine: "
                f"{source_file.machine_suggestion or 'none; nothing in the file names one'}")
        if source_file.covers_from:
            text += (f"\n  Covers {source_file.covers_from} to {source_file.covers_to}, "
                     "read off the filename.")
        if self.changing and source_file.machine is not None:
            text += (f"\n  Now confirmed as: {source_file.machine.name}, in "
                     f"{source_file.dataset.name}.\n"
                     "  Its answers follow it to the machine you choose.")
        return text

    def on_mount(self):
        """List the known machines and prefill the name."""
        options = self.query_one("#machines", OptionList)
        self.known = list(self.app.session.scalars(select(Machine).order_by(Machine.name)))
        for machine in self.known:
            # FIXME: a retired machine has no current iteration, so shows "no hardware".
            profile = machine.current_iteration
            options.add_option(f"{machine.name}" + (
                f"  ({profile.vendor}, {len(profile.partitions)} partition(s))"
                if profile else "  (no hardware recorded yet)"))
        if self.changing and self.source_file.machine is not None:
            self.query_one("#typed", Input).value = self.source_file.machine.name
        elif self.source_file.machine_suggestion:
            self.query_one("#typed", Input).value = self.source_file.machine_suggestion

    @on(OptionList.OptionSelected)
    def pick(self, event):
        """Take the picked machine and confirm."""
        self.query_one("#typed", Input).value = self.known[event.option_index].name
        self.action_confirm()

    @on(Input.Submitted)
    def typed(self, event):
        """Confirm the typed name."""
        self.action_confirm()

    def action_confirm(self):
        """Confirm or change the machine; dismiss with True or the change report lines."""
        name = self.query_one("#typed", Input).value.strip()
        if not name:
            return
        if self.changing:
            self.dismiss(change_machine(self.app.session, self.source_file, name).lines())
            return
        machine, dataset = confirm_machine(self.app.session, self.source_file, name)
        self.app.notify(f"{self.source_file.name} is {machine.name}; "
                        f"its columns belong to {dataset.name}.")
        self.dismiss(True)

    def action_cancel(self):
        """Close the screen without a machine."""
        self.dismiss(None if self.changing else False)


class GroupScreen(Screen):
    """Put ticked files in one dataset: one format, answered once for all."""

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("ctrl+s", "confirm", "Group them", priority=True),
    ]
    EXAMPLES = 3

    def __init__(self, files):
        """Keep the files to group."""
        super().__init__()
        self.files = files

    def compose(self) -> ComposeResult:
        """Lay out summary, known datasets, machine and name inputs, footer."""
        yield Header()
        with VerticalScroll():
            yield Static(self.summary(), id="which")
            yield Label("  Existing datasets (enter to add the files to one):")
            yield OptionList(id="datasets")
            yield Label("  Machine:")
            yield Input(placeholder="polaris", id="machine")
            yield Label("  Name of the format (the dataset):")
            yield Input(placeholder="e.g. POLARIS_SYSLOG", id="name")
        yield Footer()

    def summary(self):
        """Return the files to group, the first few by name."""
        names = [each.name for each in self.files[:self.EXAMPLES]]
        more = len(self.files) - len(names)
        return (f"  Group {len(self.files)} file(s) as one format:\n"
                + "\n".join(f"    {name}" for name in names)
                + (f"\n    ... and {more} more" if more > 0 else "")
                + "\n  What is answered for one of them will apply to all of them.")

    def on_mount(self):
        """List the datasets; prefill from a ticked file already in one."""
        self.known = list(self.app.session.scalars(select(Dataset).order_by(Dataset.name)))
        options = self.query_one("#datasets", OptionList)
        for dataset in self.known:
            options.add_option(f"{dataset.name}  ({dataset.machine.name}, "
                               f"{len(dataset.files)} file(s))")
        model = next((each for each in self.files if each.dataset is not None), None)
        if model is not None:
            self.fill(model.dataset)
        else:
            self.query_one("#machine", Input).value = self.files[0].machine_suggestion or ""
        self.set_focus(self.query_one("#name", Input))

    def fill(self, dataset):
        """Fill machine and name from the dataset."""
        self.query_one("#machine", Input).value = dataset.machine.name
        self.query_one("#name", Input).value = dataset.name

    @on(OptionList.OptionSelected)
    def pick(self, event):
        """Fill from the picked dataset."""
        self.fill(self.known[event.option_index])
        self.set_focus(self.query_one("#name", Input))

    @on(Input.Submitted)
    def typed(self, event):
        """Confirm."""
        self.action_confirm()

    def action_confirm(self):
        """Group the files; dismiss with the report lines."""
        machine = self.query_one("#machine", Input).value.strip()
        name = self.query_one("#name", Input).value.strip()
        if not machine or not name:
            self.app.notify("Both a machine and a name are needed.", severity="warning")
            return
        self.dismiss(group_files(self.app.session, self.files, machine, name).lines())

    def action_cancel(self):
        """Close the screen without grouping."""
        self.dismiss(None)


class SameAsScreen(Screen):
    """Pick a file whose machine and answers the ticked files take."""

    BINDINGS = [
        Binding("enter", "choose", "Same as this file", priority=True),
        Binding("escape", "cancel", "Back"),
    ]

    def __init__(self, files):
        """Keep the ticked files."""
        super().__init__()
        self.files = files

    def compose(self) -> ComposeResult:
        """Lay out header, title, the list of files with answers, footer."""
        yield Header()
        yield Static(id="title")
        yield DataTable(id="sources", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self):
        """List files that have a machine, most answered first."""
        self.query_one("#title", Static).update(
            f"  Give {len(self.files)} ticked file(s) the machine and answers of the file you pick.\n"
            "  They join its dataset: answers given later on any of them apply to all.\n")
        table = self.query_one("#sources", DataTable)
        table.add_columns("file", "dataset", "progress")
        rows = [(each, coverage) for each, coverage in files_for_listing(self.app.session)
                if each.dataset is not None]
        for source_file, coverage in sorted(rows, key=lambda row: (-row[1].share, row[0].name)):
            table.add_row(source_file.name, source_file.dataset.name, coverage.describe(),
                          key=str(source_file.id))
        table.focus()

    @on(DataTable.RowSelected)
    def row_chosen(self, event):
        """Use the selected file."""
        self.action_choose()

    def action_choose(self):
        """Move the ticked files into the chosen file's dataset."""
        key = row_key(self.query_one("#sources", DataTable))
        if key is None:
            return
        source = self.app.session.get(SourceFile, int(key))
        is_new_set = len(source.dataset.files) == 1
        self.lines = same_as(self.app.session, self.files, source).lines()
        if is_new_set:
            # A single file's dataset is named after the file; name the set now.
            self.app.push_screen(SetNameScreen(source.dataset, f"{source.machine.name.upper()}_"),
                                 self.after_naming)
        else:
            self.dismiss(self.lines)

    def after_naming(self, _):
        """Close with the move's report, named or not."""
        self.dismiss(self.lines + [f"The set is named {self.files[0].dataset.name}."])

    def action_cancel(self):
        """Close without changing anything."""
        self.dismiss(None)


class SetNameScreen(Screen):
    """Name or rename a set; ctrl+s or enter saves."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("escape", "cancel", "Back"),
    ]

    def __init__(self, dataset, suggestion=None):
        """Keep the set and the name to start from."""
        super().__init__()
        self.dataset = dataset
        self.suggestion = suggestion

    def compose(self) -> ComposeResult:
        """Lay out header, title, name, problems, footer."""
        yield Header()
        with VerticalScroll():
            yield Static(id="title")
            yield Label("  Name of the set (its Croissant is <name>.baked.json):")
            yield Input(id="name")
            yield Static(id="problems")
        yield Footer()

    def on_mount(self):
        """Show the set and fill the name."""
        dataset = self.dataset
        self.query_one("#title", Static).update(
            f"  {len(dataset.files)} file(s) on {dataset.machine.name}, now named {dataset.name}\n")
        name = self.query_one("#name", Input)
        name.value = self.suggestion or dataset.name
        name.focus()

    @on(Input.Submitted)
    def submitted(self, event):
        """Save on enter."""
        self.action_save()

    def action_save(self):
        """Rename the set, or show why not."""
        try:
            rename_set(self.app.session, self.dataset, self.query_one("#name", Input).value)
        except ValueError as problem:
            self.query_one("#problems", Static).update(f"  {problem}")
            return
        self.dismiss(True)

    def action_cancel(self):
        """Close without renaming."""
        self.dismiss(None)


class MachinesScreen(TextScreen):
    """Each machine, its hardware over time, and its datasets."""

    TEXT_ID = "machines"

    def text(self):
        """Return each machine with its iterations, partitions and datasets."""
        lines = []
        for machine in self.app.session.scalars(select(Machine).order_by(Machine.name)):
            lines.append(f"\n  {machine.name}   {machine.organization or ''}")
            if not machine.iterations:
                lines.append("      no hardware recorded; seed a documentation file, or type it in")
            for iteration in machine.iterations:
                lines.append(f"      {iteration.label}: {iteration.span}")
                lines.append(f"          {iteration.vendor or '?'} / "
                             f"{iteration.architecture or '?'} / {iteration.scheduler or '?'}")
                for partition in iteration.partitions:
                    lines += [f"          [{partition.name}] {partition.node_count or '?'} nodes, "
                              f"{partition.rack_count or '?'} racks",
                              f"              cpu {partition.cpu or '?'}",
                              f"              gpu {partition.gpu or '?'}"]
            lines += [f"      dataset {dataset.name}: {len(dataset.files)} file(s), "
                      f"{len(dataset.field_docs)} column(s) described"
                      for dataset in machine.datasets]
        return "\n".join(lines) or "\n  No machines yet. Confirm one against a file."
