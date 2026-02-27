import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtGui
from .primitive_base import DMPrimitiveCreator
from FCDirectModeling import dm_logger

class CurveCreator(DMPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.points = []
        self.is_finished = False

    def handle_click(self, event_dict):
        btn = event_dict.get("Button")
        
        # Right-click (BUTTON3) to finish
        if btn == "BUTTON3":
            if len(self.points) >= 2:
                self.finish()
            else:
                self.terminate()
            return True

        if btn != "BUTTON1":
            return False

        pt = self.get_mouse_world_pos(event_dict)
        
        # In state 0, first click sets the working plane
        if self.state == 0:
            rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
            self.working_plane = FreeCAD.Placement(pt, rot)
            self.start_point = pt
            self.points.append(pt)
            self.state = 1
            self.update_ui()
            return True
        
        # In state 1, subsequent clicks add points
        elif self.state == 1:
            # Check for click on start point (close the curve)
            if len(self.points) >= 2:
                dist = (pt - self.points[0]).Length
                if dist < 1.0: # Snapping distance for click
                    self.points.append(self.points[0]) # Exact close
                    self.finish()
                    return True

            self.points.append(pt)
            dm_logger.debug(f"DEBUG: Curve point added. Total: {len(self.points)}")
            self.update_preview()
            self.update_ui()
            return True
            
        return False

    def handle_move(self, event_dict):
        if self.state == 0:
            # Face snapping logic from base
            super().handle_move(event_dict)
        elif self.state == 1:
            # Update temporary "current_point" for preview
            pt = self.get_mouse_world_pos(event_dict)
            
            # Snapping to start point
            if len(self.points) >= 2:
                dist = (pt - self.points[0]).Length
                if dist < 1.0: # Snapping distance for move
                    pt = self.points[0]

            self.current_point = pt
            self.update_preview(debug_pt=pt)
            self.update_ui()

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        
        if key == "RETURN" or key == "ENTER":
            if len(self.points) >= 2:
                self.finish()
            else:
                self.terminate()
            return True
            
        return super().handle_keyboard(event_dict)

    def update_preview(self, debug_pt=None):
        if not self.points:
            return
            
        # Preview curve = confirmed points + current mouse position
        preview_points = list(self.points)
        if self.current_point and self.state == 1:
            preview_points.append(self.current_point)
            
        params = {
            "points": preview_points
        }
        if debug_pt:
            params["debug_pt"] = debug_pt
            
        self.update_dm_preview("curve", params, placement=FreeCAD.Placement())

    def update_ui(self):
        # Optional: update panel with point count or last segment length
        pass

    def _process_preview_queue(self):
        """Override to handle 'curve' type."""
        self._preview_queued = False
        if self._terminated:
            return

        try:
            shape_type = self._pending_shape_type
            params = self._pending_shape_params
            if not shape_type or not params:
                return

            if shape_type == "curve":
                from .. import nurbs_primitives
                shape = nurbs_primitives.build_curve(params.get("points", []))
            else:
                # Fallback to base for standard primitives
                return super()._process_preview_queue()

            doc = FreeCAD.activeDocument()
            if not doc: return

            # Setup/Update preview object
            if self._preview_obj is None or self._preview_obj not in doc.Objects:
                self._preview_obj = doc.addObject("Part::Feature", "DM_Preview")
                if hasattr(self._preview_obj, "ViewObject") and self._preview_obj.ViewObject:
                    self._preview_obj.ViewObject.LineWidth = 2.0
                    self._preview_obj.ViewObject.LineColor = (0.2, 0.6, 0.85)

            self._preview_obj.Shape = shape
            
            # Draw debug points
            # (Note: DMPrimitiveCreator._process_preview_queue already handles start/current point)
            # We might want to see all curve points.
            
            FreeCADGui.updateGui()
        except Exception as e:
            dm_logger.debug(f"DEBUG: Curve preview error: {e}")

    def _do_finish(self):
        if len(self.points) < 2:
            self.terminate()
            return
            
        params = {
            "points": self.points
        }
        
        from FCDirectModeling.dm_object import create_dm_object
        create_dm_object("Curve", "curve", params, placement=FreeCAD.Placement())
        self.terminate()
