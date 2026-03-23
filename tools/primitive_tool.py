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
        self._dragging_idx = None
        self._drag_plane_n = None
        self._drag_plane_o = None
        
        self._is_editing = False
        self._edit_pivot = None
        self._edit_last_angle = 0.0
        self._edit_is_rotating = False

        # Unified workplane pre-load logic
        self._init_working_plane()

    def get_handled_types(self):
        return ["sdf"]

    def to_lattice(self):
        """Called by the edit lattice tool."""
        dm_logger.debug("lattice tool called on shaped type")

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
        
        idx, _ = self._hit_test_perp(ray_p, ray_d, self.points)
        if idx is not None:
            self._dragging_idx = idx
            self._drag_plane_n = FreeCAD.Vector(-self.view.getViewDirection())
            self._drag_plane_o = self.points[idx]
            self._edit_is_rotating = (event_dict.get("Modifiers") == QtCore.Qt.ShiftModifier)
            if self._edit_is_rotating:
                # Pivot is center of all points
                self._edit_pivot = sum(self.points, FreeCAD.Vector()) / len(self.points)
                v = self.points[idx] - self._edit_pivot
                self._edit_last_angle = math.atan2(v.y, v.x)
            self._start_drag_timer()
            return True
        return False

    def _drag_update(self):
        """QTimer callback: move the selected handle to the current mouse position."""
        if self._drag_check_lmb_released():
            return
        if self._dragging_idx is None:
            return

        from PySide import QtGui
        mods = QtGui.QApplication.keyboardModifiers()
        is_ctrl = bool(mods & QtCore.Qt.ControlModifier)
        is_shift = bool(mods & QtCore.Qt.ShiftModifier)

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        new_pt = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos}, self._drag_plane_n, self._drag_plane_o,
            place_on_geometry=False
        )
        if new_pt:
            if is_ctrl:
                delta = new_pt - self.points[self._dragging_idx]
                self.points = [p + delta for p in self.points]
                # Sync working plane so local coordinates stay stable
                if self.working_plane:
                    self.working_plane.Base += delta
            elif is_shift and self._edit_pivot:
                v = new_pt - self._edit_pivot
                angle = math.atan2(v.y, v.x)
                da = angle - self._edit_last_angle
                rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), math.degrees(da))
                self.points = [self._edit_pivot + rot.multVec(p - self._edit_pivot) for p in self.points]
                self._edit_last_angle = angle
                # Sync working plane rotation
                if self.working_plane:
                    self.working_plane.Rotation = self.working_plane.Rotation.multiply(rot)
            else:
                self.points[self._dragging_idx] = new_pt

            # Always sync dm_point visuals and update SDF after any drag branch
            self._sync_edit_points()
            self._update_handle_positions(self.points)
            self.update_preview()
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
            self._edit_pivot = None
            self._edit_last_angle = 0.0
            self._edit_is_rotating = False
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
                if hasattr(wp, "getGlobalPlacement"):
                    self.working_plane = wp.getGlobalPlacement()
                elif hasattr(wp, "Placement"):
                    self.working_plane = wp.Placement
                else:
                    self.working_plane = wp
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

    def _compute_default_size(self):
        """Return a world-space length (~15% of viewport height) used to size the initial spawned primitive."""
        try:
            from pivy import coin
            cam = self.view.getCameraNode()
            if isinstance(cam, coin.SoOrthographicCamera):
                h = cam.height.getValue()
            else:
                import math
                h = 2.0 * cam.focalDistance.getValue() * math.tan(cam.heightAngle.getValue() / 2.0)
            size = h * 0.15
        except Exception:
            size = 200.0
        return max(5.0, min(size, 500.0))

    def _spawn_default_primitive(self, click_pt):
        """Spawn a small complete primitive at click_pt, then immediately enter edit mode.

        Subclasses must:
          1. Compute N world-space handle positions from click_pt + _compute_default_size()
          2. Populate self.points with those N positions
          3. Draw N DMPoint handles + optional wire frame
          4. Set self.state = ToolState.FINALIZED
          5. Call self._commit_and_enter_edit("PrimitiveName")
        """
        pass

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
        """Default implementation: returns self.points mapped to local space.
        
        This ensures that 'obj.Points' always stores coordinates in the field's
        local coordinate system (relative to obj.Placement / self.working_plane).
        """
        if not self.points:
            return None
            
        # Re-map stored world points to local space using the locked working plane
        return [self.to_local(p) for p in self.points]

    def update_preview(self):
        """Called on every mouse move by DMBase.handle_move. Updates the live mesh."""
        if getattr(self, "_terminated", False):
            return
        # In edit mode, use the edit-mode field builder which reads from self.points
        if getattr(self, "_is_editing", False):
            field = self._get_edit_preview_field()
        else:
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
            # Note: IsSubtractive toggle is now handled by 'Z' key, not Ctrl-drag.

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
            # We must be careful not to trigger recursive recomputes here if we are
            # already in a recompute loop.
            dm_logger.debug(f"Committing {name}: {len(points) if points else 0} points, placement={self.working_plane}")
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

        # Set placement BEFORE calling edit_object so edit_object reads the correct
        # placement and reconstructs world-space handle positions from local obj.Points.
        if self.working_plane:
            if hasattr(self.working_plane, "getGlobalPlacement"):
                obj.Placement = self.working_plane.getGlobalPlacement()
            elif hasattr(self.working_plane, "Placement"):
                obj.Placement = self.working_plane.Placement
            else:
                obj.Placement = self.working_plane

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

    def handle_keyboard(self, event_dict):
        key = event_dict.get("Key")
        if self._is_editing and key == QtCore.Qt.Key_Z:
            if self._preview_obj:
                cur = getattr(self._preview_obj, "IsSubtractive", False)
                self._preview_obj.IsSubtractive = not cur
                color = (0.2, 0.6, 1.0) if not cur else (1.0, 0.5, 0.0)
                for dp in self.dm_points:
                    dp.set_color(color)
            return True
        return False

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
        if self._drag_check_lmb_released():
            return
        if self._dragging_idx is None:
            return

        from PySide import QtGui
        mods = QtGui.QApplication.keyboardModifiers()
        is_ctrl = bool(mods & QtCore.Qt.ControlModifier)
        is_shift = bool(mods & QtCore.Qt.ShiftModifier)

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        new_pt = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos}, self._drag_plane_n, self._drag_plane_o,
            place_on_geometry=False
        )
        if new_pt:
            if is_ctrl:
                delta = new_pt - self.points[self._dragging_idx]
                self.points = [p + delta for p in self.points]
                if self.working_plane:
                    self.working_plane.Base += delta
            elif is_shift:
                # Rotate working plane
                pivot = sum(self.points, FreeCAD.Vector()) / len(self.points)
                v = new_pt - pivot
                angle = math.atan2(v.y, v.x)
                if not hasattr(self, "_edit_last_angle") or self._edit_last_angle == 0: 
                    self._edit_last_angle = angle
                da = angle - self._edit_last_angle
                rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), math.degrees(da))
                self.working_plane.Rotation = self.working_plane.Rotation.multiply(rot)
                # Also rotate the points in world space to keep them consistent with handles
                self.points = [pivot + rot.multVec(p - pivot) for p in self.points]
                self._edit_last_angle = angle
            else:
                idx = self._dragging_idx
                self.points[idx] = new_pt
                opp_idx = _BOX_OPPOSITE[idx]
                fixed_pt = self.points[opp_idx]
                
                # Re-calculate all 8 corners based on the 2 diagonal ones
                wp = self.working_plane
                if wp is not None:
                    inv = wp.inverse()
                    loc1 = inv.multVec(new_pt)
                    loc2 = inv.multVec(fixed_pt)
                else:
                    loc1, loc2 = new_pt, fixed_pt
                
                half = FreeCAD.Vector(abs(loc1.x - loc2.x)/2.0, abs(loc1.y - loc2.y)/2.0, abs(loc1.z - loc2.z)/2.0)
                center = FreeCAD.Vector((loc1.x + loc2.x)/2.0, (loc1.y + loc2.y)/2.0, (loc1.z + loc2.z)/2.0)
                
                pts_local = self._box_corners_local(center, half)
                if wp is not None:
                    self.points = [wp.multVec(p) for p in pts_local]
                else:
                    self.points = pts_local

            self._update_handle_positions(self.points)
            if self.dm_line_set:
                edges = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]
                line_pts = []
                for i, j in edges: line_pts.extend([self.points[i], self.points[j]])
                self.dm_line_set.update_lines(line_pts, segments=[2]*12)
            self.update_preview()
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
        if self._is_editing:
            return self._edit_on_mouse_press(event_dict)

        skip = [self._preview_obj] if self._preview_obj else None
        pos = self._resolve_wp_click(event_dict, skip_objects=skip)
        if pos is None:
            return True

        if self.state == ToolState.IDLE:
            self._spawn_default_primitive(pos)
        return True

    def _get_final_field(self):
        if len(self.points) < 8:
            return None
        return self._field_from_two_corners(self.points[0], self.points[6])

    def _spawn_default_primitive(self, click_pt):
        s = self._compute_default_size() / 2.0  # half-size
        self._field_placement = self._get_placement()
        loc_center = self.to_local(click_pt)
        loc_center.z += s  # Place the base on the workplane instead of centering
        half = FreeCAD.Vector(s, s, s)
        pts_local = self._box_corners_local(loc_center, half)
        world_corners = [self.to_global(lc) for lc in pts_local]
        self.points = list(world_corners)

        r = self._compute_handle_radius(ref_pt=click_pt)
        for pt in world_corners:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r, color=(1.0, 0.5, 0.0))
            self.dm_points.append(dm_pt)

        if self.dm_line_set is None:
            self.dm_line_set = DMLineSet(self.points_root, color=(1.0, 0.6, 0.2), pattern=0x0F0F)
        edges = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]
        line_pts = []
        for i, j in edges:
            line_pts.extend([world_corners[i], world_corners[j]])
        self.dm_line_set.update_lines(line_pts, segments=[2]*12)

        self.state = ToolState.FINALIZED
        self._commit_and_enter_edit("Box")

    def _get_final_points(self):
        if len(self.points) < 8:
            return None
        wp = self.working_plane
        if wp is not None:
            inv = wp.inverse()
            return [inv.multVec(p) for p in self.points]
        return list(self.points)

    def _get_edit_preview_field(self):
        if len(self.points) < 8:
            return None
        return self._field_from_two_corners(self.points[0], self.points[6])


