# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore
from .fld_base import FldBase
from freecad.fields.core.objects.fld_workplane import create_fld_workplane
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager
import math
from pivy import coin

class WorkPlaneTaskPanel:
    """Task panel for the Work Plane tool to ensure proper cleanup."""
    def __init__(self, creator):
        self.creator = creator
        from PySide import QtWidgets
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        label = QtWidgets.QLabel("Work Plane Tool active.\n\nClick to drop plane.\nDrag corners to resize.\n\nESC to cancel.")
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

class WorkPlaneCreator(FldBase):
    """Tool to create or move a FldWorkPlane object interactively.

    Document mutation happens on the drag tick (`_drag_update`), the same as
    every other interactive tool -- FldBase already mixes in DragTimerMixin, this
    tool simply never used it. It used to mutate and recompute directly in
    `handle_move`, on every motion event (IF-016); `handle_move` now does hover
    feedback only.
    """
    
    def get_command_id(self):
        return "Fields_WorkPlane"

    def __init__(self):
        super().__init__()
        # Remove the legacy wp_manager we inherited
        if hasattr(self, 'wp_manager') and self.wp_manager:
            self.wp_manager.hide()
            self.wp_manager = None

        self._preview_obj = None
        self._active_obj = None
        self._editing_obj = None # Existing WP being edited
        self.state = 0 # 0 = waiting for click, 1 = resizing/idle, 2 = dragging corner, 3 = translating
        self.active_corner_idx = -1
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
            mat = coin.SoMaterial()
            if i < 4:
                mat.diffuseColor.setValue(1, 0.5, 0)   # orange at rest (corners)
            else:
                mat.diffuseColor.setValue(1, 1, 0)     # yellow center
            mat.specularColor.setValue(0.8, 0.8, 0.8)
            mat.shininess.setValue(0.7)
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
        FreeCADGui.Control.showDialog(self.task_panel)
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

    def is_in_progress(self):
        """Returns True if a workplane has been dropped (state > 0)."""
        return self.state > 0

    def _do_terminate(self):
        self._stop_drag_timer()
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
        super().reset_state()
        self.active_corner_idx = -1
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
        return self.handle_click(event_dict)

    def on_button1_up(self, event_dict):
        if self.state in [2, 3]:
            self.state = 1
            self._sync_drag_timer()
            self.active_corner_idx = -1
            # Reset all handle colours; next mouse-move will re-evaluate hover
            for i, mat in enumerate(self.handle_mats):
                if i < 4:
                    mat.diffuseColor.setValue(1, 0.5, 0)
                else:
                    mat.diffuseColor.setValue(1, 1, 0)
            self._hovered_idx = -1
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
                return True
                
            elif self.state == 1:
                ray_p, ray_d = self._get_ray(event_dict)
                if ray_p is None or ray_d is None:
                    return False

                hit_idx, hit_dist = self._hit_test(ray_p, ray_d)
                if hit_idx != -1:
                    self.active_corner_idx = hit_idx
                    if hit_idx < 4:
                        self.state = 2 # resizing corner
                        # Get drag plane normal and origin for resizing
                        obj = self._active_obj or self._preview_obj or self._editing_obj
                        plc = obj.Placement
                        self.drag_plane_n = plc.Rotation.multVec(FreeCAD.Vector(0,0,1))
                        self.drag_plane_o = plc.Base
                    else:
                        self.state = 3 # translating center
                    self._sync_drag_timer()
                    return True
                # Missed all handles — consume the click but do nothing
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
            if self._hovered_idx < 4:
                self.handle_mats[self._hovered_idx].diffuseColor.setValue(1, 0.5, 0)
            else:
                self.handle_mats[self._hovered_idx].diffuseColor.setValue(1, 1, 0)
        self._hovered_idx = idx
        if idx == -1:
            if self._cursor_active:
                QtWidgets.QApplication.restoreOverrideCursor()
                self._cursor_active = False
        else:
            self.handle_mats[idx].diffuseColor.setValue(0.3, 1.0, 0.3)  # green on hover
            if not self._cursor_active:
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CrossCursor)
                self._cursor_active = True

    # ── Drag ticks ────────────────────────────────────────────────────────────

    def _sync_drag_timer(self):
        """Run the tick timer exactly while a document-mutating state is live.

        States 0 (preview follows the cursor), 2 (resize by corner) and 3
        (translate) all write to the document continuously; state 1 is hover
        only. Idempotent, so every state transition can just call it.
        """
        want = self.state in (0, 2, 3) and not getattr(self, "_terminated", False)
        dragging = getattr(self, "_is_dragging", False)
        if want and not dragging:
            self._start_drag_timer()
        elif dragging and not want:
            self._stop_drag_timer()

    def _drag_update(self):
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

            elif self.state == 2:
                obj = self._active_obj or self._editing_obj
                if obj:
                    pt_global = self.resolve_world_point(
                        event_dict, base_pt=self.drag_plane_o,
                        plane_normal=self.drag_plane_n, plane_origin=self.drag_plane_o,
                        allow_snap=False)   # dimensions are quantised below, not the point
                    if pt_global:
                        pt_local = obj.Placement.inverse().multVec(pt_global)
                        from freecad.fields.core.input import fld_snap
                        from freecad.fields.core.fld_settings import get_snap_grid_step, get_snap_during_direct_drag
                        step = get_snap_grid_step() if (get_snap_during_direct_drag() and fld_snap.snap_active()) else 0.0
                        obj.Length = max(1.0, fld_snap.snap_length(abs(pt_local.x) * 2.0, step))
                        obj.Width  = max(1.0, fld_snap.snap_length(abs(pt_local.y) * 2.0, step))
                        if self.doc:
                            self.doc.recompute([obj])
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

    def handle_move(self, event_dict):
        """Hover feedback only; the mutating states are driven by _drag_update."""
        if getattr(self, "_terminated", False) or self.state != 1:
            return
        try:
            ray_p, ray_d = self._get_ray(event_dict)
            if ray_p is None or ray_d is None:
                return
            hit_idx, _ = self._hit_test(ray_p, ray_d)
            self._set_hover(hit_idx)
        except Exception as e:
            fld_logger.debug(f"WorkPlaneCreator.handle_move: {e}")

    def get_context_menu(self, event_dict=None):
        return super().get_context_menu(event_dict)

    def _modal_obj(self):
        return getattr(self, "_active_obj", None) or getattr(self, "_editing_obj", None)

    def get_selected_vertex_pos(self):
        """The plane origin is the grab handle -- state 3 already drags exactly this."""
        obj = self._modal_obj()
        return FreeCAD.Vector(obj.Placement.Base) if obj and hasattr(obj, "Placement") else None

    def update_selected_vertex_pos(self, global_pos):
        obj = self._modal_obj()
        if obj and hasattr(obj, "Placement"):
            plc = FreeCAD.Placement(obj.Placement)
            plc.Base = global_pos
            obj.Placement = plc
            if self.doc:
                self.doc.recompute([obj])
            self.update_handles()

    def get_modal_pivot(self):
        return self.get_selected_vertex_pos()

    def snapshot_modal_state(self):
        obj = self._modal_obj()
        if not obj or not hasattr(obj, "Placement"):
            return None
        return (FreeCAD.Placement(obj.Placement), float(getattr(obj, "Length", 100.0)), float(getattr(obj, "Width", 100.0)))

    def restore_modal_state(self, snap):
        obj = self._modal_obj()
        if obj and snap:
            obj.Placement, obj.Length, obj.Width = snap[0], snap[1], snap[2]
            if self.doc:
                self.doc.recompute([obj])
            self.update_handles()

    def apply_modal_rotate(self, angle_deg):
        """Spin the plane about its own normal -- the only rotation that keeps it a
        plane the user can still see. An unlocked rotate on a workplane about the
        view axis would tilt it out of the drawing surface it was placed on."""
        obj = self._modal_obj()
        if not obj or not getattr(self, "_modal_snapshot", None):
            return
        base_plc = self._modal_snapshot[0]
        axis = base_plc.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        spin = FreeCAD.Rotation(axis, angle_deg)
        plc = FreeCAD.Placement(base_plc)
        plc.Rotation = spin.multiply(base_plc.Rotation)
        obj.Placement = plc
        if self.doc:
            self.doc.recompute([obj])
        self.update_handles()

    def apply_modal_scale(self, factor):
        """Scale is resize: Length and Width, not a Placement scale."""
        obj = self._modal_obj()
        if not obj or not getattr(self, "_modal_snapshot", None):
            return
        obj.Length = max(1.0, self._modal_snapshot[1] * factor)
        obj.Width = max(1.0, self._modal_snapshot[2] * factor)
        if self.doc:
            self.doc.recompute([obj])
        self.update_handles()

