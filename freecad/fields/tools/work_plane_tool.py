# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore
from .fld_base import FldBase
from freecad.fields.tools.gizmo_driver import GizmoDriverMixin
from freecad.fields.core.objects.fld_workplane import create_fld_workplane
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager
import math
from pivy import coin
from freecad.fields.core.input.gizmo.models import GizmoFrame, HandleId, Kind, handle_set
from freecad.fields.core.input.gizmo.transform import delta_rotation

class WorkPlaneTaskPanel:
    """Task panel for the Work Plane tool to ensure proper cleanup."""
    def __init__(self, creator):
        self.creator = creator
        from PySide import QtWidgets
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        label = QtWidgets.QLabel("Work Plane Tool active.\n\nClick to drop plane.\nClick a corner to edit it with the gizmo.\n\nESC to cancel.")
        layout.addWidget(label)
        
    def accept(self):
        if hasattr(self.creator, 'finish'):
            self.creator.finish()
        else:
            self.creator.terminate()
        return True
        
    def reject(self):
        self.creator.cancel()
        return True

def _mm(q):
    """A length property's value in mm (a Quantity or a float)."""
    return q.Value if hasattr(q, "Value") else float(q)


# Corners 0-3 in the plane's local x/y, as update_handles draws them.
CORNER_SIGNS = ((-1, -1), (1, -1), (1, 1), (-1, 1))


def _corner_local(length, width, k):
    sx, sy = CORNER_SIGNS[k]
    return FreeCAD.Vector(sx * length / 2.0, sy * width / 2.0, 0)


from freecad.fields.tools.selection_gizmo import SelectionGizmoMixin, SelectionGizmoTarget


