import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtGui
from tools.dm_base import DMBase, DragTimerMixin
from core import dm_logger
from core.input_manager import DMInputManager
from tools.primitive_tool import _BOX_OPPOSITE

class EditTool(DMBase, DragTimerMixin):
    """
    Interactive tool for editing lattice-driven SDF objects.
    """
    def get_command_id(self):
        return "DM_EditObject"

    def __init__(self):
        super().__init__()
        self._target_obj = None
        self._is_editing = True

        self._selected_element = None # (index, type)
        self._wp_drag_start = None

        # State
        self.drag_plane_n = None
        self.drag_plane_o = None

        self._selected_element = None # (index, type)

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
        
        inv_plac = self._target_obj.Placement.inverse()
        local_ray_p = inv_plac.multVec(ray_p)
        local_ray_d = inv_plac.Rotation.multVec(ray_d)
        
        elements = []
        for i, p in enumerate(pts):
            elements.append((i, "Point", p))
            if i < len(h_in): elements.append((i, "HandleIn", h_in[i]))
            if i < len(h_out): elements.append((i, "HandleOut", h_out[i]))
            
        points = [p[2] for p in elements if p[2] is not None]
        idx, dist = self._hit_test_perp(local_ray_p, local_ray_d, points)
        
        if idx is not None:
            # Re-map filtered points back to original elements list
            valid_elements = [e for e in elements if e[2] is not None]
            best_elem = (valid_elements[idx][0], valid_elements[idx][1])
            return best_elem
                
        return None

    def _hit_test_edge(self, event_dict):
        """Hit test against the curve edge itself."""
        ray_p, ray_d = self._get_ray(event_dict)
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
        if self.state in [1, 2]:
            self._start_drag_timer()
        return result

    def on_button1_up(self, _event_dict):
        self._stop_drag_timer()
        if self.state in [1, 2]:
            self.state = 0
            self._selected_element = None
            self._wp_drag_start = None
            dm_logger.debug("Released drag")
        return True


    def _drag_update(self):
        if self._drag_check_lmb_released():
            self._wp_drag_start = None
            return
        if self.state not in [1, 2]:
            self._stop_drag_timer()
            return
        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        event_dict = {"Position": mouse_pos}
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
            hit_p = self._hit_test_edge(event_dict) # Uses current mouse pos internally
            if hit_p:
                self._insert_point(hit_p)
                return True

        # SELECT logic
        ray_p, ray_d = self._get_ray(event_dict)

        # Check for WorkPlane origin click (dragging the plane itself)
        if self.working_plane:
            wp_o = self.working_plane.Base
            # 15px radius for WP origin handle
            if self._hit_test_perp(ray_p, ray_d, [wp_o], tolerance=self._compute_handle_radius(wp_o))[0] is not None:
                self._wp_drag_start = wp_o
                self.drag_plane_n = FreeCAD.Vector(-self.view.getViewDirection())
                self.drag_plane_o = wp_o
                self.state = 2 # WP Dragging
                self._set_cursor(QtCore.Qt.SizeAllCursor)
                return True

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
        elif self.state == 2:
             # Already dragging WP?
             return True
        else:
            # No hit — deselect current element but consume the click so
            # FreeCAD doesn't deselect the object and kill the edit tool.
            self._selected_element = None
            return True

    def handle_move(self, event_dict):
        if self.state == 1 and self._selected_element:
            # Point/Handle dragging
            pt_global = self.projector.get_mouse_world_pos(
                event_dict, self.drag_plane_n, self.drag_plane_o,
                place_on_geometry=False
            )
            if not pt_global: return
            
            pt_local = self._target_obj.Placement.inverse().multVec(pt_global)
            self._update_element(self._selected_element, pt_local)
        elif self.state == 2:
            # WorkPlane dragging
            pt_global = self.projector.get_mouse_world_pos(
                event_dict, self.drag_plane_n, self.drag_plane_o,
                place_on_geometry=False
            )
            if pt_global:
                delta = pt_global - self._wp_drag_start
                self.working_plane.Base += delta
                self._wp_drag_start = pt_global
                # If we have a WorkPlane object in selection, we should probably update its property too
                # for now just the tool's working_plane.
        else:
            # Hover check
            ray_p, ray_d = self._get_ray(event_dict)

            # Hover WP origin?
            if self.working_plane:
                if self._hit_test_perp(ray_p, ray_d, [self.working_plane.Base])[0] is not None:
                     self._set_cursor(QtCore.Qt.SizeAllCursor)
                     return

            hit = self._hit_test(ray_p, ray_d)
            if hit != self._hovered_element:
                self._hovered_element = hit
                if hit:
                    self._set_cursor(QtCore.Qt.PointingHandCursor)
                else:
                    self._restore_cursor()

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
        key_code = event_dict.get("Key")
        
        if key_code == QtCore.Qt.Key_Escape:
            self.terminate()
            return True
        if key_code in [QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return]:
            self.finish()
            return True
        
        if key_code in [QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace]:
            if self.state == 1 or self._selected_element:
                self._delete_selected_point()
                return True
        return False

    def finish(self):
        if self._target_obj:
            self._target_obj.EditMode = False
        self._is_editing = False
        self.reset_state()

    def _do_terminate(self):
        if self._target_obj:
            self._target_obj.EditMode = False
        super()._do_terminate()




