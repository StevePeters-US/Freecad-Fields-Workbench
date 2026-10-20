# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/modifiers/lattice_tool.py

Edit tool and task panel for the Lattice SDF modifier.
"""
from PySide import QtCore, QtWidgets
import numpy as np
import FreeCAD
import FreeCADGui
from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_point import FldPoint
from freecad.fields.core.objects.fld_line import FldLineSet
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase
from freecad.fields.tools.modifiers import BaseModifierTaskPanel
from freecad.fields.tools.modifiers.lattice_gizmo import LatticeGizmoMixin
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip

try:
    from pivy import coin
except ImportError:
    coin = None


class LatticeTaskPanel(BaseModifierTaskPanel):
    def __init__(self, tool):
        self.tool = tool
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(10, 10, 10, 10)

        grp = QtWidgets.QGroupBox("Lattice Parameters")
        fl = QtWidgets.QFormLayout(grp)
        layout.addWidget(grp)

        self.res_spin = QtWidgets.QSpinBox()
        self.res_spin.setRange(2, 8)
        self.res_spin.setValue(2)
        self.res_spin.setToolTip(
            rich_tooltip(
                "LatticeTaskPanel",
                QT_TRANSLATE_NOOP(
                    "LatticeTaskPanel",
                    "Control points along each axis.\n2 gives 8 points, 3 gives 27.\n<b>Default:</b> 2.",
                ),
            )
        )
        self.res_spin.valueChanged.connect(self._on_changed)
        fl.addRow("Resolution:", self.res_spin)

        self.group_btn = QtWidgets.QPushButton("Additive")
        self.group_btn.clicked.connect(self._on_group_toggled)
        fl.addRow("Group:", self.group_btn)

        layout.addStretch()
        self.update_ui()

    def update_ui(self):
        obj = self.tool._target_obj
        if not obj:
            return
        self.res_spin.blockSignals(True)
        self.res_spin.setValue(max(2, getattr(obj, "Resolution", 2)))
        self.group_btn.setText(getattr(obj, "Group", "Additive"))
        self.res_spin.blockSignals(False)

    def _on_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Resolution = self.res_spin.value()
        self.tool._resolution = obj.Resolution
        self.tool._commit_changes(force=True)
        self.tool._reconstruct_handles()


class LatticeTool(LatticeGizmoMixin, FldSdfModifierToolBase):
    SNAPSHOT_PROPERTIES = [
        ("Resolution", 2),
        ("Group", "Additive"),
    ]

    def __init__(self):
        super().__init__()
        if coin:
            self.points_root = coin.SoAnnotation()
            if self.view and self.view.getSceneGraph():
                self.view.getSceneGraph().addChild(self.points_root)
        else:
            self.points_root = None
        self.fld_points = []
        self._edge_lines = None
        self.points = []
        self._dragging_idx = None
        self._resolution = 2

    def get_command_id(self):
        return "Fields_EditObject"

    def get_handled_types(self):
        return ["FldLatticeProxy"]

    def _make_panel(self):
        return LatticeTaskPanel(self)

    def edit_object(self, obj):
        super().edit_object(obj)
        self._target_obj = obj
        self._is_editing = True
        
        # Deep snapshot of original properties
        self._original_props = {
            "Resolution": obj.Resolution,
            "Group": getattr(obj, "Group", "Additive"),
            "Displacements": [FreeCAD.Vector(d.x, d.y, d.z) for d in obj.Displacements],
            "LatticeOrigin": FreeCAD.Vector(obj.LatticeOrigin),
            "LatticeExtent": FreeCAD.Vector(obj.LatticeExtent),
            "LatticePlacement": FreeCAD.Placement(obj.LatticePlacement.Base, obj.LatticePlacement.Rotation) if obj.LatticePlacement else None,
            "HasLatticeBounds": obj.HasLatticeBounds,
        }
        
        self._resolution = obj.Resolution
        self._reconstruct_handles()
        self._init_gizmo()

    def restore_original(self):
        obj = self._target_obj
        if not obj or not hasattr(self, "_original_props"):
            return
        
        for k, v in self._original_props.items():
            if hasattr(obj, k):
                if k == "Displacements":
                    setattr(obj, k, [FreeCAD.Vector(d.x, d.y, d.z) for d in v])
                elif k == "LatticePlacement":
                    setattr(obj, k, FreeCAD.Placement(v.Base, v.Rotation) if v else None)
                else:
                    setattr(obj, k, v)
        self._resolution = obj.Resolution
        self._reconstruct_handles()
        self._commit_changes(force=True)

    def _update_points_from_field(self, field):
        R = self._resolution
        deformed_grid_local = field.deformed_grid
        self.points = []
        for i in range(R**3):
            pt_local = FreeCAD.Vector(deformed_grid_local[i, 0], deformed_grid_local[i, 1], deformed_grid_local[i, 2])
            if field.placement is not None:
                pt_world = field.placement.multVec(pt_local)
            else:
                pt_world = pt_local
            self.points.append(pt_world)

    def _clear_visuals(self):
        for fld_pt in self.fld_points:
            fld_pt.undraw()
        self.fld_points = []
        if self._edge_lines:
            self._edge_lines.undraw()
            self._edge_lines = None

    def _do_terminate(self):
        self._dispose_gizmo()
        try:
            self._clear_visuals()
            if self.points_root and self.view and self.view.getSceneGraph():
                self.view.getSceneGraph().removeChild(self.points_root)
            self.points_root = None
        except Exception as e:
            fld_logger.debug(f"LatticeTool._do_terminate exception: {e}")
        super()._do_terminate()

    def _reconstruct_handles(self):
        self._clear_visuals()

        obj = self._target_obj
        # get_sdf_field, not .SdfField: FldLatticeProxy.onChanged invalidates by
        # setting SdfField = None, so the attribute reads None on exactly the
        # tick after a Displacements write -- which is every tick of a drag.
        field = obj.Proxy.get_sdf_field(obj) if (obj and obj.Proxy) else None
        if field is None:
            self._set_selection([])
            self._update_gizmo()
            return

        self._update_points_from_field(field)

        r = self._compute_handle_radius()
        self.fld_points = []
        if self.points_root:
            for pt in self.points:
                fld_pt = FldPoint(pt)
                fld_pt.draw_point(self.points_root, r * 0.8, color=(1.0, 1.0, 1.0))
                self.fld_points.append(fld_pt)
            
            self._edge_lines = FldLineSet(self.points_root, color=(0.0, 0.8, 1.0), width=1.5)
            self._update_lines()

        self._set_selection([])
        self._update_gizmo()

    def _update_lines(self):
        if not self._edge_lines:
            return
            
        R = self._resolution
        pts = self.points
        lines = []
        for k in range(R):
            for j in range(R):
                for i in range(R):
                    idx = i + j * R + k * R**2
                    p0 = pts[idx]
                    if i + 1 < R:
                        lines.append((p0, pts[idx + 1]))
                    if j + 1 < R:
                        lines.append((p0, pts[idx + R]))
                    if k + 1 < R:
                        lines.append((p0, pts[idx + R**2]))
                        
        flat_pts = []
        for p0, p1 in lines:
            flat_pts.append(p0)
            flat_pts.append(p1)
        self._edge_lines.update_lines(flat_pts)
