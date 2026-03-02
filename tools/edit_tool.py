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

    def _get_ray(self, event_dict):
        pos = event_dict.get("Position", (0, 0))
        x, y = pos[0], pos[1]

        if not self.view:
            return None, None

        scene_pt = None
        try:
            scene_pt = self.view.getPoint(x, y)
        except Exception:
            pass

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
            tolerance = 0.02 * cam_dist 
            if dist < tolerance and dist < best_dist:
                best_dist = dist
                best_elem = (i, elem_type)
                
        return best_elem

    def handle_click(self, event_dict):
        ray_p, ray_d = self._get_ray(event_dict)
        hit = self._hit_test(ray_p, ray_d)
        
        if hit:
            self._selected_element = hit
            
            # Setup drag plane parallel to camera, pinned at the target element
            idx, elem_type = hit
            pts = getattr(self._target_obj, "Points", [])
            h_in = getattr(self._target_obj, "HandleIn", [])
            h_out = getattr(self._target_obj, "HandleOut", [])
            
            val = None
            if elem_type == "Point": val = pts[idx]
            elif elem_type == "HandleIn": val = h_in[idx]
            elif elem_type == "HandleOut": val = h_out[idx]
            
            vd = self.view.getViewDirection()
            self.drag_plane_n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
            self.drag_plane_n.normalize()
            # The coordinate is in local space, we need the drag plane origin in global space
            self.drag_plane_o = self._target_obj.Placement.multVec(val)
            
            self.state = 1 # Dragging
            dm_logger.debug(f"Began dragging {elem_type} at index {idx}")
            return True
        else:
            # Consume click to prevent FreeCAD from selecting other objects
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

    def event_cb(self, event_dict):
        try:
            event_type = event_dict.get("Type", "Unknown")

            if event_type == "SoMouseButtonEvent":
                btn = event_dict.get("Button", "None")
                state = event_dict.get("State", "None")
                if state == "DOWN":
                    if btn == "BUTTON3":
                        return self.handle_right_click(event_dict)
                    elif btn == "BUTTON1":
                        return self.handle_click(event_dict)
                    return False
                
                elif state == "UP":
                    if btn == "BUTTON1" and self.state == 1:
                        self.state = 0
                        self._selected_element = None
                        return True
                    if btn == "BUTTON3":
                        return True 
                    return False
            elif event_type == "SoLocation2Event":
                self.handle_move(event_dict)
            elif event_type == "SoKeyboardEvent":
                if event_dict["State"] == "DOWN":
                    key = event_dict.get("Key", "None")
                    return self.handle_keyboard(event_dict)

        except Exception as e:
            dm_logger.error(f"Error in EditTool event_cb: {e}")
            import traceback
            traceback.print_exc()
        return False

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
            h_in[idx] += delta
            h_out[idx] += delta
        elif elem_type == "HandleIn":
            h_in[idx] = new_pos
            if pt_type == 0: # Tangent
                delta = new_pos - pts[idx]
                h_out[idx] = pts[idx] - delta
            elif pt_type == 1: # Split (collinear, indep length)
                delta = new_pos - pts[idx]
                if delta.Length > 1e-6:
                    out_len = (h_out[idx] - pts[idx]).Length
                    h_out[idx] = pts[idx] - (delta.normalize() * out_len)
        elif elem_type == "HandleOut":
            h_out[idx] = new_pos
            if pt_type == 0: # Tangent
                delta = new_pos - pts[idx]
                h_in[idx] = pts[idx] - delta
            elif pt_type == 1: # Split
                delta = new_pos - pts[idx]
                if delta.Length > 1e-6:
                    in_len = (h_in[idx] - pts[idx]).Length
                    h_in[idx] = pts[idx] - (delta.normalize() * in_len)
                    
        self._target_obj.Points = pts
        self._target_obj.HandleIn = h_in
        self._target_obj.HandleOut = h_out
        self._target_obj.touch()
        if self._target_obj.Document:
            self._target_obj.Document.recompute()

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

    def finish(self):
        if self._cursor_active:
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False
        super().finish()

    def terminate(self):
        if self._cursor_active:
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False
        super().terminate()

def activate():
    tool = EditTool()
    tool.activate()
