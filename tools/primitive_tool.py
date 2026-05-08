import FreeCAD
import FreeCADGui
import math
from enum import IntEnum
from PySide import QtCore, QtGui
from pivy import coin
from core import dm_logger
from core.input_manager import DMInputManager
from core.dm_point import DMPoint
from core.dm_line import DMLineSet
from core.dm_object import create_dm_object, get_interactive_throttle_interval
from core.dm_mesher import mesh_timer
from tools.dm_base import DMBase, DragTimerMixin, ToolState

# Ensure we import the right storage classes. For now, defaulting to MarchingCubes
from core.sdf.sdf.box import SdfBoxField
from core.sdf.sdf.sphere import SdfSphereField
from core.sdf.sdf.cylinder import SdfCylinderField
from core.sdf.sdf.torus import SdfTorusField
from core.sdf.sdf2d.polygon import Sdf2dPolygon
from core.sdf.sdf2d.circle import Sdf2dCircle
from core.sdf.sdf_extrusion import SdfExtrusionField
from core.sdf.sdf_revolution import SdfRevolutionField

# Cell size for interactive preview in mm (larger = faster updates)
_PREVIEW_CELL_SIZE = 20.0

# Opposite corner index for box corners 0..7 (see _get_final_points ordering)
_BOX_OPPOSITE = {0: 6, 1: 7, 2: 4, 3: 5, 4: 2, 5: 3, 6: 0, 7: 1}



_STAGE_HINTS = {
    ToolState.PLACE_ANCHOR: "Click to place anchor",
    ToolState.DRAG_XY:      "Move mouse and click to set profile",
    ToolState.DRAG_Z:       "Move mouse and click to set height",
    ToolState.CUSTOM_1:     "Move mouse and click to set parameter 1",
    ToolState.CUSTOM_2:     "Move mouse and click to set parameter 2",
    ToolState.CUSTOM_3:     "Move mouse and click to set parameter 3",
}

_CREATION_PREVIEW_STAGES = frozenset({
    ToolState.DRAG_XY, ToolState.DRAG_Z,
    ToolState.CUSTOM_1, ToolState.CUSTOM_2, ToolState.CUSTOM_3,
})


