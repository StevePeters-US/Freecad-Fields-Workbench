import FreeCAD
import FreeCADGui
import math
from enum import IntEnum
from PySide import QtCore
from pivy import coin
from core import dm_logger
from core.input_manager import DMInputManager
from core.dm_point import DMPoint
from core.dm_line import DMLineSet
from core.dm_object import create_dm_object, get_interactive_throttle_interval
from core.dm_mesher import mesh_timer
from tools.dm_base import DMBase, DragTimerMixin, ToolState

# Ensure we import the right storage classes. For now, defaulting to MarchingCubes
from core.sdf.sdf.box import SdfBoxField
from core.sdf.sdf.sphere import SdfSphereField
from core.sdf.sdf.cylinder import SdfCylinderField

# Cell size for interactive preview in mm (larger = faster updates)
_PREVIEW_CELL_SIZE = 20.0

# Opposite corner index for box corners 0..7 (see _get_final_points ordering)
_BOX_OPPOSITE = {0: 6, 1: 7, 2: 4, 3: 5, 4: 2, 5: 3, 6: 0, 7: 1}



class PrimitiveCreatorBase(DMBase, DragTimerMixin):
    """Base class for SDF primitive creator tools with live mesh preview."""
    


    def __init__(self):
        super().__init__()
        self._preview_obj = None     # Live FreeCAD object for preview
        self._update_pending = False  # Throttle rapid updates
        self._creating_obj = False    # Re-entrancy guard
        # Reset the shared timer so preview calls for this tool session are isolated
        mesh_timer.reset()

        self.dm_points = []
        self.dm_line_set = None
        self.points_root = coin.SoSeparator()
        if self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)

        # Edit mode drag state
        self._edit_sel_idx = None
        self._edit_drag_n = None
        self._edit_drag_o = None

        # Unified workplane pre-load logic
        self._init_working_plane()

    def get_handled_types(self):
        return ["sdf"]

    def edit_object(self, obj):
        """Load an existing SDF object into the tool for editing.

        Base: sets preview obj, loads raw points, sets workplane.
        Subclasses call super() then reconstruct their specific state and draw handles.
        """
        super().edit_object(obj)
        dm_logger.debug(f"{type(self).__name__}: Editing existing object {obj.Label}")
        self._preview_obj = obj

        self.working_plane = obj.Placement
        self._working_plane_is_fallback = False

        if hasattr(obj, "Points"):
            self.points = [self.working_plane.multVec(pt) for pt in obj.Points]

    # ------------------------------------------------------------------
    # Edit mode: hover, handle selection, drag
    # ------------------------------------------------------------------

    def handle_move(self, event_dict):
        """Suppress creation logic during edit mode hover; normal creation otherwise."""
        if self._is_editing:
            if self.state != ToolState.DRAGGING:
                self._edit_hover(event_dict)
            return  # Never propagate to DMBase.handle_move in edit mode
        super().handle_move(event_dict)

    def _edit_hover(self, event_dict):
        """Update cursor when hovering over a handle in edit mode."""
        ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return
        pts = [dm_pt.position for dm_pt in self.dm_points]
        idx, _ = self._hit_test_perp(ray_p, ray_d, pts)
        if idx is not None:
            from PySide.QtCore import Qt
            self._set_cursor(Qt.PointingHandCursor)
        else:
            self._restore_cursor()

    def _edit_on_mouse_press(self, event_dict):
        """Hit-test handles and start drag timer in edit mode."""
        btn = event_dict.get("Button")
        if btn != QtCore.Qt.LeftButton:
            return False

        ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return True
        pts = [dm_pt.position for dm_pt in self.dm_points]
        idx, _ = self._hit_test_perp(ray_p, ray_d, pts)
        if idx is not None:
            self._edit_sel_idx = idx
            vd = self.view.getViewDirection()
            self._edit_drag_n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
            self._edit_drag_n.normalize()
            self._edit_drag_o = pts[idx]
            self.state = ToolState.DRAGGING
            self._start_drag_timer()
            self._set_cursor(QtCore.Qt.SizeAllCursor)
        return True  # always consume click in edit mode

    def _drag_update(self):
        """QTimer callback: move the selected handle to the current mouse position."""
        if self._drag_check_lmb_released():
            self._edit_sel_idx = None
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

        self.dm_points[self._edit_sel_idx].position = new_pos
        self.dm_points[self._edit_sel_idx].update_draw()

        self._sync_edit_points()

        field = self._get_edit_preview_field()
        if field is not None and self._preview_obj:
            self._apply_preview_field(field)
            self._update_pending = False
        if self.view:
            self.view.redraw()

    def _sync_edit_points(self):
        """Sync dm_point positions back to tool-specific variables. Override in subclasses."""
        pass

    def _get_edit_preview_field(self):
        """Return the SDF field for the current edit state. Defaults to _get_preview_field."""
        return self._get_preview_field()

    def finish(self):
        """In edit mode, finish resets to idle. Otherwise, standard creation finish."""
        if getattr(self, "_is_editing", False):
            self._is_editing = False
            self._preview_obj = None
            self.reset_state()
            return

        if self.is_in_progress():
            name = type(self).__name__.replace("Creator", "")
            self._finalize_object(name, terminate=False)
            self.reset_state()
            dm_logger.info(f"{name} accepted. Tool remains active.")
        else:
            self.terminate()


    def _init_working_plane(self):
        """Pre-load a workplane if one isn't already detected from selection."""
        if not self.working_plane:
            visible_wps = self.get_visible_workplanes()
            if visible_wps:
                wp = visible_wps[0]
                self.working_plane = self._get_placement()
                self._working_plane_is_fallback = False

    def _get_placement(self):
        """Return a FreeCAD.Placement from self.working_plane, or None."""
        wp = getattr(self, "working_plane", None)
        if wp is None:
            return None
        if hasattr(wp, "getGlobalPlacement"):
            return wp.getGlobalPlacement()
        elif hasattr(wp, "Placement"):
            return wp.Placement
        return wp

    @staticmethod
    def _box_corners_local(center, half_size):
        """Return list of 8 FreeCAD.Vector corners in local space.
        
        Order: (-,-,-) (+,-,-) (+,+,-) (-,+,-) (-,-,+) (+,-,+) (+,+,+) (-,+,+)
        """
        c, h = center, half_size
        return [
            FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z - h.z),
            FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z - h.z),
            FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z - h.z),
            FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z - h.z),
            FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z + h.z),
            FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z + h.z),
            FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z + h.z),
            FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z + h.z),
        ]

    def _update_handle_positions(self, world_pts, color=(1.0, 0.5, 0.0)):
        """Update dm_points to match the given world-space positions.
        
        Creates new DMPoint objects as needed, updates existing ones.
        """
        while len(self.dm_points) < len(world_pts):
            self.dm_points.append(DMPoint(world_pts[len(self.dm_points)]))
        
        r = self._compute_handle_radius(ref_pt=world_pts[0] if world_pts else None)
        for i, pt in enumerate(world_pts):
            if i >= len(self.dm_points):
                break
            self.dm_points[i].position = pt
            if self.dm_points[i]._point_sep is None:
                self.dm_points[i].draw_point(self.points_root, radius=r, color=color)
            else:
                self.dm_points[i].update_draw(radius=r)

    def _height_drag_move(self, event_dict):
        """Move current_point along workplane normal from _height_drag_base."""
        if getattr(self, "_height_drag_base", None) is None:
            return
        wp = getattr(self, "working_plane", None)
        normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
        self.current_point = DMInputManager.get_instance().get_axis_point(
            self.view, self._height_drag_base, normal, event_dict
        )

    def _get_preview_field(self):
        """Subclasses return the current field based on click state + current_point."""
        return None

    def _get_final_field(self):
        """Subclasses return the final field when placement is committed."""
        return None

    def _get_final_points(self):
        """Subclasses return the corner/boundary points for the committed object."""
        return None

    def update_preview(self):
        """Called on every mouse move by DMBase.handle_move. Updates the live mesh."""
        if getattr(self, "_terminated", False):
            return
        field = self._get_preview_field()
        if field is None:
            return

        if self._preview_obj is None:
            if getattr(self, "_creating_obj", False):
                return
            self._creating_obj = True
            try:
                # Create the preview object for the first time
                # Use last part of class name without 'Creator' suffix
                name = type(self).__name__.replace("Creator", "")
                self._preview_obj = create_dm_object(name=name, shape_type="sdf")
            finally:
                self._creating_obj = False

        self._schedule_update(lambda: self._do_full_preview_update(field))

    def _do_full_preview_update(self, field):
        """Throttled update of both the mesh and the ghost visuals."""
        self._apply_preview_field(field)
        self._update_ghost_visuals()
        if self.view:
            self.view.redraw()

    def _update_ghost_visuals(self):
        """Standard implementation for primitive tools to show points/edges."""
        pass

    def _apply_preview_field(self, field):
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            proxy = self._preview_obj.Proxy
            if proxy is None:
                return
            proxy.SdfField = field

            im = DMInputManager.get_instance()
            if hasattr(self._preview_obj, "IsSubtractive"):
                self._preview_obj.IsSubtractive = im.is_ctrl_down()

            # Direct GPU update — skip FreeCAD recompute cycle
            from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
            label = f"{self._preview_obj.Document.Name}.{self._preview_obj.Name}"
            DMSceneRayMarchRenderer.get_instance().update_field(label, field)
        except Exception as e:
            dm_logger.debug(f"PrimitiveCreatorBase preview update error: {e}")
        finally:
            self._update_pending = False

    def _finalize_object(self, name, terminate=True):
        """Commit the preview object as the final result, upgrading its mesh resolution."""
        field = self._get_final_field()
        points = self._get_final_points()
        if field is None:
            if terminate: self.terminate()
            return
        
        # We don't set self._finished = True here if we want to repeat, 
        # because _finished prevents _do_terminate from cleaning up.
        # But we DO want to sever the preview object.
        QtCore.QTimer.singleShot(0, lambda: self.__do_commit(name, field, points))
        if terminate:
            self._finished = True
            self.terminate()

    def __do_commit(self, name, field, points):
        obj = self._preview_obj
        if obj is None or not obj.Document:
            # fallback: create fresh
            obj = create_dm_object(name=name, shape_type="sdf")

        # Rename to final name
        try:
            obj.Label = name
        except Exception:
            pass

        # Add points for editing
        if points is not None:
            try:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                obj.Points = points
            except Exception:
                pass

        # Resolution is now handled globally by the GPU renderer settings.
        proxy = obj.Proxy
        proxy.SdfField = field
        
        # Set placement to match the working plane so edit mode restores correctly
        if self.working_plane:
            if hasattr(self.working_plane, "getGlobalPlacement"):
                obj.Placement = self.working_plane.getGlobalPlacement()
            elif hasattr(self.working_plane, "Placement"):
                obj.Placement = self.working_plane.Placement
            else:
                obj.Placement = self.working_plane
        
        obj.touch()
        obj.Document.recompute([obj])
        self._on_committed(obj)
        # Print accumulated timer summary now that the tool is accepted
        primitive_name = type(self).__name__.replace("Creator", "")
        mesh_timer.summary(f"{primitive_name} preview ({_PREVIEW_CELL_SIZE}mm)")
        self._preview_obj = None  # Severed; the object is now the user's

    def _commit_and_enter_edit(self, name):
        """Commit creation at full resolution, then immediately enter edit mode on the result."""
        field = self._get_final_field()
        points = self._get_final_points()
        if field is None:
            self.terminate()
            return

        # Pre-enter edit state so handle_move doesn't clobber tool vars during async delay
        self._is_editing = True
        self.state = ToolState.IDLE

        QtCore.QTimer.singleShot(0, lambda: self._do_commit_and_edit(name, field, points))

    def _do_commit_and_edit(self, name, field, points):
        """Async: commit the object at full resolution then switch to edit mode on it."""
        if getattr(self, "_terminated", False):
            return

        obj = self._preview_obj
        if obj is None or not obj.Document:
            obj = create_dm_object(name=name, shape_type="sdf")

        try:
            obj.Label = name
        except Exception:
            pass

        if points is not None:
            try:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                obj.Points = points
            except Exception:
                pass

        primitive_name = type(self).__name__.replace("Creator", "")
        obj.Proxy.SdfField = field
        obj.touch()
        obj.Document.recompute([obj])
        mesh_timer.summary(f"{primitive_name} → edit mode")

        # Clear creation visuals before entering edit mode
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()
        if self.dm_line_set:
            self.dm_line_set.undraw()
            self.dm_line_set = None

        # Enter edit mode on the committed object
        self.edit_object(obj)
        FreeCADGui.updateGui()
        if self.view:
            self.view.redraw()

    def reset_state(self):
        """Override to clear internal primitive state (points, visuals)."""
        super().reset_state()
        self.state = ToolState.IDLE  # Re-enable dynamic snapping
        self.points = []
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()
        if self.dm_line_set:
            self.dm_line_set.undraw()
            self.dm_line_set = None
        self.view.redraw()

    def _create_sdf_object(self, name, field, points=None):
        """Helper to create the FreeCAD object and assign the field (for 1-shot creation)."""
        obj = create_dm_object(name=name, shape_type="sdf")
        obj.Proxy.SdfField = field
        if points is not None:
            if not hasattr(obj, "Points"):
                obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
            obj.Points = points
        obj.touch()
        return obj


    def _do_terminate(self):
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()
        if self.dm_line_set:
            self.dm_line_set.undraw()
        try:
            if self.view and self.view.getSceneGraph() and self.points_root:
                self.view.getSceneGraph().removeChild(self.points_root)
        except Exception as e:
            dm_logger.debug(f"PrimitiveCreatorBase._do_terminate: {e}")
        super()._do_terminate()


