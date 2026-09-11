# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/nurbs_edit_tool.py

Interactive editing tool for traditional NURBS curves, points, and surface
control cages (control-point manipulation, projection, dragging).
"""
import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore
from freecad.fields.tools.fld_base import FldBase, DragTimerMixin
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager


class NurbsEditTool(FldBase, DragTimerMixin):
    """
    Interactive tool for editing lattice-driven SDF objects.
    """
    def get_command_id(self):
        return "Fields_EditObject"

    def __init__(self):
        super().__init__()
        self._target_obj = None
        self._is_editing = True

        self._selected_element = None # (index, type)
        self._selected_point_idx = None
        self._hovered_element = None
        self._is_dragging = False
        self._wp_drag_start = None

        # State
        self.drag_plane_n = None
        self.drag_plane_o = None

    def activate(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            fld_logger.error("No object selected to edit.")
            self.terminate()
            return

        from freecad.fields.core.objects.fld_object_proxy import FldObjectProxy

        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        if proxy is not None and isinstance(proxy, FldObjectProxy):
            shape_type = getattr(obj, "ShapeType", None)
            if shape_type in ("curve", "point"):
                self._target_obj = obj
            else:
                fld_logger.error("Selected object is not an editable curve or point.")
                self.terminate()
                return
        else:
            fld_logger.error("Selected object is not a Fields object.")
            self.terminate()
            return
            
        fld_logger.info(f"NurbsEditTool activated for {self._target_obj.Label}")

        shape_type = getattr(self._target_obj, "ShapeType", None)
        if shape_type == "curve":
            self.linked_nodes = list(getattr(self._target_obj, "ControlNodes", []) or [])
            # Snapshot original properties for cancel restoration
            self._original_props = {
                "Points": list(getattr(self._target_obj, "Points", [])),
                "HandleIn": list(getattr(self._target_obj, "HandleIn", [])),
                "HandleOut": list(getattr(self._target_obj, "HandleOut", [])),
                "HandleTypes": list(getattr(self._target_obj, "HandleTypes", [])),
                "PointTypes": list(getattr(self._target_obj, "PointTypes", [])),
            }
            if self.linked_nodes:
                self._original_props["ControlNodeCoords"] = [FreeCAD.Vector(node.Coordinates) for node in self.linked_nodes if node is not None]
        else:
            self.linked_nodes = []
            self._original_props = {
                "Coordinates": FreeCAD.Vector(getattr(self._target_obj, "Coordinates", self._target_obj.Position))
            }

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
            doc = self._target_obj.Document
            QtCore.QTimer.singleShot(0, doc.recompute)

    def _get_ray(self, event_dict):
        """Delegate ray acquisition to FldInputManager."""
        return FldInputManager.get_instance().get_ray(self.view, event_dict)

    def _hit_test(self, ray_p, ray_d):
        if not self._target_obj or not ray_p or not ray_d: return None
        
        shape_type = getattr(self._target_obj, "ShapeType", None)
        if shape_type == "point":
            inv_plac = self._target_obj.Placement.inverse()
            p = getattr(self._target_obj, "Coordinates", self._target_obj.Position)
            p_local = inv_plac.multVec(p)
            local_ray_p = inv_plac.multVec(ray_p)
            local_ray_d = inv_plac.Rotation.multVec(ray_d)
            idx, dist = self._hit_test_perp(local_ray_p, local_ray_d, [p_local])
            if idx is not None:
                return (0, "Point")
            return None

        if hasattr(self._target_obj, "ControlNodes") and self._target_obj.ControlNodes:
            inv_plac = self._target_obj.Placement.inverse()
            pts = [inv_plac.multVec(node.Coordinates) for node in self._target_obj.ControlNodes if node is not None]
        else:
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
        
        from freecad.fields.core.objects.fld_object import get_picking_radius
        if dist < get_picking_radius():
            return detail[0][2] # Closest point on shape (Global)
            
        return None

    def on_button1_down(self, event_dict):
        result = self.handle_click(event_dict)
        if self.state in [1, 2]:
            # Do NOT set _is_dragging here. _start_drag_timer() sets it itself
            # (tool_mixins.py:45), and its first act is _stop_drag_timer(),
            # which logs a drag summary if the flag is already True -- replaying
            # the *previous* drag's counters against this session's wall clock
            # (LE-005: a real 48.1 FPS drag reported as 20.4).
            self._start_drag_timer()
        return result

    def on_button1_up(self, _event_dict):
        was_dragging = self._is_dragging
        self._stop_drag_timer()
        self._clear_constraint_visual()
        self._is_dragging = False

        if (was_dragging or self.state in [1, 2]) and self._target_obj and self._target_obj.Document:
            doc = self._target_obj.Document
            QtCore.QTimer.singleShot(0, doc.recompute)

        if self.state in [1, 2]:
            self.state = 0
            self._selected_element = None
            self._wp_drag_start = None
            fld_logger.debug("Released drag")
        # Return False (pass-through) to match primitive-tool behaviour and keep
        # FreeCAD's button-state tracking consistent for viewport navigation.
        return False


    def _drag_update(self):
        if self._drag_check_lmb_released():
            self._wp_drag_start = None
            self._clear_constraint_visual()
            return
        if self.state not in [1, 2]:
            self._stop_drag_timer()
            return
        mouse_pos = FldInputManager.get_instance()._last_qt_pos
        event_dict = {"Position": mouse_pos}
        pt_global = self.resolve_world_point(
            event_dict,
            base_pt=self.drag_plane_o,
            plane_normal=self.drag_plane_n,
            plane_origin=self.drag_plane_o,
            exclude=[self._target_obj] if getattr(self, "_target_obj", None) else [],
            place_on_geometry=False
        )
        if pt_global:
            if self.state == 1 and (pt_global - self.drag_plane_o).Length < 1e-2:
                return
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
            self._selected_point_idx = hit[0]
            
            # Setup drag plane
            idx, elem_type = hit
            pts = getattr(self._target_obj, "Points", [])
            h_in = getattr(self._target_obj, "HandleIn", [])
            h_out = getattr(self._target_obj, "HandleOut", [])
            
            val = None
            shape_type = getattr(self._target_obj, "ShapeType", None)
            if shape_type == "point":
                p_world = getattr(self._target_obj, "Coordinates", self._target_obj.Position)
                val = self._target_obj.Placement.inverse().multVec(p_world)
            else:
                if elem_type == "Point": val = pts[idx]
                elif elem_type == "HandleIn": val = h_in[idx]
                elif elem_type == "HandleOut": val = h_out[idx]
            
            # Constraint Plane: Use the world plane if it exists, else camera
            # (Following user's "curve plane if 2d, camera if 3d")
            vd = self.view.getViewDirection()
            self.drag_plane_n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
            self.drag_plane_n.normalize()
            self.drag_plane_o = self._target_obj.Placement.multVec(val)
            self._drag_constraint_base = self.drag_plane_o
            self._dragging_idx = idx
            
            self.state = 1 # Dragging (Selecting -> Moving)
            self._update_constraint_visual()
            fld_logger.debug(f"Selected {elem_type} at index {idx}")
            return True
        elif self.state == 2:
             # Already dragging WP?
             return True
        else:
            # No hit — deselect the active control point but pass the click through
            # so FreeCAD's navigation (CAD-mode MMB+LMB rotation) still works.
            # FldSelectionObserver already prevents FreeCAD from changing the object
            # selection while a tool is active, so there is no risk of the edit tool
            # being killed by the deselection.
            self._selected_element = None
            return False

    def handle_move(self, event_dict):
        if self.state == 1 and self._selected_element:
            # Point/Handle dragging
            pt_global = self.resolve_world_point(
                event_dict,
                base_pt=self.drag_plane_o,
                plane_normal=self.drag_plane_n,
                plane_origin=self.drag_plane_o,
                exclude=[self._target_obj] if getattr(self, "_target_obj", None) else [],
                place_on_geometry=False
            )
            if not pt_global: return
            
            pt_local = self._target_obj.Placement.inverse().multVec(pt_global)
            self._update_element(self._selected_element, pt_local)
        elif self.state == 2:
            # WorkPlane origin dragging — full snap pipeline (workplane → SDF → geometry → camera plane)
            snap_result = self.projector.get_mouse_plane_pt(
                event_dict, place_on_geometry=True,
                working_plane=self.working_plane)
            pt_global = snap_result[0] if snap_result else None
            if pt_global:
                delta = pt_global - self._wp_drag_start
                self.working_plane.Base += delta
                self._wp_drag_start = pt_global
                # Move the object's placement so all control-point handles follow
                if self._target_obj:
                    plac = self._target_obj.Placement
                    plac.Base = plac.Base + delta
                    self._target_obj.Placement = plac
                    self._target_obj.touch()
                    vp = self._target_obj.ViewObject
                    if vp and hasattr(vp, "Proxy") and vp.Proxy:
                        vp.Proxy.updateData(self._target_obj, "Points")
                    if self.view:
                        self.view.redraw()
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



    def _update_element(self, element, new_pos):
        idx, elem_type = element
        shape_type = getattr(self._target_obj, "ShapeType", None)
        if shape_type == "point":
            if elem_type == "Point":
                world_pos = self._target_obj.Placement.multVec(new_pos)
                if hasattr(self._target_obj, "Coordinates"):
                    self._target_obj.Coordinates = world_pos
                if hasattr(self._target_obj, "Position"):
                    self._target_obj.Position = world_pos
                self._target_obj.touch()
                if not self._is_dragging:
                    if self._target_obj.Document:
                        doc = self._target_obj.Document
                        QtCore.QTimer.singleShot(0, doc.recompute)
                else:
                    if self.view:
                        self.view.redraw()
            return

        pts = list(getattr(self._target_obj, "Points", []))
        h_in = list(getattr(self._target_obj, "HandleIn", []))
        h_out = list(getattr(self._target_obj, "HandleOut", []))
        p_types = list(getattr(self._target_obj, "PointTypes", []))
        
        h_types = list(getattr(self._target_obj, "HandleTypes", []))
        
        while len(p_types) < len(pts): p_types.append(0)
        while len(h_types) < 2 * len(pts): h_types.append(0)
        
        pt_type = p_types[idx]
        
        if elem_type == "Point":
            delta = new_pos - pts[idx]
            pts[idx] = new_pos
            if idx < len(h_in): h_in[idx] += delta
            if idx < len(h_out): h_out[idx] += delta
            if hasattr(self, "linked_nodes") and self.linked_nodes and idx < len(self.linked_nodes):
                node = self.linked_nodes[idx]
                if node is not None:
                    node.Coordinates = self._target_obj.Placement.multVec(new_pos)
                    node.touch()
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
            
            # Dragging a handle updates its type to Aligned (2) or Free (3)
            new_h_type = 3 if pt_type == 2 else 2
            h_types[2 * idx] = new_h_type
            h_types[2 * idx + 1] = new_h_type
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
            
            # Dragging a handle updates its type to Aligned (2) or Free (3)
            new_h_type = 3 if pt_type == 2 else 2
            h_types[2 * idx] = new_h_type
            h_types[2 * idx + 1] = new_h_type
                    
        self._target_obj.Points = pts
        self._target_obj.HandleIn = h_in
        self._target_obj.HandleOut = h_out
        self._target_obj.HandleTypes = h_types
        self._target_obj.touch()

        if not self._is_dragging:
            if self._target_obj.Document:
                doc = self._target_obj.Document
                QtCore.QTimer.singleShot(0, doc.recompute)
        else:
            # Interactive update during drag: rebuild shape + control cage
            vp = self._target_obj.ViewObject
            if vp and hasattr(vp, "Proxy") and vp.Proxy:
                vp.Proxy.updateData(self._target_obj, "Points")
            if self.view:
                self.view.redraw()

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
            doc = self._target_obj.Document
            QtCore.QTimer.singleShot(0, doc.recompute)
        fld_logger.info("Inserted new point into curve")

    def _delete_selected_point(self):
        """Delete the selected point or handle's parent point."""
        if not self._target_obj or not self._selected_element: return
        
        idx, elem_type = self._selected_element
        pts = list(getattr(self._target_obj, "Points", []))
        if len(pts) <= 2:
            fld_logger.warn("Cannot delete point: curve must have at least 2 points.")
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
            doc = self._target_obj.Document
            QtCore.QTimer.singleShot(0, doc.recompute)
        fld_logger.info(f"Deleted point at index {idx}")

    def on_context_menu(self, event_dict):
        ray_p, ray_d = self._get_ray(event_dict)
        hit = self._hit_test(ray_p, ray_d)
        self._context_menu_hit = hit if hit else None

        # Capture world position for "Insert Point Here" when no element is hit
        self._context_menu_pos = None
        if self._target_obj and not hit:
            try:
                vd = self.view.getViewDirection()
                n = FreeCAD.Vector(-vd.x, -vd.y, -vd.z)
                o = self._target_obj.Placement.Base
                self._context_menu_pos = self.get_mouse_world_pos(event_dict, n, o)
            except Exception as e:
                fld_logger.debug(f"NurbsEditTool.on_context_menu: could not get cursor pos: {e}")

        items = self.get_context_menu(event_dict)
        from freecad.fields.core.input.fld_menu import FldMenuManager
        FldMenuManager.get_instance().trigger_dynamic_menu(items)
        return True

    def get_context_menu(self, event_dict=None):
        hit = getattr(self, "_context_menu_hit", None)
        shape_type = getattr(self._target_obj, "ShapeType", None) if self._target_obj else None
        items = []

        if shape_type == "curve" and hit:
            idx, _elem_type = hit
            h_types = list(getattr(self._target_obj, "HandleTypes", []))
            cur = h_types[2 * idx] if len(h_types) > 2 * idx else 0
            items += [
                ("Handle Type", [
                    ("Auto",    lambda i=idx: self._set_handle_type(i, 0), cur == 0),
                    ("Vector",  lambda i=idx: self._set_handle_type(i, 1), cur == 1),
                    ("Aligned", lambda i=idx: self._set_handle_type(i, 2), cur == 2),
                    ("Free",    lambda i=idx: self._set_handle_type(i, 3), cur == 3),
                ]),
                None,
                ("Delete Control Point", self._delete_selected_point),
            ]
        elif shape_type == "curve":
            pos = getattr(self, "_context_menu_pos", None)
            if pos is not None:
                items += [("Insert Point Here", lambda p=pos: self._insert_point(p))]

        items += [
            None,
            ("Accept", self.finish),
            ("Cancel", self.cancel),
        ]
        return items

    def _set_handle_type(self, idx, val):
        h_types = list(getattr(self._target_obj, "HandleTypes", []))
        while len(h_types) < 2 * len(getattr(self._target_obj, "Points", [])): h_types.append(0)
        h_types[2 * idx] = val
        h_types[2 * idx + 1] = val
        self._target_obj.HandleTypes = h_types
        
        p_types = list(getattr(self._target_obj, "PointTypes", []))
        while len(p_types) < len(getattr(self._target_obj, "Points", [])): p_types.append(0)
        if val == 3:
            p_types[idx] = 2
        elif val == 2:
            p_types[idx] = 1
        else:
            p_types[idx] = 0
        self._target_obj.PointTypes = p_types
        
        self._apply_handle_type(idx, val)
        self._target_obj.touch()
        obj = self._target_obj
        view = self.view
        def _deferred_recompute():
            if obj.Document:
                obj.Document.recompute()
            vp = obj.ViewObject
            if vp and hasattr(vp, "Proxy") and vp.Proxy:
                vp.Proxy.updateData(obj, "Points")
            if view:
                view.redraw()
        QtCore.QTimer.singleShot(0, _deferred_recompute)

    _HANDLE_TYPE_NAMES = {0: "Auto", 1: "Vector", 2: "Aligned", 3: "Free"}

    def _cycle_handle_type(self, point_idx):
        if not self._target_obj:
            return
        pts = list(getattr(self._target_obj, "Points", []))
        n = len(pts)
        if point_idx < 0 or point_idx >= n:
            return

        h_types = list(getattr(self._target_obj, "HandleTypes", []))
        while len(h_types) < 2 * n:
            h_types.append(0)

        in_i  = 2 * point_idx
        out_i = 2 * point_idx + 1

        current = h_types[out_i]
        next_type = (current + 1) % 4

        h_types[in_i]  = next_type
        h_types[out_i] = next_type
        
        self._target_obj.HandleTypes = h_types

        self._apply_handle_type(point_idx, next_type)
        
        # Sync PointTypes: Custom (2) for Free (3), Split (1) for Aligned (2), Tangent (0) otherwise
        p_types = list(getattr(self._target_obj, "PointTypes", []))
        while len(p_types) < n:
            p_types.append(0)
        if next_type == 3:
            p_types[point_idx] = 2
        elif next_type == 2:
            p_types[point_idx] = 1
        else:
            p_types[point_idx] = 0
        self._target_obj.PointTypes = p_types

        self._target_obj.touch()
        obj = self._target_obj
        view = self.view
        def _deferred_recompute():
            if obj.Document:
                obj.Document.recompute()
            # Rebuild visuals
            vp = obj.ViewObject
            if vp and hasattr(vp, "Proxy") and vp.Proxy:
                vp.Proxy.updateData(obj, "Points")
            if view:
                view.redraw()
        QtCore.QTimer.singleShot(0, _deferred_recompute)

        fld_logger.info(
            f"NurbsEditTool: Point {point_idx} handle type → {self._HANDLE_TYPE_NAMES.get(next_type, '?')}"
            " (V to cycle: Auto→Vector→Aligned→Free→Auto)"
        )

    def _apply_handle_type(self, idx, handle_type):
        if not self._target_obj:
            return
        pts = list(getattr(self._target_obj, "Points", []))
        h_in = list(getattr(self._target_obj, "HandleIn", []))
        h_out = list(getattr(self._target_obj, "HandleOut", []))
        n = len(pts)
        if idx >= n or idx >= len(h_in) or idx >= len(h_out):
            return
            
        p = pts[idx]
        is_closed = getattr(self._target_obj, "Closed", False)
        if is_closed:
            prev_p = pts[(idx - 1) % n]
            next_p = pts[(idx + 1) % n]
        else:
            prev_p = pts[max(0, idx - 1)]
            next_p = pts[min(n - 1, idx + 1)]

        if handle_type == 1:  # Vector
            h_out[idx] = p + (next_p - p) * (1.0 / 3.0)
            h_in[idx]  = p + (prev_p - p) * (1.0 / 3.0)
        elif handle_type == 0:  # Auto
            chord_out = (next_p - p).Length
            chord_in  = (p - prev_p).Length
            tangent = next_p - prev_p
            tlen = tangent.Length
            if tlen > 1e-6:
                tangent = tangent * (1.0 / tlen)
            else:
                tangent = FreeCAD.Vector(0, 0, 0)
            h_out[idx] = p + tangent * (chord_out / 3.0)
            h_in[idx]  = p - tangent * (chord_in  / 3.0)

        self._target_obj.HandleIn = h_in
        self._target_obj.HandleOut = h_out

    def handle_keyboard(self, event_dict):
        key_code = event_dict.get("Key")
        key_text = str(event_dict.get("Text", "")).upper()
        
        if key_code == QtCore.Qt.Key_Escape:
            self.cancel()
            return True
        if key_code in [QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return]:
            self.finish()
            return True
        
        if key_text == "V" and getattr(self, "_selected_point_idx", None) is not None:
            idx = self._selected_point_idx
            def set_handle_type(val):
                h_types = list(getattr(self._target_obj, "HandleTypes", []))
                while len(h_types) < 2 * len(getattr(self._target_obj, "Points", [])): h_types.append(0)
                h_types[2 * idx] = val
                h_types[2 * idx + 1] = val
                self._target_obj.HandleTypes = h_types
                
                p_types = list(getattr(self._target_obj, "PointTypes", []))
                while len(p_types) < len(getattr(self._target_obj, "Points", [])): p_types.append(0)
                if val == 3:
                    p_types[idx] = 2
                elif val == 2:
                    p_types[idx] = 1
                else:
                    p_types[idx] = 0
                self._target_obj.PointTypes = p_types
                
                self._apply_handle_type(idx, val)
                self._target_obj.touch()
                obj = self._target_obj
                view = self.view
                def _deferred_recompute():
                    if obj.Document:
                        obj.Document.recompute()
                    vp = obj.ViewObject
                    if vp and hasattr(vp, "Proxy") and vp.Proxy:
                        vp.Proxy.updateData(obj, "Points")
                    if view:
                        view.redraw()
                QtCore.QTimer.singleShot(0, _deferred_recompute)

            menu_items = [
                ("Auto (Blue)", lambda: set_handle_type(0)),
                ("Vector (Green)", lambda: set_handle_type(1)),
                ("Aligned (Purple)", lambda: set_handle_type(2)),
                ("Free (Red)", lambda: set_handle_type(3))
            ]
            from freecad.fields.core.input.fld_menu import FldMenuManager
            FldMenuManager.get_instance().trigger_dynamic_menu(menu_items)
            return True
            
        if key_code in [QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace]:
            if self.state == 1 or self._selected_element:
                self._delete_selected_point()
                return True
        return False

    def restore_original(self):
        if not self._target_obj or not hasattr(self, "_original_props"):
            return
        shape_type = getattr(self._target_obj, "ShapeType", None)
        if shape_type == "point":
            orig = self._original_props["Coordinates"]
            if hasattr(self._target_obj, "Coordinates"):
                self._target_obj.Coordinates = orig
            if hasattr(self._target_obj, "Position"):
                self._target_obj.Position = orig
            self._target_obj.touch()
        else:
            self._target_obj.Points = self._original_props["Points"]
            self._target_obj.HandleIn = self._original_props["HandleIn"]
            self._target_obj.HandleOut = self._original_props["HandleOut"]
            self._target_obj.HandleTypes = self._original_props["HandleTypes"]
            self._target_obj.PointTypes = self._original_props["PointTypes"]
            if "ControlNodeCoords" in self._original_props and hasattr(self, "linked_nodes") and self.linked_nodes:
                for idx, coord in enumerate(self._original_props["ControlNodeCoords"]):
                    if idx < len(self.linked_nodes) and self.linked_nodes[idx] is not None:
                        self.linked_nodes[idx].Coordinates = coord
                        self.linked_nodes[idx].touch()
        self._target_obj.touch()
        obj = self._target_obj
        view = self.view
        def _deferred_recompute():
            if obj.Document:
                obj.Document.recompute()
            vp = obj.ViewObject
            if vp and hasattr(vp, "Proxy") and vp.Proxy:
                if shape_type == "curve":
                    vp.Proxy.updateData(obj, "Points")
            if view:
                view.redraw()
        QtCore.QTimer.singleShot(0, _deferred_recompute)

    def cancel(self):
        if self._target_obj:
            self._target_obj.EditMode = False
        self._is_editing = False
        super().cancel()

    def finish(self):
        if self._target_obj:
            self._target_obj.EditMode = False
        self._is_editing = False
        self.terminate()

    def _do_terminate(self):
        try:
            if self._target_obj:
                self._target_obj.EditMode = False
        except Exception as e:
            fld_logger.debug(f"NurbsEditTool._do_terminate exception: {e}")
        super()._do_terminate()

