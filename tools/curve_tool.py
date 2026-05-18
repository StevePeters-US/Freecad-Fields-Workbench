import FreeCAD
from PySide import QtCore, QtGui
from pivy import coin
from .dm_base import NURBSPrimitiveCreator, DragTimerMixin, ToolState
from core import dm_logger
from core.dm_point import DMPoint
from core.input_manager import DMInputManager

class CurveCreator(NURBSPrimitiveCreator, DragTimerMixin):
    """Tool to create a DMCurve object from clicked points."""
    def get_command_id(self):
        return "DM_CreateCurve"

    def __init__(self):
        super().__init__()
        self.points = []      # FreeCAD.Vector — committed curve point data
        self.dm_points = []   # DMPoint — visual spheres, parallel to self.points
        self.handle_dm_points = []  # DMPoint spheres for bezier in/out handles
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
        dm_logger.debug(f"CurveCreator: Editing existing object {obj.Label}")
        self._active_obj = obj
        obj.EditMode = True

        # Set working plane FIRST — to_global depends on it
        self.working_plane = obj.Placement
        self._working_plane_is_fallback = False
        self.state = ToolState.IDLE

        self.is_closed = getattr(obj, "Closed", False)

        # obj.Points are stored in local (placement) space — convert to world space
        local_pts = list(getattr(obj, "Points", []))
        self.points = [self.to_global(pt) for pt in local_pts]
        
        local_hi = list(getattr(obj, "HandleIn", []))
        local_ho = list(getattr(obj, "HandleOut", []))
        self.handle_in = [self.to_global(pt) for pt in local_hi]
        self.handle_out = [self.to_global(pt) for pt in local_ho]
        
        if not self.handle_in or len(self.handle_in) != len(self.points):
            self.handle_in, self.handle_out = self._get_auto_handles(self.points)

        # Clear any stale visuals from creation phase
        self._clear_handle_spheres()
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()

        # Rebuild control point spheres
        for pt in self.points:
            self._add_point_sphere(pt)

        # Rebuild handle spheres
        if len(self.points) >= 2:
            self._update_handle_spheres(self.handle_in, self.handle_out, self.points)

        self.state = ToolState.IDLE
        self.update_ui()

    def _do_terminate(self):
        self._clear_handle_spheres()
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()
        if self._active_obj:
            try:
                self._active_obj.EditMode = False
                self._active_obj.touch()
                self._active_obj.Document.recompute([self._active_obj])
            except Exception as e:
                pass
        
        try:
            if self.sg and self.points_root:
                self.sg.removeChild(self.points_root)
        except Exception as e:
            pass
        super()._do_terminate()

    def is_in_progress(self):
        """Returns True if the curve has at least one point OR is in edit mode."""
        return len(self.points) > 0 or getattr(self, "_is_editing", False)

    # ------------------------------------------------------------------
    # Sphere helpers
    # ------------------------------------------------------------------

    def _add_point_sphere(self, pt):
        """Create a DMPoint sphere for a newly committed point."""
        # Lock the radius on the first point so all spheres stay the same size.
        if self._cached_radius is None:
            self._cached_radius = self._compute_handle_radius(ref_pt=pt)

        dm_pt = DMPoint(pt)
        dm_pt.draw_point(self.points_root, self._cached_radius)
        self.dm_points.append(dm_pt)

    def _clear_handle_spheres(self):
        """Remove all bezier handle spheres from the scene."""
        for dm_pt in self.handle_dm_points:
            dm_pt.undraw()
        self.handle_dm_points.clear()

    def _update_handle_spheres(self, hi, ho, ctrl_pts):
        """Sync bezier handle spheres to match current handle positions.

        Only shows handles that are visually distinct from their control point.
        """
        if self._cached_radius is None:
            return

        handle_color = (0.3, 0.7, 1.0)  # Blue for handles
        r = self._cached_radius * 0.65
        threshold = 0.01

        # Build list of visible handle positions (skip degenerate handles at ctrl pt)
        visible = []
        for i in range(len(hi)):
            if i < len(ctrl_pts):
                cp = ctrl_pts[i]
                if hi[i] and (hi[i] - cp).Length > threshold:
                    visible.append(hi[i])
                if ho[i] and (ho[i] - cp).Length > threshold:
                    visible.append(ho[i])

        # Remove excess spheres
        while len(self.handle_dm_points) > len(visible):
            self.handle_dm_points.pop().undraw()

        # Create new spheres if needed
        while len(self.handle_dm_points) < len(visible):
            dm_pt = DMPoint(visible[len(self.handle_dm_points)])
            dm_pt.draw_point(self.points_root, r, color=handle_color)
            self.handle_dm_points.append(dm_pt)

        # Update positions
        for i, pos in enumerate(visible):
            self.handle_dm_points[i].position = pos
            self.handle_dm_points[i].update_draw(radius=r)

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
        if self._hovered_idx is not None and self._hovered_idx != -1 and self._hovered_idx < len(self.dm_points):
            self.dm_points[self._hovered_idx].set_color((1, 0.5, 0))
        self._hovered_idx = idx
        if idx == -1 or idx is None:
            self._restore_cursor()
        else:
            if idx < len(self.dm_points):
                self.dm_points[idx].set_color((0.3, 1.0, 0.3))
            self._set_cursor(QtCore.Qt.CrossCursor)

    def on_button1_up(self, event_dict):
        for dm_pt in self.dm_points:
            dm_pt.set_color((1, 0.5, 0))
        self._hovered_idx = -1
        return False

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

        ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return True
        b_type, b_idx, b_dist = self._hit_test_all(ray_p, ray_d)
        if b_idx is not None:
            self._edit_sel_idx = b_idx
            self._edit_sel_type = b_type
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

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        new_pos = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos},
            self._edit_drag_n, self._edit_drag_o,
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
                self.dm_points[self._edit_sel_idx].position = new_pos
                self.dm_points[self._edit_sel_idx].update_draw()
            elif self._edit_sel_type == "handle_in":
                self.handle_in[self._edit_sel_idx] = new_pos
            else:
                self.handle_out[self._edit_sel_idx] = new_pos
                
            self._update_edit_object()

    def _edit_hover(self, event_dict):
        """Update cursor when hovering over a handle in edit mode."""
        ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
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
        params = {"Points": pts, "Closed": self.is_closed}
        if len(pts) >= 2:
            params["HandleIn"] = hi
            params["HandleOut"] = ho
            params["HandleTypes"] = [0] * (2 * len(pts))
        self.update_active_object("curve", params)
        if len(pts) >= 2:
            self._update_handle_spheres(params["HandleIn"], params["HandleOut"], pts)

    def handle_click(self, event_dict):
        if self._is_editing:
            return True  # consume click, don't add points in edit mode
        try:
            pt = self._resolve_wp_click(event_dict)

            if pt is None:
                return False

            if self.state == ToolState.IDLE:
                if not self.working_plane:
                    # _resolve_wp_click already set it
                    pass
                else:
                    n, _ = self.get_base_plane()
                    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), n)
                    self.working_plane = FreeCAD.Placement(pt, rot)
                    # Re-project onto the locked plane for exact alignment.
                    pt = self.get_mouse_plane_pt(event_dict)
                    if pt is None:
                        self.working_plane = None
                        return False

                self.start_point = pt
                self.points.append(pt)
                self._add_point_sphere(pt)
                self.state = ToolState.ACTIVE
                self.update_preview()
                self.update_ui()
                return True

            elif self.state == ToolState.ACTIVE:
                if len(self.points) >= 2:
                    from core.dm_object import get_picking_radius
                    if (pt - self.points[0]).Length < get_picking_radius():
                        self.is_closed = True
                        self.current_point = None
                        self.finish()
                        return True

                self.points.append(pt)
                self._add_point_sphere(pt)
                self.update_preview()
                self.update_ui()
                return True

            return False
        except Exception:
            dm_logger.exception("Curve handle_click error")
            return False

    def handle_move(self, event_dict):
        if self._is_editing:
            if self.state != ToolState.DRAGGING:
                self._edit_hover(event_dict)
            return
        if self.state == ToolState.IDLE:
            super().handle_move(event_dict)
        elif self.state == ToolState.ACTIVE:
            ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
            hit_idx, _ = self._hit_test(ray_p, ray_d)
            self._set_hover(hit_idx)

            pt = self.get_mouse_plane_pt(event_dict)
            if pt is None:
                return

            if len(self.points) >= 2:
                from core.dm_object import get_picking_radius
                if (pt - self.points[0]).Length < get_picking_radius():
                    pt = self.points[0]
            self.current_point = pt
            self.update_preview()
            self.update_ui()

    def handle_keyboard(self, event_dict):
        return super().handle_keyboard(event_dict)

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
                except Exception:
                    pass
            self._active_obj = None
            self.reset_state()
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

        # If we are about to terminate (e.g. from RMB close), don't enter edit mode
        if getattr(self, "_terminated", False) or getattr(self, "_finish_scheduled", False):
            # Protect the object from _do_terminate cleanup
            self._is_editing = True
            if self._active_obj:
                try:
                    self._active_obj.EditMode = False
                    self._active_obj.touch()
                    self._active_obj.Document.recompute([self._active_obj])
                except Exception:
                    pass
            # Clear tool visuals
            self._clear_handle_spheres()
            for dm_pt in self.dm_points:
                dm_pt.undraw()
            self.dm_points.clear()
            return

        # Enter edit mode on the committed object
        committed_obj = self._active_obj
        self.edit_object(committed_obj)
        dm_logger.info("Curve accepted. Entering edit mode.")
        self.view.redraw()

    def reset_state(self):
        """Override to clear internal curve state (points, visuals)."""
        super().reset_state()
        self.points = []
        self._clear_handle_spheres()
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()
        self.is_closed = False
        self.view.redraw()

    def on_tool_option_0(self):
        dm_logger.info("snapping curve tool")

    def on_tool_option_1(self):
        dm_logger.info("snapping curve tool")

    def get_context_menu(self, event_dict=None):
        base_menu = super().get_context_menu(event_dict)
        return base_menu + [
            "-",
            ("Curve Option 1", lambda: dm_logger.info("Selected Curve Option 1")),
            ("Curve Option 2", lambda: dm_logger.info("Selected Curve Option 2"))
        ]
