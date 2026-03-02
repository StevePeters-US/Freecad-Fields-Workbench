import FreeCAD
import FreeCADGui
from .primitive_base import PrimitiveBase
from core.dm_workplane import create_dm_workplane
from core import dm_logger
import math

class WorkPlaneTaskPanel:
    """Task panel for the Work Plane tool to ensure proper cleanup."""
    def __init__(self, creator):
        self.creator = creator
        from PySide import QtGui
        self.form = QtGui.QWidget()
        layout = QtGui.QVBoxLayout(self.form)
        label = QtGui.QLabel("Work Plane Tool active.\n\nClick on a face to align,\nor click in space to create a camera-facing plane.\n\nESC to cancel.")
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
        self.state = 0 # 0 = waiting for click
        
        # Create preview object
        try:
            self.preview_obj = create_dm_workplane(name="DM_WorkPlane_Preview")
            if self.preview_obj:
                self.preview_obj.Label = "Work Plane Preview"
                # Make it look slightly different? Or same.
                # ViewProvider doesn't have a 'preview' mode yet, but we could add one.
        except Exception as e:
            dm_logger.error(f"WorkPlaneCreator preview creation error: {e}")

        # Show Task Panel to manage lifecycle
        self.task_panel = WorkPlaneTaskPanel(self)
        FreeCADGui.Control.showDialog(self.task_panel)

        dm_logger.debug("WorkPlaneCreator initialized")

    def terminate(self):
        if self._terminated:
            return
            
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
                            FreeCAD.Console.PrintMessage(f"WP DBG: getRay/section error: {ray_e}\n")
                        
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
                                except Exception:
                                    pass
                        
                        if not local_n:
                            FreeCAD.Console.PrintMessage(f"WorkPlaneCreator: Error - Failed to calculate normal for {subname}.\n")
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
            FreeCAD.Console.PrintMessage(f"WorkPlaneCreator: Exception in snap logic: {e}\n")
            return None

        # Fallback to camera-facing at origin-plane depth
        try:
            mouse_pt = self.get_mouse_world_pos(event_dict)
            if mouse_pt:
                return self.get_camera_facing_placement(mouse_pt)
        except Exception:
            pass
        return None

    def handle_click(self, event_dict):
        try:
            btn = event_dict.get("Button")
            # Only BUTTON3 (right click) drops the tool. 
            if btn == "BUTTON3":
                self.terminate()
                return True # Suppress context menu

            if btn != "BUTTON1":
                # Ensure navigation (MMB / BUTTON2) doesn't set the workplane
                return False

            placement = self.get_snapped_placement(event_dict)
            if placement:
                # Commit the preview object by renaming it and clearing reference
                if self.preview_obj:
                    # Final placement
                    self.preview_obj.Placement = placement
                    # Rename to final name
                    self.preview_obj.Label = "Work Plane"
                    self.preview_obj = None # Don't delete on terminate
                else:
                    create_dm_workplane(placement=placement)
                    
                if FreeCAD.ActiveDocument:
                    FreeCAD.ActiveDocument.recompute()
                
            self.terminate()
            return True
        except Exception:
            dm_logger.exception("WorkPlaneCreator.handle_click error")
            return False

    def handle_move(self, event_dict):
        try:
            placement = self.get_snapped_placement(event_dict)
            if placement and self.preview_obj:
                self.preview_obj.Placement = placement
                if self.doc:
                    self.doc.recompute()
        except Exception:
            pass
