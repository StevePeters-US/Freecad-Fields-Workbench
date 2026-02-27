"""Base classes for DM primitive creators."""

import FreeCAD
import FreeCADGui
from PySide import QtCore

from FCDirectModeling import dm_logger



# ─────────────────────────────────────────────────────────────────────────────
# Camera helpers (no Coin3D)
# ─────────────────────────────────────────────────────────────────────────────

def _is_orthographic(view):
    """True if the active camera is orthographic (not perspective)."""
    try:
        cam = view.getCameraNode()
        return "Orthographic" in cam.getTypeId().getName()
    except Exception:
        return False

def _cam_pos(view):
    """Camera world position as a FreeCAD.Vector (perspective only)."""
    try:
        cam = view.getCameraNode()
        p = cam.position.getValue()
        return FreeCAD.Vector(p[0], p[1], p[2])
    except Exception:
        return FreeCAD.Vector(0,0,100)


# ─────────────────────────────────────────────────────────────────────────────
# PrimitiveCreatorBase
# ─────────────────────────────────────────────────────────────────────────────

class PrimitiveBase:
    def __init__(self):
        self._terminated = False
        self.view     = FreeCADGui.ActiveDocument.ActiveView
        if not self.view:
            return

        self.callback = self.view.addEventCallback("SoEvent", self.event_cb)

        self.start_point   = None
        self.current_point = None
        self.center        = None # Legacy, use start_point
        self.state         = 0
        
        self.working_plane = None
        self.snap_face = None
        self.drag_start_screen_y = None

        # Shared UX state
        self.height = 0.0
        self.is_cutter = False
        self.manual_mode_override = False
        self.panel = None

        # Shared constraint state
        self.active_axis = None
        self.locked_length = None
        self.locked_width = None
        self.locked_height = None

    def terminate(self):
        self._terminated = True
        try:
            if self.callback:
                self.view.removeEventCallback("SoEvent", self.callback)
                self.callback = None
            
            # Close task panel if open
            import FreeCADGui
            FreeCADGui.Control.closeDialog()
        except Exception:
            pass

    def finish(self):
        pass

    def set_panel(self, panel):
        self.panel = panel

    def toggle_cutter_mode(self):
         self.is_cutter = not self.is_cutter
         self.manual_mode_override = True
         self.update_material()
         self.view.redraw()

    def update_material(self):
        pass

    def update_ui(self):
        pass

    # ------------------------------------------------------------------
    # Geometry helpers — use FreeCAD view API, not Coin3D directly
    # ------------------------------------------------------------------

    def get_mouse_world_pos(self, event_dict, plane_normal=None, plane_point=None):
        """
        Unified Ray-Plane intersection. 
        If plane_normal is None, defaults to a camera-facing plane at the focal depth.
        """
        pos = event_dict.get("Position", (0, 0))
        x, y = pos[0], pos[1]
        
        world_pos = self.view.getPoint(x, y)
        return world_pos

    def get_base_plane(self):
        """Returns (normal, origin) for the current working plane."""
        if not self.working_plane:
            return None, None
        n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
        o = self.working_plane.Base
        return n, o

    def get_mouse_plane_pt(self, event_dict):
        """Intersection of mouse ray with working plane."""
        n, o = self.get_base_plane()
        return self.get_mouse_world_pos(event_dict, n, o)
            

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

    def event_cb(self, event_dict):
        try:
            event_type = event_dict.get("Type", "Unknown")

            if event_type == "SoMouseButtonEvent":
                if event_dict["State"] == "DOWN":
                    return self.handle_click(event_dict)
            elif event_type == "SoLocation2Event":
                self.handle_move(event_dict)
            elif event_type == "SoKeyboardEvent":
                if event_dict["State"] == "DOWN":
                    return self.handle_keyboard(event_dict)
            return False
        except Exception:
            return False

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        
        # ESC to cancel
        if key == "ESCAPE":
            QtCore.QTimer.singleShot(0, self.terminate)
            return True
            
        # Toggle Cutter Mode (C)
        if key == "C":
             self.toggle_cutter_mode()
             return True

        # Axis Toggles (X, Y, Z) -> Focus Panel
        target_axis = None
        if key == "X": target_axis = "x"
        elif key == "Y": target_axis = "y"
        elif key == "Z": target_axis = "z"
        
        if target_axis:
            self.toggle_axis(target_axis)
            return True
        return False

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
            self.panel.clear_focus()
            # Trigger update to snap back to mouse
            self.update_from_locks()
        else:
            # Focus Field
            self.active_axis = target_axis
            self.panel.focus_field(target_axis)

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
        pass

    def handle_click(self, event_dict):
        btn = event_dict.get("Button")
        
        # Right-click (BUTTON3) to finish or drop
        if btn == "BUTTON3":
            if self.state > 0:
                self.finish()
            else:
                self.terminate()
            return True

        if btn != "BUTTON1":
            return False

        # We always use the raw mouse pos for transitions
        pt = self.get_mouse_world_pos(event_dict)
        
        if self.state == 0:
            # Pin 1: Fix the working plane and move to State 1
            # In state 0, working_plane is already being previewed by face-snapping
            rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
            self.working_plane = FreeCAD.Placement(pt, rot)
            self.start_point = pt
            self.current_point = pt
            self.state = 1
            self.on_state_change(self.state)
            return True
            
        elif self.state == 1:
            # Pin 2: Move to State 2
            # current_point is already projected in handle_move
            self.state = 2
            self.drag_start_screen_y = event_dict["Position"][1]
            self.on_state_change(self.state)
            return True
            
        elif self.state == 2:
            # Pin 3: Finish
            self.finish()
            return True
        return False

    def handle_move(self, event_dict):
        if self.state == 0:
            # Detect face under mouse
            obj, subname = self.get_face_under_mouse(event_dict)
            if obj and subname and "Face" in subname:
                try:
                    face = obj.Shape.getElement(subname)
                    if hasattr(face, "Surface") and "GeomPlane" in face.Surface.TypeId:
                        self.working_plane = face.Surface.Position
                        self.snap_face = (obj, subname)
                    else:
                        self.working_plane = None
                        self.snap_face = None
                except Exception:
                    self.working_plane = None
                    self.snap_face = None
            else:
                self.working_plane = None
                self.snap_face = None

            raw_pt = self.get_mouse_world_pos(event_dict)
            self.update_preview(debug_pt=raw_pt)
            
        elif self.state == 1:
            pt = self.get_mouse_world_pos(event_dict)
            self.current_point = pt
            
            dm_logger.debug(f"DEBUG: Mouse Position (World): {pt.x:.2f}, {pt.y:.2f}, {pt.z:.2f}")
            dm_logger.debug(f"DEBUG: Corner Position (World): {self.current_point.x:.2f}, {self.current_point.y:.2f}, {self.current_point.z:.2f}")
            
            self.update_preview(debug_pt=pt)
            self.update_ui()
            
        elif self.state == 2:
            # Standard height drag calculation
            current_screen_y = event_dict["Position"][1]
            if self.drag_start_screen_y is not None:
                delta = current_screen_y - self.drag_start_screen_y
                # Subclasses might override how height is applied
                self.apply_height(delta / 4.0)
            
            # Show crosshair at the top
            n, o = self.get_base_plane()
            if o and hasattr(self, "height"):
                o = o + n * self.height
                raw_pt = self.get_mouse_world_pos(event_dict, n, o)
                self.update_preview(debug_pt=raw_pt)
            else:
                raw_pt = self.get_mouse_world_pos(event_dict)
                self.update_preview(debug_pt=raw_pt)
            self.update_ui()

    def on_state_change(self, new_state):
        self.update_ui()

    def apply_height(self, height):
        if hasattr(self, "height"):
            self.height = height

    def to_local(self, p):
        if not hasattr(self, "working_plane") or not self.working_plane:
            return p
        mat = self.working_plane.toMatrix()
        mat.invert()
        return mat.multVec(p)

    def to_global(self, p):
        if not hasattr(self, "working_plane") or not self.working_plane:
            return p
        return self.working_plane.toMatrix().multVec(p)

    def get_face_under_mouse(self, event_dict):
        pos = event_dict["Position"]
        try:
            info = self.view.getObjectInfo((pos[0], pos[1]))
            if info and "Object" in info and "Component" in info:
                 return info["Object"], info["Component"]
        except Exception:
            pass
        return None, None