class QuantityLineEdit(QtGui.QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.step = 1.0
        
    def wheelEvent(self, event):
        # angleDelta is the PySide6/Qt6 API; fallback to legacy delta() for Qt5
        try:
            dy = event.angleDelta().y()
        except AttributeError:
            dy = event.delta()
        if dy == 0:
            event.ignore()
            return
        try:
            q = FreeCAD.Units.Quantity(self.text())
            val = q.Value
            val += self.step if dy > 0 else -self.step
            unit_str = q.Unit.getUserString() or "mm"
            self.setText(f"{val:.2f} {unit_str}")
            self.editingFinished.emit()
        except Exception:
            pass
        # Always accept to prevent parent scroll area from stealing the event
        event.accept()


class PrimitiveTaskPanel:
    """Task panel for SDF primitives (both creation and edit modes)."""
    def __init__(self, creator):
        self.creator = creator
        from PySide import QtGui, QtCore
        self.form = QtGui.QWidget()
        self.layout = QtGui.QVBoxLayout(self.form)
        self.layout.setContentsMargins(10, 10, 10, 10)
        
        self._build_ui()
        self.update_ui()
        
    def _build_ui(self):
        from PySide import QtGui, QtCore
        # Transform Group
        self.transform_group = QtGui.QGroupBox("Transform")
        t_layout = QtGui.QFormLayout(self.transform_group)
        self.pos_x = QuantityLineEdit()
        self.pos_y = QuantityLineEdit()
        self.pos_z = QuantityLineEdit()
        
        self.pos_x.editingFinished.connect(self._on_pos_changed)
        self.pos_y.editingFinished.connect(self._on_pos_changed)
        self.pos_z.editingFinished.connect(self._on_pos_changed)
        
        t_layout.addRow("X:", self.pos_x)
        t_layout.addRow("Y:", self.pos_y)
        t_layout.addRow("Z:", self.pos_z)
        self.layout.addWidget(self.transform_group)
        
        # Params Group
        self.params_group = QtGui.QGroupBox("Parameters")
        self.p_layout = QtGui.QFormLayout(self.params_group)
        self.layout.addWidget(self.params_group)
        
        self.param_inputs = {}
        
        self.layout.addStretch()

    def update_ui(self):
        """Update line edits with current values from the tool without triggering signals."""
        creator = self.creator
        wp = creator.working_plane
        if wp:
            base = wp.Base
            self.pos_x.blockSignals(True)
            self.pos_y.blockSignals(True)
            self.pos_z.blockSignals(True)
            self.pos_x.setText(f"{base.x:.2f} mm")
            self.pos_y.setText(f"{base.y:.2f} mm")
            self.pos_z.setText(f"{base.z:.2f} mm")
            self.pos_x.blockSignals(False)
            self.pos_y.blockSignals(False)
            self.pos_z.blockSignals(False)
            
        self._update_params_ui()

    def _update_params_ui(self):
        from PySide import QtGui, QtCore
        creator = self.creator
        if not hasattr(creator, "get_parameters") or not hasattr(creator, "set_parameters"):
            self.params_group.hide()
            return
            
        params = creator.get_parameters()
        if not params:
            self.params_group.hide()
            return
            
        self.params_group.show()
        
        # Add new fields if needed
        for key in params.keys():
            if key not in self.param_inputs:
                le = QuantityLineEdit()
                le.editingFinished.connect(lambda k=key: self._on_param_changed(k))
                self.p_layout.addRow(f"{key}:", le)
                self.param_inputs[key] = le
                
        # Update values
        for key, val in params.items():
            le = self.param_inputs.get(key)
            if le:
                le.blockSignals(True)
                le.setText(f"{val:.2f} mm")
                le.blockSignals(False)

    def _on_pos_changed(self):
        import FreeCAD
        creator = self.creator
        if not creator.working_plane:
            return
        
        try:
            x = FreeCAD.Units.Quantity(self.pos_x.text()).Value
            y = FreeCAD.Units.Quantity(self.pos_y.text()).Value
            z = FreeCAD.Units.Quantity(self.pos_z.text()).Value
            
            delta = FreeCAD.Vector(x, y, z) - creator.working_plane.Base
            creator.working_plane.Base = FreeCAD.Vector(x, y, z)
            
            creator.points = [p + delta for p in creator.points]
            if getattr(creator, "_center_handle", None):
                creator._center_handle.position += delta
            if getattr(creator, "_rot_handle", None):
                creator._rot_handle.position += delta
                
            creator._update_handle_positions(creator.points)
            creator.update_preview()
            if creator.view:
                creator.view.redraw()
        except Exception as e:
            from core import dm_logger
            dm_logger.debug(f"Error parsing position formula: {e}")

    def _on_param_changed(self, key):
        import FreeCAD
        creator = self.creator
        if not hasattr(creator, "get_parameters") or not hasattr(creator, "set_parameters"):
            return
            
        params = creator.get_parameters()
        le = self.param_inputs.get(key)
        if le:
            try:
                val = FreeCAD.Units.Quantity(le.text()).Value
                params[key] = val
                creator.set_parameters(params)
                self.update_ui()
            except Exception as e:
                from core import dm_logger
                dm_logger.debug(f"Error parsing parameter formula: {e}")

    def accept(self):
        if hasattr(self.creator, 'finish'):
            self.creator.finish()
        else:
            self.creator.terminate()
        return True
        
    def reject(self):
        self.creator.terminate()
        return True



class PrimitiveCreatorBase(DMBase, DragTimerMixin):
    """Base class for SDF primitive creator tools with live mesh preview."""

    # Subclasses declare their step sequence, e.g.:
    #   CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.DRAG_Z]
    CREATION_STEPS = []



    def __init__(self):
        super().__init__()
        self._preview_obj = None     # Live FreeCAD object for preview
        self._update_pending = False  # Throttle rapid updates
        self._creating_obj = False    # Re-entrancy guard
        self._create_is_subtractive = False  # DEPRECATED - use _create_group
        self._create_group = "Group 1"  # Z hotkey toggle during creation
        # Reset the shared timer so preview calls for this tool session are isolated
        mesh_timer.reset()

        self.dm_points = []
        self.dm_line_set = None
        self.points_root = coin.SoSeparator()
        if self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)

        # Edit mode drag state
        self._dragging_idx = None
        self._drag_plane_n = None
        self._drag_plane_o = None

        self._is_editing = False
        self._drag_return_state = ToolState.IDLE
        self._edit_pivot = None
        self._edit_last_angle = 0.0
        self._edit_is_rotating = False
        self._center_handle = None
        self._rot_handle = None
        self._rot_line = None

        # Axis constraint state (edit mode)
        self._constraint_axis  = None   # 'x' | 'y' | 'z' | None
        self._constraint_plane = None   # 'yz' | 'xz' | 'xy' | None
        self._constraint_space = 'global'  # 'global' | 'local'
        self._constraint_last_key = None   # last key pressed - enables global→local cycle
        self._drag_constraint_base = None  # handle world pos at drag-start

        # Snap mode for edit drag (mirrors first-point snap pipeline when active)
        self._edit_snap_mode = 'off'    # 'off' | 'workplane_sdf' | 'all'

        # Constraint axis visual
        self._constraint_line = None    # DMLineSet drawn along active axis

        # Transform gizmo (edit mode only)
        self._gizmo = None

        # Creation-phase anchor (set on PLACE_ANCHOR accept)
        self._anchor_pt = None
        
        self.points = []

        # Unified workplane pre-load logic
        self._init_working_plane()

        # Enter first creation step
        if self.CREATION_STEPS:
            self.state = self.CREATION_STEPS[0]
            dm_logger.info(f"{type(self).__name__}: {_STAGE_HINTS.get(self.state, 'Click to begin')}")

        # Panel is shown after _post_init runs (after edit_object detection)
        self.panel = None

    def _post_init(self):
        """Override: detect selected object first, THEN show the task panel."""
        super()._post_init()  # runs _detect_selected_object() + updateGui()
        # Now show the panel (after _is_editing is correctly set)
        if not getattr(self, "_terminated", False):
            self.panel = PrimitiveTaskPanel(self)
            FreeCADGui.Control.showDialog(self.panel)
            self._dialog_open = True

    def get_command_id(self):
        if self.state == ToolState.EDIT_MODE:
            return "DM_EditObject"
        return super().get_command_id()

    def get_handled_types(self):
        return ["sdf"]

    def to_lattice(self):
        """Called by the edit lattice tool."""
        dm_logger.debug("lattice tool called on shaped type")

    def edit_object(self, obj):
        """Load an existing SDF object into the tool for editing.

        Base: sets preview obj, loads raw points, sets workplane.
        Subclasses call super() then reconstruct their specific state and draw handles.
        """
        super().edit_object(obj)
        dm_logger.debug(f"{type(self).__name__}: Editing existing object {obj.Label}")
        self._preview_obj = obj
        self.state = ToolState.EDIT_MODE
        self._drag_return_state = ToolState.EDIT_MODE

        self.working_plane = obj.Placement
        self._field_placement = None # Clear stale creation placement
        self._working_plane_is_fallback = False

        if hasattr(obj, "Points"):
            self.points = [self.working_plane.multVec(pt) for pt in obj.Points]
        self._add_transform_handles()
        self._init_gizmo()

    def _init_gizmo(self):
        from core.dm_gizmo import DMTransformGizmo
        if self._gizmo:
            self._gizmo.undraw()
            self._gizmo = None
        if not self.working_plane:
            return
        self._gizmo = DMTransformGizmo()
        length = self._compute_default_size() * 0.6
        self._gizmo.draw(self.points_root, self.working_plane.Base, length=length)

    # ------------------------------------------------------------------
    # Edit mode: hover, handle selection, drag
    # ------------------------------------------------------------------

    def handle_move(self, event_dict):
        """Route mouse-move to the correct creation stage or edit-mode hover."""
        if self._is_editing:
            if self.state != ToolState.DRAGGING:
                self._edit_hover(event_dict)
            return

        if self.state in _CREATION_PREVIEW_STAGES:
            pos = self._project_for_stage(self.state, event_dict)
            if pos:
                self._on_stage_preview(self.state, pos)
                self.update_preview()
            return

        # PLACE_ANCHOR or IDLE: normal workplane hover (snap grid shown by DMBase)
        super().handle_move(event_dict)

    def _edit_hover(self, event_dict):
        """Update cursor when hovering over a handle in edit mode."""
        ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return
        pts = [dm_pt.position for dm_pt in self.dm_points]
        if getattr(self, "_center_handle", None): pts.append(self._center_handle.position)
        if getattr(self, "_rot_handle", None): pts.append(self._rot_handle.position)
        idx, _ = self._hit_test_perp(ray_p, ray_d, pts)
        if idx is not None:
            from PySide.QtCore import Qt
            self._set_cursor(Qt.PointingHandCursor)
        else:
            self._restore_cursor()

    def _edit_on_mouse_press(self, event_dict):
        """Hit-test handles and start drag timer in edit mode."""
        dm_logger.debug(f"{type(self).__name__}._edit_on_mouse_press: _is_editing={self._is_editing}, pts={len(self.points)}, dm_pts={len(self.dm_points)}")
        btn = event_dict.get("Button")
        if btn != QtCore.Qt.LeftButton:
            return False


        ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return True

        # Test center/rot handles first - center dot always gives free drag
        special_pts = []
        if getattr(self, "_center_handle", None): special_pts.append(self._center_handle.position)
        if getattr(self, "_rot_handle", None): special_pts.append(self._rot_handle.position)
        idx, _ = self._hit_test_perp(ray_p, ray_d, special_pts)
        if idx is not None:
            self._dragging_idx = 'center' if idx == 0 else 'rot'
            self._drag_constraint_base = None  # constraints don't apply to special handles
            if self._dragging_idx == 'center':
                self._drag_plane_n = FreeCAD.Vector(-self.view.getViewDirection())
                self._drag_plane_o = self._center_handle.position
            else:
                self._drag_plane_n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1)) if self.working_plane else FreeCAD.Vector(0,0,1)
                self._drag_plane_o = self._rot_handle.position
                self._edit_pivot = self.working_plane.Base if self.working_plane else self._center_handle.position
                v = self._rot_handle.position - self._edit_pivot
                self._edit_last_angle = math.atan2(v.y, v.x)
            self._start_drag_timer()
            return True

        # Test gizmo axes (lower priority than center dot, higher than control points)
        if self._gizmo and self.working_plane:
            tol = self._compute_handle_radius() * 2.5
            axis = self._gizmo.hit_test(ray_p, ray_d, tol)
            if axis:
                self._dragging_idx = f'gizmo_{axis}'
                ax_vec = self._gizmo._axes[axis]
                click_pt = DMInputManager.get_instance().get_axis_point(
                    self.view, FreeCAD.Vector(self.working_plane.Base), ax_vec, event_dict)
                self._drag_constraint_base = click_pt if click_pt else FreeCAD.Vector(self.working_plane.Base)
                self._start_drag_timer()
                return True

        # Test regular points
        idx, _ = self._hit_test_perp(ray_p, ray_d, self.points)
        if idx is not None:
            self._dragging_idx = idx
            self._drag_constraint_base = FreeCAD.Vector(self.points[idx])
            self._drag_plane_n = FreeCAD.Vector(-self.view.getViewDirection())
            self._drag_plane_o = self.points[idx]
            self._edit_is_rotating = (event_dict.get("Modifiers") == QtCore.Qt.ShiftModifier)
            if self._edit_is_rotating:
                self._edit_pivot = sum(self.points, FreeCAD.Vector()) / len(self.points)
                v = self.points[idx] - self._edit_pivot
                self._edit_last_angle = math.atan2(v.y, v.x)
            # Refresh constraint visual now that base point is known
            self._update_constraint_visual()
            self._start_drag_timer()
            return True
        return False

    # ------------------------------------------------------------------
    # Creation state machine - driven by CREATION_STEPS
    # ------------------------------------------------------------------

    def _primitive_name(self):
        return type(self).__name__.replace("Creator", "")

    def on_button1_down(self, event_dict):
        """Unified creation click handler: accept current stage and advance."""
        if self._is_editing:
            return self._edit_on_mouse_press(event_dict)

        if not self.CREATION_STEPS or self.state not in self.CREATION_STEPS:
            return True

        skip = [self._preview_obj] if self._preview_obj else None

        # For DRAG_Z, re-project via axis instead of workplane XY
        if self.state == ToolState.DRAG_Z:
            pos = self._project_for_stage(ToolState.DRAG_Z, event_dict)
        else:
            pos = self._resolve_wp_click(event_dict, skip_objects=skip)

        if pos is None:
            return True

        self._on_stage_accept(self.state, pos)
        self._advance_creation_step()
        return True

    def _advance_creation_step(self):
        """Move to the next creation stage, or commit when all stages are done."""
        steps = self.CREATION_STEPS
        if self.state not in steps:
            return
        idx = steps.index(self.state)
        if idx + 1 < len(steps):
            next_state = steps[idx + 1]
            self.state = next_state
            dm_logger.info(f"{self._primitive_name()}: {_STAGE_HINTS.get(next_state, '')}")
        else:
            self.state = ToolState.FINALIZED
            self._commit_and_enter_edit(self._primitive_name())

    def _project_for_stage(self, state, event_dict):
        """Return the world-space point appropriate for the given creation stage."""
        wp = self.working_plane
        if state == ToolState.DRAG_Z:
            base = getattr(self, "_height_drag_base", None) or self._anchor_pt
            if base is None:
                return None
            normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
            return DMInputManager.get_instance().get_axis_point(self.view, base, normal, event_dict)
        else:
            # DRAG_XY / CUSTOM_N: project onto workplane XY locked to anchor Z
            normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
            origin = self._anchor_pt or (wp.Base if wp else FreeCAD.Vector())
            return self.projector.get_mouse_world_pos(
                event_dict, normal, origin, place_on_geometry=False
            )

    def _on_stage_accept(self, state, pos):
        """Subclass: record pos for the given creation stage."""
        pass

    def _on_stage_preview(self, state, pos):
        """Subclass: update internal state from pos so _get_preview_field() returns the right shape."""
        pass

    def _gizmo_drag_update(self):
        """Translate all points + working_plane along the clicked gizmo axis."""
        axis = self._dragging_idx[len('gizmo_'):]
        if not self._gizmo or axis not in self._gizmo._axes:
            return
        ax_vec = self._gizmo._axes[axis]
        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        new_pt = DMInputManager.get_instance().get_axis_point(
            self.view, self._drag_constraint_base, ax_vec, {"Position": mouse_pos})
        if new_pt:
            delta = new_pt - self._drag_constraint_base
            self.points = [p + delta for p in self.points]
            if self.working_plane:
                self.working_plane.Base += delta
            self._drag_constraint_base = new_pt
            self._gizmo.update(self.working_plane.Base if self.working_plane else new_pt)
            self._update_handle_positions(self.points)
            if hasattr(self, "panel") and self.panel:
                self.panel.update_ui()
            self.update_preview()
        if self.view:
            self.view.redraw()

    def _drag_update(self):
        """QTimer callback: move the selected handle to the current mouse position."""
        if self._drag_check_lmb_released():
            self._clear_constraint_visual()
            return
        if self._dragging_idx is None:
            return

        if isinstance(self._dragging_idx, str) and self._dragging_idx.startswith('gizmo_'):
            self._gizmo_drag_update()
            return

        from PySide import QtGui
        mods = QtGui.QApplication.keyboardModifiers()
        is_ctrl = bool(mods & QtCore.Qt.ControlModifier)
        is_shift = bool(mods & QtCore.Qt.ShiftModifier)

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        event_dict_pos = {"Position": mouse_pos}

        # ── Resolve drag target with constraint/snap priority ──────────────────
        base_pt = self._drag_constraint_base
        axis_vec, plane_normal = self._get_constraint_vectors()

        if base_pt is not None and axis_vec:
            # Axis constraint: closest point on axis to mouse ray (overrides snap)
            new_pt = DMInputManager.get_instance().get_axis_point(
                self.view, base_pt, axis_vec, event_dict_pos)
        elif base_pt is not None and plane_normal:
            # Plane constraint: intersect mouse ray with constraint plane
            new_pt = self.projector.get_mouse_world_pos(
                event_dict_pos, plane_normal, base_pt, place_on_geometry=False)
        elif base_pt is not None and self._edit_snap_mode != 'off':
            # Snap mode: full pipeline (workplane → SDF → NURBS → camera plane)
            skip = [self._preview_obj] if self._preview_obj else None
            place_on_geo = (self._edit_snap_mode == 'all')
            snap_result = self.projector.get_mouse_plane_pt(
                event_dict_pos, place_on_geometry=place_on_geo,
                working_plane=self.working_plane, skip_objects=skip)
            new_pt = snap_result[0] if snap_result else None
        else:
            # Default: camera-facing plane
            new_pt = self.projector.get_mouse_world_pos(
                event_dict_pos, self._drag_plane_n, self._drag_plane_o,
                place_on_geometry=False)

        if new_pt:
            if self._dragging_idx == 'center':
                delta = new_pt - self._center_handle.position
                self.points = [p + delta for p in self.points]
                if self.working_plane:
                    self.working_plane.Base += delta
                self._center_handle.position = new_pt
                if self._rot_handle:
                    self._rot_handle.position += delta
                if self._gizmo:
                    self._gizmo.update(new_pt)
            elif self._dragging_idx == 'rot':
                pivot = self._edit_pivot
                v = new_pt - pivot
                angle = math.atan2(v.y, v.x)
                da = angle - self._edit_last_angle
                rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), math.degrees(da))
                self.points = [pivot + rot.multVec(p - pivot) for p in self.points]
                if self.working_plane:
                    self.working_plane.Rotation = self.working_plane.Rotation.multiply(rot)
                self._edit_last_angle = angle
                self._rot_handle.position = new_pt
                if self._gizmo:
                    self._gizmo.update(self.working_plane.Base)
            elif is_ctrl:
                delta = new_pt - self.points[self._dragging_idx]
                self.points = [p + delta for p in self.points]
                if self.working_plane:
                    self.working_plane.Base += delta
                if self._center_handle:
                    self._center_handle.position += delta
                if self._rot_handle:
                    self._rot_handle.position += delta
                if self._gizmo:
                    self._gizmo.update(self.working_plane.Base)
            elif is_shift and self._edit_pivot:
                v = new_pt - self._edit_pivot
                angle = math.atan2(v.y, v.x)
                da = angle - self._edit_last_angle
                rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), math.degrees(da))
                self.points = [self._edit_pivot + rot.multVec(p - self._edit_pivot) for p in self.points]
                self._edit_last_angle = angle
                # Sync working plane rotation
                if self.working_plane:
                    self.working_plane.Rotation = self.working_plane.Rotation.multiply(rot)
            else:
                self.points[self._dragging_idx] = new_pt

            # Always sync dm_point visuals and update SDF after any drag branch
            self._update_handle_positions(self.points)
            if hasattr(self, "panel") and self.panel:
                self.panel.update_ui()
            self.update_preview()
        if self.view:
            self.view.redraw()


    def _sync_edit_points(self):
        """Sync dm_point positions back to tool-specific variables. Override in subclasses."""
        pass

    def _get_edit_preview_field(self):
        """Return the SDF field for the current edit state. Defaults to _get_preview_field."""
        return self._get_preview_field()

    # ── Axis constraint helpers ────────────────────────────────────────────────

    def _get_constraint_vectors(self):
        """Returns (axis_vec, plane_normal) in world space, or (None, None) if no constraint."""
        if self._constraint_space == 'local' and self.working_plane:
            rot = self.working_plane.Rotation
            axes = {
                'x': rot.multVec(FreeCAD.Vector(1, 0, 0)),
                'y': rot.multVec(FreeCAD.Vector(0, 1, 0)),
                'z': rot.multVec(FreeCAD.Vector(0, 0, 1)),
            }
        else:
            axes = {
                'x': FreeCAD.Vector(1, 0, 0),
                'y': FreeCAD.Vector(0, 1, 0),
                'z': FreeCAD.Vector(0, 0, 1),
            }
        plane_normals = {'yz': axes['x'], 'xz': axes['y'], 'xy': axes['z']}
        if self._constraint_axis:
            return axes[self._constraint_axis], None
        if self._constraint_plane:
            return None, plane_normals[self._constraint_plane]
        return None, None

    def _update_constraint_visual(self):
        """Draw or hide the colored axis line that indicates the active constraint."""
        if getattr(self, "_constraint_line", None):
            self._constraint_line.undraw()
            self._constraint_line = None
        if not self._constraint_axis:
            return
        base = getattr(self, "_drag_constraint_base", None)
        if base is None and isinstance(getattr(self, "_dragging_idx", None), int):
            idx = self._dragging_idx
            if idx < len(self.points):
                base = self.points[idx]
        if base is None:
            return
        axis_vec, _ = self._get_constraint_vectors()
        if axis_vec is None:
            return
        colors = {'x': (1.0, 0.15, 0.15), 'y': (0.15, 0.85, 0.15), 'z': (0.25, 0.45, 1.0)}
        color = colors.get(self._constraint_axis, (1.0, 1.0, 1.0))
        length = 1000
        from core.dm_line import DMLineSet
        self._constraint_line = DMLineSet(
            [base - axis_vec * length, base + axis_vec * length],
            self.view, color=color, width=2)
        self._constraint_line.draw()
        if self.view:
            self.view.redraw()

    def _clear_constraint_visual(self):
        if getattr(self, "_constraint_line", None):
            self._constraint_line.undraw()
            self._constraint_line = None

    def finish(self):
        """In edit mode, finish resets to idle. Otherwise, standard creation finish."""
        if getattr(self, "_is_editing", False):
            # Persist edits back to the FreeCAD object before leaving edit mode
            obj = self._preview_obj
            if obj and obj.Document:
                try:
                    field = self._get_edit_preview_field()
                    points = self._get_final_points()
                    if field is not None:
                        obj.Proxy.SdfField = field
                    if points is not None:
                        if not hasattr(obj, "Points"):
                            obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                        obj.Points = points
                    if self.working_plane:
                        if hasattr(self.working_plane, "getGlobalPlacement"):
                            obj.Placement = self.working_plane.getGlobalPlacement()
                        elif hasattr(self.working_plane, "Placement"):
                            obj.Placement = self.working_plane.Placement
                        else:
                            obj.Placement = self.working_plane
                    QtCore.QTimer.singleShot(0, lambda: self._commit_edit_deferred(obj))
                except Exception as e:
                    dm_logger.error(f"finish(): failed to commit edit: {e}")

            self._is_editing = False
            self._edit_pivot = None
            self._edit_last_angle = 0.0
            self._edit_is_rotating = False
            self._center_handle = None
            self._rot_handle = None
            self._rot_line = None
            self._preview_obj = None
            self.reset_state()
            return

        if self.is_in_progress():
            name = type(self).__name__.replace("Creator", "")
            self._finalize_object(name, terminate=False)
            self.reset_state()
            dm_logger.info(f"{name} accepted. Tool remains active.")
        else:
            self.terminate()

    def _commit_edit_deferred(self, obj):
        """Deferred recompute after editing - required for Shape assignment safety."""
        try:
            obj.touch()
            obj.Document.recompute([obj])
        except Exception as e:
            dm_logger.error(f"_commit_edit_deferred: {e}")


    def _init_working_plane(self):
        """Pre-load a workplane if one isn't already detected from selection."""
        if not self.working_plane:
            visible_wps = self.get_visible_workplanes()
            if visible_wps:
                wp = visible_wps[0]
                if hasattr(wp, "getGlobalPlacement"):
                    self.working_plane = wp.getGlobalPlacement()
                elif hasattr(wp, "Placement"):
                    self.working_plane = wp.Placement
                else:
                    self.working_plane = wp
                self._working_plane_is_fallback = False

    def _get_placement(self):
        """Return a FreeCAD.Placement from self.working_plane, or None."""
        wp = getattr(self, "working_plane", None)
        if wp is None:
            return None
        if hasattr(wp, "getGlobalPlacement"):
            return wp.getGlobalPlacement()
        elif hasattr(wp, "Placement"):
            return wp.Placement
        return wp

    def _compute_default_size(self):
        """Return a world-space length (~15% of viewport height) used to size the initial spawned primitive."""
        try:
            from pivy import coin
            cam = self.view.getCameraNode()
            if isinstance(cam, coin.SoOrthographicCamera):
                h = cam.height.getValue()
            else:
                import math
                h = 2.0 * cam.focalDistance.getValue() * math.tan(cam.heightAngle.getValue() / 2.0)
            size = h * 0.15
        except Exception:
            size = 200.0
        return max(5.0, min(size, 500.0))

    def _spawn_default_primitive(self, click_pt):
        """Spawn a small complete primitive at click_pt, then immediately enter edit mode.

        Subclasses must:
          1. Compute N world-space handle positions from click_pt + _compute_default_size()
          2. Populate self.points with those N positions
          3. Draw N DMPoint handles + optional wire frame
          4. Set self.state = ToolState.FINALIZED
          5. Call self._commit_and_enter_edit("PrimitiveName")
        """
        pass

    @staticmethod
    def _box_corners_local(center, half_size):
        """Return list of 8 FreeCAD.Vector corners in local space.
        
        Order: (-,-,-) (+,-,-) (+,+,-) (-,+,-) (-,-,+) (+,-,+) (+,+,+) (-,+,+)
        """
        c, h = center, half_size
        return [
            FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z - h.z),
            FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z - h.z),
            FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z - h.z),
            FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z - h.z),
            FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z + h.z),
            FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z + h.z),
            FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z + h.z),
            FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z + h.z),
        ]

    def _add_transform_handles(self):
        if not getattr(self, "_is_editing", False) or not self.working_plane:
            return
        r = self._compute_handle_radius()
        if not getattr(self, "_center_handle", None):
            self._center_handle = DMPoint(self.working_plane.Base)
            self._center_handle.draw_point(self.points_root, radius=r*1.5, color=(0.8, 0.8, 0.2))
        else:
            self._center_handle.position = self.working_plane.Base
            self._center_handle.update_draw(radius=r*1.5)
        offset = self.working_plane.Rotation.multVec(FreeCAD.Vector(self._compute_default_size() * 0.4, 0, 0))
        rot_pos = self.working_plane.Base + offset
        if not getattr(self, "_rot_handle", None):
            self._rot_handle = DMPoint(rot_pos)
            self._rot_handle.draw_point(self.points_root, radius=r*0.8, color=(0.2, 0.8, 0.8))
        else:
            self._rot_handle.position = rot_pos
            self._rot_handle.update_draw(radius=r*0.8)
        line_pts = [self.working_plane.Base, rot_pos]
        if not getattr(self, "_rot_line", None):
            self._rot_line = DMLineSet(self.points_root, color=(0.2, 0.8, 0.8), width=2.0)
        self._rot_line.update_lines(line_pts)

    def _update_handle_positions(self, world_pts, color=(1.0, 0.5, 0.0)):
        """Update dm_points to match the given world-space positions.
        
        Creates new DMPoint objects as needed, updates existing ones.
        """
        while len(self.dm_points) < len(world_pts):
            self.dm_points.append(DMPoint(world_pts[len(self.dm_points)]))
        
        r = self._compute_handle_radius(ref_pt=world_pts[0] if world_pts else None)
        for i, pt in enumerate(world_pts):
            if i >= len(self.dm_points):
                break
            self.dm_points[i].position = pt
            if self.dm_points[i]._point_sep is None:
                self.dm_points[i].draw_point(self.points_root, radius=r, color=color)
            else:
                self.dm_points[i].update_draw(radius=r)
        self._add_transform_handles()

    def _height_drag_move(self, event_dict):
        """Move current_point along workplane normal from _height_drag_base."""
        if getattr(self, "_height_drag_base", None) is None:
            return
        wp = getattr(self, "working_plane", None)
        normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
        self.current_point = DMInputManager.get_instance().get_axis_point(
            self.view, self._height_drag_base, normal, event_dict
        )

    def _get_preview_field(self):
        """Subclasses return the current field based on click state + current_point."""
        return None

    def _get_final_field(self):
        """Subclasses return the final field when placement is committed."""
        return None

    def _get_final_points(self):
        """Default implementation: returns self.points mapped to local space.
        
        This ensures that 'obj.Points' always stores coordinates in the field's
        local coordinate system (relative to obj.Placement / self.working_plane).
        """
        if not self.points:
            return None
            
        # Re-map stored world points to local space using the locked working plane
        return [self.to_local(p) for p in self.points]

    def update_preview(self):
        """Called on every mouse move by DMBase.handle_move. Updates the live mesh."""
        if getattr(self, "_terminated", False):
            return
        # In edit mode, use the edit-mode field builder which reads from self.points
        if getattr(self, "_is_editing", False):
            field = self._get_edit_preview_field()
        else:
            field = self._get_preview_field()
        if field is None:
            return

        if self._preview_obj is None:
            if getattr(self, "_creating_obj", False):
                return
            self._creating_obj = True
            try:
                # Create the preview object for the first time
                # Use last part of class name without 'Creator' suffix
                name = type(self).__name__.replace("Creator", "")
                self._preview_obj = create_dm_object(name=name, shape_type="sdf")
                if hasattr(self._preview_obj, "Group"):
                    self._preview_obj.Group = getattr(self, "_create_group", "Group 1")
            finally:
                self._creating_obj = False

        self._schedule_update(lambda: self._do_full_preview_update(field))

    def _do_full_preview_update(self, field=None):
        """Throttled update of both the mesh and the ghost visuals."""
        if field is None:
            field = (self._get_edit_preview_field()
                     if getattr(self, "_is_editing", False)
                     else self._get_preview_field())
        self._apply_preview_field(field)
        self._update_ghost_visuals()
        if getattr(self, "_is_editing", False) and self._preview_obj and self._preview_obj.Document:
            obj = self._preview_obj
            # Always push recomposed boolean fields to renderer so the result updates live.
            self._update_boolean_parents_in_renderer()
            # Full FreeCAD doc recompute only on drag release (too slow to do every frame).
            if not getattr(self, "_is_dragging", False):
                QtCore.QTimer.singleShot(0, lambda: self._recompute_boolean_parents(obj))
        if self.view:
            self.view.redraw()

    def _update_boolean_parents_in_renderer(self):
        """Push recomposed boolean fields directly to the renderer for real-time updates.
        Called every preview frame including during drags — avoids FreeCAD doc recompute.
        """
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
            from commands.cmd_boolean import _recompose_boolean
            doc = self._preview_obj.Document
            renderer = DMSceneRayMarchRenderer.get_instance()
            for obj in doc.Objects:
                if getattr(obj, "ShapeType", None) != "sdf":
                    continue
                inputs = getattr(obj, "BooleanInputs", None) or []
                if self._preview_obj not in inputs:
                    continue
                new_field = _recompose_boolean(obj)
                if new_field is not None:
                    if hasattr(obj, "Proxy"):
                        obj.Proxy.SdfField = new_field
                    label = f"{doc.Name}.{obj.Name}"
                    renderer.update_field(label, new_field)
        except Exception as e:
            dm_logger.debug(f"Boolean parent renderer update error: {e}")

    def _recompute_boolean_parents(self, obj):
        """Deferred: recompute the primitive and its boolean parent dependents."""
        if getattr(self, "_terminated", False):
            return
        try:
            if obj and obj.Document:
                obj.touch()
                obj.Document.recompute()
        except Exception as e:
            dm_logger.debug(f"Boolean parent recompute error: {e}")


    def _update_ghost_visuals(self):
        """Standard implementation for primitive tools to show points/edges."""
        pass

    def _apply_preview_field(self, field):
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            proxy = self._preview_obj.Proxy
            if proxy is None:
                return
            proxy.SdfField = field

            # Direct GPU update - skip FreeCAD recompute cycle
            from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
            label = f"{self._preview_obj.Document.Name}.{self._preview_obj.Name}"
            DMSceneRayMarchRenderer.get_instance().update_field(label, field)
        except Exception as e:
            dm_logger.debug(f"PrimitiveCreatorBase preview update error: {e}")
        finally:
            self._update_pending = False

    def _finalize_object(self, name, terminate=True):
        """Commit the preview object as the final result, upgrading its mesh resolution."""
        field = self._get_final_field()
        points = self._get_final_points()
        if field is None:
            if terminate: self.terminate()
            return
        
        # We don't set self._finished = True here if we want to repeat, 
        # because _finished prevents _do_terminate from cleaning up.
        # But we DO want to sever the preview object.
        QtCore.QTimer.singleShot(0, lambda: self.__do_commit(name, field, points))
        if terminate:
            self._finished = True
            self.terminate()

    def __do_commit(self, name, field, points):
        obj = self._preview_obj
        if obj is None or not obj.Document:
            # fallback: create fresh
            obj = create_dm_object(name=name, shape_type="sdf")

        # Rename to final name
        try:
            # We must be careful not to trigger recursive recomputes here if we are
            # already in a recompute loop.
            dm_logger.debug(f"Committing {name}: {len(points) if points else 0} points, placement={self.working_plane}")
            obj.Label = name
        except Exception:
            pass

        # Add points for editing
        if points is not None:
            try:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                obj.Points = points
            except Exception:
                pass

        # Resolution is now handled globally by the GPU renderer settings.
        proxy = obj.Proxy
        proxy.SdfField = field
        
        # Set placement to match the working plane so edit mode restores correctly
        if self.working_plane:
            if hasattr(self.working_plane, "getGlobalPlacement"):
                obj.Placement = self.working_plane.getGlobalPlacement()
            elif hasattr(self.working_plane, "Placement"):
                obj.Placement = self.working_plane.Placement
            else:
                obj.Placement = self.working_plane
        
        obj.touch()
        obj.Document.recompute([obj])
        self._on_committed(obj)
        # Print accumulated timer summary now that the tool is accepted
        primitive_name = type(self).__name__.replace("Creator", "")
        mesh_timer.summary(f"{primitive_name} preview ({_PREVIEW_CELL_SIZE}mm)")
        self._preview_obj = None  # Severed; the object is now the user's

    def _commit_and_enter_edit(self, name):
        """Commit creation at full resolution, then immediately enter edit mode on the result."""
        field = self._get_final_field()
        points = self._get_final_points()
        if field is None:
            self.terminate()
            return

        # Pre-enter edit state so handle_move doesn't clobber tool vars during async delay
        self._is_editing = True
        self.state = ToolState.IDLE

        QtCore.QTimer.singleShot(0, lambda: self._do_commit_and_edit(name, field, points))

    def _do_commit_and_edit(self, name, field, points):
        """Async: commit the object at full resolution then switch to edit mode on it."""
        if getattr(self, "_terminated", False):
            return

        obj = self._preview_obj
        if obj is None or not obj.Document:
            obj = create_dm_object(name=name, shape_type="sdf")

        try:
            obj.Label = name
        except Exception:
            pass

        if points is not None:
            try:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                obj.Points = points
            except Exception:
                pass

        primitive_name = type(self).__name__.replace("Creator", "")
        obj.Proxy.SdfField = field

        # Set placement BEFORE calling edit_object so edit_object reads the correct
        # placement and reconstructs world-space handle positions from local obj.Points.
        if self.working_plane:
            if hasattr(self.working_plane, "getGlobalPlacement"):
                obj.Placement = self.working_plane.getGlobalPlacement()
            elif hasattr(self.working_plane, "Placement"):
                obj.Placement = self.working_plane.Placement
            else:
                obj.Placement = self.working_plane

        obj.touch()
        obj.Document.recompute([obj])
        mesh_timer.summary(f"{primitive_name} → edit mode")

        # Clear creation visuals before entering edit mode
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        if getattr(self, "_center_handle", None):
            self._center_handle.undraw()
            self._center_handle = None
        if getattr(self, "_rot_handle", None):
            self._rot_handle.undraw()
            self._rot_handle = None
        if getattr(self, "_rot_line", None):
            self._rot_line.undraw()
            self._rot_line = None
        self.dm_points.clear()
        if self.dm_line_set:
            self.dm_line_set.undraw()
            self.dm_line_set = None

        # Enter edit mode on the committed object
        self.edit_object(obj)
        FreeCADGui.updateGui()
        if self.view:
            self.view.redraw()

    def handle_keyboard(self, event_dict):
        key = event_dict.get("Key")
        key_text = str(event_dict.get("Text", "None")).upper()
        # dm_logger.debug(f"PrimitiveCreatorBase.handle_keyboard: key={key}, text='{key_text}', is_editing={self._is_editing}")

        # ── Axis constraints (edit mode) ────────────────────────────────────────
        if self._is_editing and key in (QtCore.Qt.Key_X, QtCore.Qt.Key_Y, QtCore.Qt.Key_Z):
            axis = {QtCore.Qt.Key_X: 'x', QtCore.Qt.Key_Y: 'y', QtCore.Qt.Key_Z: 'z'}[key]
            mods = event_dict.get("Modifiers", 0)
            is_shift = bool(mods & QtCore.Qt.ShiftModifier)
            if is_shift:
                plane = {'x': 'yz', 'y': 'xz', 'z': 'xy'}[axis]
                if self._constraint_plane == plane and self._constraint_space == 'global':
                    self._constraint_space = 'local'
                elif self._constraint_plane == plane:
                    self._constraint_axis = None
                    self._constraint_plane = None
                else:
                    self._constraint_plane = plane
                    self._constraint_axis = None
                    self._constraint_space = 'global'
                    self._constraint_last_key = f'shift_{axis}'
            else:
                if self._constraint_axis == axis and self._constraint_space == 'global':
                    self._constraint_space = 'local'
                elif self._constraint_axis == axis:
                    self._constraint_axis = None
                    self._constraint_plane = None
                else:
                    self._constraint_axis = axis
                    self._constraint_plane = None
                    self._constraint_space = 'global'
                    self._constraint_last_key = axis
            self._update_constraint_visual()
            return True

        # ── Group toggle (was Z, now Q) ─────────────────────────────────────────
        if key == QtCore.Qt.Key_Q:
            if getattr(self, "_preview_obj", None):
                cur = getattr(self._preview_obj, "Group", "Group 1")
                new_group = "Group 2" if cur == "Group 1" else "Group 1"
                self._preview_obj.Group = new_group
                self._preview_obj.touch()
                if self._preview_obj.Document:
                    self._preview_obj.Document.recompute([self._preview_obj])
                    if hasattr(self._preview_obj, "Proxy") and hasattr(self._preview_obj.Proxy, "SdfField"):
                        from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                        label = f"{self._preview_obj.Document.Name}.{self._preview_obj.Name}"
                        DMSceneRayMarchRenderer.get_instance().update_field(label, self._preview_obj.Proxy.SdfField)
            else:
                cur = getattr(self, "_create_group", "Group 1")
                self._create_group = "Group 2" if cur == "Group 1" else "Group 1"
            return True
        return super().handle_keyboard(event_dict)

    def get_snapping_menu(self):
        return [
            ("Snap Off",            lambda: self._set_edit_snap('off'),           self._edit_snap_mode == 'off'),
            ("Snap: Workplane+SDF", lambda: self._set_edit_snap('workplane_sdf'), self._edit_snap_mode == 'workplane_sdf'),
            ("Snap: All Geometry",  lambda: self._set_edit_snap('all'),           self._edit_snap_mode == 'all'),
        ]

    def _set_edit_snap(self, mode):
        self._edit_snap_mode = mode

    def reset_state(self):
        """Override to clear internal primitive state (points, visuals)."""
        super().reset_state()
        # Return to first creation step (or IDLE if no steps defined)
        self.state = self.CREATION_STEPS[0] if self.CREATION_STEPS else ToolState.IDLE
        self._anchor_pt = None
        self._create_group = "Group 1"
        self._create_is_subtractive = False  # DEPRECATED
        self.points = []
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        if getattr(self, "_center_handle", None):
            self._center_handle.undraw()
            self._center_handle = None
        if getattr(self, "_rot_handle", None):
            self._rot_handle.undraw()
            self._rot_handle = None
        if getattr(self, "_rot_line", None):
            self._rot_line.undraw()
            self._rot_line = None
        self.dm_points.clear()
        if self.dm_line_set:
            self.dm_line_set.undraw()
            self.dm_line_set = None
        # Clear constraint state and visual
        self._constraint_axis = None
        self._constraint_plane = None
        self._constraint_space = 'global'
        self._drag_constraint_base = None
        self._edit_snap_mode = 'off'
        self._drag_return_state = ToolState.IDLE
        self._clear_constraint_visual()
        if self._gizmo:
            self._gizmo.undraw()
            self._gizmo = None
        self.view.redraw()

    def _create_sdf_object(self, name, field, points=None):
        """Helper to create the FreeCAD object and assign the field (for 1-shot creation)."""
        obj = create_dm_object(name=name, shape_type="sdf")
        obj.Proxy.SdfField = field
        if points is not None:
            if not hasattr(obj, "Points"):
                obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
            obj.Points = points
        obj.touch()
        return obj


    def _do_terminate(self):
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        if getattr(self, "_center_handle", None):
            self._center_handle.undraw()
            self._center_handle = None
        if getattr(self, "_rot_handle", None):
            self._rot_handle.undraw()
            self._rot_handle = None
        if getattr(self, "_rot_line", None):
            self._rot_line.undraw()
            self._rot_line = None
        self.dm_points.clear()
        if self.dm_line_set:
            self.dm_line_set.undraw()
        self._clear_constraint_visual()
        if getattr(self, "_gizmo", None):
            self._gizmo.undraw()
            self._gizmo = None
        try:
            if self.view and self.view.getSceneGraph() and self.points_root:
                self.view.getSceneGraph().removeChild(self.points_root)
        except Exception as e:
            dm_logger.debug(f"PrimitiveCreatorBase._do_terminate: {e}")
        super()._do_terminate()