class SdfEditTool(DMBase, DragTimerMixin):
    """
    Edit tool for SDF (SDF) box primitives.
    """
    def get_command_id(self):
        return "DM_EditObject"

    def __init__(self):
        super().__init__()
        self._target_obj = None
        self._is_editing = True
        self._field = None             # current SdfBoxField
        self._placement = None         # field placement (may be None)
        self._world_corners = []       # 8 FreeCAD.Vector in world space
        self._hovered_idx = None
        self._is_editing = True

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

        if not (hasattr(obj, "ShapeType") and obj.ShapeType == "sdf"):
            dm_logger.error("Selected object is not an SDF object.")
            self.terminate()
            return

        field = getattr(obj.Proxy, "SdfField", None)
        if field is None:
            dm_logger.error("SDF object has no SdfField.")
            self.terminate()
            return

        self._target_obj = obj
        self._field = field
        self._placement = getattr(field, "placement", None)
        self._refresh_corners()
        dm_logger.info(f"SdfEditTool: drag a corner handle to reshape '{obj.Label}'")

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
        # Use the shared helper from PrimitiveCreatorBase
        from tools.primitive_tool import PrimitiveCreatorBase
        local_corners = PrimitiveCreatorBase._box_corners_local(c, h)
        if placement:
            self._world_corners = [placement.multVec(lc) for lc in local_corners]
        else:
            self._world_corners = local_corners

    def _hit_test_corners(self, event_dict):
        """Return index of hit corner sphere or None."""
        ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
        best_idx, _ = self._hit_test_perp(ray_p, ray_d, self._world_corners)
        return best_idx

    def _field_from_corners(self, dragged_world, fixed_world):
        """Rebuild SdfBoxField so dragged_world and fixed_world are opposite corners."""
        from core.sdf.sdf.box import SdfBoxField
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
        self._fixed_world = self._world_corners[_BOX_OPPOSITE[idx]]
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
        if getattr(self, "_drag_timer", None):
            self._drag_timer.stop()
            self._drag_timer = None

    def _drag_update(self):
        if self._drag_check_lmb_released():
            self._finish_drag()
            return
        if self._dragging_idx is None:
            self._stop_drag_timer()
            return

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        new_world = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos},
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
            obj.Proxy.SdfField = new_field
            obj.touch()
            obj.Document.recompute([obj])
        except Exception as e:
            dm_logger.debug(f"SdfEditTool._drag_update: {e}")

    def _finish_drag(self):
        self._stop_drag_timer()
        self._dragging_idx = None
        self.state = 0
        # Sync stored Points property with new corner positions in local space
        obj = self._target_obj
        if obj is None or not obj.Document:
            return
        try:
            self._refresh_corners()
            if hasattr(obj, "Points") and self._world_corners:
                # IMPORTANT: obj.Points MUST be in local space relative to obj.Placement
                # We use the inverse of the object's placement to transform world corners back.
                inv = obj.Placement.inverse()
                local_corners = [inv.multVec(wc) for wc in self._world_corners]
                obj.Points = local_corners
        except Exception as e:
            dm_logger.debug(f"SdfEditTool._finish_drag: {e}")

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


    def finish(self):
        self._is_editing = False
        self.reset_state()

    def handle_move(self, event_dict):
        if self.state == 1:
            return  # Drag handled by QTimer
        # Hover: change cursor when over a corner
        idx = self._hit_test_corners(event_dict)
        if idx != self._hovered_idx:
            self._hovered_idx = idx
            if idx is not None:
                self._set_cursor(QtCore.Qt.PointingHandCursor)
            else:
                self._restore_cursor()

    def handle_keyboard(self, event_dict):
        key_code = event_dict.get("Key")
        if key_code in [QtCore.Qt.Key_Escape, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return]:
            self.terminate()
            return True
        return False

    def _do_terminate(self):
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
        if shape_type == "sdf":
            tool = SdfEditTool()
            tool.activate()
            return
        elif shape_type == "curve":
            tool = EditTool()
            tool.activate()
            return

    dm_logger.error(f"edit_tool.activate: Not editable (proxy={proxy_name}, ShapeType={shape_type}).")