class BoxCreator(PrimitiveCreatorBase):

    def get_command_id(self):
        return "DM_CreateBox"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_base = None

        dm_logger.info("Box Tool: Click 1st corner")

    def edit_object(self, obj):
        super().edit_object(obj)  # loads self.points = 8 world corners
        corners = list(getattr(self, "points", []))
        if len(corners) != 8:
            dm_logger.warning(f"BoxCreator.edit_object: expected 8 corners, got {len(corners)}")
            return
        self.points = corners  # keep all 8 for 8-handle drag
        self.current_point = None
        self.state = ToolState.IDLE

        r = self._compute_handle_radius()
        for pt in corners:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r, color=(1.0, 0.5, 0.0))
            self.dm_points.append(dm_pt)
        self.update_ui()

    def _drag_update(self):
        """8-corner drag: drag one corner while the opposite is fixed."""
        if self._drag_check_lmb_released():
            self._edit_sel_idx = None
            return
        if self._edit_sel_idx is None:
            self._stop_drag_timer()
            return

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        new_world = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos},
            self._edit_drag_n, self._edit_drag_o,
            place_on_geometry=False
        )
        if new_world is None:
            return

        fixed_world = self.points[_BOX_OPPOSITE[self._edit_sel_idx]]
        new_field = self._field_from_two_corners(new_world, fixed_world)
        self._refresh_edit_corners(new_field)

        r = self._compute_handle_radius()
        for i, pt in enumerate(self.points):
            self.dm_points[i].position = pt
            self.dm_points[i].update_draw(radius=r)

        self._apply_preview_field(new_field)
        self._update_pending = False
        if self.view:
            self.view.redraw()

    def _field_from_two_corners(self, corner_a_world, corner_b_world):
        """Rebuild SdfBoxField from two opposite world corners."""
        wp = self.working_plane
        if wp:
            inv = wp.inverse()
            lc_a = inv.multVec(corner_a_world)
            lc_b = inv.multVec(corner_b_world)
        else:
            lc_a, lc_b = corner_a_world, corner_b_world
        cx = (lc_a.x + lc_b.x) / 2.0
        cy = (lc_a.y + lc_b.y) / 2.0
        cz = (lc_a.z + lc_b.z) / 2.0
        sx = max(abs(lc_a.x - lc_b.x), 0.1)
        sy = max(abs(lc_a.y - lc_b.y), 0.1)
        sz = max(abs(lc_a.z - lc_b.z), 0.1)
        placement = getattr(self, "_field_placement", self._get_placement())
                
        return SdfBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(sx, sy, sz), placement=placement)

    def _refresh_edit_corners(self, field):
        """Update self.points (8 world corners) from a new SdfBoxField."""
        c, h = field.center, field.half_size
        wp = field.placement
        pts_local = self._box_corners_local(c, h)
        if wp:
            self.points = [wp.multVec(lc) for lc in pts_local]
        else:
            self.points = pts_local

    def on_button1_down(self, event_dict):
        dm_logger.debug(f"BoxCreator.on_button1_down: state={self.state} editing={self._is_editing}")
        if self._is_editing:
            return self._edit_on_mouse_press(event_dict)

        skip = [self._preview_obj] if self._preview_obj else None
        pos = self._resolve_wp_click(event_dict, skip_objects=skip)
        dm_logger.debug(f"BoxCreator.on_button1_down: pos={pos}")

        if pos is None:
            return True

        if self.state == ToolState.IDLE:
            # 1st click - anchor the tool
            self._field_placement = self._get_placement()
            self.points.append(pos)
            self.state = ToolState.ACTIVE
            if len(self.dm_points) < 1:
                dm_pt = DMPoint(pos)
                dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
                self.dm_points.append(dm_pt)
            else:
                self.dm_points[0].position = pos
                self.dm_points[0].update_draw()

            dm_logger.info("Box Tool: Click 2nd corner")
        elif self.state == ToolState.ACTIVE:
            # 2nd click - determines base size (x/y)
            self.points.append(pos)
            self.state = ToolState.DRAGGING
            if len(self.dm_points) < 2:
                dm_pt = DMPoint(pos)
                dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
                self.dm_points.append(dm_pt)
            else:
                self.dm_points[1].position = pos
                self.dm_points[1].update_draw()
            self._height_drag_base = pos
            dm_logger.info("Box Tool: Click height")
            
        elif self.state == ToolState.DRAGGING:
            # 3rd click - determines height (z). Finalize shape.
            self.points.append(self.current_point)
            if self.current_point:
                dm_pt = DMPoint(self.current_point)
                dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=self.current_point))
                self.dm_points.append(dm_pt)

            # Transition to a finalized state or finish tool
            self.state = ToolState.FINALIZED
            self._commit_and_enter_edit("Box")

        return True

    def _update_ghost_visuals(self):
        if not self.points or self.current_point is None:
            return

        # 1. Coordinate calculation
        loc_p1 = self.to_local(self.points[0])
        if len(self.points) == 1:
            loc_cur = self.to_local(self.current_point)
            loc_p2 = FreeCAD.Vector(loc_cur.x, loc_cur.y, loc_p1.z)
            loc_p3 = loc_p1
        else:
            loc_p2 = self.to_local(self.points[1])
            loc_p3 = self.to_local(self.current_point)

        size_x = abs(loc_p2.x - loc_p1.x)
        size_y = abs(loc_p2.y - loc_p1.y)
        size_z = abs(loc_p3.z - loc_p1.z)
        cx = (loc_p1.x + loc_p2.x) / 2.0
        cy = (loc_p1.y + loc_p2.y) / 2.0
        cz = (loc_p1.z + loc_p3.z) / 2.0

        pts_local = self._box_corners_local(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(size_x/2, size_y/2, size_z/2))
        world_corners = [self.to_global(pt) for pt in pts_local]

        # 2. Update lines
        if self.dm_line_set is None:
            self.dm_line_set = DMLineSet(self.points_root, color=(1.0, 0.6, 0.2), pattern=0x0F0F)
        
        # 12 edges of a box
        edges = [
            (0,1), (1,2), (2,3), (3,0), # Bottom
            (4,5), (5,6), (6,7), (7,4), # Top
            (0,4), (1,5), (2,6), (3,7)  # Verticals
        ]
        line_pts = []
        for i, j in edges:
            line_pts.extend([world_corners[i], world_corners[j]])
        
        self.dm_line_set.update_lines(line_pts, segments=[2]*12)

        # 3. Update corner balls
        self._update_handle_positions(world_corners)

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_plane_pt(event_dict)

    def on_move_state_2(self, event_dict):
        self._height_drag_move(event_dict)

    def _get_preview_field(self):
        if not self.points or self.current_point is None:
            return None
        if len(self.points) == 1:
            # Footprint preview: hold local Z constant at first point
            loc_p1 = self.to_local(self.points[0])
            loc_cur = self.to_local(self.current_point)
            # clamp local Z so footprint stays flat on the workplane
            loc_p2 = FreeCAD.Vector(loc_cur.x, loc_cur.y, loc_p1.z)
            p2 = self.to_global(loc_p2)
            return self._make_field(self.points[0], p2, self.points[0])
        elif len(self.points) == 2:
            return self._make_field(self.points[0], self.points[1], self.current_point)
        return None

    def _get_final_field(self):
        pts = list(self.points)
        if self.current_point and len(pts) < 3:
            while len(pts) < 3:
                pts.append(self.current_point)
        if len(pts) == 3:
            return self._make_field(*pts)
        return None

    def _make_field(self, p1, p2, p3):
        wp = getattr(self, "working_plane", None)
        
        loc_p1 = self.to_local(p1)
        loc_p2 = self.to_local(p2)
        loc_p3 = self.to_local(p3)
        
        size_x = abs(loc_p2.x - loc_p1.x)
        size_y = abs(loc_p2.y - loc_p1.y)
        # Height = distance of 3rd point along local Z from the base plane (local Z midpoint of p1 and p3)
        size_z = abs(loc_p3.z - loc_p1.z)
        if size_x < 0.01 or size_y < 0.01:
            return None
            
        cx = (loc_p1.x + loc_p2.x) / 2.0
        cy = (loc_p1.y + loc_p2.y) / 2.0
        cz = (loc_p1.z + loc_p3.z) / 2.0
        
        placement = getattr(self, "_field_placement", self._get_placement())
                
        return SdfBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(size_x, size_y, max(size_z, 0.01)), placement=placement)

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        loc_p1 = self.to_local(self.points[0])
        loc_p2 = self.to_local(self.points[1])
        loc_p3 = self.to_local(self.points[2])
        
        size_x = abs(loc_p2.x - loc_p1.x)
        size_y = abs(loc_p2.y - loc_p1.y)
        size_z = abs(loc_p3.z - loc_p1.z)
        cx = (loc_p1.x + loc_p2.x) / 2.0
        cy = (loc_p1.y + loc_p2.y) / 2.0
        cz = (loc_p1.z + loc_p3.z) / 2.0
        
        pts_local = self._box_corners_local(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(size_x/2, size_y/2, size_z/2))
        return pts_local