class BoxCreator(PrimitiveCreatorBase):

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.DRAG_Z]

    def get_command_id(self):
        return "DM_CreateBox"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_base = None
        self._profile_end = None

    def edit_object(self, obj):
        super().edit_object(obj)  # loads self.points = 8 world corners
        corners = list(getattr(self, "points", []))
        if len(corners) != 8:
            dm_logger.warning(f"BoxCreator.edit_object: expected 8 corners, got {len(corners)}")
            return
        self.points = corners  # keep all 8 for 8-handle drag
        self.current_point = None
        self.state = ToolState.IDLE

        r = self._compute_handle_radius()
        for pt in corners:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r, color=(1.0, 0.5, 0.0))
            self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    # ------------------------------------------------------------------
    # Creation stage hooks
    # ------------------------------------------------------------------

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self._anchor_pt = pos
            self._profile_end = pos
            self._field_placement = self._get_placement()
        elif state == ToolState.DRAG_XY:
            self._profile_end = pos
            a, b = self._anchor_pt, self._profile_end
            self._height_drag_base = (a + b) * 0.5
            self.current_point = self._height_drag_base
        elif state == ToolState.DRAG_Z:
            self.current_point = pos
            self._rebuild_box_points()

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY:
            self._profile_end = pos
            self._draw_base_rect()
        elif state == ToolState.DRAG_Z:
            self.current_point = pos
            self._rebuild_box_points()

    def _rebuild_box_points(self):
        """Build self.points (8 world corners) from anchor, profile_end, and current_point."""
        if not self._anchor_pt or not self._profile_end:
            return
        wp = self.working_plane
        if wp:
            inv = wp.inverse()
            loc_a = inv.multVec(self._anchor_pt)
            loc_b = inv.multVec(self._profile_end)
            if self.current_point and self.current_point != self._height_drag_base:
                loc_h = inv.multVec(self.current_point)
                top_z = loc_h.z
            else:
                top_z = loc_a.z + 1.0  # minimal height before user sets it
            base_z = loc_a.z
        else:
            loc_a = self._anchor_pt
            loc_b = self._profile_end
            top_z = self.current_point.z if self.current_point else loc_a.z + 1.0
            base_z = loc_a.z

        cx = (loc_a.x + loc_b.x) / 2.0
        cy = (loc_a.y + loc_b.y) / 2.0
        cz = (base_z + top_z) / 2.0
        hx = max(abs(loc_a.x - loc_b.x) / 2.0, 0.5)
        hy = max(abs(loc_a.y - loc_b.y) / 2.0, 0.5)
        hz = max(abs(top_z - base_z) / 2.0, 0.5)
        center = FreeCAD.Vector(cx, cy, cz)
        half = FreeCAD.Vector(hx, hy, hz)
        pts_local = self._box_corners_local(center, half)
        if wp:
            self.points = [wp.multVec(lc) for lc in pts_local]
        else:
            self.points = pts_local

    def _draw_base_rect(self):
        """Draw a 4-edge rectangle ghost on the workplane during DRAG_XY."""
        if not self._anchor_pt or not self._profile_end:
            return
        wp = self.working_plane
        if wp:
            inv = wp.inverse()
            loc_a = inv.multVec(self._anchor_pt)
            loc_b = inv.multVec(self._profile_end)
            z = loc_a.z
            corners_local = [
                FreeCAD.Vector(loc_a.x, loc_a.y, z),
                FreeCAD.Vector(loc_b.x, loc_a.y, z),
                FreeCAD.Vector(loc_b.x, loc_b.y, z),
                FreeCAD.Vector(loc_a.x, loc_b.y, z),
            ]
            corners = [wp.multVec(lc) for lc in corners_local]
        else:
            a, b = self._anchor_pt, self._profile_end
            corners = [
                a,
                FreeCAD.Vector(b.x, a.y, a.z),
                b,
                FreeCAD.Vector(a.x, b.y, a.z),
            ]
        line_pts = []
        for i in range(4):
            line_pts.extend([corners[i], corners[(i + 1) % 4]])
        if self.dm_line_set is None:
            self.dm_line_set = DMLineSet(self.points_root, color=(1.0, 0.6, 0.2), pattern=0x0F0F)
        self.dm_line_set.update_lines(line_pts, segments=[2] * 4)

    def _drag_update(self):
        if self._drag_check_lmb_released():
            return
        if self._dragging_idx is None:
            return

        if self._dragging_idx in ('center', 'rot') or (
                isinstance(self._dragging_idx, str) and self._dragging_idx.startswith('gizmo_')):
            super()._drag_update()
            return

        from PySide import QtGui
        mods = QtGui.QApplication.keyboardModifiers()
        is_ctrl = bool(mods & QtCore.Qt.ControlModifier)
        is_shift = bool(mods & QtCore.Qt.ShiftModifier)

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        new_pt = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos}, self._drag_plane_n, self._drag_plane_o,
            place_on_geometry=False
        )
        if new_pt:
            if is_ctrl:
                delta = new_pt - self.points[self._dragging_idx]
                self.points = [p + delta for p in self.points]
                if self.working_plane:
                    self.working_plane.Base += delta
            elif is_shift:
                # Rotate working plane
                pivot = sum(self.points, FreeCAD.Vector()) / len(self.points)
                v = new_pt - pivot
                angle = math.atan2(v.y, v.x)
                if not hasattr(self, "_edit_last_angle") or self._edit_last_angle == 0: 
                    self._edit_last_angle = angle
                da = angle - self._edit_last_angle
                rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), math.degrees(da))
                self.working_plane.Rotation = self.working_plane.Rotation.multiply(rot)
                # Also rotate the points in world space to keep them consistent with handles
                self.points = [pivot + rot.multVec(p - pivot) for p in self.points]
                self._edit_last_angle = angle
            else:
                idx = self._dragging_idx
                self.points[idx] = new_pt
                opp_idx = _BOX_OPPOSITE[idx]
                fixed_pt = self.points[opp_idx]
                
                # Re-calculate all 8 corners based on the 2 diagonal ones
                wp = self.working_plane
                if wp is not None:
                    inv = wp.inverse()
                    loc1 = inv.multVec(new_pt)
                    loc2 = inv.multVec(fixed_pt)
                else:
                    loc1, loc2 = new_pt, fixed_pt
                
                half = FreeCAD.Vector(abs(loc1.x - loc2.x)/2.0, abs(loc1.y - loc2.y)/2.0, abs(loc1.z - loc2.z)/2.0)
                center = FreeCAD.Vector((loc1.x + loc2.x)/2.0, (loc1.y + loc2.y)/2.0, (loc1.z + loc2.z)/2.0)
                
                pts_local = self._box_corners_local(center, half)
                if wp is not None:
                    self.points = [wp.multVec(p) for p in pts_local]
                else:
                    self.points = pts_local

            self._update_handle_positions(self.points)
            if self.dm_line_set:
                edges = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]
                line_pts = []
                for i, j in edges: line_pts.extend([self.points[i], self.points[j]])
                self.dm_line_set.update_lines(line_pts, segments=[2]*12)
            self.update_preview()
        if self.view:
            self.view.redraw()

    def _field_from_two_corners(self, corner_a_world, corner_b_world):
        """Rebuild SdfBoxField from two opposite world corners."""
        wp = self.working_plane
        if wp:
            inv = wp.inverse()
            lc_a = inv.multVec(corner_a_world)
            lc_b = inv.multVec(corner_b_world)
        else:
            lc_a, lc_b = corner_a_world, corner_b_world
        cx = (lc_a.x + lc_b.x) / 2.0
        cy = (lc_a.y + lc_b.y) / 2.0
        cz = (lc_a.z + lc_b.z) / 2.0
        sx = max(abs(lc_a.x - lc_b.x), 0.1)
        sy = max(abs(lc_a.y - lc_b.y), 0.1)
        sz = max(abs(lc_a.z - lc_b.z), 0.1)
        placement = self._get_placement()
                
        return SdfBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(sx, sy, sz), placement=placement)

    def _refresh_edit_corners(self, field):
        """Update self.points (8 world corners) from a new SdfBoxField."""
        c, h = field.center, field.half_size
        wp = field.placement
        pts_local = self._box_corners_local(c, h)
        if wp:
            self.points = [wp.multVec(lc) for lc in pts_local]
        else:
            self.points = pts_local

    def _get_preview_field(self):
        if not self._anchor_pt or not self._profile_end:
            return None
        self._rebuild_box_points()
        if len(self.points) < 8:
            return None
        return self._field_from_two_corners(self.points[0], self.points[6])

    def _get_final_field(self):
        if self._is_editing:
            if len(self.points) < 8:
                return None
            return self._field_from_two_corners(self.points[0], self.points[6])
        self._rebuild_box_points()
        if len(self.points) < 8:
            return None
        return self._field_from_two_corners(self.points[0], self.points[6])

    def _get_final_points(self):
        if self._is_editing:
            if len(self.points) < 8:
                return None
            wp = self.working_plane
            if wp is not None:
                inv = wp.inverse()
                return [inv.multVec(p) for p in self.points]
            return list(self.points)
        self._rebuild_box_points()
        if len(self.points) < 8:
            return None
        wp = self.working_plane
        if wp is not None:
            inv = wp.inverse()
            return [inv.multVec(p) for p in self.points]
        return list(self.points)

    def get_parameters(self):
        if len(self.points) < 8: return {}
        wp = self.working_plane
        if wp:
            inv = wp.inverse()
            local_pts = [inv.multVec(p) for p in self.points]
        else:
            local_pts = self.points
        xs = [p.x for p in local_pts]
        ys = [p.y for p in local_pts]
        zs = [p.z for p in local_pts]
        l = max(xs) - min(xs)
        w = max(ys) - min(ys)
        h = max(zs) - min(zs)
        return {"Length": l, "Width": w, "Height": h}

    def set_parameters(self, params):
        if len(self.points) < 8: return
        l = params.get("Length", 1.0)
        w = params.get("Width", 1.0)
        h = params.get("Height", 1.0)
        wp = self.working_plane
        if wp:
            inv = wp.inverse()
            local_pts = [inv.multVec(p) for p in self.points]
        else:
            local_pts = self.points
        xs = [p.x for p in local_pts]; cx = (max(xs) + min(xs))/2
        ys = [p.y for p in local_pts]; cy = (max(ys) + min(ys))/2
        zs = [p.z for p in local_pts]; cz = (max(zs) + min(zs))/2
        center = FreeCAD.Vector(cx, cy, cz)
        half = FreeCAD.Vector(l/2.0, w/2.0, h/2.0)
        pts_local = self._box_corners_local(center, half)
        if wp:
            self.points = [wp.multVec(p) for p in pts_local]
        else:
            self.points = pts_local
        self._update_handle_positions(self.points)
        if hasattr(self, "panel") and self.panel:
            self.panel.update_ui()
        self.update_preview()
        if self.view:
            self.view.redraw()

    def _get_edit_preview_field(self):
        if len(self.points) < 8:
            return None
        return self._field_from_two_corners(self.points[0], self.points[6])


