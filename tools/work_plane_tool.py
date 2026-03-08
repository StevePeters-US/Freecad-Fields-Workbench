import FreeCAD
import FreeCADGui
from .dm_base import DMBase
from core.dm_workplane import create_dm_workplane
from core import dm_logger
from core.input_manager import DMInputManager
import math
from pivy import coin
from PySide import QtCore

class WorkPlaneTaskPanel:
    """Task panel for the Work Plane tool to ensure proper cleanup."""
    def __init__(self, creator):
        self.creator = creator
        from PySide import QtGui
        self.form = QtGui.QWidget()
        layout = QtGui.QVBoxLayout(self.form)
        label = QtGui.QLabel("Work Plane Tool active.\n\nClick to drop plane.\nDrag corners to resize.\n\nESC to cancel.")
        layout.addWidget(label)
        
    def accept(self):
        if hasattr(self.creator, 'finish'):
            self.creator.finish()
        else:
            self.creator.terminate()
        return True
        
    def reject(self):
        self.creator.terminate()
        return True

class WorkPlaneCreator(DMBase):
    """Tool to create a DMWorkPlane object interactively."""
    
    def __init__(self):
        super().__init__()
        # Remove the legacy wp_manager we inherited
        if hasattr(self, 'wp_manager') and self.wp_manager:
            self.wp_manager.hide()
            self.wp_manager = None

        self.preview_obj = None
        self.target_wp = None
        self.state = 0 # 0 = waiting for click, 1 = resizing/idle, 2 = dragging corner
        self.active_corner_idx = -1
        self._cursor_active = False

        # Check if an existing WP is selected
        sel = FreeCADGui.Selection.getSelection()
        for obj in sel:
            if hasattr(obj, "Proxy") and getattr(obj.Proxy, "__class__", None).__name__ == "DMWorkPlane":
                self.target_wp = obj
                self.state = 1
                break
        
        self._is_new = False
        if not self.target_wp:
            self._is_new = True
            # Create preview object
            try:
                self.preview_obj = create_dm_workplane(name="DM_WorkPlane_Preview")
                if self.preview_obj:
                    self.preview_obj.Label = "Work Plane Preview"
            except Exception as e:
                dm_logger.error(f"WorkPlaneCreator preview creation error: {e}")

        # Setup handles visual — one sphere per corner
        self.sg = self.view.getSceneGraph()
        self.handles_root = coin.SoSeparator()
        self.handle_seps = []
        self.handle_mats = []
        self.handle_transforms = []
        self.handle_spheres = []
        for _ in range(4):
            sep = coin.SoSeparator()
            mat = coin.SoMaterial()
            mat.diffuseColor.setValue(1, 0.5, 0)   # orange at rest
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

    def _do_terminate(self):
        if hasattr(self, "_terminated") and self._terminated:
            return
            
        if getattr(self, "_cursor_active", False):
            from PySide import QtGui
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False
            
        # Clean up handles
        try:
            if self.sg and self.handles_root:
                self.sg.removeChild(self.handles_root)
        except Exception as e:
            dm_logger.debug(f"WorkPlaneCreator.terminate: Failed to remove handles: {e}")
            
        # Clean up preview
        if self.preview_obj:
            try:
                doc = FreeCAD.ActiveDocument
                name = self.preview_obj.Name
                self.preview_obj = None # Clear before deletion to avoid issues
                if doc and doc.getObject(name):
                    doc.removeObject(name)
                    doc.recompute()
            except Exception as e:
                dm_logger.debug(f"Cleanup error: {e}")
                
        # Clean up target_wp if we created it but didn't finish
        if getattr(self, "_is_new", False) and not getattr(self, "_finished", False) and self.target_wp is not None:
            try:
                doc = FreeCAD.ActiveDocument
                name = self.target_wp.Name
                self.target_wp = None
                if doc and doc.getObject(name):
                    doc.removeObject(name)
                    doc.recompute()
            except Exception as e:
                dm_logger.debug(f"Cleanup error (target_wp): {e}")

        super()._do_terminate()

    def finish(self):
        self._finished = True
        if getattr(self, "state", 0) == 0:
            if self.preview_obj:
                self.preview_obj.Label = "Work Plane"
                self.target_wp = self.preview_obj
                self.preview_obj = None
                if FreeCAD.ActiveDocument:
                    FreeCAD.ActiveDocument.recompute()
        self.terminate()

    def get_camera_facing_placement(self, mouse_pt):
        """Delegated to DMInputManager."""
        return DMInputManager.get_instance().get_view_transform(self.view, mouse_pt)

    def get_snapped_placement(self, event_dict):
        """Delegated to ViewProjector."""
        geo = self.projector.get_geometry_info(event_dict, skip_objects=[self.preview_obj] if self.preview_obj else None)
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
            return FreeCAD.Placement(m)
        return None

        # Fallback to camera-facing at origin-plane depth
        try:
            pos_2d = DMInputManager.get_instance().get_mouse_pos(event_dict)
            mouse_pt = None
            try: mouse_pt = self.view.getPoint(pos_2d[0], pos_2d[1])
            except: pass
            
            if not mouse_pt:
                focus = self.view.getFocus() if hasattr(self.view, "getFocus") else FreeCAD.Vector(0,0,0)
                mouse_pt = focus
                
            return self.get_camera_facing_placement(mouse_pt)
        except Exception as e:
            dm_logger.debug(f"WorkPlaneCreator: camera-facing fallback failed: {e}")
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
            dm_logger.debug(f"WorkPlaneCreator: _get_initial_size failed: {e}")
            return 100.0

    def _compute_handle_radius(self):
        """Sphere radius in world units — sized to look ~8 px on screen."""
        try:
            cam = self.view.getCameraNode()
            viewer = self.view.getViewer()
            vp_h = 800.0
            try:
                if hasattr(viewer, "getGlxSize"):
                    sz = viewer.getGlxSize(); vp_h = float(sz[1])
                elif hasattr(viewer, "getSize"):
                    sz = viewer.getSize()
                    vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
            except Exception:
                pass
            obj = self.target_wp or self.preview_obj
            if hasattr(cam, "height"):
                half_world_h = cam.height.getValue() / 2.0
            elif hasattr(cam, "heightAngle"):
                cam_vals = cam.position.getValue()
                cam_pos_v = FreeCAD.Vector(cam_vals[0], cam_vals[1], cam_vals[2])
                ref = obj.Placement.Base if obj else FreeCAD.Vector(0, 0, 0)
                depth = (ref - cam_pos_v).Length
                fov = cam.heightAngle.getValue()
                half_world_h = depth * math.tan(fov / 2.0)
            else:
                half_world_h = 100.0
            px_per_world = (vp_h / 2.0) / max(half_world_h, 1e-6)
            return max(2.0, 8.0 / px_per_world)
        except Exception:
            return 5.0

    def update_handles(self):
        obj = self.target_wp if self.target_wp else self.preview_obj
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
        radius = self._compute_handle_radius()
        for corner, xf, sphere in zip(corners_global, self.handle_transforms, self.handle_spheres):
            xf.translation.setValue(corner.x, corner.y, corner.z)
            xf.scaleFactor.setValue(1, 1, 1)
            sphere.radius = radius

    def _get_ray(self, event_dict):
        """Delegated to DMInputManager."""
        return DMInputManager.get_instance().get_ray(self.view, event_dict)

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
        obj = self.target_wp if self.target_wp else self.preview_obj
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
        radius = self._compute_handle_radius()

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
        if self.state == 2:
            self.state = 1
            self.active_corner_idx = -1
            # Reset all handle colours; next mouse-move will re-evaluate hover
            for mat in self.handle_mats:
                mat.diffuseColor.setValue(1, 0.5, 0)
            self._hovered_idx = -1
            return True
        return False

    def handle_click(self, event_dict):
        try:
            btn = event_dict.get("Button")
            
            if btn == "BUTTON3":
                self.terminate()
                return True

            if btn != "BUTTON1":
                return False

            if self.state == 0:
                # First click drops the workplane
                placement = self.get_snapped_placement(event_dict)
                if placement:
                    size = self._get_initial_size(placement.Base)
                    if self.preview_obj:
                        self.preview_obj.Placement = placement
                        self.preview_obj.Label = "Work Plane"
                        self.preview_obj.Length = size
                        self.preview_obj.Width = size
                        self.target_wp = self.preview_obj
                        self.preview_obj = None
                    else:
                        self.target_wp = create_dm_workplane(placement=placement)
                        self.target_wp.Length = size
                        self.target_wp.Width = size
                        
                    if FreeCAD.ActiveDocument:
                        FreeCAD.ActiveDocument.recompute()
                        
                    self.state = 1
                    self.update_handles()
                return True
                
            elif self.state == 1:
                ray_p, ray_d = self._get_ray(event_dict)
                if ray_p is None or ray_d is None:
                    return False

                hit_idx, hit_dist = self._hit_test(ray_p, ray_d)
                if hit_idx != -1:
                    self.active_corner_idx = hit_idx
                    self.state = 2 # dragging
                    # Get drag plane normal and origin
                    plc = self.target_wp.Placement
                    self.drag_plane_n = plc.Rotation.multVec(FreeCAD.Vector(0,0,1))
                    self.drag_plane_o = plc.Base
                    return True
                # Missed all handles — consume the click but do nothing
                return True
                    
        except Exception:
            dm_logger.exception("WorkPlaneCreator.handle_click error")
            return False
        return False

    def _set_hover(self, idx):
        """Recolour handle spheres and update OS cursor for handle idx (-1 = none)."""
        from PySide import QtCore, QtGui
        if idx == self._hovered_idx:
            return
        # Restore previous handle to orange
        if self._hovered_idx != -1:
            self.handle_mats[self._hovered_idx].diffuseColor.setValue(1, 0.5, 0)
        self._hovered_idx = idx
        if idx == -1:
            if self._cursor_active:
                QtGui.QApplication.restoreOverrideCursor()
                self._cursor_active = False
        else:
            self.handle_mats[idx].diffuseColor.setValue(0.3, 1.0, 0.3)  # green on hover
            if not self._cursor_active:
                QtGui.QApplication.setOverrideCursor(QtCore.Qt.CrossCursor)
                self._cursor_active = True

    def handle_move(self, event_dict):
        try:
            if self.state == 0:
                placement = self.get_snapped_placement(event_dict)
                if placement and self.preview_obj:
                    self.preview_obj.Placement = placement
                    size = self._get_initial_size(placement.Base)
                    self.preview_obj.Length = size
                    self.preview_obj.Width = size
                    if self.doc:
                        self.doc.recompute()
                    self.update_handles()

            elif self.state == 1:
                ray_p, ray_d = self._get_ray(event_dict)
                hit_idx, _ = self._hit_test(ray_p, ray_d)
                self._set_hover(hit_idx)

            elif self.state == 2 and self.target_wp:
                # Bypass place_on_geometry — always intersect the workplane drag plane
                pt_global = self.projector.get_mouse_world_pos(
                    event_dict, self.drag_plane_n, self.drag_plane_o,
                    place_on_geometry=False
                )
                if pt_global:
                    pt_local = self.target_wp.Placement.inverse().multVec(pt_global)
                    new_l = abs(pt_local.x) * 2.0
                    new_w = abs(pt_local.y) * 2.0
                    self.target_wp.Length = max(1.0, new_l)
                    self.target_wp.Width = max(1.0, new_w)
                    if self.doc:
                        self.doc.recompute()
                    self.update_handles()
        except Exception as e:
            dm_logger.error(f"[handle_move] Exception: {e}")
            import traceback
            traceback.print_exc()

    def get_context_menu(self, event_dict=None):
        base_menu = super().get_context_menu(event_dict)
        return base_menu + [
            "-",
            ("Apply Work Plane", self.finish),
            ("Cancel", self.terminate)
        ]
