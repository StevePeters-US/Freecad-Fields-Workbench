# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.tools/modifiers — per-modifier edit tools (Twist, Bend, Lattice)."""
from contextlib import contextmanager
from PySide import QtWidgets


def axis_combo():
    c = QtWidgets.QComboBox()
    c.addItems(["X", "Y", "Z"])
    c.setCurrentIndex(2)  # default Z
    return c


@contextmanager
def block_signals(*widgets):
    """Context manager to temporarily block Qt signals on given widgets."""
    for w in widgets:
        w.blockSignals(True)
    try:
        yield
    finally:
        for w in widgets:
            w.blockSignals(False)


class BaseModifierTaskPanel:
    """Shared accept/reject/group-toggle behavior for modifier task panels."""

    def _on_group_toggled(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Group = "Subtractive" if getattr(obj, "Group", "Additive") == "Additive" else "Additive"
        self.group_btn.setText(obj.Group)
        self.tool._commit_changes()

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        self.tool._dialog_open = False
        self.tool.cancel()
        return True