class SphereCreator(PrimitiveCreatorBase):

    def get_command_id(self):
        return "DM_CreateSphere"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None

        dm_logger.info("Sphere Tool: Click center")

    def edit_object(self, obj):
        super().edit_object(obj) # loads self.points from obj.Points
        if not self.points:
            # Reconstruct fallback if Points property is empty
            field = getattr(obj.Proxy, "SdfField", None)
            if field:
                loc_c = field.center
                r = field.radius if hasattr(field, "radius") else 10.0
                self.points = [self.to_global(loc_c), self.to_global(loc_c + FreeCAD.Vector(r, 0, 0))]
        
        self.state = ToolState.IDLE

        r = self._compute_handle_radius()
        for pt in self.points:
            if pt is not None:
                dm_pt = DMPoint(pt)
                dm_pt.draw_point(self.points_root, r)
                self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    def _sync_edit_points(self):
        for i in range(len(self.dm_points)):
            if i < len(self.points):
                self.points[i] = self.dm_points[i].position

    def _spawn_default_primitive(self, click_pt):
        r = self._compute_default_size() * 0.3
        loc_center = self.to_local(click_pt)
        radius_pt = self.to_global(loc_center + FreeCAD.Vector(r, 0, 0))
        self.points = [click_pt, radius_pt]

        r_handle = self._compute_handle_radius(ref_pt=click_pt)
        for pt in self.points:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r_handle, color=(1.0, 0.5, 0.0))
            self.dm_points.append(dm_pt)

        self.state = ToolState.FINALIZED
        self._commit_and_enter_edit("Sphere")

    def _get_edit_preview_field(self):
        if len(self.points) < 2:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        radius = (loc_r - loc_c).Length
        if radius < 0.01:
            return None
        return SdfSphereField(loc_c, radius, placement=self._get_placement())

    def on_button1_down(self, event_dict):
        if self._is_editing:
            return self._edit_on_mouse_press(event_dict)

        skip = [self._preview_obj] if self._preview_obj else None
        pos = self._resolve_wp_click(event_dict, skip_objects=skip)
        if pos is None:
            return True

        if self.state == ToolState.IDLE:
            self._spawn_default_primitive(pos)
        return True

    def _update_ghost_visuals(self):
        if not self.points or self.current_point is None:
            return
        
        center = self.points[0]
        import math
        loc_center = self.to_local(center)
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
        self._update_handle_positions([self.points[0], self.current_point])

    def _get_final_field(self):
        """Build SdfSphereField from self.points[0] (center) and self.points[1] (radius pt)."""
        if len(self.points) < 2:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        radius = (loc_r - loc_c).Length
        if radius < 0.01:
            return None
        return SdfSphereField(loc_c, radius, placement=self._get_placement())

    def _get_final_points(self):
        """Use default to save the 2 original points (center and radius-defining point)."""
        pts = list(self.points)
        return [self.to_local(p) for p in pts]


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
        # self.points from super() = [base_center, radius_pt, height_pt] (all 3)
        pts = list(getattr(self, "points", []))
        # Keep all 3 points in self.points so _get_edit_preview_field can read points[2]
        if len(pts) >= 3:
            self.current_point = pts[2]
            self.points = pts  # all 3
        elif len(pts) >= 2:
            self.current_point = pts[1]
            self.points = pts
        self.state = ToolState.IDLE # In edit mode, we are 'idle' relative to creation steps

        r = self._compute_handle_radius()
        for pt in self.points:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r)
            self.dm_points.append(dm_pt)
        self.update_preview()
        self.update_ui()

    def _sync_edit_points(self):
        # Sync all dm_point positions back into self.points (all 3 for cylinder)
        for i in range(len(self.dm_points)):
            if i < len(self.points):
                self.points[i] = self.dm_points[i].position
            elif i == len(self.points):
                # Extend self.points if dm_points has more entries
                self.points.append(self.dm_points[i].position)

    def _spawn_default_primitive(self, click_pt):
        s = self._compute_default_size()
        r = s * 0.3
        h = s * 0.6
        self._field_placement = self._get_placement()
        loc_base = self.to_local(click_pt)
        radius_pt = self.to_global(loc_base + FreeCAD.Vector(r, 0, 0))
        height_pt = self.to_global(loc_base + FreeCAD.Vector(0, 0, h))
        self.points = [click_pt, radius_pt, height_pt]
        self.current_point = height_pt  # needed by _get_final_field fallback

        r_handle = self._compute_handle_radius(ref_pt=click_pt)
        for pt in self.points:
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, r_handle, color=(1.0, 0.5, 0.0))
            self.dm_points.append(dm_pt)

        self.state = ToolState.FINALIZED
        self._commit_and_enter_edit("Cylinder")

    def _get_edit_preview_field(self):
        if len(self.points) < 3:
            return None
        import math
        loc_base = self.to_local(self.points[0])
        loc_rad  = self.to_local(self.points[1])
        loc_h    = self.to_local(self.points[2])
        radius = math.sqrt((loc_rad.x - loc_base.x)**2 + (loc_rad.y - loc_base.y)**2)
        height = loc_h.z - loc_base.z
        if radius < 0.01:
            return None
        if abs(height) < 0.01:
            height = 0.01 if height >= 0 else -0.01
        fp = getattr(self, "_field_placement", self._get_placement())
        return SdfCylinderField(loc_base, FreeCAD.Vector(0, 0, 1), radius, height, placement=fp)

    def on_button1_down(self, event_dict):
        if self._is_editing:
            return self._edit_on_mouse_press(event_dict)

        skip = [self._preview_obj] if self._preview_obj else None
        pos = self._resolve_wp_click(event_dict, skip_objects=skip)
        if pos is None:
            return True

        if self.state == ToolState.IDLE:
            self._spawn_default_primitive(pos)
        return True

    def _get_final_field(self):
        if len(self.points) < 3:
            return None
        import math
        loc_base = self.to_local(self.points[0])
        loc_rad = self.to_local(self.points[1])
        loc_h = self.to_local(self.points[2])
        radius = math.sqrt((loc_rad.x - loc_base.x)**2 + (loc_rad.y - loc_base.y)**2)
        height = loc_h.z - loc_base.z
        if abs(height) < 0.01:
            height = 0.01 if height >= 0 else -0.01
        fp = getattr(self, "_field_placement", self._get_placement())
        return SdfCylinderField(loc_base, FreeCAD.Vector(0, 0, 1), radius, height, placement=fp)

    def _get_final_points(self):
        return [self.to_local(p) for p in self.points]
