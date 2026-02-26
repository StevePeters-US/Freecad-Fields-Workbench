import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtGui
from .base import SDFMeshPrimitiveCreator
from FCDirectModeling import sdf_logger

class BoxCreator(SDFMeshPrimitiveCreator):
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
        pass

    def toggle_cutter_mode(self):
         self.is_cutter = not self.is_cutter
         self.manual_mode_override = True
         self.view.redraw()

    def terminate(self):
        super().terminate()
        self.panel = None

    def set_panel(self, panel):
        self.panel = panel

    def get_face_under_mouse(self, event_dict):
        pos = event_dict["Position"]
        try:
            sdf_logger.debug(f"DEBUG: get_face_under_mouse at {pos}")
            # getObjectInfo returns a dict with 'Object', 'Component', etc.
            info = self.view.getObjectInfo((pos[0], pos[1]))
            if info and "Object" in info and "Component" in info:
                 sdf_logger.debug(f"DEBUG: Found {info['Object'].Label} : {info['Component']}")
                 return info["Object"], info["Component"]
            sdf_logger.debug("DEBUG: No object under mouse")
        except Exception as e:
            sdf_logger.debug(f"DEBUG: getObjectInfo error: {e}")
            pass
        return None, None

    def set_length_lock(self, length):
        self.locked_length = length
        self.update_from_locks()
        
    def set_width_lock(self, width):
        self.locked_width = width
        self.update_from_locks()
        
    def set_height_lock(self, height):
        self.locked_height = height
        self.height = height
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
        try:
            if not self.start_point or not self.current_point:
                return
                
            p1 = self.start_point
            p2 = self.current_point
            
            # Send SIGNED deltas to UI so positive = positive direction
            length = p2.x - p1.x
            width = p2.y - p1.y
            height = self.height
            
            sdf_logger.debug(f"DEBUG: update_ui {length}, {width}, {height}")
            if self.panel:
                 self.panel.update_values(length, width, height)
                 sdf_logger.debug("DEBUG: panel.update_values finished")
        except Exception as e:
            sdf_logger.debug(f"DEBUG: update_ui error: {e}")

    def to_local(self, p):
        sdf_logger.debug(f"DEBUG: to_local {p}")
        if not self.working_plane:
            return p
        # inverse matrix
        mat = self.working_plane.toMatrix()
        mat.invert()
        v = mat.multVec(p)
        sdf_logger.debug(f"DEBUG: to_local result {v}")
        return v

    def to_global(self, p):
        sdf_logger.debug(f"DEBUG: to_global {p}")
        if not self.working_plane:
            return p
        v = self.working_plane.toMatrix().multVec(p)
        sdf_logger.debug(f"DEBUG: to_global result {v}")
        return v

    def update_preview(self):
        if not self.start_point or not self.current_point:
            return
            
        p1 = self.to_local(self.start_point)
        p2 = self.to_local(self.current_point)
        h = self.height
        
        min_x = min(p1.x, p2.x);  max_x = max(p1.x, p2.x)
        min_y = min(p1.y, p2.y);  max_y = max(p1.y, p2.y)
        
        # Show a thin slab during base-draw (h==0) so user sees feedback
        min_thick = max(1.0, max(max_x - min_x, max_y - min_y) * 0.02)
        h_abs = max(min_thick, abs(h))
        z_min = -h_abs if h < 0 else 0.0
        z_max = z_min + h_abs
        
        bounds_min = [min_x, min_y, z_min]
        bounds_max = [max_x, max_y, z_max]
        
        self.update_sdf_preview("box", {"bounds_min": bounds_min, "bounds_max": bounds_max})
        
        # Override the base state with the REAL (unclamped) bounds for finalization
        self._last_sdf_params = {
            "bounds_min": [min_x, min_y, -abs(h) if h < 0 else 0.0],
            "bounds_max": [max_x, max_y, abs(h) if h != 0 else z_max]
        }

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
            if self.panel:
                import FreeCADGui
                FreeCADGui.Control.closeDialog()
            else:
                QtCore.QTimer.singleShot(0, self.terminate)
            return True
            
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
        sdf_logger.debug(f"DEBUG: handle_click, state={self.state}")
        n = FreeCAD.Vector(0,0,1)
        o = FreeCAD.Vector(0,0,0)
        if self.working_plane:
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = self.working_plane.Base
            
        pt = self.get_point_on_plane(event_dict, n, o)
        
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
        sdf_logger.debug(f"DEBUG: BoxCreator.handle_move, state={self.state}")
        if self.state == 0:
            # Detect face under mouse
            obj, subname = self.get_face_under_mouse(event_dict)
            if obj and subname and "Face" in subname:
                try:
                    sdf_logger.debug(f"DEBUG: Accessing face {subname}...")
                    face = obj.Shape.getElement(subname)
                    sdf_logger.debug(f"DEBUG: Face Surface Type: {face.Surface.TypeId}")
                    if hasattr(face, "Surface") and "GeomPlane" in face.Surface.TypeId:
                        self.working_plane = face.Surface.Position
                        self.snap_face = (obj, subname)
                        sdf_logger.debug("DEBUG: Working plane set")
                    else:
                        self.working_plane = None
                        self.snap_face = None
                except Exception as e:
                    sdf_logger.debug(f"DEBUG: Face detection error: {e}")
                    self.working_plane = None
                    self.snap_face = None
            return
            
        elif self.state == 1:
            n = FreeCAD.Vector(0,0,1)
            o = FreeCAD.Vector(0,0,0)
            if self.working_plane:
                n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
                o = self.working_plane.Base
            
            sdf_logger.debug(f"DEBUG: Calling get_point_on_plane with n={n}, o={o}")
            raw_pt = self.get_point_on_plane(event_dict, n, o)
            
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

    def _do_finish(self):
        if not self.start_point or (not self.current_point and self.state == 0):
             # If completely uninitialized, just terminate
            self.terminate()
            return
            
        super()._do_finish()

