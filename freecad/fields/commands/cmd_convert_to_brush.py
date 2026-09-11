# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
commands/cmd_convert_to_brush.py

Command to turn the selected SDF object into a sculpt library brush.
"""
import FreeCAD
import FreeCADGui
from PySide import QtWidgets
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf.sculpt_brush import SculptBrush
from freecad.fields.core.sdf.sdf.brush_library import list_brushes, save_brush


class CommandFldConvertToBrush:
    """Converts the selected SDF object into a normalized sculpt library brush."""

    def GetResources(self):
        return {
            "Pixmap": "Fields_ConvertToBrush",
            "MenuText": "Convert to Brush",
            "ToolTip": "Turn the selected SDF object into a sculpt library brush.",
        }

    def IsActive(self):
        if not FreeCAD.ActiveDocument:
            return False
        sel = FreeCADGui.Selection.getSelection() if hasattr(FreeCADGui, "Selection") else []
        if len(sel) != 1:
            return False
        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        return proxy is not None and hasattr(proxy, "get_sdf_field")

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) != 1:
            return
        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        if proxy is None or not hasattr(proxy, "get_sdf_field"):
            return

        field = proxy.get_sdf_field(obj)
        if field is None:
            fld_logger.warn(f"cmd_convert_to_brush: could not resolve SDF field from {obj.Label}")
            return

        default_name = getattr(obj, "Label", getattr(obj, "Name", "NewBrush"))
        name, ok = QtWidgets.QInputDialog.getText(
            None, "Convert to Brush", "Brush Name:",
            QtWidgets.QLineEdit.Normal, default_name
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        if name in list_brushes():
            reply = QtWidgets.QMessageBox.question(
                None, "Overwrite Brush?",
                f"A brush named '{name}' already exists in the library. Overwrite?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return

        brush = SculptBrush.from_field(field, name, resolution=32)
        if save_brush(brush):
            fld_logger.info(f"cmd_convert_to_brush: saved brush '{name}' to library")


FreeCADGui.addCommand("Fields_ConvertToBrush", CommandFldConvertToBrush())
