# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/tree_item_status.py

Qt tree-widget error coloring, pulled out of `scene_volume.py`'s bake failure
path because it is pure GUI bookkeeping unrelated to GL/volume state.
"""
from freecad.fields.core import fld_logger


def _mark_tree_item_failed(label, failed=True):
    """Mark the tree item with an error color if FreeCADGui is available."""
    if not label:
        return
    try:
        import FreeCAD
        obj = None
        parts = label.split(".", 1)
        if len(parts) == 2:
            doc = FreeCAD.getDocument(parts[0])
            obj = doc.getObject(parts[1]) if doc else None
            name = parts[1]
        else:
            doc = getattr(FreeCAD, "ActiveDocument", None)
            obj = doc.getObject(label) if doc else None
            name = label
        if obj and hasattr(obj, "ViewObject") and obj.ViewObject:
            if hasattr(obj.ViewObject, "TextColor"):
                obj.ViewObject.TextColor = (1.0, 0.2, 0.2) if failed else (0.0, 0.0, 0.0)
            if hasattr(obj.ViewObject, "TreeTextColor"):
                obj.ViewObject.TreeTextColor = (1.0, 0.2, 0.2) if failed else (0.0, 0.0, 0.0)

        import FreeCADGui
        from PySide import QtGui, QtCore
        mw = FreeCADGui.getMainWindow() if hasattr(FreeCADGui, "getMainWindow") else None
        if mw and name:
            tree = mw.findChild(QtGui.QTreeWidget, "TreeWidget") or mw.findChild(QtGui.QTreeWidget)
            if tree and hasattr(tree, "findItems"):
                items = tree.findItems(name, QtCore.Qt.MatchContains | QtCore.Qt.MatchRecursive)
                color = QtGui.QColor(255, 50, 50) if failed else QtGui.QColor(0, 0, 0)
                brush = QtGui.QBrush(color)
                for item in items:
                    if hasattr(item, "setForeground"):
                        item.setForeground(0, brush)
    except Exception as e:
        fld_logger.render_debug(f"SceneVolume: _mark_tree_item_failed for {label} failed: {e}")
