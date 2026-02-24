import FreeCAD
import FreeCADGui
import Part
from pivy import coin
from PySide import QtCore, QtGui
import numpy as np
from .base import BRepPrimitiveCreator, log_to_file

class BoxCreator(BRepPrimitiveCreator):
    def __init__(self):
        super().__init__()
        
        # State is initialized by super: self.start_point, self.current_point, self.state=0
        
        self.height = 0.0
        
        self.active_axis = None
        self.locked_length = None
        self.locked_width = None
        self.locked_height = None
        
        self.manual_mode_override = False
        
        self.working_plane = None # FreeCAD.Placement
        self.snap_face = None # (obj, face_name)
        
        self.panel = None

        self.is_cutter = False
        
        # Base initializes: self.sg, self.material, self.preview_sep, self.sdf_sep
        
        # Add Box-specific nodes
        
    def update_material(self):
        # We don't have a live BRep object anymore during creation.
        # Could color the wireframe if desired, but default green is fine for now.
        pass
            
    def toggle_cutter_mode(self):
         self.is_cutter = not self.is_cutter
         self.manual_mode_override = True
         self.update_material() # Insert at beginning
         
         self.view.redraw()

    def terminate(self):
        super().terminate()
        # Close task panel
        FreeCADGui.Control.closeDialog()

    def set_panel(self, panel):
        self.panel = panel
        
    def get_face_under_mouse(self, event_dict):
        pos = event_dict["Position"]
        # getObjectInfo returns a dict with 'Object', 'Component', etc.
        # It takes pixel coordinates (x, y)
        try:
            info = self.view.getObjectInfo((pos[0], pos[1]))
        except Exception:
            return None, None
            
        if info and "Object" in info and "Component" in info:
             return info["Object"], info["Component"]
        return None, None

    def get_mouse_point_on_plane(self, event_dict, plane_placement=None):
        pos = event_dict["Position"]
        
        # Get point on focal plane and view direction
        point_on_focal_plane = self.view.getPoint(pos[0], pos[1])
        view_dir = self.view.getViewDirection()
        
        # Get Camera to check type
        cam = self.view.getCameraNode()
        
        ray_origin = FreeCAD.Vector(0,0,0)
        ray_dir = FreeCAD.Vector(0,0,1)
        
        if cam.getTypeId() == coin.SoOrthographicCamera.getClassTypeId():
            ray_origin = point_on_focal_plane
            ray_dir = view_dir
        else: # Perspective
            # For perspective, ray originates at camera position
            cam_pos_sb = cam.position.getValue()
            cam_pos = FreeCAD.Vector(cam_pos_sb[0], cam_pos_sb[1], cam_pos_sb[2])
            
            ray_origin = cam_pos
            ray_dir = point_on_focal_plane - ray_origin
            ray_dir.normalize()

        # Plane Definition
        if plane_placement:
            plane_normal = plane_placement.Rotation.multVec(FreeCAD.Vector(0,0,1))
            plane_point = plane_placement.Base
        else:
            # Default to Z=0
            plane_normal = FreeCAD.Vector(0,0,1)
            plane_point = FreeCAD.Vector(0,0,0)
        
        denom = ray_dir.dot(plane_normal)
        
        if abs(denom) < 1e-6:
            # Ray is parallel to plane, return focal point projected to Z=0 or plane base as fallback
            return plane_point
            
        t = (plane_point - ray_origin).dot(plane_normal) / denom
        return ray_origin + ray_dir * t

    def set_length_lock(self, length):
        self.locked_length = length
        self.update_from_locks()
        
    def set_width_lock(self, width):
        self.locked_width = width
        self.update_from_locks()
        
    def set_height_lock(self, height):
        self.locked_height = height
        self.height = height
        self.update_geometry()
        self.view.redraw()

    def update_from_locks(self):
        if not self.start_point:
            self.start_point = FreeCAD.Vector(0,0,0)
            
        p1 = self.start_point
        p2 = self.current_point if self.current_point else FreeCAD.Vector(0,0,0)
        
        # Calculate raw deltas (signed)
        dx = p2.x - p1.x
        dy = p2.y - p1.y
        
        # Determine signs from mouse (for unlocked dimensions)
        sign_x = 1.0 if dx >= 0 else -1.0
        sign_y = 1.0 if dy >= 0 else -1.0
        
        # Use simple mouse delta magnitude if unlocked
        new_dx = abs(dx) * sign_x
        new_dy = abs(dy) * sign_y
        
        # If locked, OVERRIDE with the lock value directly (Signed Input = Signed Direction)
        if self.locked_length is not None:
             new_dx = self.locked_length
             
        if self.locked_width is not None:
             new_dy = self.locked_width
             
        # Reconstruct point
        new_x = p1.x + new_dx
        new_y = p1.y + new_dy
        
        self.current_point = FreeCAD.Vector(new_x, new_y, 0)
        self.update_preview()
        self.view.redraw()

    def update_ui(self):
        if not self.start_point or not self.current_point:
            return
            
        p1 = self.start_point
        p2 = self.current_point
        
        # Send SIGNED deltas to UI so positive = positive direction
        length = p2.x - p1.x
        width = p2.y - p1.y
        height = self.height
        
        if self.panel:
             self.panel.update_values(length, width, height)

    def to_local(self, p):
        if not self.working_plane:
            return p
        # inverse matrix
        mat = self.working_plane.toMatrix()
        mat.invert()
        return mat.multVec(p)

    def to_global(self, p):
        if not self.working_plane:
            return p
        return self.working_plane.toMatrix().multVec(p)

    def update_preview(self):
        if not self.start_point or not self.current_point:
            return
            
        p1 = self.to_local(self.start_point)
        p2 = self.to_local(self.current_point)
        h = self.height
        
        min_x = min(p1.x, p2.x)
        max_x = max(p1.x, p2.x)
        min_y = min(p1.y, p2.y)
        max_y = max(p1.y, p2.y)
        
        width = max(0.001, max_x - min_x)
        length = max(0.001, max_y - min_y)
        h_abs = max(0.001, abs(h))
        
        z_offset = 0.0
        if h < 0:
            z_offset = h
            
        try:
            shape = Part.makeBox(width, length, h_abs)
            
            # Base in Local
            local_base = FreeCAD.Vector(min_x, min_y, z_offset)
            
            pl = FreeCAD.Placement()
            
            if self.working_plane:
                 local_placement = FreeCAD.Placement(local_base, FreeCAD.Rotation())
                 final_placement = self.working_plane.multiply(local_placement)
                 pl = final_placement
            else:
                 pl.Base = local_base
                 
            super().update_preview(shape, pl)
        except Exception as e:
            log_to_file(f"Box preview error: {e}")

    def event_cb(self, event_dict):
        event_type = event_dict["Type"]
        
        if event_type == "SoMouseButtonEvent":
            if event_dict["State"] == "DOWN":
                button = event_dict["Button"]
                if button == "BUTTON1":
                    self.handle_click(event_dict)
                
        elif event_type == "SoLocation2Event":
            self.handle_move(event_dict)
            
        elif event_type == "SoKeyboardEvent":
            if event_dict["State"] == "DOWN":
                handled = self.handle_keyboard(event_dict)
                if handled:
                    return True
            
        return False
        

            
    def handle_keyboard(self, event_dict):
        key = str(event_dict["Key"]).upper()
        
        # ESC to cancel
        if key == "ESCAPE":
            QtCore.QTimer.singleShot(0, self.terminate)
            return
            
        # Toggle Cutter Mode (C)
        if key == "C":
             self.is_cutter = not self.is_cutter
             self.update_material()
             self.view.redraw()
             return

        # Axis Toggles -> Focus Panel
        target_axis = None
        if key == "X": target_axis = "x"
        elif key == "Y": target_axis = "y"
        elif key == "Z": target_axis = "z"
        
        if target_axis:
            self.toggle_axis(target_axis)

    def toggle_axis(self, target_axis):
        if not self.panel:
            return
            
        if self.active_axis == target_axis:
            # Toggle OFF
            self.active_axis = None
            if target_axis == 'x': self.locked_length = None
            if target_axis == 'y': self.locked_width = None
            if target_axis == 'z': self.locked_height = None
            
            # Clear focus from panel fields
            if self.panel:
                self.panel.clear_focus()
                
            # Trigger update to snap back to mouse
            self.update_from_locks()
        else:
            # Focus Field
            self.active_axis = target_axis
            self.panel.focus_field(target_axis)

    def handle_click(self, event_dict):
        # If left click, proceed with drawing logic
        
        pt = self.get_mouse_point_on_plane(event_dict, self.working_plane)
        
        if self.state == 0: # Start
            self.start_point = pt
            self.current_point = pt
            self.state = 1
            self.update_ui()
            
        elif self.state == 1: # End base -> Start Height
            self.state = 2
            self.drag_start_screen_y = event_dict["Position"][1]
            # Height lock handling?
            if self.locked_height is not None:
                self.height = self.locked_height
            else:
                self.height = 0.0
            
        elif self.state == 2: # Finish
            self.finish()

    def handle_move(self, event_dict):
        if self.state == 0:
            # Detect face under mouse
            obj, subname = self.get_face_under_mouse(event_dict)
            if obj and subname and "Face" in subname:
                try:
                    face = obj.Shape.getElement(subname)
                    # Use GeomPlane check via TypeId or isinstance if available. 
                    # Assuming Part.GeomPlane logic. Safe mostly to check TypeId.
                    if hasattr(face, "Surface") and "GeomPlane" in face.Surface.TypeId:
                        self.working_plane = face.Surface.Position
                        self.snap_face = (obj, subname)
                        # Optional: Highlight face? existing preselection might be enough.
                    else:
                        self.working_plane = None
                        self.snap_face = None
                except Exception:
                    self.working_plane = None
                    self.snap_face = None
            return
            
        elif self.state == 1:
            raw_pt = self.get_mouse_point_on_plane(event_dict, self.working_plane)
            
            # Work in Local Coords for standard delta logic
            local_raw_pt = self.to_local(raw_pt)
            local_p1 = self.to_local(self.start_point)
            
            # Raw Signed Deltas from Mouse (Local)
            dx_mouse = local_raw_pt.x - local_p1.x
            dy_mouse = local_raw_pt.y - local_p1.y
            
            # Final Deltas (Lock Overrides)
            new_dx = dx_mouse
            new_dy = dy_mouse
            
            if self.locked_length is not None:
                 new_dx = self.locked_length
                 
            if self.locked_width is not None:
                 new_dy = self.locked_width
            
            if self.locked_width is not None:
                 new_dy = self.locked_width
            
            # Reconstruct (Local)
            new_local_x = local_p1.x + new_dx
            new_local_y = local_p1.y + new_dy
            
            # Back to Global
            new_local_pt = FreeCAD.Vector(new_local_x, new_local_y, 0)
            self.current_point = self.to_global(new_local_pt)
            
            self.update_preview()
            self.update_ui()
            
        elif self.state == 2:
            # Handle height
            current_screen_y = event_dict["Position"][1]
            
            if self.locked_height is not None:
                self.height = self.locked_height
            else:
                if hasattr(self, 'drag_start_screen_y'):
                    delta = current_screen_y - self.drag_start_screen_y
                    self.height = delta / 4.0 
            
            # Auto-Cutter / Fuse Logic
            # If manual override is OFF, and we have a snap face:
            # Height < 0 (into face) -> Cut
            # Height > 0 (out of face) -> Fuse (Create)
            if not self.manual_mode_override and self.snap_face:
                if self.height < -1e-4:
                     if not self.is_cutter:
                         self.is_cutter = True
                         self.update_material()
                else:
                     if self.is_cutter:
                         self.is_cutter = False
                         self.update_material()
            
            self.update_preview()
            self.update_ui()

    def finish(self):
        if not self.start_point or (not self.current_point and self.state == 0):
             # If completely uninitialized, just terminate
            self.terminate()
            return

        doc = FreeCAD.activeDocument()
        if not doc:
            doc = FreeCAD.newDocument()

        # Ensure we have points
        if not self.current_point:
             self.current_point = self.start_point
             
        p1 = self.to_local(self.start_point)
        p2 = self.to_local(self.current_point)
        
        min_x = min(p1.x, p2.x)
        max_x = max(p1.x, p2.x)
        min_y = min(p1.y, p2.y)
        max_y = max(p1.y, p2.y)
        
        width = max_x - min_x
        length = max_y - min_y
        
        # Prevent zero dimensions
        if width < 0.001: width = 1.0
        if length < 0.001: length = 1.0
        
        final_height = self.height if abs(self.height) > 0.001 else 1.0
        
        # Lofting / Extruding Approach:
        # Create 4 points of the local base rectangle
        pt1 = FreeCAD.Vector(min_x, min_y, 0)
        pt2 = FreeCAD.Vector(max_x, min_y, 0)
        pt3 = FreeCAD.Vector(max_x, max_y, 0)
        pt4 = FreeCAD.Vector(min_x, max_y, 0)
        
        # Create curves (LineSegments)
        edge1 = Part.LineSegment(pt1, pt2).toShape()
        edge2 = Part.LineSegment(pt2, pt3).toShape()
        edge3 = Part.LineSegment(pt3, pt4).toShape()
        edge4 = Part.LineSegment(pt4, pt1).toShape()
        
        # Form Wire -> Face
        base_wire = Part.Wire([edge1, edge2, edge3, edge4])
        base_face = Part.Face(base_wire)
        
        # Extrude to form Solid
        prism_shape = base_face.extrude(FreeCAD.Vector(0, 0, final_height))
        
        from FCDirectModeling.dm_part import create_dm_part
        box = create_dm_part("Box")
        box.Shape = prism_shape
        
        # Final Placement
        if self.working_plane:
             box.Placement = self.working_plane
             
        doc.recompute()
        
        # Auto Fuse/Cut Logic
        if not self.is_cutter and self.snap_face:
            # Fuse box with base object
            base_obj = self.snap_face[0]
            if base_obj:
                try:
                    fused_name = f"Result"
                    fuse = doc.addObject("Part::MultiFuse", fused_name)
                    fuse.Shapes = [base_obj, box]
                    
                    if hasattr(base_obj, "ViewObject") and base_obj.ViewObject:
                        base_obj.ViewObject.Visibility = False
                    if hasattr(box, "ViewObject") and box.ViewObject:
                        box.ViewObject.Visibility = False
                        
                    doc.recompute()
                except Exception as e:
                    FreeCAD.Console.PrintError(f"Auto-Fuse Failed: {e}\n")
        
        # Boolean Cut Logic
        if self.is_cutter:
            intersecting_objs = []
            for obj in doc.Objects:
                if obj == box:
                    continue
                # Simple check: does it have a Shape?
                if hasattr(obj, "Shape") and obj.Shape.isValid():
                    try:
                        # Check collision/intersection
                        # common volume check is robust
                        if not box.Shape.isValid():
                            continue
                            
                        common = box.Shape.common(obj.Shape)
                        if common.Volume > 1e-5:
                            intersecting_objs.append(obj)
                    except Exception as e:
                        FreeCAD.Console.PrintError(f"Boolean Check Failed for {obj.Name}: {e}\n")
                        continue
            
            if intersecting_objs:
                for target in intersecting_objs:
                    try:
                        name = f"Cut_{target.Name}"
                        cut = doc.addObject("Part::Cut", name)
                        cut.Base = target
                        cut.Tool = box
                        
                        # hide original objects
                        if hasattr(target, "ViewObject") and target.ViewObject:
                            target.ViewObject.Visibility = False
                    except Exception as e:
                        FreeCAD.Console.PrintError(f"Failed to create Cut for {target.Name}: {e}\n")
                        
                # Hide the tool (box) if it made cuts
                if hasattr(box, "ViewObject") and box.ViewObject:
                    box.ViewObject.Visibility = False
                         
                    doc.recompute()

        # Defer termination
        QtCore.QTimer.singleShot(0, self.terminate)
