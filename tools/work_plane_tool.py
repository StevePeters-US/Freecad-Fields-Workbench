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

        # Setup handles visual
        self.sg = self.view.getSceneGraph()
        self.handles_root = coin.SoSeparator()
        self.handles_coords = coin.SoCoordinate3()
        self.handles_nodes = coin.SoMarkerSet()
        self.handles_nodes.markerIndex = coin.SoMarkerSet.CIRCLE_FILLED_9_9
        self.handles_mat = coin.SoMaterial()
        self.handles_mat.diffuseColor.setValue(1, 0.5, 0)
        
        self.handles_root.addChild(self.handles_mat)
        self.handles_root.addChild(self.handles_coords)
        self.handles_root.addChild(self.handles_nodes)
        
        if self.sg:
            self.sg.addChild(self.handles_root)

        # Show Task Panel to manage lifecycle
        self.task_panel = WorkPlaneTaskPanel(self)
        FreeCADGui.Control.showDialog(self.task_panel)

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

    def update_handles(self):
        obj = self.target_wp if self.target_wp else self.preview_obj
        if not obj or not hasattr(obj, "Length"):
            self.handles_coords.point.setNum(0)
            return
            
        l_val = obj.Length.Value if hasattr(obj.Length, "Value") else float(obj.Length)
        w_val = obj.Width.Value if hasattr(obj.Width, "Value") else float(obj.Width)
        l = l_val / 2.0
        w = w_val / 2.0
        
        corners_local = [
            FreeCAD.Vector(-l, -w, 0),
            FreeCAD.Vector(l, -w, 0),
            FreeCAD.Vector(l, w, 0),
            FreeCAD.Vector(-l, w, 0)
        ]
        
        plc = obj.Placement
        corners_global = [plc.multVec(c) for c in corners_local]
        
        self.handles_coords.point.setValues(0, 4, [(c.x, c.y, c.z) for c in corners_global])

    def _get_ray(self, event_dict):
        """Delegated to DMInputManager."""
        return DMInputManager.get_instance().get_ray(self.view, event_dict)

    def _hit_test(self, ray_p, ray_d):
        obj = self.target_wp if self.target_wp else self.preview_obj
        if not obj or ray_p is None or ray_d is None: 
            return -1, float('inf')
        
        l_val = obj.Length.Value if hasattr(obj.Length, "Value") else float(obj.Length)
        w_val = obj.Width.Value if hasattr(obj.Width, "Value") else float(obj.Width)
        l = l_val / 2.0
        w = w_val / 2.0
        corners_local = [
            FreeCAD.Vector(-l, -w, 0),
            FreeCAD.Vector(l, -w, 0),
            FreeCAD.Vector(l, w, 0),
            FreeCAD.Vector(-l, w, 0)
        ]
        
        plc = obj.Placement
        inv_plac = plc.inverse()
        local_ray_p = inv_plac.multVec(ray_p)
        local_ray_d = inv_plac.Rotation.multVec(ray_d)
        
        best_dist = float('inf')
        best_idx = -1
        
        for i, pos in enumerate(corners_local):
            v = pos - local_ray_p
            dist = v.cross(local_ray_d).Length
            cam_dist = v.dot(local_ray_d)
            if cam_dist < 0: continue
            
            # Use configurable picking radius
            from core.dm_object import get_picking_radius
            tolerance = max(get_picking_radius(), 0.08 * cam_dist) 
            if dist < tolerance and dist < best_dist:
                best_dist = dist
                best_idx = i
                
        return best_idx, best_dist

    def on_button1_down(self, event_dict):
        return self.handle_click(event_dict)

    def on_button1_up(self, event_dict):
        if self.state == 2:
            self.state = 1
            self.active_corner_idx = -1
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
                # Check if clicking on a corner
                pos = DMInputManager.get_instance().get_mouse_pos(event_dict)
                dm_logger.debug(f"[handle_click] State 1 - Generating ray for pos: {pos}")
                ray_p, ray_d = self._get_ray(event_dict)
                if ray_p is None or ray_d is None:
                    dm_logger.debug("[handle_click] State 1 - Ray acquisition failed")
                    return False
                
                hit_idx, hit_dist = self._hit_test(ray_p, ray_d)
                # dm_logger.debug(f"[handle_click] Hit idx: {hit_idx}")
                if hit_idx != -1:
                    self.active_corner_idx = hit_idx
                    self.state = 2 # dragging
                    # Get drag plane normal and origin
                    plc = self.target_wp.Placement
                    self.drag_plane_n = plc.Rotation.multVec(FreeCAD.Vector(0,0,1))
                    self.drag_plane_o = plc.Base
                    return True
                else:
                    self.terminate()
                    return True
                    
        except Exception:
            dm_logger.exception("WorkPlaneCreator.handle_click error")
            return False
        return False

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
                pass
            elif self.state == 2 and self.target_wp:
                pt_global = self.get_mouse_world_pos(event_dict, self.drag_plane_n, self.drag_plane_o)
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
