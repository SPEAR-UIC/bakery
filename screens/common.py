"""bakery/screens/common.py - helpers and base screens shared by all screens

TextScreen: a page of text, escape to go back. ReportScreen: lines from a bulk change.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static


def indented(lines):
    """Join lines, each indented two spaces, after a blank line."""
    return "\n" + "\n".join(f"  {line}" for line in lines)


def row_key(table):
    """Return the key of the row under the cursor, or None."""
    if not table.row_count:
        return None
    key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
    return key.value if key else None


SQLITE_BANNER = ("YOU ARE IN SQLITE: a local file, not the MySQL database.  "
                 "Set BAKERY_DATABASE_URL or pass --database to use MySQL.")


def database_banner(app):
    """Return the SQLite warning banner, or an empty one for any other database."""
    banner = Static(SQLITE_BANNER if app.database.is_sqlite else "", id="sqlite")
    banner.display = app.database.is_sqlite
    return banner


def has_machine(app, source_file):
    """Return whether the file has a dataset; warn if it does not."""
    if source_file.dataset is None:
        app.notify("Say which machine this came from first.", severity="warning")
        return False
    return True


class TextScreen(Screen):
    """A page of text; escape goes back. Subclasses set text() and TEXT_ID."""

    BINDINGS = [Binding("escape", "back", "Back")]
    TEXT_ID = "text"

    def compose(self) -> ComposeResult:
        """Lay out header, text, footer."""
        yield Header()
        with VerticalScroll():
            yield Static(self.text(), id=self.TEXT_ID)
        yield Footer()

    def text(self):
        """Return the page text."""
        raise NotImplementedError

    def action_back(self):
        """Close the screen."""
        self.dismiss(None)


class ReportScreen(TextScreen):
    """The lines a bulk change reported."""

    BINDINGS = TextScreen.BINDINGS + [Binding("enter", "back", "Back")]
    TEXT_ID = "report"

    def __init__(self, lines):
        """Keep the lines to show."""
        super().__init__()
        self.lines = lines

    def text(self):
        """Return the lines, indented."""
        return indented(self.lines)
