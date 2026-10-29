#!/usr/bin/env python3
"""bakery/bakery.py - finish par-baked Croissants: say which machine, describe the columns

    python3 bakery.py                          work through the database
    python3 bakery.py --import <parbake out>   read par-baked files in first
    python3 bakery.py --seed <finished files>  read finished work in first
    python3 bakery.py --bake [--out DIR]       write every Croissant and stop
    python3 bakery.py --notes <directory>      list every note and stop
    python3 bakery.py --database <url>         somewhere other than ./bakery.sqlite
Answers are saved as they are given.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import argparse
from pathlib import Path

from textual.app import App

from baking import bake_everything
from database import Database
from importing import import_target
from notes import find_notes, report
from screens import SetListScreen
from seeding import seed_from


class BakeryApp(App):
    """The terminal app; starts on the file sets."""

    TITLE = "bakery"
    SUB_TITLE = "finishing par-baked Croissants"
    CSS = """
    #title { text-style: bold; padding: 1 0 0 0; }
    #heading { text-style: bold; padding: 1 0 0 0; }
    #evidence { color: $text-muted; padding: 0 0 1 0; }
    #offer { color: $accent; padding: 0 0 1 0; }
    #where, #hint { color: $text-muted; }
    #sqlite { background: $error; color: $text; text-style: bold; padding: 1 2;
              content-align: center middle; width: 100%; }
    DataTable { height: 1fr; }
    Select { width: 24; }
    """

    def __init__(self, database, output_directory="baked"):
        """Keep the database, output directory, and one open session."""
        super().__init__()
        self.database = database
        self.output_directory = Path(output_directory)
        self.session = database.session()

    def on_mount(self):
        """Show the file sets; warn in every header when the database is SQLite."""
        if self.database.is_sqlite:
            self.sub_title = "SQLITE, NOT MYSQL"
        self.push_screen(SetListScreen())

    def on_unmount(self):
        """Close the session."""
        self.session.close()


def print_lines(lines):
    """Print each line, indented two spaces."""
    for line in lines:
        print(f"  {line}")


def main():
    """Parse arguments; run the one-shot commands, then the app."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", help="a database URL; default ./bakery.sqlite")
    parser.add_argument("--import", dest="import_from", metavar="DIRECTORY",
                        help="read a parbake output directory in before starting")
    parser.add_argument("--seed", nargs="+", metavar="PATH",
                        help="read finished Croissants, field dictionaries and "
                             "documentation in before starting")
    parser.add_argument("--out", default="baked", metavar="DIRECTORY",
                        help="where written Croissants go (default: ./baked)")
    parser.add_argument("--bake", action="store_true",
                        help="write every dataset's Croissant, and stop")
    parser.add_argument("--notes", nargs="+", metavar="PATH",
                        help="list every note left for later in the Croissants "
                             "under these paths, and stop")
    arguments = parser.parse_args()

    if arguments.notes:
        # Reads the files, not the database: finds notes in files never imported.
        for line in report(*find_notes(*arguments.notes)):
            print(line)
        return

    # Printed, not shown in the app, so the report stays in the scrollback.
    database = Database(arguments.database)
    with database.session() as session:
        if arguments.seed:
            print_lines(seed_from(session, *arguments.seed).lines())
        if arguments.import_from:
            print_lines(import_target(session, arguments.import_from).lines())
        if arguments.bake:
            print_lines(bake_everything(session, arguments.out).lines())
            return
    BakeryApp(database, arguments.out).run()


if __name__ == "__main__":
    main()