class SphereCreator(PrimitiveCreatorBase):

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY]

    def get_command_id(self):
        return "DM_CreateSphere"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None

    def edit_object(self, obj):
        super().edit_object(obj) # loads self.points from obj.Points
        if not self.points:
            # Reconstruct fallback if Points property is empty
            field = getattr(obj.Proxy, "SdfField", None)
            if field:
                loc_c = field.center
                r = field.radius if hasattr(field, "radius") else 10.0
                self.points = [self.to_global(loc_c), self.to_global(loc_c + FreeCAD.Vector(r, 0, 0))]
        
        self.state = ToolState.IDLE

        r = self._compute_handle_radius()
        for pt in self.points:
            if pt is not None:
                dm_pt = DMPoint(pt)
                dm_pt.draw_point(self.points_root, r)
                self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    def _sync_edit_points(self):
        for i in range(len(self.dm_points)):
            if i < len(self.points):
                self.points[i] = self.dm_points[i].position

    # ------------------------------------------------------------------
    # Creation stage hooks
    # ------------------------------------------------------------------

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self.points = [pos, pos]
        elif state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos

    def _sphere_field_from_points(self):
        if len(self.points) < 2:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        radius = (loc_r - loc_c).Length
        if radius < 0.01:
            return None
        return SdfSphereField(loc_c, radius, placement=self._get_placement())

    def _get_preview_field(self):
        return self._sphere_field_from_points()

    def _get_edit_preview_field(self):
        return self._sphere_field_from_points()

    def _get_final_field(self):
        return self._sphere_field_from_points()

    def _get_final_points(self):
        if len(self.points) < 2:
            return None
        return [self.to_local(p) for p in self.points]

    def get_parameters(self):
        if len(self.points) < 2: return {}
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        return {"Radius": (loc_r - loc_c).Length}

    def set_parameters(self, params):
        if len(self.points) < 2: return
        r = params.get("Radius", 1.0)
        loc_c = self.to_local(self.points[0])
        loc_r = loc_c + FreeCAD.Vector(r, 0, 0)
        self.points[1] = self.to_global(loc_r)
        self._update_handle_positions(self.points)
        if hasattr(self, "panel") and self.panel:
            self.panel.update_ui()
        self.update_preview()
        if self.view:
            self.view.redraw()


