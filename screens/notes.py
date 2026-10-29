"""bakery/screens/notes.py - the notes recorded in this database

--notes reads Croissant files on disk instead.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from notes import by_dataset, notes_recorded_in
from screens.common import TextScreen

NO_NOTES = """
  No notes recorded yet.

  A note is the thing somebody should come back to: a count that
  looks wrong, a meaning that is a guess, a join to check. Put one
  on any column while working, and it is written into the published
  description as "NOTE: ..." so a reader sees it too.

  To list the notes in Croissant files on disk instead:
      python3 bakery.py --notes <directory>"""


class NotesScreen(TextScreen):
    """The notes recorded in the database, by dataset."""
    TEXT_ID = "notes"

    def text(self):
        """Return the notes by dataset, or NO_NOTES if there are none."""
        found = notes_recorded_in(self.app.session)
        if not found:
            return NO_NOTES
        lines = [f"\n  {len(found)} note(s) recorded."]
        for dataset, notes in by_dataset(found).items():
            lines.append(f"\n  {dataset}")
            for note in notes:
                marker = "  (column not finished)" if note.unfinished else ""
                lines += [f"      {note.where}{marker}", f"          {note.text}"]
        return "\n".join(lines)
