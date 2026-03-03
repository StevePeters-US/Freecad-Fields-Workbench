import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui
from tools.primitive_base import PrimitiveBase
from core import dm_logger

class EditTool(PrimitiveBase):
    """
    Interactive tool for editing DM curves control points and handles.
    """
    def __init__(self):
        super().__init__()
        self._target_obj = None
        
        self._hovered_element = None # (index, type)
        self._selected_element = None # (index, type)
        
        # State
        self.drag_plane_n = None
        self.drag_plane_o = None
        
        self._cursor_active = False
        self._last_click_time = 0

    def activate(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            dm_logger.error("No object selected to edit.")
            self.terminate()
            return

        obj = sel[0]
        if hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMObjectProxy":
            if hasattr(obj, "ShapeType") and obj.ShapeType == "curve":
                self._target_obj = obj
            else:
                dm_logger.error("Selected object is not an editable curve.")
                self.terminate()
                return
        else:
            dm_logger.error("Selected object is not a DM object.")
            self.terminate()
            return
            
        dm_logger.info(f"EditTool activated for {self._target_obj.Label}")
        
        # Toggle EditMode ON
        self._target_obj.EditMode = True
        self._target_obj.ViewObject.show() # Ensure overlay is updated
        if self._target_obj.Document:
            self._target_obj.Document.recompute()

    def _get_ray(self, event_dict):
        pos = event_dict.get("Position", (0, 0))
        x, y = pos[0], pos[1]

        if not self.view:
            return None, None

        scene_pt = None
        try:
            scene_pt = self.view.getPoint(x, y)
        except Exception as e:
            dm_logger.debug(f"EditTool._get_ray: getPoint failed: {e}")

        if scene_pt is None:
            # Fallback point generated randomly ahead
            vd = self.view.getViewDirection()
            focus = self.view.getFocus() if hasattr(self.view, "getFocus") else FreeCAD.Vector(0,0,0)
            scene_pt = focus

        try:
            cam = self.view.getCameraNode()
            if not cam or not hasattr(cam, "position"):
                return None, None
                
            cam_vec = cam.position.getValue()
            if hasattr(cam_vec, "getValue"):
                cam_pos_tuple = cam_vec.getValue()
            else:
                cam_pos_tuple = (cam_vec[0], cam_vec[1], cam_vec[2])
            
            ray_p = FreeCAD.Vector(*cam_pos_tuple)
            ray_d = scene_pt - ray_p
            ray_d.normalize()
            
            return ray_p, ray_d
        except Exception as e:
            dm_logger.debug(f"EditTool get ray failed: {e}")
            return None, None

    def _hit_test(self, ray_p, ray_d):
        if not self._target_obj or not ray_p or not ray_d: return None
        
        pts = getattr(self._target_obj, "Points", [])
        h_in = getattr(self._target_obj, "HandleIn", [])
        h_out = getattr(self._target_obj, "HandleOut", [])
        
        best_dist = float('inf')
        best_elem = None
        
        inv_plac = self._target_obj.Placement.inverse()
        local_ray_p = inv_plac.multVec(ray_p)
        local_ray_d = inv_plac.Rotation.multVec(ray_d)
        
        elements = []
        for i, p in enumerate(pts):
            elements.append((i, "Point", p))
            if i < len(h_in): elements.append((i, "HandleIn", h_in[i]))
            if i < len(h_out): elements.append((i, "HandleOut", h_out[i]))
            
        for i, elem_type, pos in elements:
            if not pos: continue
            
            v = pos - local_ray_p
            dist = v.cross(local_ray_d).Length
            cam_dist = v.dot(local_ray_d)
            
            if cam_dist < 0: continue
            
            # Approximate visual clicking tolerance
            from core.dm_object import get_picking_radius
            tolerance = get_picking_radius()
            
            # Hit test against the line (dist)
            if dist < tolerance and dist < best_dist:
                best_dist = dist
                best_elem = (i, elem_type)
                
        return best_elem

    def _hit_test_edge(self, ray_p, ray_d):
        """Hit test against the curve edge itself."""
        if not self._target_obj or not ray_p or not ray_d: return None
        
        shape = self._target_obj.Shape
        if not shape: return None
        
        # Use Part.Shape.distToShape or similar to find distance from ray to edge
        # We can approximate by checking points on the ray
        best_t = None
        best_p = None
        min_dist = float('inf')
        
        # For simplicity, let's use the local plane intersection and check distance to shape
        vd = self.view.getViewDirection()
        n = FreeCAD.Vector(-vd.x, -vd.y, -vd.z)
        o = self._target_obj.Placement.Base
        
        pos_global = self.get_mouse_world_pos({"Position": self.view.getCursorPos()}, n, o)
        if not pos_global: return None
        
        dist, detail = shape.distToShape(Part.Point(pos_global).toShape())
        
        from core.dm_object import get_picking_radius
        if dist < get_picking_radius():
            return detail[0][2] # Closest point on shape (Global)
            
        return None

    def handle_click(self, event_dict):
        click_count = event_dict.get("ClickCount", 1)
        
        if self.state == 1:
            # DROP logic
            self.state = 0
            self._selected_element = None
            dm_logger.debug("Dropped element")
            return True
            
        # DOUBLE-CLICK to ADD point
        if click_count > 1:
            hit_p = self._hit_test_edge(None, None) # Uses current mouse pos internally
            if hit_p:
                self._insert_point(hit_p)
                return True

        # SELECT logic
        ray_p, ray_d = self._get_ray(event_dict)
        hit = self._hit_test(ray_p, ray_d)
        
        if hit:
            self._selected_element = hit
            
            # Setup drag plane
            idx, elem_type = hit
            pts = getattr(self._target_obj, "Points", [])
            h_in = getattr(self._target_obj, "HandleIn", [])
            h_out = getattr(self._target_obj, "HandleOut", [])
            
            val = None
            if elem_type == "Point": val = pts[idx]
            elif elem_type == "HandleIn": val = h_in[idx]
            elif elem_type == "HandleOut": val = h_out[idx]
            
            # Constraint Plane: Use the world plane if it exists, else camera
            # (Following user's "curve plane if 2d, camera if 3d")
            vd = self.view.getViewDirection()
            self.drag_plane_n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
            self.drag_plane_n.normalize()
            self.drag_plane_o = self._target_obj.Placement.multVec(val)
            
            self.state = 1 # Dragging (Selecting -> Moving)
            dm_logger.debug(f"Selected {elem_type} at index {idx}")
            return True
        else:
            return True

    def handle_move(self, event_dict):
        if self.state == 1 and self._selected_element:
            # Dragging
            pt_global = self.get_mouse_world_pos(event_dict, self.drag_plane_n, self.drag_plane_o)
            if not pt_global: return
            
            pt_local = self._target_obj.Placement.inverse().multVec(pt_global)
            self._update_element(self._selected_element, pt_local)
        else:
            # Hover check
            ray_p, ray_d = self._get_ray(event_dict)
            hit = self._hit_test(ray_p, ray_d)
            if hit != self._hovered_element:
                self._hovered_element = hit
                if hit:
                    QtGui.QApplication.setOverrideCursor(QtCore.Qt.PointingHandCursor)
                    self._cursor_active = True
                else:
                    QtGui.QApplication.restoreOverrideCursor()
                    self._cursor_active = False

    def on_button1_down(self, event_dict):
        return self.handle_click(event_dict)

    def on_button2_down(self, event_dict):
        # Middle Mouse mapped to context menu previously
        return self.handle_right_click(event_dict)

    def on_button3_down(self, event_dict):
        # Right click finishes the tool normally, but user asked for overridable hook
        # Let this also trigger the context menu as an option, or leave to finish
        # For now, let's map Right Click to the context menu too, since `handle_right_click` implies so
        return self.handle_right_click(event_dict)

    def _update_element(self, element, new_pos):
        idx, elem_type = element
        pts = list(getattr(self._target_obj, "Points", []))
        h_in = list(getattr(self._target_obj, "HandleIn", []))
        h_out = list(getattr(self._target_obj, "HandleOut", []))
        p_types = list(getattr(self._target_obj, "PointTypes", []))
        
        while len(p_types) < len(pts): p_types.append(0)
        
        pt_type = p_types[idx]
        
        if elem_type == "Point":
            delta = new_pos - pts[idx]
            pts[idx] = new_pos
            if idx < len(h_in): h_in[idx] += delta
            if idx < len(h_out): h_out[idx] += delta
        elif elem_type == "HandleIn":
            h_in[idx] = new_pos
            if pt_type == 0: # Tangent
                delta = new_pos - pts[idx]
                h_out[idx] = pts[idx] - delta
            elif pt_type == 1: # Split
                delta = new_pos - pts[idx]
                if delta.Length > 1e-6:
                    out_len = (h_out[idx] - pts[idx]).Length
                    delta.normalize()
                    h_out[idx] = pts[idx] - (delta * out_len)
        elif elem_type == "HandleOut":
            h_out[idx] = new_pos
            if pt_type == 0: # Tangent
                delta = new_pos - pts[idx]
                h_in[idx] = pts[idx] - delta
            elif pt_type == 1: # Split
                delta = new_pos - pts[idx]
                if delta.Length > 1e-6:
                    in_len = (h_in[idx] - pts[idx]).Length
                    delta.normalize()
                    h_in[idx] = pts[idx] - (delta * in_len)
                    
        self._target_obj.Points = pts
        self._target_obj.HandleIn = h_in
        self._target_obj.HandleOut = h_out
        self._target_obj.touch()
        if self._target_obj.Document:
            self._target_obj.Document.recompute()

    def _insert_point(self, pos_global):
        """Insert a point into the curve at the given global position."""
        if not self._target_obj: return
        
        pos_local = self._target_obj.Placement.inverse().multVec(pos_global)
        pts = list(getattr(self._target_obj, "Points", []))
        
        if len(pts) < 2:
            pts.append(pos_local)
        else:
            # Find which segment is closest to the hit point
            # We can use Part.BSplineCurve.parameter() and check it against knot values if we want to be very precise,
            # or just find the two closest existing points.
            best_idx = 1
            min_d = float('inf')
            for i in range(len(pts)-1):
                p1, p2 = pts[i], pts[i+1]
                # Distance to segment p1-p2
                v = p2 - p1
                w = pos_local - p1
                t = w.dot(v) / v.dot(v)
                t = max(0, min(1, t))
                proj = p1 + v * t
                d = (pos_local - proj).Length
                if d < min_d:
                    min_d = d
                    best_idx = i + 1
            
            pts.insert(best_idx, pos_local)
            
        self._target_obj.Points = pts
        self._target_obj.touch()
        if self._target_obj.Document:
            self._target_obj.Document.recompute()
        dm_logger.info("Inserted new point into curve")

    def _delete_selected_point(self):
        """Delete the selected point or handle's parent point."""
        if not self._target_obj or not self._selected_element: return
        
        idx, elem_type = self._selected_element
        pts = list(getattr(self._target_obj, "Points", []))
        if len(pts) <= 2:
            dm_logger.warn("Cannot delete point: curve must have at least 2 points.")
            return

        pts.pop(idx)
        self._target_obj.Points = pts
        
        # Cleanup other properties
        for prop in ["HandleIn", "HandleOut", "PointTypes"]:
            vals = list(getattr(self._target_obj, prop, []))
            if idx < len(vals):
                vals.pop(idx)
                setattr(self._target_obj, prop, vals)
                
        self.state = 0
        self._selected_element = None
        self._target_obj.touch()
        if self._target_obj.Document:
            self._target_obj.Document.recompute()
        dm_logger.info(f"Deleted point at index {idx}")

    def handle_right_click(self, event_dict):
        ray_p, ray_d = self._get_ray(event_dict)
        hit = self._hit_test(ray_p, ray_d)
        if hit:
            idx, elem_type = hit
            self.show_context_menu(idx)
            return True # Consume click
        else:
            # Consume click to prevent default context menus/selection
            return True

    def show_context_menu(self, idx):
        menu = QtGui.QMenu()
        
        action_tangent = menu.addAction("Set Tangent (Smooth)")
        action_split = menu.addAction("Set Split (Smooth but uneven)")
        action_custom = menu.addAction("Set Custom (Sharp/Corner)")
        menu.addSeparator()
        
        angle_menu = menu.addMenu("Quick Angles")
        
        def set_type(val):
            p_types = list(getattr(self._target_obj, "PointTypes", []))
            while len(p_types) < len(getattr(self._target_obj, "Points", [])): p_types.append(0)
            p_types[idx] = val
            self._target_obj.PointTypes = p_types
            
            # Force immediate enforcement by triggering an update of handle in place
            self._update_element((idx, "HandleOut"), getattr(self._target_obj, "HandleOut")[idx])
            
        def set_angle(degrees):
            import math
            pts = list(getattr(self._target_obj, "Points", []))
            h_out = list(getattr(self._target_obj, "HandleOut", []))
            if idx >= len(pts) or idx >= len(h_out): return
            
            p = pts[idx]
            v_out = h_out[idx] - p
            dist = v_out.Length
            if dist < 1e-4:
                dist = 10.0 # Default visible handle size
                
            rad = math.radians(degrees)
            # Apply angle in local XY space
            new_v_out = FreeCAD.Vector(dist * math.cos(rad), dist * math.sin(rad), 0)
            self._update_element((idx, "HandleOut"), p + new_v_out)
            
        action_tangent.triggered.connect(lambda: set_type(0))
        action_split.triggered.connect(lambda: set_type(1))
        action_custom.triggered.connect(lambda: set_type(2))
        
        # Add quick angles
        for a in [0, 45, 90, 135, 180, 225, 270, 315]:
            act = angle_menu.addAction(f"{a}°")
            act.triggered.connect(lambda checked=False, val=a: set_angle(val))
        
        cursor_pos = QtGui.QCursor.pos()
        menu.exec_(cursor_pos)

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        
        if key in ["ESCAPE", "ESC"]:
            self.terminate()
            return True
        if key in ["ENTER", "RETURN"]:
            self.finish()
            return True
            
        if key in ["DELETE", "X", "BACKSPACE"]:
            if self.state == 1 or self._selected_element:
                self._delete_selected_point()
                return True
        return False

    def finish(self):
        if self._target_obj:
            self._target_obj.EditMode = False
        if self._cursor_active:
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False
        super().finish()

    def _do_terminate(self):
        if self._target_obj:
            self._target_obj.EditMode = False
        if self._cursor_active:
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False
        super()._do_terminate()

def activate():
    tool = EditTool()
    tool.activate()
