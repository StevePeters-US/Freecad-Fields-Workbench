import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui
from tools.dm_base import DMBase
from core import dm_logger
from core.input_manager import DMInputManager

class EditTool(DMBase):
    """
    Interactive tool for editing lattice-driven SDF objects.

    NOTE: This tool's scope has changed — it is no longer for curve editing.
    Curve editing is handled by the CurveCreator tool (invoked via the curve
    button when a curve is already selected).
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
        self._drag_timer = None

    def activate(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            dm_logger.error("No object selected to edit.")
            self.terminate()
            return

        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        if proxy is not None and proxy.__class__.__name__ == "DMObjectProxy":
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

        # Toggle EditMode ON and force the control cage to rebuild directly —
        # updateData may not fire reliably for the EditMode property change.
        self._target_obj.EditMode = True
        vp = self._target_obj.ViewObject
        vp_proxy = getattr(vp, "Proxy", None)
        renderer = getattr(vp_proxy, "renderer", None) if vp_proxy else None
        if renderer:
            renderer.rebuild_control_cage(self._target_obj)
        vp.show()
        if self._target_obj.Document:
            self._target_obj.Document.recompute()

    def _get_ray(self, event_dict):
        """Delegate ray acquisition to DMInputManager."""
        return DMInputManager.get_instance().get_ray(self.view, event_dict)

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
            base_tolerance = get_picking_radius()
            
            try:
                import math
                cam = self.view.getCameraNode()
                viewer = self.view.getViewer()
                vp_h = float(viewer.getGlxSize()[1]) if hasattr(viewer, "getGlxSize") else 1000.0
                
                if hasattr(cam, "heightAngle"):
                    global_pos = self._target_obj.Placement.multVec(pos)
                    cam_pos = FreeCAD.Vector(*cam.position.getValue().getValue())
                    depth = (global_pos - cam_pos).Length
                    half_h = depth * math.tan(cam.heightAngle.getValue() / 2.0)
                else:
                    half_h = cam.height.getValue() / 2.0
                    
                px_to_world = (half_h * 2.0) / vp_h
                # Guarantee at least a 15 pixel selection radius
                dynamic_tol = 15.0 * px_to_world
                tolerance = max(base_tolerance, dynamic_tol)
            except Exception as e:
                dm_logger.debug(f"Dynamic tolerance failed: {e}")
                tolerance = base_tolerance
            
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
        
        pos_global = self.get_mouse_world_pos(event_dict, n, o)
        if not pos_global: return None
        
        dist, detail = shape.distToShape(Part.Point(pos_global).toShape())
        
        from core.dm_object import get_picking_radius
        if dist < get_picking_radius():
            return detail[0][2] # Closest point on shape (Global)
            
        return None

    def on_button1_down(self, event_dict):
        result = self.handle_click(event_dict)
        if self.state == 1:
            self._start_drag_timer()
        return result

    def on_button1_up(self, _event_dict):
        self._stop_drag_timer()
        if self.state == 1:
            self.state = 0
            self._selected_element = None
            dm_logger.debug("Dropped element")
        return True

    def _start_drag_timer(self):
        from core.dm_object import get_interactive_throttle_interval
        self._stop_drag_timer()
        self._drag_timer = QtCore.QTimer()
        self._drag_timer.timeout.connect(self._drag_update)
        self._drag_timer.start(50)

    def _stop_drag_timer(self):
        if self._drag_timer:
            self._drag_timer.stop()
            self._drag_timer = None

    def _drag_update(self):
        # Self-terminate if LMB was released (handles cases where on_button1_up is intercepted)
        if not DMInputManager.get_instance()._left_mouse_down:
            self._stop_drag_timer()
            self.state = 0
            self._selected_element = None
            return
        if self.state != 1 or not self._selected_element:
            self._stop_drag_timer()
            return
        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        event_dict = {"QtPosition": mouse_pos}
        pt_global = self.projector.get_mouse_world_pos(
            event_dict, self.drag_plane_n, self.drag_plane_o, place_on_geometry=False
        )
        if pt_global:
            pt_local = self._target_obj.Placement.inverse().multVec(pt_global)
            self._update_element(self._selected_element, pt_local)

    def handle_click(self, event_dict):
        click_count = event_dict.get("ClickCount", 1)

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
            # No hit — deselect current element but consume the click so
            # FreeCAD doesn't deselect the object and kill the edit tool.
            self._selected_element = None
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

    # on_button1_down is defined above (line ~154) with drag timer support.
    # Do NOT redefine it here — Python uses the last definition, which would
    # shadow the drag timer logic.

    def on_button2_down(self, event_dict):
        # Allow middle mouse for view rotation
        return False

    def on_button3_down(self, event_dict):
        # Right-click on a control point → context menu; otherwise finish editing
        ray_p, ray_d = self._get_ray(event_dict)
        hit = self._hit_test(ray_p, ray_d)
        if hit:
            return self.handle_right_click(event_dict)
        # No hit → finish editing (same as other tools via DMBase)
        return super().on_button3_down(event_dict)

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
            items = self.get_context_menu(event_dict)
            from core.dm_menu import DMMenuManager
            DMMenuManager.get_instance().trigger_dynamic_menu(items)
            return True # Consume click
        else:
            return False

    def get_context_menu(self, event_dict=None):
        base_menu = super().get_context_menu(event_dict)
        hit = None
        if event_dict:
            ray_p, ray_d = self._get_ray(event_dict)
        # Fallback to hovered element if available
        if not hit and self._hovered_element:
            hit = self._hovered_element

        if not hit and not self._selected_element:
            return base_menu + [
                "-",
                ("Finish Editing", self.finish)
            ]

        idx, elem_type = hit if hit else self._selected_element

        def set_type(val):
            p_types = list(getattr(self._target_obj, "PointTypes", []))
            while len(p_types) < len(getattr(self._target_obj, "Points", [])): p_types.append(0)
            p_types[idx] = val
            self._target_obj.PointTypes = p_types
            self._update_element((idx, "HandleOut"), getattr(self._target_obj, "HandleOut")[idx])
            
        def set_angle(degrees):
            import math
            pts = list(getattr(self._target_obj, "Points", []))
            h_out = list(getattr(self._target_obj, "HandleOut", []))
            if idx >= len(pts) or idx >= len(h_out): return
            p = pts[idx]
            v_out = h_out[idx] - p
            dist = v_out.Length
            if dist < 1e-4: dist = 10.0
            rad = math.radians(degrees)
            new_v_out = FreeCAD.Vector(dist * math.cos(rad), dist * math.sin(rad), 0)
            self._update_element((idx, "HandleOut"), p + new_v_out)

        angles = [(f"{a}°", lambda checked=False, val=a: set_angle(val)) for a in [0, 45, 90, 135, 180, 225, 270, 315]]

        return base_menu + [
            "-",
            ("Set Tangent (Smooth)", lambda: set_type(0)),
            ("Set Split (Smooth but uneven)", lambda: set_type(1)),
            ("Set Custom (Sharp/Corner)", lambda: set_type(2)),
            "-",
            ("Quick Angles", angles)
        ]

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
        self._stop_drag_timer()
        if self._target_obj:
            self._target_obj.EditMode = False
        if self._cursor_active:
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False
        super().finish()

    def _do_terminate(self):
        self._stop_drag_timer()
        if self._target_obj:
            self._target_obj.EditMode = False
        if self._cursor_active:
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False
        super()._do_terminate()


# Opposite corner index for a box defined by the standard 8-corner ordering:
# 0:(-,-,-) 1:(+,-,-) 2:(+,+,-) 3:(-,+,-) 4:(-,-,+) 5:(+,-,+) 6:(+,+,+) 7:(-,+,+)
_FREP_OPPOSITE = {0: 6, 1: 7, 2: 4, 3: 5, 4: 2, 5: 3, 6: 0, 7: 1}


class FRepEditTool(DMBase):
    """
    Edit tool for F-Rep (SDF) box primitives.
    Drag one of the 8 rendered corner handles to reshape the box.
    The opposite corner stays fixed; the SdfBoxField is rebuilt live.
    """

    def __init__(self):
        super().__init__()
        self._target_obj = None
        self._field = None             # current SdfBoxField
        self._placement = None         # field placement (may be None)
        self._world_corners = []       # 8 FreeCAD.Vector in world space
        self._dragging_idx = None      # index of corner being dragged
        self._fixed_world = None       # world pos of opposite (fixed) corner
        self._drag_plane_n = None
        self._drag_plane_o = None
        self._drag_timer = None
        self._cursor_active = False
        self._hovered_idx = None

    def activate(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            dm_logger.error("No object selected to edit.")
            self.terminate()
            return

        obj = sel[0]
        if not (hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMObjectProxy"):
            dm_logger.error("Selected object is not a DM object.")
            self.terminate()
            return

        if not (hasattr(obj, "ShapeType") and obj.ShapeType == "frep"):
            dm_logger.error("Selected object is not an F-Rep object.")
            self.terminate()
            return

        field = getattr(obj.Proxy, "FRepField", None)
        if field is None:
            dm_logger.error("F-Rep object has no FRepField.")
            self.terminate()
            return

        self._target_obj = obj
        self._field = field
        self._placement = getattr(field, "placement", None)
        self._refresh_corners()
        dm_logger.info(f"FRepEditTool: drag a corner handle to reshape '{obj.Label}'")

    # ------------------------------------------------------------------
    # Corner geometry helpers
    # ------------------------------------------------------------------

    def _refresh_corners(self):
        """Recompute self._world_corners from the current field."""
        field = self._field
        center = getattr(field, "center", None)
        half_size = getattr(field, "half_size", None)
        placement = getattr(field, "placement", None)
        if center is None or half_size is None:
            self._world_corners = []
            return
        c, h = center, half_size
        local_corners = [
            FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z - h.z),
            FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z - h.z),
            FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z - h.z),
            FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z - h.z),
            FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z + h.z),
            FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z + h.z),
            FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z + h.z),
            FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z + h.z),
        ]
        if placement:
            self._world_corners = [placement.multVec(lc) for lc in local_corners]
        else:
            self._world_corners = local_corners

    def _hit_test_corners(self, event_dict):
        """Return index of hit corner sphere or None."""
        if not self._world_corners:
            return None
        im = DMInputManager.get_instance()
        ray_p, ray_d = im.get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return None

        # Dynamic tolerance: ~15 screen pixels in world units
        try:
            cam = self.view.getCameraNode()
            viewer = self.view.getViewer()
            vp_h = 800.0
            try:
                if hasattr(viewer, "getGlxSize"):
                    vp_h = float(viewer.getGlxSize()[1])
                elif hasattr(viewer, "getSize"):
                    sz = viewer.getSize()
                    vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
            except Exception:
                pass
            half_world_h = cam.height.getValue() / 2.0 if hasattr(cam, "height") else 100.0
            px_per_world = (vp_h / 2.0) / max(half_world_h, 1e-6)
            tolerance = max(5.0, 15.0 / px_per_world)
        except Exception:
            tolerance = 5.0

        best_dist = float("inf")
        best_idx = None
        for i, corner in enumerate(self._world_corners):
            v = corner - ray_p
            cam_dist = v.dot(ray_d)
            if cam_dist < 0:
                continue
            dist = v.cross(ray_d).Length
            if dist < tolerance and dist < best_dist:
                best_dist = dist
                best_idx = i
        return best_idx

    def _field_from_corners(self, dragged_world, fixed_world):
        """Rebuild SdfBoxField so dragged_world and fixed_world are opposite corners."""
        from core.frep.sdf.box import SdfBoxField
        placement = self._placement
        if placement:
            inv = placement.inverse()
            lc1 = inv.multVec(dragged_world)
            lc2 = inv.multVec(fixed_world)
        else:
            lc1, lc2 = dragged_world, fixed_world
        cx = (lc1.x + lc2.x) / 2.0
        cy = (lc1.y + lc2.y) / 2.0
        cz = (lc1.z + lc2.z) / 2.0
        sx = max(abs(lc1.x - lc2.x), 0.1)
        sy = max(abs(lc1.y - lc2.y), 0.1)
        sz = max(abs(lc1.z - lc2.z), 0.1)
        return SdfBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(sx, sy, sz),
                           placement=placement)

    # ------------------------------------------------------------------
    # Drag mechanics
    # ------------------------------------------------------------------

    def _start_drag(self, idx):
        from core.dm_object import get_interactive_throttle_interval
        self._dragging_idx = idx
        self._fixed_world = self._world_corners[_FREP_OPPOSITE[idx]]
        # Drag plane: camera-facing plane through the grabbed corner
        vd = self.view.getViewDirection()
        self._drag_plane_n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
        self._drag_plane_n.normalize()
        self._drag_plane_o = self._world_corners[idx]
        self.state = 1
        # Start polling timer
        self._drag_timer = QtCore.QTimer()
        self._drag_timer.timeout.connect(self._drag_update)
        interval_ms = int(get_interactive_throttle_interval() * 1000)
        self._drag_timer.start(interval_ms)

    def _stop_drag_timer(self):
        if self._drag_timer:
            self._drag_timer.stop()
            self._drag_timer = None

    def _drag_update(self):
        # Self-terminate if LMB was released (handles FreeCAD nav interception)
        if not DMInputManager.get_instance()._left_mouse_down:
            self._finish_drag()
            return
        if self._dragging_idx is None:
            self._stop_drag_timer()
            return

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        new_world = self.projector.get_mouse_world_pos(
            {"QtPosition": mouse_pos},
            self._drag_plane_n, self._drag_plane_o,
            place_on_geometry=False
        )
        if new_world is None:
            return

        new_field = self._field_from_corners(new_world, self._fixed_world)
        self._field = new_field

        obj = self._target_obj
        if obj is None or not obj.Document:
            return
        try:
            obj.Proxy.FRepField = new_field
            obj.touch()
            obj.Document.recompute([obj])
        except Exception as e:
            dm_logger.debug(f"FRepEditTool._drag_update: {e}")

    def _finish_drag(self):
        self._stop_drag_timer()
        self._dragging_idx = None
        self.state = 0
        # Sync stored Points property with new corner positions
        obj = self._target_obj
        if obj is None or not obj.Document:
            return
        try:
            self._refresh_corners()
            if hasattr(obj, "Points") and self._world_corners:
                obj.Points = self._world_corners
        except Exception as e:
            dm_logger.debug(f"FRepEditTool._finish_drag: {e}")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_button1_down(self, event_dict):
        idx = self._hit_test_corners(event_dict)
        if idx is not None:
            self._start_drag(idx)
        return True

    def on_button1_up(self, event_dict):
        if self.state == 1:
            self._finish_drag()
        return True

    def on_button3_down(self, event_dict):
        self.terminate()
        return True

    def handle_move(self, event_dict):
        if self.state == 1:
            return  # Drag handled by QTimer
        # Hover: change cursor when over a corner
        idx = self._hit_test_corners(event_dict)
        if idx != self._hovered_idx:
            self._hovered_idx = idx
            if idx is not None:
                QtGui.QApplication.setOverrideCursor(QtCore.Qt.PointingHandCursor)
                self._cursor_active = True
            else:
                QtGui.QApplication.restoreOverrideCursor()
                self._cursor_active = False

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        if key in ["ESCAPE", "ESC", "ENTER", "RETURN"]:
            self.terminate()
            return True
        return False

    def _do_terminate(self):
        self._stop_drag_timer()
        if self._cursor_active:
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False
        super()._do_terminate()


def activate():
    sel = FreeCADGui.Selection.getSelection()
    if not sel:
        dm_logger.error("edit_tool.activate: No object selected.")
        return

    obj = sel[0]
    proxy = getattr(obj, "Proxy", None)
    proxy_name = proxy.__class__.__name__ if proxy is not None else "None"
    shape_type = getattr(obj, "ShapeType", None)

    if proxy is not None and proxy_name == "DMObjectProxy":
        if shape_type == "frep":
            tool = FRepEditTool()
            tool.activate()
            return
        elif shape_type == "curve":
            tool = EditTool()
            tool.activate()
            return

    dm_logger.error(f"edit_tool.activate: Not editable (proxy={proxy_name}, ShapeType={shape_type}).")