class CylinderCreator(PrimitiveCreatorBase):

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.DRAG_Z]

    def get_command_id(self):
        return "DM_CreateCylinder"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_base = None

    def edit_object(self, obj):
        super().edit_object(obj)
        pts = list(getattr(self, "points", []))
        if len(pts) >= 3:
            self.current_point = pts[2]
            self.points = pts
        elif len(pts) >= 2:
            self.current_point = pts[1]
            self.points = pts
        self.state = ToolState.IDLE

        r = self._compute_handle_radius()
        for pt in self.points:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r)
            self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    def _sync_edit_points(self):
        for i in range(len(self.dm_points)):
            if i < len(self.points):
                self.points[i] = self.dm_points[i].position
            elif i == len(self.points):
                self.points.append(self.dm_points[i].position)

    # ------------------------------------------------------------------
    # Creation stage hooks
    # ------------------------------------------------------------------

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self.points = [pos, pos]
            self._field_placement = self._get_placement()
        elif state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos
            self._height_drag_base = self.points[0]
            self.current_point = self.points[0]
            if len(self.points) < 3:
                self.points.append(self.points[0])
        elif state == ToolState.DRAG_Z:
            if len(self.points) >= 3:
                self.points[2] = pos
            self.current_point = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos
        elif state == ToolState.DRAG_Z:
            if len(self.points) >= 3:
                self.points[2] = pos
            self.current_point = pos

    def _cylinder_field_from_points(self):
        if len(self.points) < 3:
            return None
        loc_base = self.to_local(self.points[0])
        loc_rad  = self.to_local(self.points[1])
        loc_h    = self.to_local(self.points[2])
        radius = math.sqrt((loc_rad.x - loc_base.x)**2 + (loc_rad.y - loc_base.y)**2)
        height = loc_h.z - loc_base.z
        if radius < 0.01:
            return None
        if abs(height) < 0.01:
            height = 0.01 if height >= 0 else -0.01
        fp = self._get_placement()
        return SdfCylinderField(loc_base, FreeCAD.Vector(0, 0, 1), radius, height, placement=fp)

    def _get_preview_field(self):
        if len(self.points) < 2:
            return None
        # During DRAG_XY, only 2 points set: show a flat disk preview
        if len(self.points) == 2 or self.points[0] == self.points[2]:
            loc_base = self.to_local(self.points[0])
            loc_rad  = self.to_local(self.points[1])
            radius = math.sqrt((loc_rad.x - loc_base.x)**2 + (loc_rad.y - loc_base.y)**2)
            if radius < 0.01:
                return None
            fp = self._get_placement()
            return SdfCylinderField(loc_base, FreeCAD.Vector(0, 0, 1), radius, 1.0, placement=fp)
        return self._cylinder_field_from_points()

    def _get_edit_preview_field(self):
        return self._cylinder_field_from_points()

    def _get_final_field(self):
        return self._cylinder_field_from_points()

    def _get_final_points(self):
        if not self.points:
            return None
        return [self.to_local(p) for p in self.points]

    def get_parameters(self):
        if len(self.points) < 3: return {}
        loc_base = self.to_local(self.points[0])
        loc_rad = self.to_local(self.points[1])
        loc_h = self.to_local(self.points[2])
        radius = math.sqrt((loc_rad.x - loc_base.x)**2 + (loc_rad.y - loc_base.y)**2)
        height = loc_h.z - loc_base.z
        return {"Radius": radius, "Height": height}

    def set_parameters(self, params):
        if len(self.points) < 3: return
        r = params.get("Radius", 1.0)
        h = params.get("Height", 1.0)
        loc_base = self.to_local(self.points[0])
        loc_rad = loc_base + FreeCAD.Vector(r, 0, 0)
        loc_h = loc_base + FreeCAD.Vector(0, 0, h)
        self.points[1] = self.to_global(loc_rad)
        self.points[2] = self.to_global(loc_h)
        self._update_handle_positions(self.points)
        if hasattr(self, "panel") and self.panel:
            self.panel.update_ui()
        self.update_preview()
        if self.view:
            self.view.redraw()


