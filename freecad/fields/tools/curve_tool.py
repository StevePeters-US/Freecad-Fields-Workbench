# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore
from pivy import coin
from .fld_base import NURBSPrimitiveCreator, DragTimerMixin, ToolState
from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_point import FldPoint
from freecad.fields.core.input.input_manager import FldInputManager

class CurveCreator(NURBSPrimitiveCreator, DragTimerMixin):
    """Tool to create a FldCurve object from clicked points."""
    def get_command_id(self):
        return "Fields_CreateCurve"

    def __init__(self):
        super().__init__()
        self.points = []      # FreeCAD.Vector — committed curve point data
        self.linked_nodes = [] # FldObject point nodes linked to this curve
        self.created_nodes = [] # FldObject point nodes created by this session
        self.fld_points = []   # FldPoint — visual spheres, parallel to self.points
        self.handle_fld_points = []  # FldPoint spheres for bezier in/out handles
        self.is_closed = False
        self.current_point = None
        self.dragged_index = -1
        self._drag_start_pos = None

        self._hovered_idx = -1
        self._hovered_type = None
        self._cached_radius = None

        # Edit mode drag state (for DragTimerMixin)
        self._edit_sel_idx = None
        self._edit_sel_type = None
        self._edit_drag_n = None
        self._edit_drag_o = None
        self._selected_point_idx = None   # last clicked/active point for V-key
        self.handle_types = []            # flat [In0, Out0, In1, Out1, ...]

        self.state = ToolState.IDLE
        self.sg = self.view.getSceneGraph() if self.view else None
        self.points_root = coin.SoSeparator()
        if self.sg:
            self.sg.addChild(self.points_root)

    def get_handled_types(self):
        return ["curve"]

    def edit_object(self, obj):
        """Load an existing curve into the tool for editing."""
        super().edit_object(obj)
        fld_logger.debug(f"CurveCreator: Editing existing object {obj.Label}")
        self._active_obj = obj
        obj.EditMode = True
        self.linked_nodes = list(getattr(obj, "ControlNodes", []) or [])
        self.created_nodes = []

        # Set working plane FIRST — to_global depends on it
        self.working_plane = obj.Placement
        self._working_plane_is_fallback = False
        self.state = ToolState.IDLE

        self.is_closed = getattr(obj, "Closed", False)

        # obj.Points are stored in local (placement) space — convert to world space
        if self.linked_nodes:
            self.points = [node.Coordinates for node in self.linked_nodes if node is not None]
        else:
            local_pts = list(getattr(obj, "Points", []))
            self.points = [self.to_global(pt) for pt in local_pts]
        
        local_hi = list(getattr(obj, "HandleIn", []))
        local_ho = list(getattr(obj, "HandleOut", []))
        self.handle_in = [self.to_global(pt) for pt in local_hi]
        self.handle_out = [self.to_global(pt) for pt in local_ho]
        
        if not self.handle_in or len(self.handle_in) != len(self.points):
            self.handle_in, self.handle_out = self._get_auto_handles(self.points)

        raw_ht = list(getattr(obj, "HandleTypes", []) or [])
        n_pts = len(self.points)
        # Ensure length = 2*n_pts, defaulting missing entries to Auto (0)
        self.handle_types = [raw_ht[i] if i < len(raw_ht) else 0 for i in range(2 * n_pts)]
        self._selected_point_idx = None

        # Clear any stale visuals from creation phase
        self._clear_handle_spheres()
        for fld_pt in self.fld_points:
            fld_pt.undraw()
        self.fld_points.clear()

        # Rebuild control point spheres
        for pt in self.points:
            self._add_point_sphere(pt)

        # Rebuild handle spheres
        if len(self.points) >= 2:
            self._update_handle_spheres(self.handle_in, self.handle_out, self.points)

        self.state = ToolState.IDLE
        self.update_ui()

    def _do_terminate(self):
        try:
            try:
                self._clear_handle_spheres()
            except Exception as e:
                fld_logger.debug(f"CurveCreator._do_terminate: failed to clear handle spheres: {e}")
            for fld_pt in self.fld_points:
                try:
                    fld_pt.undraw()
                except Exception as e:
                    fld_logger.debug(f"CurveCreator._do_terminate: failed to undraw point: {e}")
            self.fld_points.clear()
            if self._active_obj:
                try:
                    self._active_obj.EditMode = False
                    self._active_obj.touch()
                    self._active_obj.Document.recompute([self._active_obj])
                except Exception as e:
                    fld_logger.debug(f"CurveCreator._do_terminate: failed to exit edit mode and recompute: {e}")
            
            if not getattr(self, "_is_editing", False) and not getattr(self, "_finished", False):
                if hasattr(self, "created_nodes") and self.created_nodes:
                    doc = FreeCAD.ActiveDocument
                    if doc:
                        for node in self.created_nodes:
                            try:
                                doc.removeObject(node.Name)
                            except Exception as e:
                                fld_logger.debug(f"CurveCreator._do_terminate: failed to remove node {node.Name}: {e}")
                        doc.recompute()
            self.created_nodes = []
            self.linked_nodes = []
            if self.sg and self.points_root:
                self.sg.removeChild(self.points_root)
        except Exception as e:
            fld_logger.debug(f"CurveCreator._do_terminate exception: {e}")
        super()._do_terminate()

    def is_in_progress(self):
        """Returns True if the curve has at least one point OR is in edit mode."""
        return len(self.points) > 0 or getattr(self, "_is_editing", False)

    # ------------------------------------------------------------------
    # Sphere helpers
    # ------------------------------------------------------------------

    def _add_point_sphere(self, pt):
        """Create a FldPoint sphere for a newly committed point."""
        # Lock the radius on the first point so all spheres stay the same size.
        if self._cached_radius is None:
            self._cached_radius = self._compute_handle_radius(ref_pt=pt)

        fld_pt = FldPoint(pt)
        fld_pt.draw_point(self.points_root, self._cached_radius)
        self.fld_points.append(fld_pt)

    def _clear_handle_spheres(self):
        """Remove all bezier handle spheres from the scene."""
        for fld_pt in self.handle_fld_points:
            fld_pt.undraw()
        self.handle_fld_points.clear()

    def _update_handle_spheres(self, hi, ho, ctrl_pts):
        """Sync bezier handle spheres to match current handle positions.

        Only shows handles that are visually distinct from their control point.
        """
        if self._cached_radius is None:
            return

        r = self._cached_radius * 0.65
        threshold = 0.01

        # Colors for handle types:
        # 0 = Auto (light blue), 1 = Vector (green), 2 = Aligned (purple/magenta), 3 = Free (red)
        HANDLE_COLORS = {
            0: (0.2, 0.7, 1.0),
            1: (0.2, 0.8, 0.2),
            2: (0.8, 0.2, 0.8),
            3: (1.0, 0.2, 0.2),
        }

        # Build list of visible handle positions (skip degenerate handles at ctrl pt)
        visible = []
        visible_indices = []
        for i in range(len(hi)):
            if i < len(ctrl_pts):
                cp = ctrl_pts[i]
                if hi[i] and (hi[i] - cp).Length > threshold:
                    visible.append(hi[i])
                    visible_indices.append(2 * i)
                if ho[i] and (ho[i] - cp).Length > threshold:
                    visible.append(ho[i])
                    visible_indices.append(2 * i + 1)

        # Remove excess spheres
        while len(self.handle_fld_points) > len(visible):
            self.handle_fld_points.pop().undraw()

        # Create new spheres if needed
        while len(self.handle_fld_points) < len(visible):
            idx_in_visible = len(self.handle_fld_points)
            fld_pt = FldPoint(visible[idx_in_visible])
            h_idx = visible_indices[idx_in_visible]
            h_type = self.handle_types[h_idx] if h_idx < len(self.handle_types) else 0
            color = HANDLE_COLORS.get(h_type, (0.2, 0.7, 1.0))
            fld_pt.draw_point(self.points_root, r, color=color)
            self.handle_fld_points.append(fld_pt)

        # Update positions and colors
        for i, pos in enumerate(visible):
            self.handle_fld_points[i].position = pos
            self.handle_fld_points[i].update_draw(radius=r)
            h_idx = visible_indices[i]
            h_type = self.handle_types[h_idx] if h_idx < len(self.handle_types) else 0
            color = HANDLE_COLORS.get(h_type, (0.2, 0.7, 1.0))
            self.handle_fld_points[i].set_color(color)

    def _hit_test(self, ray_p, ray_d):
        """Perpendicular distance hit test against placed control point spheres."""
        return self._hit_test_perp(ray_p, ray_d, self.points)

    def _hit_test_all(self, ray_p, ray_d, tol=None):
        """Hit test main points and handles, returning (type, idx, dist)."""
        b_type, b_idx, b_dist = "point", None, float('inf')
        idx, dist = self._hit_test_perp(ray_p, ray_d, self.points, tolerance=tol)
        if idx is not None and dist < b_dist:
            b_type, b_idx, b_dist = "point", idx, dist
            
        if hasattr(self, "handle_in") and self.handle_in:
            idx, dist = self._hit_test_perp(ray_p, ray_d, self.handle_in, tolerance=tol)
            if idx is not None and dist < b_dist:
                b_type, b_idx, b_dist = "handle_in", idx, dist
                
        if hasattr(self, "handle_out") and self.handle_out:
            idx, dist = self._hit_test_perp(ray_p, ray_d, self.handle_out, tolerance=tol)
            if idx is not None and dist < b_dist:
                b_type, b_idx, b_dist = "handle_out", idx, dist
                
        return b_type, b_idx, b_dist

    def _set_hover(self, idx):
        """Recolour point spheres and update OS cursor for hovered index (-1 = none)."""
        if idx == self._hovered_idx:
            return
        if self._hovered_idx is not None and self._hovered_idx != -1 and self._hovered_idx < len(self.fld_points):
            self.fld_points[self._hovered_idx].set_color((1, 0.5, 0))
        self._hovered_idx = idx
        if idx == -1 or idx is None:
            self._restore_cursor()
        else:
            if idx < len(self.fld_points):
                self.fld_points[idx].set_color((0.3, 1.0, 0.3))
            self._set_cursor(QtCore.Qt.CrossCursor)

    def on_button1_up(self, event_dict):
        for fld_pt in self.fld_points:
            fld_pt.set_color((1, 0.5, 0))
        self._hovered_idx = -1
        return False

    def on_context_menu(self, event_dict):
        if self._is_editing:
            ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
            if ray_p and ray_d:
                b_type, b_idx, b_dist = self._hit_test_all(ray_p, ray_d)
                self._context_menu_hit = (b_idx, b_type) if b_idx is not None else None
            else:
                self._context_menu_hit = None
            items = self.get_context_menu(event_dict)
            from freecad.fields.core.input.fld_menu import FldMenuManager
            FldMenuManager.get_instance().trigger_dynamic_menu(items)
            return True
        return super().on_context_menu(event_dict)

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def on_button1_down(self, event_dict):
        """In edit mode, hit-test handles and start drag. Otherwise route to handle_click."""
        if self._is_editing:
            return self._edit_on_mouse_press(event_dict)
        return self.handle_click(event_dict)

    def _edit_on_mouse_press(self, event_dict):
        """Hit-test control point handles and start drag timer in edit mode."""
        btn = event_dict.get("Button")
        if btn != QtCore.Qt.LeftButton:
            return False

        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return True
        b_type, b_idx, b_dist = self._hit_test_all(ray_p, ray_d)
        if b_idx is not None:
            self._edit_sel_idx = b_idx
            self._edit_sel_type = b_type
            if b_type == "point":
                self._selected_point_idx = b_idx
            vd = self.view.getViewDirection()
            self._edit_drag_n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
            self._edit_drag_n.normalize()
            if b_type == "point":
                self._edit_drag_o = self.points[b_idx]
            elif b_type == "handle_in":
                self._edit_drag_o = self.handle_in[b_idx]
            else:
                self._edit_drag_o = self.handle_out[b_idx]
            self.state = ToolState.DRAGGING
            self._start_drag_timer()
            self._set_cursor(QtCore.Qt.SizeAllCursor)
            return True
        return False

    def _drag_update(self):
        """QTimer callback: move the selected control point to the current mouse position."""
        if self._drag_check_lmb_released():
            self._edit_sel_idx = None
            self.state = ToolState.IDLE
            return
        if self._edit_sel_idx is None:
            self._stop_drag_timer()
            return

        mouse_pos = FldInputManager.get_instance()._last_qt_pos
        new_pos = self.resolve_world_point(
            {"Position": mouse_pos},
            base_pt=self._edit_drag_o,
            plane_normal=self._edit_drag_n,
            plane_origin=self._edit_drag_o,
            extra_points=self.points,
            place_on_geometry=False
        )
        if new_pos is None:
            return

        dist_moved = (new_pos - self._edit_drag_o).Length
        if dist_moved > 1e-6:
            self._edit_drag_o = new_pos
            if self._edit_sel_type == "point":
                delta = new_pos - self.points[self._edit_sel_idx]
                self.points[self._edit_sel_idx] = new_pos
                self.handle_in[self._edit_sel_idx] = self.handle_in[self._edit_sel_idx] + delta
                self.handle_out[self._edit_sel_idx] = self.handle_out[self._edit_sel_idx] + delta
                self.fld_points[self._edit_sel_idx].position = new_pos
                self.fld_points[self._edit_sel_idx].update_draw()
                if hasattr(self, "linked_nodes") and self.linked_nodes and self._edit_sel_idx < len(self.linked_nodes):
                    node = self.linked_nodes[self._edit_sel_idx]
                    if node is not None:
                        node.Coordinates = new_pos
                        node.touch()
            elif self._edit_sel_type == "handle_in":
                self.handle_in[self._edit_sel_idx] = new_pos
                n = len(self.points)
                while len(self.handle_types) < 2 * n: self.handle_types.append(0)
                p_types = list(getattr(self._active_obj, "PointTypes", [])) if self._active_obj else []
                pt_type = p_types[self._edit_sel_idx] if self._edit_sel_idx < len(p_types) else 0
                new_h_type = 3 if pt_type == 2 else 2
                self.handle_types[2 * self._edit_sel_idx] = new_h_type
                self.handle_types[2 * self._edit_sel_idx + 1] = new_h_type
            else:
                self.handle_out[self._edit_sel_idx] = new_pos
                n = len(self.points)
                while len(self.handle_types) < 2 * n: self.handle_types.append(0)
                p_types = list(getattr(self._active_obj, "PointTypes", [])) if self._active_obj else []
                pt_type = p_types[self._edit_sel_idx] if self._edit_sel_idx < len(p_types) else 0
                new_h_type = 3 if pt_type == 2 else 2
                self.handle_types[2 * self._edit_sel_idx] = new_h_type
                self.handle_types[2 * self._edit_sel_idx + 1] = new_h_type
                
            self._update_edit_object()

    def _edit_hover(self, event_dict):
        """Update cursor when hovering over a handle in edit mode."""
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return
        # Use slightly larger tolerance for hover to make it easier to hit
        tol = self._compute_handle_radius() * 1.5
        b_type, b_idx, b_dist = self._hit_test_all(ray_p, ray_d, tol=tol)
        if b_idx is not None:
            self._set_cursor(QtCore.Qt.PointingHandCursor)
        else:
            self._restore_cursor()

    def _update_edit_object(self):
        """Update the curve object with current control points during edit drag."""
        if self._active_obj is None:
            return
        pts = list(self.points)
        hi = list(self.handle_in)
        ho = list(self.handle_out)
        n = len(pts)
        # Ensure handle_types is always the right length
        if len(self.handle_types) != 2 * n:
            self.handle_types = [self.handle_types[i] if i < len(self.handle_types) else 0
                                 for i in range(2 * n)]
        params = {"Points": pts, "Closed": self.is_closed}
        if hasattr(self, "linked_nodes") and self.linked_nodes:
            params["ControlNodes"] = list(self.linked_nodes)
        if n >= 2:
            params["HandleIn"] = hi
            params["HandleOut"] = ho
            params["HandleTypes"] = list(self.handle_types)
        self.update_active_object("curve", params)
        if n >= 2:
            self._update_handle_spheres(params["HandleIn"], params["HandleOut"], pts)

    def handle_click(self, event_dict):
        if self._is_editing:
            return True  # consume click, don't add points in edit mode
        try:
            # Check for existing point node under cursor
            hit_node = None
            x, y = FldInputManager.get_instance().get_gl_pos_phys(self.view, event_dict)
            if x is not None and y is not None:
                info = self.view.getObjectInfo((x, y))
                if info and "Object" in info:
                    obj_name = info["Object"]
                    doc = FreeCAD.ActiveDocument
                    obj = doc.getObject(obj_name) if doc else None
                    if obj and hasattr(obj, "ShapeType") and obj.ShapeType == "point":
                        hit_node = obj

            if hit_node is not None:
                pt = hit_node.Coordinates if hasattr(hit_node, "Coordinates") else hit_node.Position
            else:
                pt = self._resolve_wp_click(event_dict)

            if pt is None:
                return False

            if hit_node is None:
                # Create a new shared node at projection
                doc = FreeCAD.ActiveDocument
                node_name = "Node_0"
                idx = 0
                while doc.getObject(node_name):
                    idx += 1
                    node_name = f"Node_{idx}"
                
                from freecad.fields.core.objects.fld_object import create_fld_object
                hit_node = create_fld_object(node_name, "point", params={"Coordinates": pt})
                # create_fld_object() only touches now (IF-016). Recompute the one
                # node so it gets a Shape; the old full-document recompute the
                # factory used to do here was never needed for a single point.
                if hit_node is not None and self.doc:
                    self.doc.recompute([hit_node])
                self.created_nodes.append(hit_node)

            if self.state == ToolState.IDLE:
                if not self.working_plane:
                    # _resolve_wp_click already set it
                    pass
                else:
                    n, _ = self.get_base_plane()
                    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), n)
                    self.working_plane = FreeCAD.Placement(pt, rot)
                    # Re-project onto the locked plane for exact alignment.
                    if hit_node not in self.created_nodes:
                        pass
                    else:
                        pt = self.get_mouse_plane_pt(event_dict)
                        if pt is None:
                            self.working_plane = None
                            return False
                        hit_node.Coordinates = pt
                        hit_node.touch()

                self.start_point = pt
                self.points.append(pt)
                self.linked_nodes.append(hit_node)
                self._add_point_sphere(pt)
                self.state = ToolState.ACTIVE
                self.update_preview()
                self.update_ui()
                return True

            elif self.state == ToolState.ACTIVE:
                if len(self.points) >= 2:
                    from freecad.fields.core.objects.fld_object import get_picking_radius
                    is_closing = (pt - self.points[0]).Length < get_picking_radius()
                    if not is_closing and self.linked_nodes and hit_node == self.linked_nodes[0]:
                        is_closing = True
                    
                    if is_closing:
                        self.is_closed = True
                        self.current_point = None
                        if hit_node in self.created_nodes:
                            doc = FreeCAD.ActiveDocument
                            node_name = hit_node.Name
                            def _deferred_remove(d=doc, n=node_name):
                                try:
                                    d.removeObject(n)
                                except Exception as e:
                                    fld_logger.debug(f"CurveCreator.handle_click: failed to remove closing node {n}: {e}")
                            QtCore.QTimer.singleShot(0, _deferred_remove)
                            self.created_nodes.remove(hit_node)
                        
                        # Commit the curve and transition to edit mode directly
                        self.current_point = None
                        self._update_pending = False
                        self._do_update_preview()
                        self._on_committed(self._active_obj)
                        
                        committed_obj = self._active_obj
                        self.edit_object(committed_obj)
                        fld_logger.info("Curve closed. Entering edit mode.")
                        self.view.redraw()
                        return True

                self.points.append(pt)
                self.linked_nodes.append(hit_node)
                self._add_point_sphere(pt)
                self.update_preview()
                self.update_ui()
                return True

            return False
        except Exception:
            fld_logger.exception("Curve handle_click error")
            return False

    def handle_move(self, event_dict):
        if self._is_editing:
            if self.state != ToolState.DRAGGING:
                self._edit_hover(event_dict)
            return
        if self.state == ToolState.IDLE:
            super().handle_move(event_dict)
        elif self.state == ToolState.ACTIVE:
            ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
            hit_idx, _ = self._hit_test(ray_p, ray_d)
            self._set_hover(hit_idx)

            pt = self.resolve_world_point(event_dict, extra_points=self.points)
            if pt is None:
                return

            if len(self.points) >= 2:
                from freecad.fields.core.objects.fld_object import get_picking_radius
                if (pt - self.points[0]).Length < get_picking_radius():
                    pt = self.points[0]
            self.current_point = pt
            self.update_preview()
            self.update_ui()

    def handle_keyboard(self, event_dict):
        if self._is_editing:
            key_text = str(event_dict.get("Text", "")).upper()
            if key_text == "V" and self._selected_point_idx is not None:
                self._cycle_handle_type(self._selected_point_idx)
                return True
        return super().handle_keyboard(event_dict)

    # ------------------------------------------------------------------
    # Handle type cycling (V key in edit mode)
    # ------------------------------------------------------------------

    _HANDLE_TYPE_NAMES = {0: "Auto", 1: "Vector", 2: "Aligned", 3: "Free"}

    def _cycle_handle_type(self, point_idx):
        """Cycle Auto→Vector→Aligned→Free→Auto for the given control point."""
        n = len(self.points)
        if point_idx < 0 or point_idx >= n:
            return

        in_i  = 2 * point_idx
        out_i = 2 * point_idx + 1

        # Extend handle_types list if it was shorter
        while len(self.handle_types) < 2 * n:
            self.handle_types.append(0)

        current = self.handle_types[out_i]
        next_type = (current + 1) % 4

        self.handle_types[in_i]  = next_type
        self.handle_types[out_i] = next_type

        self._apply_handle_type(point_idx, next_type)
        self._update_edit_object()
        fld_logger.info(
            f"CurveCreator: Point {point_idx} handle type → {self._HANDLE_TYPE_NAMES.get(next_type, '?')}"
            " (V to cycle: Auto→Vector→Aligned→Free→Auto)"
        )

    def _apply_handle_type(self, idx, handle_type):
        """Recompute handle positions at idx based on the new handle_type.

        Auto:    Catmull-Rom symmetric tangent.
        Vector:  Each handle points 1/3 of its chord toward the adjacent point.
        Aligned: Keep current positions — user drags one, the other mirrors direction.
        Free:    Keep current positions — both handles move independently.
        """
        n = len(self.points)
        p = self.points[idx]
        if self.is_closed:
            prev_p = self.points[(idx - 1) % n]
            next_p = self.points[(idx + 1) % n]
        else:
            prev_p = self.points[max(0, idx - 1)]
            next_p = self.points[min(n - 1, idx + 1)]

        if handle_type == 1:  # Vector
            self.handle_out[idx] = p + (next_p - p) * (1.0 / 3.0)
            self.handle_in[idx]  = p + (prev_p - p) * (1.0 / 3.0)
        elif handle_type == 0:  # Auto — re-snap to Catmull-Rom
            chord_out = (next_p - p).Length
            chord_in  = (p - prev_p).Length
            tangent = next_p - prev_p
            tlen = tangent.Length
            if tlen > 1e-6:
                tangent = tangent * (1.0 / tlen)
            else:
                tangent = FreeCAD.Vector(0, 0, 0)
            self.handle_out[idx] = p + tangent * (chord_out / 3.0)
            self.handle_in[idx]  = p - tangent * (chord_in  / 3.0)
        # Aligned (2) and Free (3): leave existing handle positions unchanged;

    # ------------------------------------------------------------------
    # Curve geometry
    # ------------------------------------------------------------------

    def _get_auto_handles(self, points):
        """Compute automatic smooth handles for each point in the list."""
        n = len(points)
        if n < 2:
            return [], []

        h_in = [p for p in points]
        h_out = [p for p in points]

        for i in range(n):
            p = points[i]
            if self.is_closed:
                prev_p = points[(i - 1) % n]
                next_p = points[(i + 1) % n]
            else:
                prev_p = points[i-1] if i > 0 else (points[1] - (points[1]-points[0]) if n > 1 else p)
                next_p = points[i+1] if i < n-1 else (points[-1] + (points[-1]-points[-2]) if n > 1 else p)

            tangent = (next_p - prev_p) * 0.5
            if tangent.Length > 0.0001:
                h_out[i] = p + (tangent * 0.33)
                h_in[i] = p - (tangent * 0.33)

            if not self.is_closed:
                if i == 0:
                    h_in[i] = p
                if i == n - 1:
                    h_out[i] = p

        return h_in, h_out

    def update_preview(self, drag_pt=None):
        self._schedule_update(self._do_update_preview)

    def _do_update_preview(self):
        self._update_pending = False
        if self._terminated:
            return
        if not self.points:
            return
        pts = list(self.points)
        if self.current_point and self.dragged_index == -1:
            pts.append(self.current_point)

        params = {"Points": pts, "Closed": self.is_closed}
        if hasattr(self, "linked_nodes") and self.linked_nodes:
            # Match elements of pts
            params["ControlNodes"] = list(self.linked_nodes)
        if len(pts) >= 2:
            hi, ho = self._get_auto_handles(pts)
            self.handle_in, self.handle_out = hi, ho
            params["HandleIn"] = hi
            params["HandleOut"] = ho
            params["PointTypes"] = [0] * len(pts)
            params["HandleTypes"] = [0] * (2 * len(pts))
        else:
            self.handle_in, self.handle_out = pts, pts
            params["HandleIn"] = pts
            params["HandleOut"] = pts
            params["PointTypes"] = [0] * len(pts)
            params["HandleTypes"] = [0] * (2 * len(pts))

        self.update_active_object("curve", params)

        # Update handle sphere positions during creation
        if len(pts) >= 2:
            self._update_handle_spheres(params["HandleIn"], params["HandleOut"], pts)

    def update_ui(self):
        pass

    def finish(self):
        """Finalize the curve and enter edit mode."""
        if getattr(self, "_is_editing", False):
            self._is_editing = False
            if self._active_obj:
                try:
                    self._active_obj.EditMode = False
                    self._active_obj.touch()
                    self._active_obj.Document.recompute([self._active_obj])
                except Exception as e:
                    fld_logger.debug(f"CurveCreator.finish: failed to exit edit mode and recompute: {e}")
            self._active_obj = None
            self.reset_state()
            self.terminate()
            
            if self.autorepeat:
                cmd_id = self.get_command_id()
                if cmd_id:
                    from PySide import QtCore
                    import FreeCADGui
                    QtCore.QTimer.singleShot(0, lambda: FreeCADGui.runCommand(cmd_id))
            return

        if len(self.points) < 2:
            self.terminate()
            return

        # Drop the trailing mouse-follow point and do a synchronous final
        # update so the object is correct before we enter edit mode.
        self.current_point = None
        self._update_pending = False
        self._do_update_preview()
        self._on_committed(self._active_obj)

        if self.autorepeat:
            super().finish()
            return

        # If autorepeat is disabled, accept/finalize the curve and terminate the tool completely.
        self._finished = True
        self.terminate()

    def reset_state(self):
        """Override to clear internal curve state (points, visuals)."""
        super().reset_state()
        if hasattr(self, "created_nodes") and self.created_nodes:
            doc = FreeCAD.ActiveDocument
            if doc:
                nodes = list(self.created_nodes)
                def _deferred_remove(d=doc, ns=nodes):
                    for node in ns:
                        try:
                            d.removeObject(node.Name)
                        except Exception as e:
                            fld_logger.debug(f"CurveCreator.reset_state: failed to remove node {node.Name}: {e}")
                    d.recompute()
                QtCore.QTimer.singleShot(0, _deferred_remove)
        self.created_nodes = []
        self.linked_nodes = []
        self.points = []
        self._clear_handle_spheres()
        for fld_pt in self.fld_points:
            fld_pt.undraw()
        self.fld_points.clear()
        self.is_closed = False
        self.view.redraw()

    def on_tool_option_0(self):
        fld_logger.info("snapping curve tool")

    def on_tool_option_1(self):
        fld_logger.info("snapping curve tool")

    def get_context_menu(self, event_dict=None):
        hit = getattr(self, "_context_menu_hit", None)
        items = []
        if self._is_editing and hit:
            idx, _elem_type = hit
            items += [
                ("Handle Type", [
                    ("Auto",    lambda i=idx: self._set_handle_type(i, 0)),
                    ("Vector",  lambda i=idx: self._set_handle_type(i, 1)),
                    ("Aligned", lambda i=idx: self._set_handle_type(i, 2)),
                    ("Free",    lambda i=idx: self._set_handle_type(i, 3)),
                ]),
                None,
            ]
        items += super().get_context_menu(event_dict)
        return items

    def _set_handle_type(self, idx, val):
        n = len(self.points)
        if idx < 0 or idx >= n:
            return
        while len(self.handle_types) < 2 * n:
            self.handle_types.append(0)
        self.handle_types[2 * idx] = val
        self.handle_types[2 * idx + 1] = val
        self._apply_handle_type(idx, val)
        self._update_edit_object()
        if self.view:
            self.view.redraw()
