"""bakery/screens/actions.py - changing a file's machine and copying answers in

Mixin for the file list and review screens; the host screen needs action_refresh().

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

from screens.common import ReportScreen
from screens.copy import CopySourceScreen
from screens.machine import MachineScreen


class FileActions:
    """Actions on one file, for a screen with action_refresh()."""

    def change_machine_of(self, source_file):
        """Open the machine screen to change the file's machine."""
        self.app.push_screen(MachineScreen(source_file, changing=True), self.after_change)

    def copy_answers_into(self, source_file):
        """Open the copy screen to copy answers into the file."""
        self.app.push_screen(CopySourceScreen(source_file), self.after_change)

    def after_change(self, report):
        """Refresh, then show the report if there is one."""
        self.action_refresh()
        if report:
            self.app.push_screen(ReportScreen(report))