class TorusCreator(PrimitiveCreatorBase):
    """3-click torus creation: anchor → major radius → tube radius."""

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.CUSTOM_1]

    def get_command_id(self):
        return "DM_CreateTorus"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None

    def edit_object(self, obj):
        super().edit_object(obj)
        if not self.points:
            field = getattr(obj.Proxy, "SdfField", None)
            if field:
                loc_c = field.center
                R = field.major_radius
                r = field.tube_radius
                self.points = [
                    self.to_global(loc_c),
                    self.to_global(loc_c + FreeCAD.Vector(R, 0, 0)),
                    self.to_global(loc_c + FreeCAD.Vector(R + r, 0, 0)),
                ]
        self.state = ToolState.IDLE
        hr = self._compute_handle_radius()
        for pt in self.points:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, hr)
            self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    def _sync_edit_points(self):
        for i in range(len(self.dm_points)):
            if i < len(self.points):
                self.points[i] = self.dm_points[i].position

    # ------------------------------------------------------------------
    # Creation stage hooks
    # ------------------------------------------------------------------

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self.points = [pos, pos, pos]
            self._field_placement = self._get_placement()
        elif state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos
            # Start CUSTOM_1 from the same plane; keep tube point at ring edge
            self.points[2] = pos
        elif state == ToolState.CUSTOM_1:
            if len(self.points) >= 3:
                self.points[2] = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos
                self.points[2] = pos
        elif state == ToolState.CUSTOM_1:
            if len(self.points) >= 3:
                self.points[2] = pos

    # ------------------------------------------------------------------
    # Field builders
    # ------------------------------------------------------------------

    def _major_radius(self):
        if len(self.points) < 2:
            return 0.0
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        return math.sqrt((loc_r.x - loc_c.x) ** 2 + (loc_r.y - loc_c.y) ** 2)

    def _torus_field_from_points(self):
        if len(self.points) < 3:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        loc_t = self.to_local(self.points[2])
        major_r = math.sqrt((loc_r.x - loc_c.x) ** 2 + (loc_r.y - loc_c.y) ** 2)
        if major_r < 0.5:
            return None
        dist_t = math.sqrt((loc_t.x - loc_c.x) ** 2 + (loc_t.y - loc_c.y) ** 2)
        tube_r = max(abs(dist_t - major_r), 0.5)
        fp = self._get_placement()
        return SdfTorusField(loc_c, major_r, tube_r, placement=fp)

    def _get_preview_field(self):
        if len(self.points) < 2:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        major_r = math.sqrt((loc_r.x - loc_c.x) ** 2 + (loc_r.y - loc_c.y) ** 2)
        if major_r < 0.5:
            return None
        # During DRAG_XY only 2 unique points: show a thin ring as preview
        if len(self.points) < 3 or self.points[1] == self.points[2]:
            tube_r = max(major_r * 0.15, 1.0)
            fp = self._get_placement()
            return SdfTorusField(loc_c, major_r, tube_r, placement=fp)
        return self._torus_field_from_points()

    def _get_edit_preview_field(self):
        return self._torus_field_from_points()

    def _get_final_field(self):
        return self._torus_field_from_points()

    def _get_final_points(self):
        if not self.points:
            return None
        return [self.to_local(p) for p in self.points]

    def get_parameters(self):
        if len(self.points) < 3: return {}
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        loc_t = self.to_local(self.points[2])
        major_r = math.sqrt((loc_r.x - loc_c.x)**2 + (loc_r.y - loc_c.y)**2)
        dist_t = math.sqrt((loc_t.x - loc_c.x)**2 + (loc_t.y - loc_c.y)**2)
        tube_r = abs(dist_t - major_r)
        return {"Major Radius": major_r, "Tube Radius": tube_r}

    def set_parameters(self, params):
        if len(self.points) < 3: return
        R = params.get("Major Radius", 10.0)
        r = params.get("Tube Radius", 2.0)
        loc_c = self.to_local(self.points[0])
        loc_r = loc_c + FreeCAD.Vector(R, 0, 0)
        loc_t = loc_c + FreeCAD.Vector(R + r, 0, 0)
        self.points[1] = self.to_global(loc_r)
        self.points[2] = self.to_global(loc_t)
        self._update_handle_positions(self.points)
        if hasattr(self, "panel") and self.panel:
            self.panel.update_ui()
        self.update_preview()
        if self.view:
            self.view.redraw()

    # ------------------------------------------------------------------
    # Edit drag
    # ------------------------------------------------------------------

    def _drag_update(self):
        if self._drag_check_lmb_released():
            return
        if self._dragging_idx is None:
            return

        if self._dragging_idx in ('center', 'rot') or (
                isinstance(self._dragging_idx, str) and self._dragging_idx.startswith('gizmo_')):
            super()._drag_update()
            return

        from PySide import QtGui
        mods = QtGui.QApplication.keyboardModifiers()
        is_ctrl = bool(mods & QtCore.Qt.ControlModifier)

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        new_pt = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos}, self._drag_plane_n, self._drag_plane_o,
            place_on_geometry=False
        )
        if new_pt:
            if is_ctrl:
                delta = new_pt - self.points[self._dragging_idx]
                self.points = [p + delta for p in self.points]
                if self.working_plane:
                    self.working_plane.Base += delta
            else:
                self.points[self._dragging_idx] = new_pt
            self._update_handle_positions(self.points)
            self.update_preview()
        if self.view:
            self.view.redraw()


