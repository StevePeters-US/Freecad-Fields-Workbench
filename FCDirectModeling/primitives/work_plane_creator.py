import FreeCAD
import FreeCADGui
from FCDirectModeling.primitives.primitive_base import PrimitiveBase
from FCDirectModeling.dm_workplane import create_dm_workplane
from FCDirectModeling import dm_logger
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
            # Robust hit test
            info = self.view.getObjectInfo(pos)
            if info and "Object" in info and "Component" in info:
                obj_name = info["Object"]
                doc = FreeCAD.ActiveDocument
                obj = doc.getObject(obj_name) if doc else None
                
                # Check if it's a valid object and not our preview
                if obj and obj.Name != (self.preview_obj.Name if self.preview_obj else ""):
                    subname = info["Component"]
                    if "Face" in subname:
                        face = obj.Shape.getElement(subname)
                        # Fallback for missing 'Point' in some view modes
                        raw_pt = info.get("Point", self.view.getPoint(pos[0], pos[1]))
                        hit_pt = FreeCAD.Vector(raw_pt)
                        
                        # Project hit_pt onto face to get u,v
                        import Part
                        dists = face.distToShape(Part.Vertex(hit_pt))
                        if dists and len(dists) >= 3:
                            params = dists[2]
                            if params and len(params) > 0:
                                u, v = None, None
                                info_param = params[0]
                                if isinstance(info_param, (list, tuple)) and len(info_param) >= 2:
                                    if isinstance(info_param[0], (int, float)):
                                        u, v = info_param[0], info_param[1]
                                    elif isinstance(info_param[0], (list, tuple)):
                                        u, v = info_param[0][0], info_param[0][1]
                                
                                if u is not None:
                                    # Get normal from surface
                                    local_n = face.Surface.normal(u, v)
                                    z_axis = FreeCAD.Vector(local_n)
                                    
                                    # Transform normal to world space using the object's transform
                                    if hasattr(obj, "getGlobalPlacement"):
                                        z_axis = obj.getGlobalPlacement().Rotation.multVec(z_axis)
                                    elif hasattr(obj, "Placement"):
                                        z_axis = obj.Placement.Rotation.multVec(z_axis)
                                    z_axis.normalize()
                                    
                                    # Build orthonormal basis
                                    global_z = FreeCAD.Vector(0, 0, 1)
                                    if abs(z_axis.dot(global_z)) > 0.99:
                                        x_axis = FreeCAD.Vector(1, 0, 0)
                                    else:
                                        x_axis = global_z.cross(z_axis)
                                        x_axis.normalize()
                                    y_axis = z_axis.cross(x_axis)
                                    y_axis.normalize()
                                    
                                    m = FreeCAD.Matrix(
                                        x_axis.x, y_axis.x, z_axis.x, hit_pt.x,
                                        x_axis.y, y_axis.y, z_axis.y, hit_pt.y,
                                        x_axis.z, y_axis.z, z_axis.z, hit_pt.z,
                                        0.0,      0.0,      0.0,      1.0
                                    )
                                    return FreeCAD.Placement(m)
        except Exception as e:
            dm_logger.debug(f"WorkPlaneCreator alignment logic fallback: {e}")

        # Fallback to camera-facing grid at origin depth
        try:
            mouse_pt = self.get_mouse_world_pos(event_dict)
            return self.get_camera_facing_placement(mouse_pt)
        except Exception:
            return None

    def handle_click(self, event_dict):
        try:
            btn = event_dict.get("Button")
            dm_logger.debug(f"WorkPlaneCreator.handle_click: btn={btn}")
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
