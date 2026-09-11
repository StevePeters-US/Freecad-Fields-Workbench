# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
commands/cmd_sdf_export.py

Export an SDF field to a meshed Part.Shape solid.
"""

import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtWidgets
from freecad.fields.core import fld_logger
from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR
from freecad.fields.core.fld_mesh_to_shape import _triangles_to_shape
import numpy as np


class CommandFldSdfToShape:
    """Export a Fields SDF object to a triangulated Part.Shape."""

    def GetResources(self):
        return {
            'Pixmap': 'SDFToShape',
            'MenuText': 'SDF to Shape',
            'ToolTip': (
                'Generate a triangle mesh from the selected SDF object\n'
                'and create a Part.Shape solid.\n\n'
                'Meshing parameters are configured locally in the export dialog.'
            ),
        }

    def IsActive(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) != 1:
            return False
        return getattr(sel[0], "ShapeType", None) == "sdf"

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) != 1:
            fld_logger.error("SDF to Shape: Select exactly one SDF object.")
            return

        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        field = (proxy.get_sdf_field(obj) if (proxy and hasattr(proxy, "get_sdf_field"))
                 else getattr(proxy, "SdfField", None)) if proxy else None
        if field is None:
            fld_logger.error(f"SDF to Shape: '{obj.Label}' has no SdfField.")
            return

        # Show local export dialog
        from PySide import QtWidgets
        dlg = _ExportDialog(FreeCADGui.getMainWindow(), obj.Label)
        if not dlg.exec_():
            return

        params = dlg.get_params()
        
        try:
            from freecad.fields.core.fld_mesher import get_active_mesher

            cell_size = params['cell_size']
            m_type = params['meshing_type']
            mesher = get_active_mesher(type_override=m_type)

            fld_logger.info(
                f"SDF to Shape: meshing '{obj.Label}' at {cell_size:.2f} mm..."
            )
            result = mesher.mesh(field, **params)
            if result is None:
                fld_logger.error("SDF to Shape: mesher returned None.")
                return

            flat_verts, flat_idx = result

            # Convert flat triangle arrays to Part.Shape via Mesh
            shape = _triangles_to_shape(flat_verts, flat_idx)
            if shape is None or shape.isNull():
                fld_logger.error(
                    "SDF to Shape: failed to build Part.Shape from mesh."
                )
                return

            # Create a new Part::Feature with the solid shape
            doc = FreeCAD.activeDocument()
            new_obj = doc.addObject("Part::Feature", f"{obj.Label}_Mesh")
            new_obj.Shape = shape

            # Style: match the orange Fields look
            if hasattr(new_obj, "ViewObject") and new_obj.ViewObject:
                new_obj.ViewObject.ShapeColor = DEFAULT_ADDITIVE_COLOR
                try:
                    new_obj.ViewObject.DisplayMode = "Shaded"
                except Exception as e:
                    fld_logger.debug(f"CommandFldSdfToShape: Failed to set DisplayMode: {e}")

            QtCore.QTimer.singleShot(0, doc.recompute)
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(new_obj)
            n_tris = len(flat_idx) // 4
            fld_logger.info(
                f"SDF to Shape: created '{new_obj.Label}' "
                f"({n_tris} triangles, cell_size={cell_size:.2f} mm)"
            )

        except Exception as e:
            import traceback
            fld_logger.error(f"SDF to Shape failed: {e}\n{traceback.format_exc()}")


class _ExportDialog(QtWidgets.QDialog):
    def __init__(self, parent, label):
        super().__init__(parent)
        self.setWindowTitle(f"Mesh Export: {label}")
        self.setMinimumWidth(350)
        
        layout = QtWidgets.QFormLayout(self)
        
        # Meshing Type
        self._type_combo = QtWidgets.QComboBox()
        self._type_combo.addItems([
            "Marching Cubes",
            "Surface Nets",
            "Dual Contouring"
        ])
        layout.addRow("Algorithm:", self._type_combo)
        
        # Resolution (Cell Size)
        self._res_spin = QtWidgets.QDoubleSpinBox()
        self._res_spin.setRange(0.01, 10.0)
        self._res_spin.setSingleStep(0.1)
        self._res_spin.setDecimals(2)
        self._res_spin.setValue(1.0)
        self._res_spin.setToolTip("Cell size in mm (smaller = more detail). 0.1mm is high quality.")
        layout.addRow("Resolution (mm):", self._res_spin)
        
        # Decimate
        self._decimate_check = QtWidgets.QCheckBox()
        self._decimate_check.setChecked(True)
        self._decimate_check.setToolTip("Remove redundant triangles from flat areas.")
        layout.addRow("Decimate Mesh:", self._decimate_check)

        # Deduplicate 
        self._dedup_check = QtWidgets.QCheckBox()
        self._dedup_check.setChecked(True)
        self._dedup_check.setToolTip("Merge coincidental vertices.")
        layout.addRow("Deduplicate Vertices:", self._dedup_check)

        # Buttons
        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def get_params(self):
        return {
            'meshing_type': self._type_combo.currentIndex(),
            'cell_size': self._res_spin.value(),
            'decimate': self._decimate_check.isChecked(),
            'deduplicate': self._dedup_check.isChecked()
        }


FreeCADGui.addCommand('Fields_SDFToShape', CommandFldSdfToShape())