class PrismCreator(PrimitiveCreatorBase):
    """N-sided regular prism via polygon extrusion. Default 6 sides (hexagonal prism).

    3-click creation: anchor (center) → XY circumradius → Z half-height.
    The solid extends ±half-height from the anchor along local Z.
    """

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.DRAG_Z]
    _DEFAULT_SIDES = 6

    def get_command_id(self):
        return "DM_CreatePrism"

    def __init__(self):
        super().__init__()
        self.points = []
        self.n_sides = self._DEFAULT_SIDES
        self._height_drag_base = None

    def edit_object(self, obj):
        super().edit_object(obj)
        # points[3] encodes n_sides in its .x component (stored in local space)
        if len(self.points) >= 4:
            ns = round(self.to_local(self.points[3]).x)
            if 3 <= ns <= 64:
                self.n_sides = ns
        self.state = ToolState.IDLE
        r = self._compute_handle_radius()
        for pt in self.points[:3]:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r)
            self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    def _sync_edit_points(self):
        for i in range(min(len(self.dm_points), 3)):
            if i < len(self.points):
                self.points[i] = self.dm_points[i].position

    # ── Creation stage hooks ──────────────────────────────────────────────────

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self._anchor_pt = pos
            self.points = [pos, pos, pos]
            self._field_placement = self._get_placement()
        elif state == ToolState.DRAG_XY:
            self.points[1] = pos
            self._height_drag_base = self.points[0]
            if len(self.points) < 3:
                self.points.append(self.points[0])
        elif state == ToolState.DRAG_Z:
            self.points[2] = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY and len(self.points) >= 2:
            self.points[1] = pos
        elif state == ToolState.DRAG_Z and len(self.points) >= 3:
            self.points[2] = pos

    # ── Field builders ────────────────────────────────────────────────────────

    def _prism_field(self):
        if len(self.points) < 2:
            return None
        loc_center = self.to_local(self.points[0])
        loc_rad = self.to_local(self.points[1])
        dx = loc_rad.x - loc_center.x
        dy = loc_rad.y - loc_center.y
        radius = math.sqrt(dx * dx + dy * dy)
        if radius < 0.1:
            return None

        if len(self.points) >= 3:
            loc_h = self.to_local(self.points[2])
            half_h = abs(loc_h.z - loc_center.z)
        else:
            half_h = radius * 0.5

        if half_h < 0.01:
            half_h = 0.01

        n = self.n_sides
        vertices = [
            (loc_center.x + radius * math.cos(2.0 * math.pi * i / n),
             loc_center.y + radius * math.sin(2.0 * math.pi * i / n))
            for i in range(n)
        ]
        profile = Sdf2dPolygon(vertices)
        return SdfExtrusionField(profile, height=2.0 * half_h, placement=self._get_placement())

    def _get_preview_field(self):
        return self._prism_field()

    def _get_edit_preview_field(self):
        return self._prism_field()

    def _get_final_field(self):
        return self._prism_field()

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        pts = [self.to_local(p) for p in self.points[:3]]
        pts.append(FreeCAD.Vector(float(self.n_sides), 0.0, 0.0))
        return pts

    def get_parameters(self):
        if len(self.points) < 3:
            return {}
        loc_center = self.to_local(self.points[0])
        loc_rad = self.to_local(self.points[1])
        loc_h = self.to_local(self.points[2])
        radius = math.sqrt((loc_rad.x - loc_center.x) ** 2 + (loc_rad.y - loc_center.y) ** 2)
        half_h = abs(loc_h.z - loc_center.z)
        return {"Radius": radius, "Height": 2.0 * half_h}

    def set_parameters(self, params):
        if len(self.points) < 3:
            return
        r = params.get("Radius", 10.0)
        h = params.get("Height", 20.0)
        loc_center = self.to_local(self.points[0])
        self.points[1] = self.to_global(loc_center + FreeCAD.Vector(r, 0, 0))
        self.points[2] = self.to_global(loc_center + FreeCAD.Vector(0, 0, h * 0.5))
        self._update_handle_positions(self.points)
        if hasattr(self, "panel") and self.panel:
            self.panel.update_ui()
        self.update_preview()
        if self.view:
            self.view.redraw()


class RevolveCreator(PrimitiveCreatorBase):
    """Revolves a circle profile around the local Z axis.

    3-click creation: anchor (center) → ring offset point (XY) → tube radius point.
    offset=0 collapses to a sphere; offset>0 gives a toroidal ring.
    """

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.CUSTOM_1]

    def get_command_id(self):
        return "DM_CreateRevolve"

    def __init__(self):
        super().__init__()
        self.points = []

    def edit_object(self, obj):
        super().edit_object(obj)
        if not self.points:
            field = getattr(obj.Proxy, "SdfField", None)
            if field and hasattr(field, "offset") and hasattr(field, "profile"):
                offset = field.offset
                tube_r = getattr(field.profile, "radius", 5.0)
                loc_c = FreeCAD.Vector(0.0, 0.0, 0.0)
                self.points = [
                    self.to_global(loc_c),
                    self.to_global(loc_c + FreeCAD.Vector(max(offset, tube_r), 0, 0)),
                    self.to_global(loc_c + FreeCAD.Vector(offset + tube_r, 0, 0)),
                ]
        self.state = ToolState.IDLE
        r = self._compute_handle_radius()
        for pt in self.points:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r)
            self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    def _sync_edit_points(self):
        for i in range(len(self.dm_points)):
            if i < len(self.points):
                self.points[i] = self.dm_points[i].position

    # ── Creation stage hooks ──────────────────────────────────────────────────

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self._anchor_pt = pos
            self.points = [pos, pos, pos]
            self._field_placement = self._get_placement()
        elif state == ToolState.DRAG_XY:
            self.points[1] = pos
            self.points[2] = pos
        elif state == ToolState.CUSTOM_1:
            self.points[2] = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY and len(self.points) >= 2:
            self.points[1] = pos
            self.points[2] = pos
        elif state == ToolState.CUSTOM_1 and len(self.points) >= 3:
            self.points[2] = pos

    # ── Field builders ────────────────────────────────────────────────────────

    def _ring_and_tube(self):
        """Return (ring_offset, tube_radius) in local units, or None."""
        if len(self.points) < 2:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        ring_d = math.sqrt((loc_r.x - loc_c.x) ** 2 + (loc_r.y - loc_c.y) ** 2)

        if len(self.points) >= 3 and self.points[1] != self.points[2]:
            loc_t = self.to_local(self.points[2])
            dt = math.sqrt((loc_t.x - loc_c.x) ** 2 + (loc_t.y - loc_c.y) ** 2)
            tube_r = max(abs(dt - ring_d), 0.5)
        else:
            tube_r = max(ring_d * 0.15, 1.0)

        return ring_d, tube_r

    def _revolve_field(self):
        result = self._ring_and_tube()
        if result is None:
            return None
        ring_d, tube_r = result
        if ring_d < 0.1 and tube_r < 0.1:
            return None
        profile = Sdf2dCircle(tube_r)
        return SdfRevolutionField(profile, offset=ring_d, placement=self._get_placement())

    def _get_preview_field(self):
        result = self._ring_and_tube()
        if result is None:
            return None
        ring_d, tube_r = result
        if ring_d < 0.1 and tube_r < 0.1:
            return None
        profile = Sdf2dCircle(tube_r)
        return SdfRevolutionField(profile, offset=ring_d, placement=self._get_placement())

    def _get_edit_preview_field(self):
        return self._revolve_field()

    def _get_final_field(self):
        return self._revolve_field()

    def _get_final_points(self):
        if not self.points:
            return None
        return [self.to_local(p) for p in self.points]

    def get_parameters(self):
        result = self._ring_and_tube()
        if result is None:
            return {}
        ring_d, tube_r = result
        return {"Ring Offset": ring_d, "Tube Radius": tube_r}

    def set_parameters(self, params):
        if len(self.points) < 3:
            return
        offset = params.get("Ring Offset", 10.0)
        tube_r = params.get("Tube Radius", 2.0)
        loc_c = self.to_local(self.points[0])
        self.points[1] = self.to_global(loc_c + FreeCAD.Vector(offset, 0, 0))
        self.points[2] = self.to_global(loc_c + FreeCAD.Vector(offset + tube_r, 0, 0))
        self._update_handle_positions(self.points)
        if hasattr(self, "panel") and self.panel:
            self.panel.update_ui()
        self.update_preview()
        if self.view:
            self.view.redraw()


