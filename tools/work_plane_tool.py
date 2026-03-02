import FreeCAD
import FreeCADGui
from .primitive_base import PrimitiveBase
from core.dm_workplane import create_dm_workplane
from core import dm_logger
import math
from pivy import coin

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
        self.creator.terminate()
        return True
        
    def reject(self):
        self.creator.terminate()
        return True

class WorkPlaneCreator(PrimitiveBase):
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
        
        if not self.target_wp:
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

    def terminate(self):
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
                if doc and name in doc.Objects:
                    doc.removeObject(name)
                    doc.recompute()
            except Exception as e:
                dm_logger.debug(f"Cleanup error: {e}")
        
        super().terminate()

    def get_camera_facing_placement(self, mouse_pt):
        """Returns a placement perfectly parallel to the screen, centered at origin depth."""
        try:
            cam_node = self.view.getCameraNode()
            if not cam_node:
                return FreeCAD.Placement(mouse_pt, FreeCAD.Rotation())
            
            # The camera orientation exactly defines the screen-parallel plane
            q = cam_node.orientation.getValue().getValue()
            if not q or len(q) < 4:
                return FreeCAD.Placement(mouse_pt, FreeCAD.Rotation())
            rot = FreeCAD.Rotation(q[0], q[1], q[2], q[3])
            
            # Stable mouse position: intersect ray with plane at origin
            # Normal of the plane is the camera direction
            vd = self.view.getViewDirection()
            n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
            n.normalize()
            
            # Use Ray-Plane intersection for stable positioning
            # Note: PrimitiveBase.get_mouse_world_pos can do this if we pass n and o
            pos = self.get_mouse_world_pos({"Position": self.view.getCursorPos()}, n, FreeCAD.Vector(0,0,0))
            if pos is None: pos = mouse_pt
            
            return FreeCAD.Placement(pos, rot)
        except Exception as e:
            dm_logger.error(f"WorkPlaneCreator rotation fallback: {e}")
            return FreeCAD.Placement(mouse_pt, FreeCAD.Rotation())

    def get_snapped_placement(self, event_dict):
        """Returns snapped placement if hovering over a face, otherwise camera facing."""
        pos = event_dict.get("Position")
        if not pos:
            return None
            
        try:
            # 1. Pixel-based hit test
            # Try to get ALL objects under the cursor to bypass the Work Plane Preview
            infos = []
            if hasattr(self.view, "getObjectsInfo"):
                infos = self.view.getObjectsInfo((int(pos[0]), int(pos[1])))
                if infos is None:
                    infos = []
            else:
                single_info = self.view.getObjectInfo((int(pos[0]), int(pos[1])))
                infos = [single_info] if single_info else []
                
            info = None
            for i in infos:
                if not i or "Object" not in i or "Component" not in i:
                    continue
                obj_name = i["Object"]
                
                # Skip the preview object itself!
                if self.preview_obj and obj_name == self.preview_obj.Name:
                    continue
                    
                info = i
                break
                
            if info:
                obj_name = info["Object"]
                doc = FreeCAD.ActiveDocument
                obj = doc.getObject(obj_name) if doc and obj_name else None
                
                # Check if it's a valid object and not our preview
                if obj and obj.Name != (self.preview_obj.Name if self.preview_obj else ""):

                    subname = info["Component"]
                    if "Face" in subname:
                        face = obj.Shape.getElement(subname)
                        
                        # Get true 3D surface intersection using face.section().
                        # view.getPoint() only hits the working plane, not the surface.
                        # We pick the front-face hit by choosing the vertex closest to the near
                        # end of the ray (local_near), which is correct for both Perspective
                        # and Orthographic cameras.
                        import Part
                        world_hit_pt = None
                        try:
                            ray_p, ray_d = None, None
                            if hasattr(self.view, "getRay"):
                                r = self.view.getRay(pos[0], pos[1])
                                if r:
                                    ray_p = FreeCAD.Vector(r[0])
                                    ray_d = FreeCAD.Vector(r[1])
                                    ray_d.normalize()
                            
                            if ray_p is None:
                                # Fallback: build direction from camera-to-plane-point  
                                cam_node = self.view.getCameraNode()
                                cam_pos = FreeCAD.Vector(*cam_node.position.getValue().getValue())
                                wp_pt = self.view.getPoint(pos[0], pos[1])
                                if wp_pt:
                                    ray_p = cam_pos
                                    ray_d = (wp_pt - cam_pos)
                                    ray_d.normalize()
                            
                            if ray_p is not None and ray_d is not None:
                                gpl = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
                                gpl_inv = gpl.inverse()
                                
                                # Build ray in local object space, extending well past the object
                                local_near = gpl_inv.multVec(ray_p + ray_d * 1)
                                local_far  = gpl_inv.multVec(ray_p + ray_d * 100000)
                                
                                ray_wire = Part.makeLine(tuple(local_near), tuple(local_far))
                                inter = face.section(ray_wire)
                                
                                if inter.Vertexes:
                                    # Pick the FRONT face: vertex closest to local_near (start of ray).
                                    # Do NOT use distance to camera — orthographic cameras are at infinity!
                                    best_pt = min(inter.Vertexes, key=lambda v: (v.Point - local_near).Length).Point
                                    world_hit_pt = gpl.multVec(best_pt)
                                
                        except Exception as ray_e:
                            dm_logger.info(f"WP DBG: getRay/section error: {ray_e}")
                        
                        if world_hit_pt is None:
                            # Fallback: working-plane intersection (may snap to silhouette on curves)
                            wp_fallback = self.view.getPoint(pos[0], pos[1])
                            if wp_fallback is None:
                                return None
                            world_hit_pt = wp_fallback

                        
                        
                        # Get object's world transform
                        gpl = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
                        
                        # Project world point to local space for geometry analysis
                        local_hit_pt = gpl.inverse().multVec(world_hit_pt)
                        
                        # Calculate normal using direct shape projection
                        local_n = None
                        projected_local_pt = local_hit_pt
                        
                        import Part
                        dists = face.distToShape(Part.Vertex(local_hit_pt))
                        
                        # Layout: (distance, [(pt1, pt2)], [('Face', 0, (u, v), 'Vertex', 0, None)])
                        if dists and len(dists) >= 3 and len(dists[2]) > 0:
                            if len(dists[1]) > 0 and len(dists[1][0]) > 0:
                                projected_local_pt = FreeCAD.Vector(dists[1][0][0])
                                
                            info_tuple = dists[2][0]
                            # Look for the (u, v) parameter tuple in the face info
                            if len(info_tuple) >= 3 and isinstance(info_tuple[2], (tuple, list)) and len(info_tuple[2]) == 2:
                                u, v = info_tuple[2]
                                local_n = face.Surface.normal(u, v)
                            elif hasattr(face, "Surface") and hasattr(face.Surface, "parameter"):
                                # Fallback if distToShape structure changes
                                try:
                                    u, v = face.Surface.parameter(local_hit_pt)
                                    local_n = face.Surface.normal(u, v)
                                except Exception as e:
                                    dm_logger.debug(f"WorkPlaneCreator: parameter/normal calculation failed: {e}")
                        
                        if not local_n:
                            dm_logger.error(f"WorkPlaneCreator: Error - Failed to calculate normal for {subname}.")
                            return None
                        
                        if face.Orientation == "Reversed":
                            local_n.multiply(-1.0)
                        
                        # Transform normal and exact mathematical point to WORLD space
                        world_n = gpl.Rotation.multVec(FreeCAD.Vector(local_n))
                        world_n.normalize()
                        
                        projected_world_pt = gpl.multVec(projected_local_pt)
                        
                        
                        # Build stable basis: Z=normal, Y=camera-influenced-up, X=across
                        camera_up = self.view.getUpDirection()
                        if abs(world_n.dot(camera_up)) > 0.95:
                            # Normal is parallel to camera up, use view direction
                            x_axis = self.view.getViewDirection().cross(world_n)
                        else:
                            x_axis = camera_up.cross(world_n)
                        
                        x_axis.normalize()
                        y_axis = world_n.cross(x_axis)
                        y_axis.normalize()
                        
                        world_m = FreeCAD.Matrix(
                            x_axis.x, y_axis.x, world_n.x, projected_world_pt.x,
                            x_axis.y, y_axis.y, world_n.y, projected_world_pt.y,
                            x_axis.z, y_axis.z, world_n.z, projected_world_pt.z,
                            0.0,      0.0,      0.0,      1.0
                        )
                        world_placement = FreeCAD.Placement(world_m)
                        
                        # Adjust for parent transform (Body/Part/Group)
                        final_placement = world_placement
                        if self.preview_obj and hasattr(self.preview_obj, "InList"):
                            for p in self.preview_obj.InList:
                                if hasattr(p, "Placement") and p.isDerivedFrom("App::GeoFeature"):
                                    pgpl = p.getGlobalPlacement() if hasattr(p, "getGlobalPlacement") else p.Placement
                                    final_placement = pgpl.inverse().multiply(world_placement)
                                    break
                        
                        
                        return final_placement

        except Exception as e:
            dm_logger.error(f"WorkPlaneCreator: Exception in snap logic: {e}")
            return None

        # Fallback to camera-facing at origin-plane depth
        try:
            mouse_pt = self.get_mouse_world_pos(event_dict)
            if mouse_pt:
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
        pos = event_dict.get("Position", (0, 0))
        x, y = int(pos[0]), int(pos[1])
        try:
            r = self.view.getRay(x, y)
            if r and len(r) == 2:
                ray_p = FreeCAD.Vector(r[0])
                ray_d = FreeCAD.Vector(r[1])
                ray_d.normalize()
                return ray_p, ray_d
        except Exception as e:
            dm_logger.debug(f"WorkPlaneCreator._get_ray: getRay failed: {e}")
            
        # Fallback if getRay fails
        try:
            scene_pt = self.view.getPoint(x, y)
            focus = self.view.getFocus() if hasattr(self.view, "getFocus") else FreeCAD.Vector(0,0,0)
            if scene_pt is None: scene_pt = focus
            cam = self.view.getCameraNode()
            if cam and hasattr(cam, "position"):
                cam_vec = cam.position.getValue()
                cam_pos_tuple = cam_vec.getValue() if hasattr(cam_vec, "getValue") else (cam_vec[0], cam_vec[1], cam_vec[2])
                ray_p = FreeCAD.Vector(*cam_pos_tuple)
                ray_d = scene_pt - ray_p
                ray_d.normalize()
                return ray_p, ray_d
        except Exception as e:
            dm_logger.debug(f"WorkPlaneCreator._get_ray: Fallback failed: {e}")
        return None, None

    def _hit_test(self, ray_p, ray_d):
        obj = self.target_wp if self.target_wp else self.preview_obj
        if not obj or not ray_p or not ray_d: return -1
        
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
            
            # Use a more forgiving tolerance for snapping
            tolerance = max(2.0, 0.08 * cam_dist) 
            if dist < tolerance and dist < best_dist:
                best_dist = dist
                best_idx = i
                
        return best_idx, best_dist

    def event_cb(self, event_dict):
        try:
            event_type = event_dict.get("Type", "Unknown")

            if event_type == "SoMouseButtonEvent":
                btn = event_dict.get("Button", "None")
                state = event_dict.get("State", "None")
                if state == "DOWN":
                    if btn == "BUTTON1":
                        return self.handle_click(event_dict)
                    elif btn == "BUTTON3":
                        self.terminate()
                        return True
                    return False
                elif state == "UP":
                    if btn == "BUTTON1" and self.state == 2:
                        self.state = 1
                        self.active_corner_idx = -1
                        return True
                    if btn == "BUTTON3":
                        return True 
                    return False
            elif event_type == "SoLocation2Event":
                self.handle_move(event_dict)
            elif event_type == "SoKeyboardEvent":
                if event_dict["State"] == "DOWN":
                    return self.handle_keyboard(event_dict)

        except Exception as e:
            dm_logger.error(f"Error in WorkPlaneCreator event_cb: {e}")
            import traceback
            traceback.print_exc()
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
                dm_logger.debug(f"[handle_click] State 1 - Generating ray for pos: {event_dict.get('Position')}")
                ray_p, ray_d = self._get_ray(event_dict)
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
