# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The canonical keymap: every binding in the workbench, as data.

This module is the single source of truth for input bindings. A binding that is
not in ACTIONS is not implemented. Nothing outside this module may compare a key
event against a literal -- call action_for() instead.
"""
import FreeCAD
from PySide import QtCore, QtGui

_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/Fields/Keymap"

# Contexts, most specific first. action_for() walks them in this order and the
# first context that both applies and binds the event wins.
CTX_MODAL  = "modal"    # a modal transform owns the keyboard
CTX_EDIT   = "edit"     # tool active, _is_editing True
CTX_TOOL   = "tool"     # tool active
CTX_GLOBAL = "global"   # no tool active
CONTEXT_ORDER = (CTX_MODAL, CTX_EDIT, CTX_TOOL, CTX_GLOBAL)

# (action_id, default_binding, context, description)
ACTIONS = (
    # -- Global (no tool active) --
    ("global.edit_mode",      "Tab",       CTX_GLOBAL, "Enter edit mode for the selection"),
    ("global.edit_mode_alt",  "E",         CTX_GLOBAL, "Enter edit mode (alias)"),
    ("global.context_menu",   "D",         CTX_GLOBAL, "Show the Fields context menu"),
    ("global.toggle_group",   "Q",         CTX_GLOBAL, "Toggle Additive/Subtractive on the selection"),
    ("global.translate",      "T",         CTX_GLOBAL, "Start the Translate tool"),
    ("global.maximize_view",  "Ctrl+Space",CTX_GLOBAL, "Toggle viewport maximize"),

    # -- Tool active --
    ("tool.cancel",           "Esc",       CTX_TOOL,   "Cancel the tool and restore state"),
    ("tool.commit",           "Return",    CTX_TOOL,   "Commit and finish the tool"),
    ("tool.toggle_edit",      "Tab",       CTX_TOOL,   "Toggle edit mode"),
    ("tool.menu",             "D",         CTX_TOOL,   "Show the tool options menu"),
    ("tool.snap_menu",        "S",         CTX_TOOL,   "Show the snapping menu (outside edit mode)"),
    ("tool.toggle_group",     "Q",         CTX_TOOL,   "Toggle Additive/Subtractive on the selection"),

    # -- Edit mode --
    ("edit.grab",             "G",         CTX_EDIT,   "Start a modal grab (translate)"),
    ("edit.rotate",           "R",         CTX_EDIT,   "Start a modal rotate"),
    ("edit.scale",            "S",         CTX_EDIT,   "Start a modal scale"),
    ("edit.constrain_x",      "X",         CTX_EDIT,   "Toggle the X axis constraint"),
    ("edit.constrain_y",      "Y",         CTX_EDIT,   "Toggle the Y axis constraint"),
    ("edit.constrain_z",      "Z",         CTX_EDIT,   "Toggle the Z axis constraint"),
    ("edit.constrain_yz",     "Shift+X",   CTX_EDIT,   "Toggle the YZ plane constraint"),
    ("edit.constrain_xz",     "Shift+Y",   CTX_EDIT,   "Toggle the XZ plane constraint"),
    ("edit.constrain_xy",     "Shift+Z",   CTX_EDIT,   "Toggle the XY plane constraint"),

    # -- Modal transform active --
    ("modal.commit",          "Return",    CTX_MODAL,  "Commit the modal transform"),
    ("modal.cancel",          "Esc",       CTX_MODAL,  "Cancel the modal transform"),
    ("modal.lock_x",          "X",         CTX_MODAL,  "Lock to the world/local X axis"),
    ("modal.lock_y",          "Y",         CTX_MODAL,  "Lock to the world/local Y axis"),
    ("modal.lock_z",          "Z",         CTX_MODAL,  "Lock to the world/local Z axis"),
    ("modal.lock_yz",         "Shift+X",   CTX_MODAL,  "Lock to the YZ plane"),
    ("modal.lock_xz",         "Shift+Y",   CTX_MODAL,  "Lock to the XZ plane"),
    ("modal.lock_xy",         "Shift+Z",   CTX_MODAL,  "Lock to the XY plane"),
)

# Actions whose binding must not be rebound: they are structural, and the
# settings editor greys them out rather than pretending they are free.
LOCKED_ACTIONS = frozenset({"tool.cancel", "tool.commit", "modal.commit", "modal.cancel"})

_DEFAULTS = {a: b for a, b, _c, _d in ACTIONS}
_CONTEXTS = {a: c for a, _b, c, _d in ACTIONS}
_DESCRIPTIONS = {a: d for a, _b, _c, d in ACTIONS}


def default_binding(action):
    """The shipped binding for `action`, ignoring user overrides."""
    return _DEFAULTS[action]


def context_of(action):
    return _CONTEXTS[action]


def describe(action):
    return _DESCRIPTIONS[action]


def binding_for(action):
    """The binding in force: the user override if set, else the default."""
    stored = FreeCAD.ParamGet(_PARAM_PATH).GetString(action, "")
    return stored if stored else _DEFAULTS[action]


def set_binding(action, binding):
    """Store a user override. Passing the default (or "") clears the override."""
    p = FreeCAD.ParamGet(_PARAM_PATH)
    if not binding or binding == _DEFAULTS[action]:
        p.RemString(action)
    else:
        p.SetString(action, str(binding))


def reset_all():
    """Drop every override, returning the whole map to defaults."""
    p = FreeCAD.ParamGet(_PARAM_PATH)
    for action in _DEFAULTS:
        p.RemString(action)


def _key_combo_int(combo):
    """Convert QKeySequence element (int in Qt5, QKeyCombination in Qt6), enum, or flag to int."""
    if combo is None:
        return 0
    if hasattr(combo, "toCombined"):
        return int(combo.toCombined())
    if hasattr(combo, "value"):
        return int(combo.value)
    return int(combo)


def _event_sequence(event_dict):
    """Normalise a Fields event_dict to a QKeySequence-comparable int.

    Only Shift/Ctrl/Alt/Meta are kept: the keypad and auto-repeat bits vary by
    platform and would make an otherwise-equal binding miss.
    """
    key = event_dict.get("Key")
    if key is None:
        return None
    mods = event_dict.get("Modifiers")
    if mods is None:
        mods = QtCore.Qt.NoModifier
    keep = (_key_combo_int(QtCore.Qt.ShiftModifier) | _key_combo_int(QtCore.Qt.ControlModifier)
            | _key_combo_int(QtCore.Qt.AltModifier) | _key_combo_int(QtCore.Qt.MetaModifier))
    return _key_combo_int(key) | (_key_combo_int(mods) & keep)


def action_for(event_dict, contexts):
    """Resolve `event_dict` to an action id.

    `contexts` is an iterable of the contexts that currently apply, e.g.
    (CTX_MODAL, CTX_TOOL). They are tried in CONTEXT_ORDER, most specific first,
    so a modal X lock beats the edit-mode X constraint without either side
    needing to know about the other. Returns None if nothing binds the event.
    """
    seq = _event_sequence(event_dict)
    if seq is None:
        return None
    active = [c for c in CONTEXT_ORDER if c in contexts]
    for ctx in active:
        for action, _b, a_ctx, _d in ACTIONS:
            if a_ctx != ctx:
                continue
            b = binding_for(action)
            qseq = QtGui.QKeySequence(b)
            if qseq.isEmpty():
                continue
            if _key_combo_int(qseq[0]) == seq:
                return action
    return None


def find_conflicts():
    """Return [(binding, [action, action, ...]), ...] for same-context clashes.

    Cross-context sharing is legal by design -- edit.scale and tool.snap_menu are
    both S, and CONTEXT_ORDER decides. Only a clash inside one context is a bug.
    """
    by_ctx_seq = {}
    for action, _b, ctx, _d in ACTIONS:
        b = binding_for(action)
        seq = QtGui.QKeySequence(b)
        if seq.isEmpty():
            continue
        by_ctx_seq.setdefault((ctx, _key_combo_int(seq[0])), []).append(action)
    return [(binding_for(actions[0]), actions)
            for actions in by_ctx_seq.values() if len(actions) > 1]
