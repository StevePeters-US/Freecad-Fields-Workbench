# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.tools.noise_preset_dialogs

The prompt a graph-replacing action shows before it discards an edited graph (NV-018).
"""
from PySide import QtWidgets

from freecad.fields.core import fld_logger


def _compiled(graph):
    from freecad.fields.core.gui.node_editor.nodes import compile_graph
    res = compile_graph(graph) if graph else None
    return res.formula if res and not res.err else None


def graph_is_saved(label, formula):
    """True when `formula` is what the built-in template `label` compiles to, or what
    some user preset compiles to. An empty formula has nothing to lose."""
    if not formula:
        return True
    from freecad.fields.core.gui.node_editor.nodes import get_template_graph
    from freecad.fields.core.objects.fld_noise_user_presets import (
        get_user_preset, user_preset_names)
    tmpl_g = get_template_graph(label)
    if tmpl_g and formula == _compiled(tmpl_g):
        return True
    return any(formula == _compiled(get_user_preset(n)[0]) for n in user_preset_names())


def confirm_graph_replace(parent, obj, graph, formula):
    """Ask before replacing an unsaved graph. `graph`/`formula` are what would be lost:
    the object's own for the panel, or the editor scene's (which may be ahead of the
    object when live update is off). Returns True to go ahead, False to abort."""
    if graph_is_saved(getattr(obj, "NoiseType", "Custom"), formula):
        return True
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle("Unsaved noise graph")
    box.setText("This noise graph has edits that aren't saved as a preset.\n"
                "Switching type replaces it.")
    save = box.addButton("Save as Preset…", QtWidgets.QMessageBox.AcceptRole)
    discard = box.addButton("Discard", QtWidgets.QMessageBox.DestructiveRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(save)
    box.exec_()
    clicked = box.clickedButton()
    if clicked is discard:
        return True
    if clicked is not save:
        return False
    return _save_as_preset(parent, obj, graph)


def _save_as_preset(parent, obj, graph):
    """Ask for a name until it is valid, or the user cancels (False)."""
    from freecad.fields.core.objects.fld_noise_user_presets import (
        save_user_preset, spec_from_object, user_preset_names, validate_name)
    name = ""
    while True:
        name, ok = QtWidgets.QInputDialog.getText(
            parent, "Save Noise Preset", "Preset name:", text=name)
        if not ok:
            return False
        try:
            name = validate_name(name)
        except ValueError as e:
            QtWidgets.QMessageBox.warning(parent, "Save Noise Preset", str(e))
            continue
        if name in user_preset_names():
            r = QtWidgets.QMessageBox.question(
                parent, "Save Noise Preset", f"Replace the preset {name!r}?")
            if r != QtWidgets.QMessageBox.Yes:
                continue
        save_user_preset(name, graph, spec_from_object(obj))
        fld_logger.info(f"Saved noise preset {name!r}")
        return True
