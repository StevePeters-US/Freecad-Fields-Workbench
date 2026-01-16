import FreeCAD
import FreeCADGui
from pivy import coin
from PySide import QtCore, QtGui
# box_input_dialog import removed

class BoxCreator:
    def __init__(self):
        self.view = FreeCADGui.ActiveDocument.ActiveView
        self.callback = self.view.addEventCallback("SoEvent", self.event_cb)
        self.state = 0 # 0: Waiting, 1: Dragging Base, 2: Dragging Height
        self.start_point = None
        self.current_point = None
        self.height = 0.0
        
        # UI Reference (Side Panel)
        self.panel = None 
        
        # Interactive Inputs
        self.active_axis = None
        self.locked_length = None
        self.locked_width = None
        self.locked_height = None
        
        # Scenegraph
        self.sg = coin.SoSeparator()
        self.sg.ref() # Reference counting to prevent premature deletion
        
        # Material
        self.material = coin.SoMaterial()
        self.material.diffuseColor.setValue(0.2, 0.6, 0.8) 
        self.material.transparency.setValue(0.5)
        self.sg.addChild(self.material)
        
        # Coordinates
        self.coords = coin.SoCoordinate3()
        self.sg.addChild(self.coords)
        
        # Faces
        self.face_set = coin.SoIndexedFaceSet()
        self.sg.addChild(self.face_set)
        
        # Outline (Black lines)
        self.line_sep = coin.SoSeparator()
        self.line_mat = coin.SoMaterial()
        self.line_mat.diffuseColor.setValue(0,0,0)
        self.line_sep.addChild(self.line_mat)
        self.line_coords = coin.SoCoordinate3() # Shared coords technically better, but separate is easier to manage
        self.line_sep.addChild(self.line_coords)
        self.line_set = coin.SoIndexedLineSet()
        self.line_sep.addChild(self.line_set)
        
        self.sg.addChild(self.line_sep)
        
        self.view.getSceneGraph().addChild(self.sg)
        
    def terminate(self):
        if self.callback:
            self.view.removeEventCallback("SoEvent", self.callback)
            self.callback = None
        if self.sg:
            self.view.getSceneGraph().removeChild(self.sg)
            self.sg.unref()
            self.sg = None
            
        # Close task panel
        FreeCADGui.Control.closeDialog()

    def set_panel(self, panel):
        self.panel = panel
            
    def get_mouse_point_on_plane(self, event_dict):
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

        # Intersect with Z=0 plane (Normal=(0,0,1), Point=(0,0,0))
        plane_normal = FreeCAD.Vector(0,0,1)
        plane_point = FreeCAD.Vector(0,0,0)
        
        denom = ray_dir.dot(plane_normal)
        
        if abs(denom) < 1e-6:
            # Ray is parallel to plane, return focal point projected to Z=0 as fallback
            return FreeCAD.Vector(point_on_focal_plane.x, point_on_focal_plane.y, 0)
            
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
        self.update_geometry()
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

    def update_geometry(self):
        if not self.start_point or not self.current_point:
            return
            
        p1 = self.start_point
        p2 = self.current_point
        h = self.height
        
        # Min/Max for geometry creation (doesn't care about direction, just bounds)
        min_x = min(p1.x, p2.x)
        max_x = max(p1.x, p2.x)
        min_y = min(p1.y, p2.y)
        max_y = max(p1.y, p2.y)
        
        # 8 Coordinates
        coords = [
            [min_x, min_y, 0], [max_x, min_y, 0], [max_x, max_y, 0], [min_x, max_y, 0],
            [min_x, min_y, h], [max_x, min_y, h], [max_x, max_y, h], [min_x, max_y, h]
        ]
        
        self.coords.point.setValues(0, 8, coords)
        self.line_coords.point.setValues(0, 8, coords)
        
        # Faces/Lines setup remains same (omitted for brevity in replacement if unchanged)
        # But wait, replace_file_content needs contiguity. Steps below cover it all?
        # Re-adding faces/lines setup just in case I need to cover large block.
        # Actually I can just skip the invariant parts if I target carefully.
        # I will replace from set_length_lock down to handle_move start to cover update_from_locks and update_ui.
        
        faces = [
            0,3,2,1,-1, # Bottom
            4,5,6,7,-1, # Top
            0,1,5,4,-1, # Front
            1,2,6,5,-1, # Right
            2,3,7,6,-1, # Back
            3,0,4,7,-1  # Left
        ]
        self.face_set.coordIndex.setValues(0, len(faces), faces)
        
        lines = [
            0,1,2,3,0,-1, # Base loop
            4,5,6,7,4,-1, # Top loop
            0,4,-1, 1,5,-1, 2,6,-1, 3,7,-1 # Vertical struts
        ]
        self.line_set.coordIndex.setValues(0, len(lines), lines)

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
                self.handle_keyboard(event_dict)
            
        return False
        
    def handle_keyboard(self, event_dict):
        key = event_dict["Key"]
        
        # ESC to cancel
        if key == "ESCAPE":
            QtCore.QTimer.singleShot(0, self.terminate)
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
        
        pt = self.get_mouse_point_on_plane(event_dict)
        
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
            return
            
        if self.state == 1:
            raw_pt = self.get_mouse_point_on_plane(event_dict)
            p1 = self.start_point
            
            # Raw Signed Deltas from Mouse
            dx_mouse = raw_pt.x - p1.x
            dy_mouse = raw_pt.y - p1.y
            
            # Final Deltas (Lock Overrides)
            new_dx = dx_mouse
            new_dy = dy_mouse
            
            if self.locked_length is not None:
                 new_dx = self.locked_length
                 
            if self.locked_width is not None:
                 new_dy = self.locked_width
            
            new_x = p1.x + new_dx
            new_y = p1.y + new_dy
            
            self.current_point = FreeCAD.Vector(new_x, new_y, 0)
            
            self.update_geometry()
            self.update_ui()
            
        elif self.state == 2:
            # Handle height
            current_screen_y = event_dict["Position"][1]
            
            if self.locked_height is not None:
                self.height = self.locked_height
            else:
                if hasattr(self, 'drag_start_screen_y'):
                    delta = current_screen_y - self.drag_start_screen_y
                    self.height = delta / 2.0 
            
            self.update_geometry()
            self.update_ui()

    def finish(self):
        if not self.start_point or (not self.current_point and self.state == 0):
             # If completely uninitialized, just terminate
            self.terminate()
            return

        doc = FreeCAD.activeDocument()
        if not doc:
            doc = FreeCAD.newDocument()
            
        box = doc.addObject("Part::Box", "Box")
        
        # Ensure we have points
        if not self.current_point:
             self.current_point = self.start_point
             
        p1 = self.start_point
        p2 = self.current_point
        
        min_x = min(p1.x, p2.x)
        max_x = max(p1.x, p2.x)
        min_y = min(p1.y, p2.y)
        max_y = max(p1.y, p2.y)
        
        width = max_x - min_x
        length = max_y - min_y
        
        # Prevent zero dimensions
        if width < 0.001: width = 1.0
        if length < 0.001: length = 1.0
        
        box.Length = width
        box.Width = length
        box.Height = self.height if abs(self.height) > 0.001 else 1.0
        
        box.Placement.Base = FreeCAD.Vector(min_x, min_y, 0)
        
        doc.recompute()
        # Defer termination
        QtCore.QTimer.singleShot(0, self.terminate)