class CurveExtrudeCreator(PrimitiveCreatorBase):
    """
    Extrudes a selected closed curve into an SDF solid using exact cubic Bezier distance.
    Uses obj.Placement directly as the extrusion direction (2D curves only).
    """

    CREATION_STEPS = [ToolState.DRAG_Z]

    def get_command_id(self):
        return "DM_ExtrudeCurve"

    def __init__(self):
        super().__init__()
        self._curve_obj      = None
        self._height         = 5.0   # full extrusion height in mm
        self._bezier_segs    = None
        # GLSL-stable field cache: id() must stay constant to avoid shader recompiles
        self._cached_profile = None   # Sdf2dNurbsCurveField
        self._cached_extrude = None   # SdfExtrusionField

        # Visual handle state
        self._base_dm_pts     = []    # DMPoint spheres on base curve
        self._top_dm_pts      = []    # DMPoint spheres on top curve
        self._base_wire       = None  # DMLineSet: base curve outline
        self._top_wire        = None  # DMLineSet: top curve outline
        self._connector_lines = None  # DMLineSet: vertical edges between rings

        import FreeCADGui
        for obj in FreeCADGui.Selection.getSelection():
            if getattr(obj, "ShapeType", None) == "curve" and getattr(obj, "Closed", False):
                self._curve_obj = obj
                break

        if self._curve_obj is not None:
            from core.sdf.curve_sampler import (
                sample_curve_world_pts, compute_best_fit_placement,
                extract_bezier_segments_in_placement,
            )
            world_pts = sample_curve_world_pts(self._curve_obj, n_samples=64)
            best_fit  = compute_best_fit_placement(
                world_pts, fallback_placement=self._curve_obj.Placement
            )
            if best_fit is None:
                best_fit = self._curve_obj.Placement
            self.working_plane              = best_fit
            self._working_plane_is_fallback = False
            self._anchor_pt                 = best_fit.Base
            self._bezier_segs               = extract_bezier_segments_in_placement(
                self._curve_obj, best_fit
            )
            self._update_extrude_handles()

    # Prevent _detect_selected_workplane from overriding the curve's placement
    def _detect_selected_workplane(self):
        pass

    def _clamp_height(self, h):
        return max(0.1, h)

    def _on_stage_accept(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            wp   = self.working_plane
            norm = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
            self._height = self._clamp_height((pos - self._anchor_pt).dot(norm))
            self._update_extrude_handles()

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            wp   = self.working_plane
            norm = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
            self._height = self._clamp_height((pos - self._anchor_pt).dot(norm))
            self._update_extrude_handles()

    @staticmethod
    def _sample_seg_world(p0, p1, p2, p3, wp, n=12):
        """Sample n points on a cubic Bezier segment (2D local) → world FreeCAD.Vectors.
        Excludes the endpoint (t=1) so segments can be concatenated without duplicates."""
        pts = []
        for i in range(n):
            t = i / n
            s = 1.0 - t
            x = s**3*p0[0] + 3*s**2*t*p1[0] + 3*s*t**2*p2[0] + t**3*p3[0]
            y = s**3*p0[1] + 3*s**2*t*p1[1] + 3*s*t**2*p2[1] + t**3*p3[1]
            pts.append(wp.multVec(FreeCAD.Vector(x, y, 0)))
        return pts

    def _update_extrude_handles(self):
        """Draw/update base + top curve rings and vertical connector lines."""
        if not self._bezier_segs or not self.working_plane:
            return
        wp   = self.working_plane
        norm = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        offset = norm * self._height
        r = self._compute_handle_radius()

        # Control points = first endpoint of each segment (closed curve, so they cover all nodes)
        base_ctrl = [wp.multVec(FreeCAD.Vector(p0[0], p0[1], 0))
                     for p0, p1, p2, p3 in self._bezier_segs]
        top_ctrl  = [p + offset for p in base_ctrl]

        # Ensure enough DMPoint spheres exist for both rings
        while len(self._base_dm_pts) < len(base_ctrl):
            dm = DMPoint(base_ctrl[len(self._base_dm_pts)])
            dm.draw_point(self.points_root, r, color=(1.0, 0.5, 0.0))
            self._base_dm_pts.append(dm)
        while len(self._top_dm_pts) < len(top_ctrl):
            dm = DMPoint(top_ctrl[len(self._top_dm_pts)])
            dm.draw_point(self.points_root, r, color=(1.0, 0.5, 0.0))
            self._top_dm_pts.append(dm)

        for i, pt in enumerate(base_ctrl):
            self._base_dm_pts[i].position = pt
            self._base_dm_pts[i].update_draw(radius=r)
        for i, pt in enumerate(top_ctrl):
            self._top_dm_pts[i].position = pt
            self._top_dm_pts[i].update_draw(radius=r)

        # Sample the closed bezier wire (n points per segment, no duplicate joints)
        wire_base = []
        for seg in self._bezier_segs:
            wire_base.extend(self._sample_seg_world(*seg, wp=wp))
        wire_base.append(wire_base[0])   # close the loop
        wire_top = [p + offset for p in wire_base]

        if self._base_wire is None:
            self._base_wire = DMLineSet(self.points_root, color=(1.0, 0.5, 0.0), width=2.0)
        self._base_wire.update_lines(wire_base)

        if self._top_wire is None:
            self._top_wire = DMLineSet(self.points_root, color=(1.0, 0.5, 0.0), width=2.0)
        self._top_wire.update_lines(wire_top)

        # Vertical connectors: one 2-point line per control point pair
        conn_pts    = []
        conn_counts = []
        for b, t in zip(base_ctrl, top_ctrl):
            conn_pts.extend([b, t])
            conn_counts.append(2)
        if self._connector_lines is None:
            self._connector_lines = DMLineSet(self.points_root, color=(0.8, 0.8, 0.8), width=1.0)
        self._connector_lines.update_lines(conn_pts, conn_counts)

    def _offset_placement(self):
        """Working plane shifted height/2 along its normal - centers the ±height/2 extrusion."""
        wp   = self.working_plane
        norm = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        return FreeCAD.Placement(wp.Base + norm * (self._height * 0.5), wp.Rotation)

    def _update_field_inplace(self, field):
        """Sync height and placement on an existing SdfExtrusionField without changing id()."""
        import numpy as np
        field.height = self._height
        op = self._offset_placement()
        field.placement = op
        m = op.toMatrix()
        m.invert()
        field.inv_matrix = np.array([
            [m.A11, m.A12, m.A13, m.A14],
            [m.A21, m.A22, m.A23, m.A24],
            [m.A31, m.A32, m.A33, m.A34],
            [m.A41, m.A42, m.A43, m.A44],
        ], dtype=np.float32)

    def _extrude_field(self):
        if self._height < 0.01 or not self._curve_obj:
            return None
        from core.sdf.sdf2d.bezier_curve import Sdf2dBezierCurve
        from core.sdf.sdf_extrusion import SdfExtrusionField

        if self._cached_profile is None:
            if self._bezier_segs:
                self._cached_profile = Sdf2dBezierCurve(self._bezier_segs)
            else:
                return None

        if self._cached_extrude is None:
            self._cached_extrude = SdfExtrusionField(
                self._cached_profile,
                height=self._height,
                placement=self._offset_placement(),
            )
        else:
            self._update_field_inplace(self._cached_extrude)

        return self._cached_extrude

    def _get_preview_field(self):       return self._extrude_field()
    def _get_edit_preview_field(self):  return self._extrude_field()
    def _get_final_field(self):         return self._extrude_field()

    def _get_final_points(self):
        return [FreeCAD.Vector(0.0, 0.0, self._height)] if self._curve_obj else None

    def get_parameters(self):
        return {"Height": self._height}

    def set_parameters(self, params):
        self._height = self._clamp_height(params.get("Height", 10.0))
        if self._cached_extrude is not None:
            self._update_field_inplace(self._cached_extrude)
        self._update_extrude_handles()
        self.update_preview()

    def _primitive_name(self):
        return "CurveExtrude"

    def finish(self):
        """One-shot tool: commit then terminate (prevents a second object being created)."""
        if getattr(self, "_is_editing", False):
            super().finish()   # commits edits, resets to DRAG_Z
            self._finished = True
            self.terminate()
        elif self.is_in_progress():
            self._finalize_object(self._primitive_name(), terminate=True)
        else:
            self.terminate()


class CurveExtrude3DCreator(PrimitiveCreatorBase):
    """
    Creates a round tube/pipe swept along a selected 3D curve path.
    Works with open and closed curves of any 3D shape.
    The user drags to set the tube radius.
    """

    CREATION_STEPS = [ToolState.DRAG_Z]

    def get_command_id(self):
        return "DM_CurvePipe"

    def __init__(self):
        super().__init__()
        self._curve_obj   = None
        self._radius      = 3.0
        self._segs_3d     = None   # list of (p0,p1,p2,p3), each pi = (x,y,z) world
        self._cached_pipe = None   # SdfPipeField — kept stable to avoid recompile

        # Visuals
        self._path_wire     = None  # DMLineSet: sampled curve path
        self._ctrl_dm_pts   = []    # DMPoint spheres at control points
        self._radius_circle = None  # DMLineSet: cross-section circle at path start

        import FreeCADGui
        for obj in FreeCADGui.Selection.getSelection():
            if getattr(obj, "ShapeType", None) == "curve":
                self._curve_obj = obj
                break

        if self._curve_obj is not None:
            from core.sdf.curve_sampler import extract_bezier_segments_3d
            self._segs_3d = extract_bezier_segments_3d(self._curve_obj)

            # Working plane: horizontal at the centroid of all control points
            pts = list(getattr(self._curve_obj, "Points", []))
            pl  = self._curve_obj.Placement
            world_pts = [pl.multVec(p) for p in pts]
            if world_pts:
                cx = sum(p.x for p in world_pts) / len(world_pts)
                cy = sum(p.y for p in world_pts) / len(world_pts)
                cz = sum(p.z for p in world_pts) / len(world_pts)
                centroid = FreeCAD.Vector(cx, cy, cz)
            else:
                centroid = FreeCAD.Vector(0, 0, 0)
            self.working_plane              = FreeCAD.Placement(centroid, FreeCAD.Rotation())
            self._working_plane_is_fallback = False
            self._anchor_pt                 = centroid
            self._update_pipe_handles()

    def _detect_selected_workplane(self):
        pass

    def _clamp_radius(self, r):
        return max(0.1, r)

    def _on_stage_accept(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            self._radius = self._clamp_radius((pos - self._anchor_pt).Length)
            if self._cached_pipe is not None:
                self._cached_pipe.radius = self._radius
            self._update_pipe_handles()

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            self._radius = self._clamp_radius((pos - self._anchor_pt).Length)
            if self._cached_pipe is not None:
                self._cached_pipe.radius = self._radius
            self._update_pipe_handles()

    @staticmethod
    def _sample_seg_3d_world(p0, p1, p2, p3, n=12):
        """Sample n evenly-spaced points on a 3D cubic Bezier (excludes t=1)."""
        pts = []
        for i in range(n):
            t = i / n
            s = 1.0 - t
            x = s**3*p0[0] + 3*s**2*t*p1[0] + 3*s*t**2*p2[0] + t**3*p3[0]
            y = s**3*p0[1] + 3*s**2*t*p1[1] + 3*s*t**2*p2[1] + t**3*p3[1]
            z = s**3*p0[2] + 3*s**2*t*p1[2] + 3*s*t**2*p2[2] + t**3*p3[2]
            pts.append(FreeCAD.Vector(x, y, z))
        return pts

    @staticmethod
    def _circle_pts(center, tangent, radius, n=24):
        """Sample a circle of `radius` at `center` in the plane perpendicular to `tangent`."""
        import math
        n_vec = FreeCAD.Vector(tangent).normalize() if tangent.Length > 1e-6 else FreeCAD.Vector(0, 0, 1)
        if abs(n_vec.z) < 0.9:
            u = FreeCAD.Vector(0, 0, 1).cross(n_vec)
        else:
            u = FreeCAD.Vector(1, 0, 0).cross(n_vec)
        if u.Length < 1e-6:
            u = FreeCAD.Vector(1, 0, 0)
        u.normalize()
        v = n_vec.cross(u)
        pts = []
        for i in range(n + 1):
            a = 2.0 * math.pi * i / n
            pts.append(center + u * (radius * math.cos(a)) + v * (radius * math.sin(a)))
        return pts

    def _update_pipe_handles(self):
        if not self._segs_3d:
            return
        r = self._compute_handle_radius()

        # Control points (one per segment start)
        ctrl_world = [FreeCAD.Vector(*seg[0]) for seg in self._segs_3d]
        if not getattr(self._curve_obj, "Closed", False):
            ctrl_world.append(FreeCAD.Vector(*self._segs_3d[-1][3]))

        while len(self._ctrl_dm_pts) < len(ctrl_world):
            dm = DMPoint(ctrl_world[len(self._ctrl_dm_pts)])
            dm.draw_point(self.points_root, r, color=(0.3, 0.8, 1.0))
            self._ctrl_dm_pts.append(dm)
        for i, pt in enumerate(ctrl_world):
            self._ctrl_dm_pts[i].position = pt
            self._ctrl_dm_pts[i].update_draw(radius=r)

        # Path wire
        is_closed = getattr(self._curve_obj, "Closed", False)
        wire_pts = []
        for seg in self._segs_3d:
            wire_pts.extend(self._sample_seg_3d_world(*seg))
        if is_closed and wire_pts:
            wire_pts.append(wire_pts[0])

        if self._path_wire is None:
            self._path_wire = DMLineSet(self.points_root, color=(0.3, 0.8, 1.0), width=2.0)
        self._path_wire.update_lines(wire_pts)

        # Radius circle at the start of the first segment, perpendicular to its tangent
        p0 = FreeCAD.Vector(*self._segs_3d[0][0])
        p1 = FreeCAD.Vector(*self._segs_3d[0][1])
        tangent = p1 - p0
        circle = self._circle_pts(p0, tangent, self._radius)
        if self._radius_circle is None:
            self._radius_circle = DMLineSet(self.points_root, color=(0.3, 0.8, 1.0), width=1.5)
        self._radius_circle.update_lines(circle)

    def _pipe_field(self):
        if not self._segs_3d:
            return None
        from core.sdf.sdf_pipe import SdfPipeField
        if self._cached_pipe is None:
            self._cached_pipe = SdfPipeField(self._segs_3d, self._radius)
        else:
            self._cached_pipe.radius = self._radius
        return self._cached_pipe

    def _get_preview_field(self):      return self._pipe_field()
    def _get_edit_preview_field(self): return self._pipe_field()
    def _get_final_field(self):        return self._pipe_field()

    def _get_final_points(self):
        return [FreeCAD.Vector(self._radius, 0.0, 0.0)] if self._curve_obj else None

    def get_parameters(self):
        return {"Radius": self._radius}

    def set_parameters(self, params):
        self._radius = self._clamp_radius(params.get("Radius", 3.0))
        if self._cached_pipe is not None:
            self._cached_pipe.radius = self._radius
        self._update_pipe_handles()
        self.update_preview()

    def _primitive_name(self):
        return "CurvePipe"

    def finish(self):
        """One-shot tool: commit then terminate."""
        if getattr(self, "_is_editing", False):
            super().finish()
            self._finished = True
            self.terminate()
        elif self.is_in_progress():
            self._finalize_object(self._primitive_name(), terminate=True)
        else:
            self.terminate()