class SphereCreator(PrimitiveCreatorBase):

    def get_command_id(self):
        return "DM_CreateSphere"

    def __init__(self):
        super().__init__()
        self.center = None
        self.current_point = None

        dm_logger.info("Sphere Tool: Click center")

    def edit_object(self, obj):
        super().edit_object(obj)
        pts = list(getattr(self, "points", []))
        if pts:
            self.center = pts[0]
            self.current_point = pts[1] if len(pts) >= 2 else pts[0] + FreeCAD.Vector(10, 0, 0)
        self.state = ToolState.IDLE

        r = self._compute_handle_radius()
        for pt in [self.center, self.current_point]:
            if pt is not None:
                dm_pt = DMPoint(pt)
                dm_pt.draw_point(self.points_root, r)
                self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    def _sync_edit_points(self):
        if len(self.dm_points) >= 1:
            self.center = self.dm_points[0].position
        if len(self.dm_points) >= 2:
            self.current_point = self.dm_points[1].position

    def on_button1_down(self, event_dict):
        if self._is_editing:
            return self._edit_on_mouse_press(event_dict)

        skip = [self._preview_obj] if self._preview_obj else None
        pos = self._resolve_wp_click(event_dict, skip_objects=skip)

        if pos is None:
            return True

        if self.state == ToolState.IDLE:
            self.center = pos
            self.state = ToolState.ACTIVE

            if len(self.dm_points) < 1:
                dm_pt = DMPoint(pos)
                dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
                self.dm_points.append(dm_pt)
            else:
                self.dm_points[0].position = pos
                self.dm_points[0].update_draw()

            dm_logger.info("Sphere Tool: Click radius")
        elif self.state == ToolState.ACTIVE:
            self.state = ToolState.FINALIZED
            self._commit_and_enter_edit("Sphere")

        return True

    def _update_ghost_visuals(self):
        if self.center is None or self.current_point is None:
            return

        import math
        loc_center = self.to_local(self.center)
        loc_cur = self.to_local(self.current_point)
        radius = (loc_cur - loc_center).Length
        if radius < 0.1:
            return

        # Draw 3 orthogonal circles
        segments = 32
        line_pts = []
        for axis in [0, 1, 2]: # X, Y, Z planes
            circle_pts = []
            for i in range(segments + 1):
                angle = 2 * math.pi * i / segments
                if axis == 0: # YZ plane
                    p = FreeCAD.Vector(0, radius * math.cos(angle), radius * math.sin(angle))
                elif axis == 1: # XZ plane
                    p = FreeCAD.Vector(radius * math.cos(angle), 0, radius * math.sin(angle))
                else: # XY plane
                    p = FreeCAD.Vector(radius * math.cos(angle), radius * math.sin(angle), 0)
                circle_pts.append(self.to_global(loc_center + p))
            line_pts.extend(circle_pts)

        if self.dm_line_set is None:
            self.dm_line_set = DMLineSet(self.points_root, color=(1.0, 0.6, 0.2), pattern=0x0F0F)
        self.dm_line_set.update_lines(line_pts, segments=[segments+1]*3)

        # Update points (center and one radius point)
        self._update_handle_positions([self.center, self.current_point])

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_plane_pt(event_dict)

    def _get_preview_field(self):
        if self.center is None or self.current_point is None:
            return None
        
        # Calculate radius in local space
        loc_center = self.to_local(self.center)
        loc_current = self.to_local(self.current_point)
        radius = (loc_current - loc_center).Length
        
        if radius < 0.01:
            return None
        placement = self._get_placement()
                
        return SdfSphereField(loc_center, radius, placement=placement)

    def _get_final_field(self):
        return self._get_preview_field()

    def _get_final_points(self):
        if self.center is None or self.current_point is None:
            return None
        # We need local coordinates
        loc_center = self.to_local(self.center)
        loc_current = self.to_local(self.current_point)
        radius = (loc_current - loc_center).Length
        return [loc_center, loc_center + FreeCAD.Vector(radius, 0, 0)]


