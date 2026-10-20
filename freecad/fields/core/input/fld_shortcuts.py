# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Pure functions for keyboard shortcuts on menu trees (INP-B07)."""
from typing import Optional, Tuple
from PySide import QtCore, QtGui

from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_menu_tree import MenuItem, SEPARATOR


def _key_combo_int(combo):
    """Convert QKeySequence element (int in Qt5, QKeyCombination in Qt6), enum, or flag to int."""
    if combo is None:
        return 0
    if hasattr(combo, "toCombined"):
        return int(combo.toCombined())
    if hasattr(combo, "value"):
        return int(combo.value)
    return int(combo)


_MODIFIER_MASK = (
    _key_combo_int(QtCore.Qt.ShiftModifier)
    | _key_combo_int(QtCore.Qt.ControlModifier)
    | _key_combo_int(QtCore.Qt.AltModifier)
    | _key_combo_int(QtCore.Qt.MetaModifier)
)


def _normalise(combo_int):
    """Keypad Enter is Return, on both sides of a match (INP-207)."""
    key = combo_int & ~_MODIFIER_MASK
    if key == _key_combo_int(QtCore.Qt.Key_Enter):
        return (combo_int & _MODIFIER_MASK) | _key_combo_int(QtCore.Qt.Key_Return)
    return combo_int


def event_sequence(event_dict):
    """Normalise a Fields event_dict to a QKeySequence-comparable int.

    Only Shift/Ctrl/Alt/Meta are kept: the keypad and auto-repeat bits vary by
    platform and would make an otherwise-equal binding miss.
    Key_Enter normalises to Key_Return so keypad Enter hits Accept.
    """
    key = event_dict.get("Key")
    if key is None:
        return None
    k_int = _key_combo_int(key)
    mods = event_dict.get("Modifiers")
    if mods is None:
        mods = QtCore.Qt.NoModifier
    val = k_int | (_key_combo_int(mods) & _MODIFIER_MASK)
    return _normalise(val)


def sequence_of(shortcut: Optional[str]) -> Optional[int]:
    """The int for a shortcut string (QKeySequence(s)[0] through _key_combo_int), or None."""
    if not shortcut:
        return None
    try:
        ks = QtGui.QKeySequence(shortcut)
        if ks.isEmpty():
            return None
        combo = ks[0]
        val = _key_combo_int(combo)
        if val == 0:
            return None
        if val & ~_MODIFIER_MASK == _key_combo_int(QtCore.Qt.Key_unknown):
            return None
        return _normalise(val)
    except Exception as e:
        fld_logger.debug(f"[Shortcuts] sequence_of failed for {shortcut!r}: {e}")
        return None


def find(items, event_dict) -> Tuple[Optional[MenuItem], bool]:
    """Look up an event in a menu tree.

    Walks depth-first, submenus included regardless of their enabled state.
    Returns (item, matched):
      - matched is True if >= 1 leaf matches the event sequence.
      - item is that leaf if exactly 1 matches; if >= 2 match, item is None
        and fld_logger.error logs both/all labels.
      - If 0 matches, returns (None, False).
    """
    seq = event_sequence(event_dict)
    if seq is None:
        return None, False

    matches = []

    def _walk(item_list):
        for item in item_list:
            if item is SEPARATOR:
                continue
            if item.is_submenu:
                if item.children:
                    _walk(item.children)
            else:
                if item.shortcut:
                    s_int = sequence_of(item.shortcut)
                    if s_int is not None and s_int == seq:
                        matches.append(item)

    _walk(items)

    if not matches:
        return None, False
    if len(matches) == 1:
        return matches[0], True

    labels = ", ".join(repr(m.label) for m in matches)
    fld_logger.error(
        f"[Shortcuts] Duplicate shortcut {matches[0].shortcut!r} on items: {labels}")
    return None, True