# ─────────────────────────────────────────────────────────────────────────────
# DMPrimitiveCreator
# ─────────────────────────────────────────────────────────────────────────────

class DMPrimitiveCreator(PrimitiveBase):
    """
    Base for creators that produce a DM object.
    Live preview updates the main object's shape directly.
    """

    def __init__(self):
        super().__init__()
        self._preview_obj = None   # Mesh::Feature used for live preview
        self._pending_shape_type = None
        self._pending_shape_params = None
        self._preview_queued = False
        self._finished = False     # Guard for finalization

        self._last_shape_type = None
        self._last_shape_params = None
        self._last_placement = None
        
        self._debug_pt = None      # Store current mouse 3D for debug dot
        self._preview_cursor = None # Red dot object

    # ------------------------------------------------------------------
    # Preview — create once, replace .Mesh in-place (no recompute)
    # ------------------------------------------------------------------

    def update_dm_preview(self, shape_type, params, resolution=None, placement=None):
        """
        Setup the pending mesh request, but defer the exact execution 
        to avoid crashing in Coin3D event traversal.
        """
        if self._terminated:
            return

        self._pending_shape_type = shape_type
        self._pending_shape_params = params
        
        if resolution is None:
            # Resolution no longer used for NURBS, keeping stub for compatibility
            resolution = 15
            
        self._pending_resolution = resolution
        
        self._last_shape_type = shape_type
        self._last_shape_params = params
        
        # Track 3D cursor for debugging
        if "debug_pt" in params:
            self._debug_pt = params["debug_pt"]
        
        # Track placement for finalization
        if placement:
            self._last_placement = placement
        elif hasattr(self, "working_plane"):
            self._last_placement = self.working_plane
        
        if not self._preview_queued:
            self._preview_queued = True
            QtCore.QTimer.singleShot(0, self._process_preview_queue)

    def _process_preview_queue(self):
        self._preview_queued = False
        if self._terminated:
            return

        try:
            shape_type = self._pending_shape_type
            params = self._pending_shape_params
            if not shape_type or not params:
                return

            from .. import nurbs_primitives
            import Part

            # Build the raw shape at origin
            if shape_type == "box":
                shape = nurbs_primitives.build_box(params.get("length", 1), params.get("width", 1), params.get("height", 1))
            elif shape_type == "sphere":
                shape = nurbs_primitives.build_sphere(params.get("radius", 1))
            elif shape_type == "cone":
                shape = nurbs_primitives.build_cone(params.get("radius", 1), params.get("height", 1))
            elif shape_type == "torus":
                shape = nurbs_primitives.build_torus(params.get("major_r", 1), params.get("minor_r", 1))
            else:
                shape = Part.Shape()



            doc = FreeCAD.activeDocument()
            if not doc:
                return

            # Hierarchy: Part::Feature (DM_Preview)
            if self._preview_obj is None or self._preview_obj not in doc.Objects:
                self._preview_obj = doc.addObject("Part::Feature", "DM_Preview")
                if hasattr(self._preview_obj, "ViewObject") and self._preview_obj.ViewObject:
                    try:
                        self._preview_obj.ViewObject.Visibility = True
                        self._preview_obj.ViewObject.ShapeColor = (0.20, 0.60, 0.85)
                        self._preview_obj.ViewObject.Transparency = 50 # More transparent for preview
                        self._preview_obj.ViewObject.DisplayMode = "Shaded"
                    except Exception:
                        pass

            # Update shape and placement
            self._preview_obj.Shape = shape
            if self._last_placement:
                 self._preview_obj.Placement = self._last_placement

            # Update Debug Cursor(s)
            debug_pts = []
            if self.start_point: debug_pts.append(self.start_point)
            if self.current_point: debug_pts.append(self.current_point)
            
            # Plus any explicit debug_pt passed in params
            if "debug_pt" in params and params["debug_pt"] not in debug_pts:
                debug_pts.append(params["debug_pt"])

            if debug_pts:
                if self._preview_cursor is None or self._preview_cursor not in doc.Objects:
                    self._preview_cursor = doc.addObject("Part::Feature", "DM_DebugCursor")
                    if hasattr(self._preview_cursor, "ViewObject") and self._preview_cursor.ViewObject:
                        self._preview_cursor.ViewObject.ShapeColor = (0.0, 0.4, 1.0) # Blue
                        self._preview_cursor.ViewObject.LineColor = (0.0, 0.4, 1.0)
                        self._preview_cursor.ViewObject.LineWidth = 3.0
                        self._preview_cursor.ViewObject.PointSize = 10.0
                        self._preview_cursor.ViewObject.Selectable = False
                        if hasattr(self._preview_cursor.ViewObject, "LightModel"):
                            self._preview_cursor.ViewObject.LightModel = "NoLight"
                        if hasattr(self._preview_cursor, "ShowInTree"):
                             self._preview_cursor.ShowInTree = False

                # Create a compound of crosshairs
                crosses = []
                for pt in debug_pts:
                    crosses.extend([
                        Part.makeLine((pt.x-5,pt.y,pt.z),(pt.x+5,pt.y,pt.z)),
                        Part.makeLine((pt.x,pt.y-5,pt.z),(pt.x,pt.y+5,pt.z)),
                        Part.makeLine((pt.x,pt.y,pt.z-5),(pt.x,pt.y,pt.z+5))
                    ])
                self._preview_cursor.Shape = Part.Compound(crosses)
                self._preview_cursor.Placement = FreeCAD.Placement()

            # A plain updateGui() is sufficient to repaint. No recompute needed.
            FreeCADGui.updateGui()

        except Exception as e:
            dm_logger.debug(f"DEBUG: _process_preview_queue error: {e}")
            pass


    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def finish(self):
        """Schedule the finalization to happen safely outside the event loop."""
        QtCore.QTimer.singleShot(0, self._do_finish)

    def _do_finish(self):
        """Standard finalization for all DM primitives."""
        if self._finished:
            return
            
        try:
            if self._last_shape_type and self._last_shape_params:
                from FCDirectModeling.dm_object import create_dm_object
                dm_logger.debug(f"_do_finish: Finalizing. type={self._last_shape_type}, params={self._last_shape_params}, placement={self._last_placement}")
                # Create the final high-res DMObject
                create_dm_object(
                    self._last_shape_type.capitalize(), 
                    self._last_shape_type, 
                    self._last_shape_params,
                    placement=self._last_placement
                )
            else:
                dm_logger.debug(f"_do_finish: No data to finalize (type={self._last_shape_type})")
        except Exception as e:
            dm_logger.error(f"_do_finish FAILED: {e}")
            import traceback
            dm_logger.error(traceback.format_exc())

        self._finished = True
        self.terminate()

        # Close task panel if open
        try:
            import FreeCADGui
            FreeCADGui.Control.closeDialog()
        except Exception:
            pass

    def terminate(self):
        """Remove preview objects and unregister the event callback."""
        if self._preview_obj is not None:
            try:
                doc = FreeCAD.activeDocument()
                if doc:
                    # Remove debug cursor
                    if self._preview_cursor is not None:
                        if self._preview_cursor in doc.Objects:
                            doc.removeObject(self._preview_cursor.Name)
                        self._preview_cursor = None
                    # Remove the preview object
                    if self._preview_obj in doc.Objects:
                        doc.removeObject(self._preview_obj.Name)
                    doc.recompute()
            except Exception as e:
                dm_logger.debug(f"Error removing preview objects: {e}")
            self._preview_obj = None
        super().terminate()