class CylinderCreator(PrimitiveCreatorBase):
    _last_working_plane = None
    _last_wp_is_fallback = True


    def get_command_id(self):
        return "DM_CreateCylinder"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_base = None
        self.state = ToolState.IDLE
        
        dm_logger.info("Cylinder Tool: Click base center")

    def edit_object(self, obj):
        super().edit_object(obj)
        # self.points from super() = [base_center, radius_pt, height_pt]
        pts = list(getattr(self, "points", []))
        if len(pts) >= 3:
            self.current_point = pts[2]
            self.points = pts[:2]
        elif len(pts) >= 2:
            self.current_point = pts[1]
            self.points = pts[:1]
        self.state = ToolState.IDLE # In edit mode, we are 'idle' relative to creation steps

        r = self._compute_handle_radius()
        all_pts = list(self.points) + ([self.current_point] if self.current_point else [])
        for pt in all_pts:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r)
            self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    def _sync_edit_points(self):
        if len(self.dm_points) >= 1 and len(self.points) >= 1:
            self.points[0] = self.dm_points[0].position
        if len(self.dm_points) >= 2 and len(self.points) >= 2:
            self.points[1] = self.dm_points[1].position
        if len(self.dm_points) >= 3:
            self.current_point = self.dm_points[2].position

    def on_button1_down(self, event_dict):
        if self._is_editing:
            return self._edit_on_mouse_press(event_dict)

        skip = [self._preview_obj] if self._preview_obj else None
        pos = self._resolve_wp_click(event_dict, skip_objects=skip)

        if pos is None:
            return True

        if self.state == ToolState.IDLE:
            self._field_placement = self._get_placement()
            type(self)._last_working_plane = self.working_plane
            type(self)._last_wp_is_fallback = self._working_plane_is_fallback

            self.points.append(pos)
            self.state = ToolState.PICK_RADIUS

            dm_pt = DMPoint(pos)
            dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
            self.dm_points.append(dm_pt)

            dm_logger.info("Cylinder Tool: Click radius")
        elif self.state == ToolState.PICK_RADIUS:
            self.points.append(pos)
            self.state = ToolState.PICK_HEIGHT
            self._height_drag_base = pos

            dm_pt = DMPoint(pos)
            dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
            self.dm_points.append(dm_pt)

            dm_logger.info("Cylinder Tool: Click height")
        elif self.state == ToolState.PICK_HEIGHT:
            # 3rd click - determines height. Finalize shape.
            self.points.append(self.current_point)
            self._finalize_object("Cylinder")

        return True

    def _update_ghost_visuals(self):
        if not self.points or self.current_point is None:
            return

        import math
        loc_base = self.to_local(self.points[0])
        loc_cur = self.to_local(self.current_point)
        
        if self.state == ToolState.PICK_RADIUS:
            radius = math.sqrt((loc_cur.x - loc_base.x)**2 + (loc_cur.y - loc_base.y)**2)
            height = 0.1
        else:
            loc_p1 = self.to_local(self.points[1])
            radius = math.sqrt((loc_p1.x - loc_base.x)**2 + (loc_p1.y - loc_base.y)**2)
            height = (loc_cur - loc_base).z

        if radius < 0.1:
            return

        segments = 32
        line_pts = []
        counts = []
        
        # Base and Top circles
        for z in [0, height]:
            circle = []
            for i in range(segments + 1):
                angle = 2 * math.pi * i / segments
                p = FreeCAD.Vector(radius * math.cos(angle), radius * math.sin(angle), z)
                circle.append(self.to_global(loc_base + p))
            line_pts.extend(circle)
            counts.append(segments + 1)
        
        # 4 Vertical ribs
        for angle in [0, math.pi/2, math.pi, 3*math.pi/2]:
            p1 = FreeCAD.Vector(radius * math.cos(angle), radius * math.sin(angle), 0)
            p2 = FreeCAD.Vector(radius * math.cos(angle), radius * math.sin(angle), height)
            line_pts.extend([self.to_global(loc_base + p1), self.to_global(loc_base + p2)])
            counts.append(2)

        if self.dm_line_set is None:
            self.dm_line_set = DMLineSet(self.points_root, color=(1.0, 0.6, 0.2), pattern=0x0F0F)
        self.dm_line_set.update_lines(line_pts, segments=counts)

        # Update points
        pts = list(self.points) + [self.current_point]
        self._update_handle_positions(pts)

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_plane_pt(event_dict)

    def on_move_state_2(self, event_dict):
        self._height_drag_move(event_dict)

    def _get_preview_field(self):
        if not self.points or self.current_point is None:
            return None
            
        wp = getattr(self, "working_plane", None)
        loc_base = self.to_local(self.points[0])
        loc_current = self.to_local(self.current_point)
        
        # Axis in local space is always Z (0,0,1) for this tool's logic
        loc_axis = FreeCAD.Vector(0, 0, 1)
        
        if self.state == ToolState.PICK_RADIUS:
            # Radius is distance in local XY plane
            radius = math.sqrt((loc_current.x - loc_base.x)**2 + (loc_current.y - loc_base.y)**2)
            height = 1.0  # minimal placeholder
        else:
            loc_p1 = self.to_local(self.points[1])
            radius = math.sqrt((loc_p1.x - loc_base.x)**2 + (loc_p1.y - loc_base.y)**2)
            height = (loc_current - loc_base).z # Project onto local Z
            
        if radius < 0.01:
            return None
            
        # Ensure height isn't exactly zero to avoid SDF singularities
        if abs(height) < 0.01:
            height = 0.01 if height >= 0 else -0.01
            
        field_placement = getattr(self, "_field_placement", self._get_placement())
                
        return SdfCylinderField(loc_base, loc_axis, radius, height, placement=field_placement)

    def _get_final_field(self):
        pts = list(self.points)
        if self.current_point and len(pts) < 3:
            while len(pts) < 3:
                pts.append(self.current_point)
        if len(pts) < 3:
            return None
            
        wp = getattr(self, "working_plane", None)
        loc_base = self.to_local(pts[0])
        loc_p_rad = self.to_local(pts[1])
        loc_p_height = self.to_local(pts[2])
        
        loc_axis = FreeCAD.Vector(0, 0, 1)
        radius = math.sqrt((loc_p_rad - loc_base).x**2 + (loc_p_rad - loc_base).y**2)
        height = (loc_p_height - loc_base).z
        
        if abs(height) < 0.01:
            height = 0.01 if height >= 0 else -0.01
            
        field_placement = getattr(self, "_field_placement", self._get_placement())
                
        return SdfCylinderField(loc_base, loc_axis, radius, height, placement=field_placement)

    def _get_final_points(self):
        pts = list(self.points)
        if self.current_point and len(pts) < 3:
            while len(pts) < 3:
                pts.append(self.current_point)
        if len(pts) < 3:
            return None
        return pts
