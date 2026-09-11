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
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.tools.modifiers import BaseModifierTaskPanel

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
        self.res_spin.setToolTip("Control points per axis (2 = 8 points, 3 = 27, etc.)")
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


class LatticeTool(FldSdfModifierToolBase):
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

    def _do_terminate(self):
        try:
            for fld_pt in self.fld_points:
                fld_pt.undraw()
            self.fld_points = []
            if self._edge_lines:
                self._edge_lines.undraw()
                self._edge_lines = None
            if self.points_root and self.view and self.view.getSceneGraph():
                self.view.getSceneGraph().removeChild(self.points_root)
            self.points_root = None
        except Exception as e:
            fld_logger.debug(f"LatticeTool._do_terminate exception: {e}")
        super()._do_terminate()

    def _reconstruct_handles(self):
        for fld_pt in self.fld_points:
            fld_pt.undraw()
        self.fld_points = []
        if self._edge_lines:
            self._edge_lines.undraw()
            self._edge_lines = None
            
        obj = self._target_obj
        # get_sdf_field, not .SdfField: FldLatticeProxy.onChanged invalidates by
        # setting SdfField = None, so the attribute reads None on exactly the
        # tick after a Displacements write -- which is every tick of a drag.
        field = obj.Proxy.get_sdf_field(obj) if (obj and obj.Proxy) else None
        if field is None:
            return

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
            
        r = self._compute_handle_radius()
        self.fld_points = []
        if self.points_root:
            for pt in self.points:
                fld_pt = FldPoint(pt)
                fld_pt.draw_point(self.points_root, r * 0.8, color=(1.0, 1.0, 1.0))
                self.fld_points.append(fld_pt)
            
            self._edge_lines = FldLineSet(self.points_root, color=(0.0, 0.8, 1.0), width=1.5)
            self._update_lines()

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

    def on_button1_down(self, event_dict):
        btn = event_dict.get("Button")
        if btn != QtCore.Qt.LeftButton:
            return False
            
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return True
            
        r = self._compute_handle_radius()
        best_idx = None
        best_dist = float('inf')
        for i, pt in enumerate(self.points):
            idx, dist = self._hit_test_perp(ray_p, ray_d, [pt], tolerance=r * 1.0)
            if idx is not None and dist < best_dist:
                best_dist = dist
                best_idx = i
                
        if best_idx is not None:
            self._dragging_idx = best_idx
            self._drag_constraint_base = self.points[best_idx]
            
            vd = self.view.getViewDirection()
            self._drag_plane_n = FreeCAD.Vector(-vd.x, -vd.y, -vd.z).normalize()
            self._drag_plane_o = self.points[best_idx]
            
            self.fld_points[best_idx].set_color(DEFAULT_ADDITIVE_COLOR)
            self._update_constraint_visual()
            self._start_drag_timer()
            return True
            
        return False

    def on_button1_up(self, event_dict):
        self._stop_drag_timer()
        self._clear_constraint_visual()
        if self._dragging_idx is not None:
            self.fld_points[self._dragging_idx].set_color((1.0, 1.0, 1.0))
            self._dragging_idx = None
            self._commit_changes(force=True)
        return True

    def _drag_update(self):
        if self._drag_check_lmb_released():
            return
        if self._dragging_idx is None:
            return
            
        mouse_pos = FldInputManager.get_instance()._last_qt_pos
        event_dict = {"Position": mouse_pos}
        
        pt_global = self.resolve_world_point(
            event_dict,
            base_pt=self._drag_plane_o,
            plane_normal=self._drag_plane_n,
            plane_origin=self._drag_plane_o,
            exclude=[self._target_obj] if getattr(self, "_target_obj", None) else [],
            place_on_geometry=False,
        )
            
        if pt_global:
            obj = self._target_obj
            field = obj.Proxy.get_sdf_field(obj) if (obj and obj.Proxy) else None
            if field is None:
                return

            R = self._resolution
            
            if field.placement is not None:
                pt_local = field.placement.inverse().multVec(pt_global)
            else:
                pt_local = pt_global
                
            new_disp = np.array([pt_local.x, pt_local.y, pt_local.z], dtype=np.float32) - field.grid_local[self._dragging_idx]
            
            current_disps = field.displacements.copy()
            proposed_disps = current_disps.copy()
            proposed_disps[self._dragging_idx] = new_disp
            
            clamped_disps = field.clamp_displacements(proposed_disps, current_disps)
            field.displacements = clamped_disps
            
            deformed_grid_local = field.deformed_grid
            for i in range(R**3):
                p_loc = FreeCAD.Vector(deformed_grid_local[i, 0], deformed_grid_local[i, 1], deformed_grid_local[i, 2])
                if field.placement is not None:
                    p_world = field.placement.multVec(p_loc)
                else:
                    p_world = p_loc
                self.points[i] = p_world
                self.fld_points[i].position = p_world
                self.fld_points[i].update_draw()
                
            vec_list = [FreeCAD.Vector(float(d[0]), float(d[1]), float(d[2])) for d in clamped_disps]
            obj.Displacements = vec_list
            
            self._update_lines()
            self._commit_changes()