class WorkPlaneGizmoTarget(SelectionGizmoTarget):
    """The universal gizmo's `GizmoTarget` for a work plane (Round 10 design). Arrows and
    plane squares move the origin; ring Z spins it about its normal; cubes X/Y resize
    Length/Width. World offers only the moves: a world ring would tilt the plane and world
    cubes don't line up with Length/Width. With a corner selected (Round 10b) the frame sits
    on that corner and an arrow or square moves it: the in-plane parts resize the plane with
    the opposite corner held, the normal part moves the whole plane. The ring and the cubes
    still act about the centre. Every apply is a total from the snapshot. The centre box is
    uniform scale only (Length and Width together), in both spaces: its plain press belongs to
    the centre sphere, which drops the plane onto a surface (`WorkPlaneCreator._tool_press`)."""

    LOCAL = handle_set(move="xyz", planes=("xy", "xz", "yz"), rotate="z", scale="xy",
                       uniform_scale=True)
    WORLD = handle_set(move="xyz", planes=("xy", "xz", "yz"), uniform_scale=True)
    MIN_SIZE = 1.0      # mm; the corner drag's floor (`_drag_update`)

    def live(self):
        return self.tool._plane_obj() is not None

    def rotation(self):
        obj = self.tool._plane_obj()
        return FreeCAD.Rotation(obj.Placement.Rotation) if obj is not None else FreeCAD.Rotation()

    def pivot(self, centre):
        obj = self.tool._plane_obj()
        return FreeCAD.Vector(obj.Placement.Base) if obj is not None else centre

    def offered(self):
        return self.WORLD if self.tool._gizmo_space == "world" else self.LOCAL

    def geometry(self):
        obj = self.tool._plane_obj()
        return {
            "placement": FreeCAD.Placement(obj.Placement),
            "length": _mm(obj.Length),
            "width": _mm(obj.Width),
        }

    def gizmo_apply(self, snap, delta):
        obj = self.tool._plane_obj()
        pl0 = snap["placement"]
        l0 = snap["length"]
        w0 = snap["width"]
        corner = snap["selection"][0] if snap["selection"] else None
        kind = delta.handle.kind
        if kind is Kind.ROTATE:
            pl = FreeCAD.Placement(pl0)
            pl.Rotation = delta_rotation(delta).multiply(pl0.Rotation)
            obj.Placement = pl
        elif kind is Kind.SCALE:
            obj.Length = max(self.MIN_SIZE, l0 * delta.scale["x"])
            obj.Width = max(self.MIN_SIZE, w0 * delta.scale["y"])
        else:
            pl = FreeCAD.Placement(pl0)
            if corner is None:
                pl.move(delta.translation)
            else:
                # The corner follows the translation; the opposite corner stays put in the
                # plane, so the in-plane parts resize and the centre shifts by half.
                sx, sy = CORNER_SIGNS[corner]
                ax, ay, az = (pl0.Rotation.multVec(FreeCAD.Vector(*v))
                              for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
                t = delta.translation
                length = max(self.MIN_SIZE, l0 + sx * t.dot(ax))
                width = max(self.MIN_SIZE, w0 + sy * t.dot(ay))
                pl.Base = (pl0.Base + ax * (sx * (length - l0) / 2.0)
                           + ay * (sy * (width - w0) / 2.0) + az * t.dot(az))
                obj.Length, obj.Width = length, width
            obj.Placement = pl
        self.tool._plane_changed()

    def gizmo_restore(self, snap):
        obj = self.tool._plane_obj()
        obj.Placement = FreeCAD.Placement(snap["placement"])
        obj.Length, obj.Width = snap["length"], snap["width"]
        self.tool._plane_changed()

    def gizmo_commit(self):
        self.tool._plane_changed()


class WorkPlaneCreator(SelectionGizmoMixin, FldBase):
    """Tool to create or move a FldWorkPlane object interactively.

    Document mutation happens on the drag tick (`_drag_update`), the same as
    every other interactive tool -- FldBase already mixes in DragTimerMixin, this
    tool simply never used it. It used to mutate and recompute directly in
    `handle_move`, on every motion event (IF-016); `handle_move` now does hover
    feedback only.
    """
    
    GIZMO_TARGET = WorkPlaneGizmoTarget
    MULTI_SELECT = False

    CORNER_COLOR   = (1.0, 0.5, 0.0)    # orange at rest
    CENTRE_COLOR   = (1.0, 1.0, 0.0)    # yellow
    SELECTED_COLOR = (1.0, 1.0, 1.0)    # white, as the primitives' HANDLE_COLOR_SELECTED
    HOVER_COLOR    = (0.3, 1.0, 0.3)    # green

    def get_command_id(self):
        return "Fields_WorkPlane"

    # --- selection gizmo hooks & properties -------------------------------------------

    @property
    def _selected_corner(self):
        return self._selection[0] if self._selection else None

    @_selected_corner.setter
    def _selected_corner(self, k):
        self._selection = [] if k is None else [k]

    def _select_corner(self, k):
        """Select corner `k` (0-3), or nothing (None): the gizmo moves onto it, or back to
        the centre."""
        self._set_selection([] if k is None else [k])

    def _gizmo_editable(self):
        return self._plane_obj() is not None

    def _pick_point(self, ray_p, ray_d):
        hit_idx, _ = self._hit_test(ray_p, ray_d)
        return None if hit_idx == -1 else hit_idx

    def _is_selectable(self, key):
        return key < 4

    def _point_world(self, key):
        obj = self._plane_obj()
        if obj is None:
            return None
        pl = obj.Placement
        return pl.multVec(_corner_local(_mm(obj.Length), _mm(obj.Width), key))

    def _gizmo_unselected_centre(self):
        obj = self._plane_obj()
        return FreeCAD.Vector(obj.Placement.Base) if obj is not None else None

    def _show_hover(self, key):
        self._set_hover(-1 if key is None else key)

    def _point_drag_handle(self, key):
        return HandleId(Kind.PLANE, ("x", "y")), "local"

    def _selection_changed(self):
        self._recolor_handles()

    def _rest_color(self, i):
        if i == self._selected_corner:
            return self.SELECTED_COLOR
        return self.CORNER_COLOR if i < 4 else self.CENTRE_COLOR

    def _recolor_handles(self):
        for i, mat in enumerate(self.handle_mats):
            c = self.HOVER_COLOR if i == self._hovered_idx else self._rest_color(i)
            mat.diffuseColor.setValue(*c)

    def __init__(self):
        super().__init__()
        # Remove the legacy wp_manager we inherited
        if hasattr(self, 'wp_manager') and self.wp_manager:
            self.wp_manager.hide()
            self.wp_manager = None

        self._preview_obj = None
        self._active_obj = None
        self._editing_obj = None # Existing WP being edited
        self.state = 0 # 0 = waiting for click, 1 = idle, 3 = moving the centre onto a surface
        self._cursor_active = False

        self._is_new = True
        # Create preview object
        try:
            self._preview_obj = create_fld_workplane(name="Fld_WorkPlane_Preview")
            if self._preview_obj:
                self._preview_obj.Label = "Work Plane Preview"
        except Exception as e:
            fld_logger.error(f"WorkPlaneCreator preview creation error: {e}")

        # Setup handles visual — four corners + one center
        self.sg = self.view.getSceneGraph()
        self.handles_root = coin.SoSeparator()
        self.handle_seps = []
        self.handle_mats = []
        self.handle_transforms = []
        self.handle_spheres = []
        for i in range(5):
            sep = coin.SoSeparator()
            light = coin.SoLightModel()
            light.model.setValue(coin.SoLightModel.BASE_COLOR)
            sep.addChild(light)
            mat = coin.SoMaterial()
            c = self.CORNER_COLOR if i < 4 else self.CENTRE_COLOR
            mat.diffuseColor.setValue(*c)
            xf = coin.SoTransform()
            sphere = coin.SoSphere()
            sphere.radius = 5.0  # updated in update_handles
            sep.addChild(mat)
            sep.addChild(xf)
            sep.addChild(sphere)
            self.handles_root.addChild(sep)
            self.handle_seps.append(sep)
            self.handle_mats.append(mat)
            self.handle_transforms.append(xf)
            self.handle_spheres.append(sphere)

        self._hovered_idx = -1

        if self.sg:
            self.sg.addChild(self.handles_root)

        # Show Task Panel to manage lifecycle
        self.task_panel = WorkPlaneTaskPanel(self)
        gui_doc = FreeCADGui.getDocument(self.doc.Name)
        FreeCADGui.Control.showDialog(self.task_panel, gui_doc)
        self._dialog_open = True

        self.update_handles()
        self._sync_drag_timer()   # state 0: the preview follows the cursor on the tick

    def get_handled_types(self):
        return ["FldWorkPlane"]

    def edit_object(self, obj):
        """Load an existing workplane into the tool for editing."""
        super().edit_object(obj)
        fld_logger.debug(f"WorkPlaneCreator: Editing existing object {obj.Label}")
        self._editing_obj = obj
        self.state = 1
        self._sync_drag_timer()   # hover-only state; no preview to follow the cursor
        self._is_new = False
        # Remove the preview if we are editing
        if self._preview_obj:
            try:
                # Targeted doc cleanup instead of full _do_terminate()
                if self.doc and self.doc.getObject(self._preview_obj.Name):
                    self.doc.removeObject(self._preview_obj.Name)
            except Exception as e:
                fld_logger.debug(f"WorkPlaneCreator.edit_object: Failed to remove preview: {e}")
            self._preview_obj = None

        self.update_handles()
        self._init_gizmo()

    def is_in_progress(self):
        """Returns True if a workplane has been dropped (state > 0)."""
        return self.state > 0

    def _do_terminate(self):
        self._stop_drag_timer()
        self._dispose_gizmo()
        try:
            self._restore_cursor()
                
            # Clean up handles (Coin3D)
            if self.sg and self.handles_root:
                self.sg.removeChild(self.handles_root)
        except Exception as e:
            fld_logger.debug(f"WorkPlaneCreator._do_terminate: Failed to remove handles: {e}")
            
        # The rest of the object cleanup (for _preview_obj and _active_obj) 
        # is now handled robustly by super()._do_terminate()
        super()._do_terminate()

    def finish(self):
        """Accept the current workplane and reset for another one."""
        self._dispose_gizmo(commit=True)
        # Stop ticking before committing; reset_state() restarts it if we repeat.
        self._stop_drag_timer()
        self._finished = True
        obj = self._active_obj or self._preview_obj or self._editing_obj
        is_new_creation = self._is_new
        
        if self.state > 0 and obj:
            if is_new_creation:
                obj.Label = "Work Plane"
                fld_logger.info(f"WorkPlane accepted: {obj.Label}")
            else:
                fld_logger.info(f"WorkPlane edit finished: {obj.Label}")
            
            if self.autorepeat and is_new_creation:
                self._finished = False # Reset for next one if we repeat
                self._active_obj = None
                self._preview_obj = None
                self._editing_obj = None
                
                self.reset_state()
                
                # Create fresh preview for the next one (Accept & Repeat)
                try:
                    self._preview_obj = create_fld_workplane(name="Fld_WorkPlane_Preview")
                    self._preview_obj.Label = "Work Plane Preview"
                    self._is_new = True
                except Exception as e:
                    fld_logger.error(f"WorkPlaneCreator repeat preview error: {e}")
            else:
                # If we were editing, or autorepeat is False, we are DONE. Terminate the tool.
                self._finished = True
                self.terminate()
            
            if FreeCAD.ActiveDocument:
                FreeCAD.ActiveDocument.recompute()
        else:
            self.terminate()

    def reset_state(self):
        """Clear internal state for next workplane."""
        self._dispose_gizmo()
        super().reset_state()
        self._recolor_handles()
        # Clear handles visuals if needed (they will be updated on next move)
        self.update_handles()
        # super() put us back in state 0, so the preview tick starts again
        # (Accept & Repeat lands here).
        self._sync_drag_timer()

    def get_camera_facing_placement(self, mouse_pt):
        """Delegated to FldInputManager."""
        return FldInputManager.get_instance().get_view_transform(self.view, mouse_pt)

    def get_oriented_placement(self, event_dict):
        """Delegated to ViewProjector."""
        obj = self._active_obj or self._preview_obj or self._editing_obj
        geo = self.projector.get_geometry_info(event_dict, skip_objects=[obj] if obj else None)
        if geo:
            world_hit, world_n, obj, subname = geo
            # Ensure the normal faces toward the viewer (not into the surface)
            vd = self.view.getViewDirection()
            view_dir = FreeCAD.Vector(vd[0], vd[1], vd[2])
            if world_n.dot(view_dir) > 0:
                world_n = world_n.negative()
            # Basis vectors from normal
            z_axis = world_n
            global_z = FreeCAD.Vector(0, 0, 1)
            x_axis = global_z.cross(z_axis) if abs(z_axis.dot(global_z)) < 0.99 else FreeCAD.Vector(1, 0, 0)
            x_axis.normalize()
            y_axis = z_axis.cross(x_axis); y_axis.normalize()
            m = FreeCAD.Matrix(
                x_axis.x, y_axis.x, z_axis.x, world_hit.x,
                x_axis.y, y_axis.y, z_axis.y, world_hit.y,
                x_axis.z, y_axis.z, z_axis.z, world_hit.z,
                0.0,      0.0,      0.0,      1.0
            )
            plc = FreeCAD.Placement(m)
            plc.Base = self.resolve_world_point(
                event_dict, base_pt=plc.Base, plane_normal=z_axis, plane_origin=plc.Base,
                exclude=[self._preview_obj] if self._preview_obj else [])
            return plc

        # SDF surface snap — snap normal to SDF surface when no NURBS geometry is under mouse
        skip = [obj] if obj else None
        sdf_result = self.projector.get_sdf_hit(event_dict, skip_objects=skip)
        if sdf_result:
            world_hit, world_n, _sdf_obj = sdf_result
            # Ensure normal faces toward viewer
            vd = self.view.getViewDirection()
            view_dir = FreeCAD.Vector(vd[0], vd[1], vd[2])
            if world_n.dot(view_dir) > 0:
                world_n = world_n.negative()
            z_axis = world_n
            global_z = FreeCAD.Vector(0, 0, 1)
            x_axis = global_z.cross(z_axis) if abs(z_axis.dot(global_z)) < 0.99 else FreeCAD.Vector(1, 0, 0)
            x_axis.normalize()
            y_axis = z_axis.cross(x_axis); y_axis.normalize()
            m = FreeCAD.Matrix(
                x_axis.x, y_axis.x, z_axis.x, world_hit.x,
                x_axis.y, y_axis.y, z_axis.y, world_hit.y,
                x_axis.z, y_axis.z, z_axis.z, world_hit.z,
                0.0,      0.0,      0.0,      1.0
            )
            plc = FreeCAD.Placement(m)
            plc.Base = self.resolve_world_point(
                event_dict, base_pt=plc.Base, plane_normal=z_axis, plane_origin=plc.Base,
                exclude=[self._preview_obj] if self._preview_obj else [])
            return plc

        # Fallback to camera-facing plane
        try:
            mouse_pt = FldInputManager.get_instance().get_scene_point(self.view, event_dict)
            
            if not mouse_pt:
                focus = self.view.getFocus() if hasattr(self.view, "getFocus") else FreeCAD.Vector(0,0,0)
                mouse_pt = focus
                
            plc = self.get_camera_facing_placement(mouse_pt)
            if plc:
                cam_normal = plc.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
                plc.Base = self.resolve_world_point(
                    event_dict, base_pt=plc.Base, plane_normal=cam_normal, plane_origin=plc.Base,
                    exclude=[self._preview_obj] if self._preview_obj else [])
            return plc
        except Exception as e:
            fld_logger.debug(f"WorkPlaneCreator: workplane fallback failed: {e}")
        return None

    def _get_initial_size(self, pos):
        if not self.view: return 100.0
        try:
            cam_node = self.view.getCameraNode()
            if not cam_node: return 100.0
            
            cam_vec = cam_node.position.getValue()
            cam_pos_tuple = cam_vec.getValue() if hasattr(cam_vec, "getValue") else (cam_vec[0], cam_vec[1], cam_vec[2])
            cam_pos = FreeCAD.Vector(*cam_pos_tuple)
            dist = (cam_pos - pos).Length
            
            if hasattr(cam_node, 'height') and hasattr(cam_node.height, 'getValue'):
                viewport_height = cam_node.height.getValue()
                scale = viewport_height / 300.0
            else:
                fov = cam_node.heightAngle.getValue() if hasattr(cam_node, 'heightAngle') else 0.785
                viewport_height = 2.0 * dist * math.tan(fov / 2.0)
                scale = viewport_height / 300.0
                
            return max(10.0, 100.0 * scale)
        except Exception as e:
            fld_logger.debug(f"WorkPlaneCreator: _get_initial_size failed: {e}")
            return 100.0

    def update_handles(self):
        obj = self._active_obj or self._preview_obj or self._editing_obj
        if not obj or not hasattr(obj, "Length"):
            # Hide spheres by zeroing scale
            for xf in self.handle_transforms:
                xf.scaleFactor.setValue(0, 0, 0)
            self._update_gizmo()
            return

        l_val = obj.Length.Value if hasattr(obj.Length, "Value") else float(obj.Length)
        w_val = obj.Width.Value if hasattr(obj.Width, "Value") else float(obj.Width)
        l = l_val / 2.0
        w = w_val / 2.0
        corners_local = [
            FreeCAD.Vector(-l, -w, 0),
            FreeCAD.Vector( l, -w, 0),
            FreeCAD.Vector( l,  w, 0),
            FreeCAD.Vector(-l,  w, 0),
        ]
        plc = obj.Placement
        corners_global = [plc.multVec(c) for c in corners_local]
        # Add center point
        corners_global.append(plc.Base)
        
        radius = self._compute_handle_radius(ref_pt=obj.Placement.Base if obj else None)
        for i, (point, xf, sphere) in enumerate(zip(corners_global, self.handle_transforms, self.handle_spheres)):
            xf.translation.setValue(point.x, point.y, point.z)
            xf.scaleFactor.setValue(1, 1, 1)
            sphere.radius = radius if i < 4 else radius * 1.2 # slightly larger center handle
        self._update_gizmo()

    def _get_ray(self, event_dict):
        """Delegated to FldInputManager."""
        return FldInputManager.get_instance().get_ray(self.view, event_dict)

    def _hit_test(self, ray_p, ray_d):
        """
        Hit-test the four corner spheres against a view ray.

        Uses perpendicular distance from the ray to each sphere centre (world
        space).  This works for both perspective and orthographic cameras: for
        orthographic, get_ray() returns (scene_pt_on_focal_plane, view_dir),
        so the ray origin may be at a different depth than the sphere — a
        pure ray-sphere intersection (requiring positive t) would miss.
        Perpendicular distance is depth-independent and always correct.
        """
        obj = self._active_obj or self._preview_obj or self._editing_obj
        if not obj or ray_p is None or ray_d is None:
            return -1, float('inf')

        l_val = obj.Length.Value if hasattr(obj.Length, "Value") else float(obj.Length)
        w_val = obj.Width.Value if hasattr(obj.Width, "Value") else float(obj.Width)
        l = l_val / 2.0
        w = w_val / 2.0
        corners_world = [obj.Placement.multVec(c) for c in [
            FreeCAD.Vector(-l, -w, 0), FreeCAD.Vector(l, -w, 0),
            FreeCAD.Vector(l,  w, 0),  FreeCAD.Vector(-l, w, 0),
        ]]
        corners_world.append(obj.Placement.Base) # index 4 is center
        
        radius = self._compute_handle_radius(ref_pt=obj.Placement.Base if obj else None)

        best_dist = float('inf')
        best_idx = -1
        for i, center in enumerate(corners_world):
            v = center - ray_p
            proj = v.dot(ray_d)
            perp = (ray_p + ray_d * proj - center).Length
            if perp < radius and perp < best_dist:
                best_dist = perp
                best_idx = i
        return best_idx, best_dist

    def on_button1_down(self, event_dict):
        """No gizmo yet (state 0): the click drops the plane. Otherwise the driver: TYPED
        commits (D-49), then the spheres, then the gizmo. A click on nothing in state 1
        deselects and is consumed."""
        if self.gizmo_controller is None:
            return self.handle_click(event_dict)
        return super().on_button1_down(event_dict)

    def _press_missed(self, event_dict):
        if self.state == 1:
            self._set_selection([])
            return True
        return False

    def on_button1_up(self, event_dict):
        if self._gizmo_release():
            return True
        if self.state == 3:
            self.state = 1
            self._sync_drag_timer()
            # Next mouse-move will re-evaluate hover
            self._hovered_idx = -1
            self._recolor_handles()
            return True
        return False

    def handle_click(self, event_dict):
        if getattr(self, "_terminated", False):
            return
        try:
            btn = event_dict.get("Button")
            if btn != QtCore.Qt.LeftButton:
                return False

            if self.state == 0:
                # First click drops the workplane
                placement = self.get_oriented_placement(event_dict)
                if placement:
                    size = self._get_initial_size(placement.Base)
                    if self._preview_obj:
                        self._preview_obj.Placement = placement
                        self._preview_obj.Label = "Work Plane"
                        self._preview_obj.Length = size
                        self._preview_obj.Width = size
                        self._active_obj = self._preview_obj
                        self._preview_obj = None
                    else:
                        self._active_obj = create_fld_workplane(placement=placement)
                        self._active_obj.Length = size
                        self._active_obj.Width = size
                        
                    # Deferred: this runs inside the Qt button-press callback, and
                    # state 1 stops the tick timer that would otherwise pick it up.
                    obj = self._active_obj
                    if obj is not None:
                        QtCore.QTimer.singleShot(
                            0, lambda o=obj: o.Document.recompute([o]))

                    self.state = 1
                    self._sync_drag_timer()
                    self.update_handles()
                    self._init_gizmo()
                return True
                
            elif self.state == 1:
                self._tool_press(event_dict)
                return True

        except Exception:
            fld_logger.exception("WorkPlaneCreator.handle_click error")
            return False
        return False

    def _set_hover(self, idx):
        """Recolour handle spheres and update OS cursor for handle idx (-1 = none)."""
        from PySide import QtCore, QtWidgets
        if idx == self._hovered_idx:
            return
        # Restore previous handle to its rest colour
        if self._hovered_idx != -1:
            self.handle_mats[self._hovered_idx].diffuseColor.setValue(*self._rest_color(self._hovered_idx))
        self._hovered_idx = idx
        if idx == -1:
            if self._cursor_active:
                QtWidgets.QApplication.restoreOverrideCursor()
                self._cursor_active = False
        else:
            self.handle_mats[idx].diffuseColor.setValue(*self.HOVER_COLOR)
            if not self._cursor_active:
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CrossCursor)
                self._cursor_active = True

    def _tool_hover(self, event_dict):
        """State 1: the sphere under the mouse is lit, with the cross cursor."""
        if self.state != 1:
            return False
        return super()._tool_hover(event_dict)

    def _tool_press(self, event_dict):
        """State 1: a corner is selected (the gizmo edits it); the centre deselects and
        starts the move onto the surface under the cursor (state 3). With Shift held the
        centre is left to the gizmo's box under it (uniform scale, D-59); Shift is the
        gizmo host's, the one the controller reads at the same press. False on a miss."""
        if self.state != 1:
            return False
        ray_p, ray_d = self._get_ray(event_dict)
        if ray_p is not None and ray_d is not None:
            hit_idx, _ = self._hit_test(ray_p, ray_d)
            ctl = self.gizmo_controller
            if hit_idx == 4 and ctl is not None and ctl.host.shift_held():
                return False
            if hit_idx == 4:
                self._select_corner(None)
                self.state = 3
                self._sync_drag_timer()
                return True
        return super()._tool_press(event_dict)

    # ── Drag ticks ────────────────────────────────────────────────────────────

    def _sync_drag_timer(self):
        """Run the tick timer exactly while a document-mutating state is live.

        States 0 (preview follows the cursor) and 3 (centre moves onto a
        surface) write to the document continuously; state 1 is hover only. Idempotent, so every state transition can just call it.
        """
        want = self.state in (0, 3) and not getattr(self, "_terminated", False)
        dragging = getattr(self, "_is_dragging", False)
        if want and not dragging:
            self._start_drag_timer()
        elif dragging and not want:
            self._stop_drag_timer()

    def _tool_refresh(self):
        self.update_handles()

    def _tool_drag_update(self):
        """Apply the latest cursor position. Runs on the timer, never in a callback."""
        if getattr(self, "_terminated", False):
            self._stop_drag_timer()
            return
        event_dict = {"Position": FldInputManager.get_instance().get_mouse_pos()}
        try:
            if self.state == 0:
                placement = self.get_oriented_placement(event_dict)
                if placement and self._preview_obj:
                    self._preview_obj.Placement = placement
                    size = self._get_initial_size(placement.Base)
                    self._preview_obj.Length = size
                    self._preview_obj.Width = size
                    if self.doc:
                        self.doc.recompute([self._preview_obj])
                    self.update_handles()

            elif self.state == 3:
                obj = self._active_obj or self._editing_obj
                if obj:
                    placement = self.get_oriented_placement(event_dict)
                    if placement:
                        obj.Placement = placement
                        if self.doc:
                            self.doc.recompute([obj])
                        self.update_handles()
        except Exception as e:
            fld_logger.debug(f"WorkPlaneCreator._drag_update: {e}")

    def _plane_obj(self):
        """The placed plane (dropped, or being edited); None in state 0."""
        return getattr(self, "_active_obj", None) or getattr(self, "_editing_obj", None)

    def _make_gizmo_target(self):
        return WorkPlaneGizmoTarget(self) if self._plane_obj() is not None else None

    def _gizmo_root(self):
        return self.handles_root

    def _plane_changed(self):
        """After the gizmo changed the plane: recompute it and move the spheres (which
        refreshes the gizmo)."""
        obj = self._plane_obj()
        if obj is not None and self.doc:
            self.doc.recompute([obj])
        self.update_handles()


