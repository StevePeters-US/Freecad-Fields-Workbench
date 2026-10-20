# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The menu tree: every operation the workbench offers, as data.

A menu is a list of MenuItem and SEPARATOR. FldMenuManager renders it as a QMenu
today; a radial menu will render the same list later, so nothing here knows
about Qt.
"""
from dataclasses import dataclass
from typing import Callable, Optional

SEPARATOR = None


@dataclass
class MenuItem:
    """One menu entry: a leaf that runs an action, or a submenu.

    Attributes:
        label: Text shown to the user.
        action: Zero-argument callable run when the leaf is chosen. None on a submenu.
        children: The submenu's entries (MenuItem or SEPARATOR). None on a leaf.
        checked: None for a plain entry; True or False draws a check mark showing state.
        enabled: False greys the entry out, and its action never runs.
        icon: Path to an icon file, or None.
        shortcut: Optional QKeySequence portable string (e.g. "Return", "Del", "Shift+A").
    """
    label: str
    action: Optional[Callable[[], None]] = None
    children: Optional[list] = None
    checked: Optional[bool] = None
    enabled: bool = True
    icon: Optional[str] = None
    shortcut: Optional[str] = None

    def __post_init__(self):
        if (self.action is None) == (self.children is None):
            raise ValueError(
                f"MenuItem {self.label!r} needs exactly one of action and children")
        if self.children is not None and self.shortcut is not None:
            raise ValueError(
                f"Submenu {self.label!r} cannot have a shortcut: only leaves run")

    @property
    def is_submenu(self):
        return self.children is not None
