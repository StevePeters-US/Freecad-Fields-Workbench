import FreeCAD
import FreeCADGui
import Part
from pivy import coin
from PySide import QtCore, QtGui

class BoxCreator:
    def __init__(self):
        self.view = FreeCADGui.ActiveDocument.ActiveView
        self.callback = self.view.addEventCallback("SoEvent", self.event_cb)
        
        self.start_point = None
        self.current_point = None
        
        self.state = 0
        self.height = 0.0
        
        self.active_axis = None
        self.locked_length = None
        self.locked_width = None
        self.locked_height = None
        
        self.locked_height = None
        
        self.locked_height = None
        
        self.manual_mode_override = False
        
        self.working_plane = None # FreeCAD.Placement
        self.snap_face = None # (obj, face_name)
        
        self.panel = None

        self.sg = coin.SoSeparator()
        self.sg.ref() 
        
        # Material (for Tool)
        self.material = coin.SoMaterial()
        self.sg.addChild(self.material)
        self.is_cutter = False
        
        # Preview Intersection Nodes
        self.preview_sep = coin.SoSeparator()
        self.preview_mat = coin.SoMaterial()
        # Bright Yellow/Orange for cut intersection
        self.preview_mat.diffuseColor.setValue(1.0, 0.8, 0.0) 
        self.preview_mat.transparency.setValue(0.2)
        self.preview_sep.addChild(self.preview_mat)
        
        self.preview_coords = coin.SoCoordinate3()
        self.preview_sep.addChild(self.preview_coords)
        
        self.preview_face_set = coin.SoIndexedFaceSet()
        self.preview_sep.addChild(self.preview_face_set)
        
        self.sg.addChild(self.preview_sep)
        

        
        # Coordinates (Tool Box)
        self.coords = coin.SoCoordinate3()
        self.sg.addChild(self.coords)
        
        # Faces (Tool Box)
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
        
        # Initial Material Update
        self.update_material()

    def update_material(self):
        # Force update by explicitly setting fields
        # Ideally we shouldn't need to remove/add, but if ghosts appear, 
        # let's try ensuring the transparency is correctly applied.
        if self.is_cutter:
            # Red for Cut
            self.material.diffuseColor.setValue(1.0, 0.0, 0.0) 
            self.material.transparency.setValue(0.6)
        else:
            # Blue for Create
            self.material.diffuseColor.setValue(0.2, 0.6, 0.8) 
            self.material.transparency.setValue(0.5)
            
    def toggle_cutter_mode(self):
         self.is_cutter = not self.is_cutter
         self.manual_mode_override = True
         self.update_material()
         # Nuclear option: remove and re-add material to force SceneGraph update
         self.sg.removeChild(self.material)
         self.sg.insertChild(self.material, 0) # Insert at beginning
         
         self.view.redraw()

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

    def update_geometry(self):
        if not self.start_point or not self.current_point:
            return
            
        p1 = self.to_local(self.start_point)
        p2 = self.to_local(self.current_point)
        h = self.height
        
        # Min/Max for geometry creation (doesn't care about direction, just bounds)
        min_x = min(p1.x, p2.x)
        max_x = max(p1.x, p2.x)
        min_y = min(p1.y, p2.y)
        max_y = max(p1.y, p2.y)
        
        # 8 Coordinates in LOCAL
        local_coords = [
            FreeCAD.Vector(min_x, min_y, 0), FreeCAD.Vector(max_x, min_y, 0), 
            FreeCAD.Vector(max_x, max_y, 0), FreeCAD.Vector(min_x, max_y, 0),
            FreeCAD.Vector(min_x, min_y, h), FreeCAD.Vector(max_x, min_y, h), 
            FreeCAD.Vector(max_x, max_y, h), FreeCAD.Vector(min_x, max_y, h)
        ]
        
        # Convert to GLOBAL for Coin3D
        coords = [[v.x, v.y, v.z] for v in [self.to_global(lp) for lp in local_coords]]
        
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
                handled = self.handle_keyboard(event_dict)
                if handled:
                    return True
            
        return False
        
    def preview_cut(self):
        # Update preview only if cutter mode is active
        if not self.is_cutter:
            return

        doc = FreeCAD.activeDocument()
        if not doc or not self.current_point:
             return

        # 1. Create Temporary Box Shape
        # Calculate in Local
        p1 = self.to_local(self.start_point)
        p2 = self.to_local(self.current_point)
        
        min_x = min(p1.x, p2.x)
        max_x = max(p1.x, p2.x)
        min_y = min(p1.y, p2.y)
        max_y = max(p1.y, p2.y)
        
        width = max_x - min_x
        length = max_y - min_y
        
        # Avoid zero dimensions for shape creation
        if width < 0.001: width = 0.001
        if length < 0.001: length = 0.001
        
        raw_height = self.height if abs(self.height) > 0.001 else 1.0
        
        # Part.makeBox requires positive dimensions
        box_h = abs(raw_height)
        z_offset = 0.0
        if raw_height < 0:
            z_offset = raw_height
            
        try:
            # Create box in local coords
            box_shape = Part.makeBox(width, length, box_h)
            
            # Apply local translation (offset to min_x, min_y, z_offset)
            local_pos = FreeCAD.Vector(min_x, min_y, z_offset)
            box_shape.translate(local_pos)
            
            # Apply Working Plane (Global Transform)
            if self.working_plane:
                # box_shape is currently 'aligned' to local system.
                # We need to transform it to global.
                box_shape.transformShape(self.working_plane.toMatrix())
                
        except Exception as e:
            FreeCAD.Console.PrintError(f"Preview Box Creation Failed: {e}\n")
            return
        
        # 2. Find Intersections (Volume to be removed)
        intersecting_shapes = []
        for obj in doc.Objects:
             if hasattr(obj, "Shape") and obj.Shape.isValid() and hasattr(obj, "ViewObject") and obj.ViewObject.Visibility:
                 try:
                     # Check BBox first
                     if not obj.Shape.BoundBox.intersect(box_shape.BoundBox):
                         continue
                     
                     # Check Common
                     # FreeCAD.Console.PrintMessage(f"Checking intersection with {obj.Name}...\n")
                     common = obj.Shape.common(box_shape)
                     if common.Volume > 1e-6:
                         intersecting_shapes.append(common)
                 except Exception as e:
                     # Don't spam console for expected failures on weird shapes, but log once if needed
                     # FreeCAD.Console.PrintError(f"Preview Check Failed for {obj.Name}: {e}\n")
                     continue

        if not intersecting_shapes:
            # Clear preview
            self.preview_coords.point.setNum(0)
            self.preview_face_set.coordIndex.setNum(0)
            return
            
        # 3. Fuse intersections for visualization
        try:
            visual_shape = intersecting_shapes[0]
            if len(intersecting_shapes) > 1:
                for s in intersecting_shapes[1:]:
                    visual_shape = visual_shape.fuse(s)
        except Exception as e:
            FreeCAD.Console.PrintError(f"Preview Fuse Failed: {e}\n")
            return
                
        # 4. Tessellate and Update Node
        try:
            # Use simple deflection
            tess = visual_shape.tessellate(0.5) 
            verts = tess[0]
            faces = tess[1]
            
            self.preview_coords.point.setValues(0, len(verts), verts)
            
            coord_indices = []
            for f in faces:
                coord_indices.extend(f)
                coord_indices.append(-1)
                
            self.preview_face_set.coordIndex.setValues(0, len(coord_indices), coord_indices)
        except Exception as e:
             FreeCAD.Console.PrintError(f"Preview Tessellation Failed: {e}\n")
            
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
            else:
                self.working_plane = None
                self.snap_face = None
            return
            
        if self.state == 1:
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
            
            self.update_geometry()
            self.update_ui()
            
            if self.is_cutter:
                self.preview_cut()
            else:
                self.preview_coords.point.setNum(0)
                self.preview_face_set.coordIndex.setNum(0)
            
        elif self.state == 2:
            # Handle height
            current_screen_y = event_dict["Position"][1]
            
            if self.locked_height is not None:
                self.height = self.locked_height
            else:
                if hasattr(self, 'drag_start_screen_y'):
                    delta = current_screen_y - self.drag_start_screen_y
                    self.height = delta / 2.0 
            
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
            
            self.update_geometry()
            self.update_ui()
            
            if self.is_cutter:
                self.preview_cut()
            else:
                self.preview_coords.point.setNum(0)
                self.preview_face_set.coordIndex.setNum(0)

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
        
        box.Length = width
        box.Width = length
        box.Height = self.height if abs(self.height) > 0.001 else 1.0
        
        # Placement
        # Base in Local
        local_base = FreeCAD.Vector(min_x, min_y, 0)
        
        # Final Placement
        if self.working_plane:
             local_placement = FreeCAD.Placement(local_base, FreeCAD.Rotation())
             # Correct: Global = Plane * Local
             final_placement = self.working_plane.multiply(local_placement)
             box.Placement = final_placement
        else:
             box.Placement.Base = local_base
        
        doc.recompute()
        
        # Auto Fuse Logic
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
                        # Ensure box shape is valid?
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
