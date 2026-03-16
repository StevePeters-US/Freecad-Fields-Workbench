import FreeCAD
from PySide import QtCore
from pivy import coin
from core import dm_logger
from core.input_manager import DMInputManager
from core.dm_point import DMPoint
from core.dm_line import DMLineSet
from core.dm_object import create_dm_object, get_meshing_cell_size, get_interactive_throttle_interval
from core.dm_mesher import mesh_timer
from tools.dm_base import DMBase

# Ensure we import the right storage classes. For now, defaulting to MarchingCubes
from core.frep.sdf.box import SdfBoxField
from core.frep.sdf.sphere import SdfSphereField
from core.frep.sdf.cylinder import SdfCylinderField

# Cell size for interactive preview in mm (larger = faster updates)
_PREVIEW_CELL_SIZE = 20.0
# Cell size for final committed mesh (smaller = more detail)
def _get_final_cell_size():
    return get_meshing_cell_size()


class PrimitiveCreatorBase(DMBase):
    """Base class for F-Rep primitive creator tools with live mesh preview."""
    def __init__(self):
        super().__init__()
        self._preview_obj = None     # Live FreeCAD object for preview
        self._update_pending = False  # Throttle rapid updates
        # Reset the shared timer so preview calls for this tool session are isolated
        mesh_timer.reset()

        self.dm_points = []
        self.dm_line_set = None
        self.points_root = coin.SoSeparator()
        if self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)

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
        field = self._get_preview_field()
        if field is None:
            return

        if self._preview_obj is None:
            # Create the preview object for the first time
            # Use last part of class name without 'Creator' suffix
            name = type(self).__name__.replace("Creator", "")
            self._preview_obj = create_dm_object(name=name, shape_type="frep")

        # Throttle: only queue one update per frame
        interval_ms = int(get_interactive_throttle_interval() * 1000)
        if not self._update_pending:
            self._update_pending = True
            QtCore.QTimer.singleShot(interval_ms, lambda: self._apply_preview_field(field))
        
        # Update ghost visuals (points and lines)
        self._update_ghost_visuals()

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
            proxy.FRepField = field
            self._preview_obj.touch()
            # Only recompute this one object for speed
            self._preview_obj.Document.recompute([self._preview_obj])
        except Exception as e:
            dm_logger.debug(f"PrimitiveCreatorBase preview update error: {e}")
        finally:
            self._update_pending = False

    def _finalize_object(self, name):
        """Commit the preview object as the final result, upgrading its mesh resolution."""
        field = self._get_final_field()
        points = self._get_final_points()
        if field is None:
            return
        self._finished = True  # Prevent _do_terminate from cleaning up the committed object
        QtCore.QTimer.singleShot(0, lambda: self.__do_commit(name, field, points))
        self.terminate()

    def __do_commit(self, name, field, points):
        obj = self._preview_obj
        if obj is None or not obj.Document:
            # fallback: create fresh
            obj = create_dm_object(name=name, shape_type="frep")

        # Rename to final name
        try:
            obj.Label = name
        except Exception:
            pass

        # Add points for editing
        if points is not None:
            try:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "FRep", "Control Points")
                obj.Points = points
            except Exception:
                pass

        # Upgrade resolution for final mesh
        primitive_name = type(self).__name__.replace("Creator", "")
        proxy = obj.Proxy
        proxy.FRepField = field
        
        # Set per-object properties
        if hasattr(obj, "MeshingCellSize"):
            obj.MeshingCellSize = float(_get_final_cell_size())
        
        obj.touch()
        obj.Document.recompute([obj])
        # Print accumulated timer summary now that the tool is accepted
        mesh_timer.summary(f"{primitive_name} preview ({_PREVIEW_CELL_SIZE}mm) + final ({getattr(obj, 'MeshingCellSize', _get_final_cell_size()):.1f}mm)")
        self._preview_obj = None  # Severed; the object is now the user's



    def _create_frep_object(self, name, field, points=None):
        """Helper to create the FreeCAD object and assign the field (for 1-shot creation)."""
        obj = create_dm_object(name=name, shape_type="frep")
        obj.Proxy.FRepField = field
        if points is not None:
            if not hasattr(obj, "Points"):
                obj.addProperty("App::PropertyVectorList", "Points", "FRep", "Control Points")
            obj.Points = points
        obj.touch()
        return obj


class BoxCreator(PrimitiveCreatorBase):
    _last_working_plane = None  # Persists across instances; set on first click

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_base = None
        # Pre-load a workplane for the initial preview, in priority order:
        #   1. Workplane detected from current selection (_detect_selected_workplane, set by super)
        #   2. Last workplane clicked during a previous box tool session
        #   3. First visible workplane in document order (fallback)
        if not self.working_plane:
            if BoxCreator._last_working_plane is not None:
                self.working_plane = BoxCreator._last_working_plane
            else:
                visible_wps = self.get_visible_workplanes()
                if visible_wps:
                    self.working_plane = visible_wps[0].getGlobalPlacement() if hasattr(visible_wps[0], "getGlobalPlacement") else visible_wps[0].Placement
        dm_logger.info("Box Tool: Click 1st corner")

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

    def on_button1_down(self, event_dict):
        # Call projector directly so we can capture which workplane was hit.
        result = self.projector.get_mouse_plane_pt(
            event_dict,
            place_on_geometry=False,
            working_plane=getattr(self, "working_plane", None)
        )
        if isinstance(result, tuple):
            pos, wp_hit = result
        else:
            pos, wp_hit = result, None

        if pos is None:
            return True

        if self.state == 0:
            # Lock onto the workplane that was actually clicked.
            if wp_hit is not None:
                self.working_plane = (
                    wp_hit.getGlobalPlacement()
                    if hasattr(wp_hit, "getGlobalPlacement")
                    else wp_hit.Placement
                )
            # Remember this workplane for the next invocation of the box tool.
            BoxCreator._last_working_plane = self.working_plane
            # 1st click - anchor the tool
            self.points.append(pos)
            self.state = 1
            dm_pt = DMPoint(pos)
            dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
            self.dm_points.append(dm_pt)

            dm_logger.info("Box Tool: Click 2nd corner")
        elif self.state == 1:
            # 2nd click - determines base size (x/y)
            self.points.append(pos)
            self.state = 2
            dm_pt = DMPoint(pos)
            dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
            self.dm_points.append(dm_pt)
            self._height_drag_base = pos
            dm_logger.info("Box Tool: Click height")
            
        elif self.state == 2:
            # 3rd click - determines height (z). Finalize shape.
            self.points.append(self.current_point)
            if self.current_point:
                dm_pt = DMPoint(self.current_point)
                dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=self.current_point))
                self.dm_points.append(dm_pt)

            # Transition to a finalized state or finish tool
            self.state = 3
            self._finalize_object("Box")

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

        pts_local = [
            FreeCAD.Vector(cx - size_x/2, cy - size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy - size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy + size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy + size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy - size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy - size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy + size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy + size_y/2, cz + size_z/2),
        ]
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
        while len(self.dm_points) < 8:
            self.dm_points.append(DMPoint(world_corners[len(self.dm_points)]))
        
        r = self._compute_handle_radius(ref_pt=world_corners[0])
        for i, pt in enumerate(world_corners):
            self.dm_points[i].position = pt
            if self.dm_points[i]._point_sep is None:
                self.dm_points[i].draw_point(self.points_root, radius=r, color=(1.0, 0.5, 0.0))
            else:
                self.dm_points[i].update_draw(radius=r)

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_plane_pt(event_dict)

    def on_move_state_2(self, event_dict):
        """Height drag: move current_point along workplane normal."""
        if self._height_drag_base is None:
            return

        wp = getattr(self, "working_plane", None)
        normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)

        self.current_point = DMInputManager.get_instance().get_axis_point(
            self.view, self._height_drag_base, normal, event_dict
        )

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
        if len(self.points) == 3:
            return self._make_field(*self.points)
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
        
        return SdfBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(size_x, size_y, max(size_z, 0.01)), placement=wp)

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
        
        pts_local = [
            FreeCAD.Vector(cx - size_x/2, cy - size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy - size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy + size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy + size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy - size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy - size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy + size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy + size_y/2, cz + size_z/2),
        ]
        return [self.to_global(pt) for pt in pts_local]


class SphereCreator(PrimitiveCreatorBase):
    _last_working_plane = None

    def __init__(self):
        super().__init__()
        self.center = None
        self.current_point = None
        # Pre-load a workplane

        if not self.working_plane:
            if SphereCreator._last_working_plane is not None:
                self.working_plane = SphereCreator._last_working_plane
            else:
                visible_wps = self.get_visible_workplanes()
                if visible_wps:
                    self.working_plane = visible_wps[0].getGlobalPlacement() if hasattr(visible_wps[0], "getGlobalPlacement") else visible_wps[0].Placement

        dm_logger.info("Sphere Tool: Click center")

    def _do_terminate(self):
        super()._do_terminate()

    def on_button1_down(self, event_dict):
        result = self.projector.get_mouse_plane_pt(
            event_dict,
            place_on_geometry=False,
            working_plane=getattr(self, "working_plane", None)
        )
        if isinstance(result, tuple):
            pos, wp_hit = result
        else:
            pos, wp_hit = result, None

        if pos is None:
            return True

        if self.state == 0:
            if wp_hit is not None:
                self.working_plane = (
                    wp_hit.getGlobalPlacement()
                    if hasattr(wp_hit, "getGlobalPlacement")
                    else wp_hit.Placement
                )
            SphereCreator._last_working_plane = self.working_plane

            self.center = pos
            self.state = 1

            dm_pt = DMPoint(pos)
            dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
            self.dm_points.append(dm_pt)

            dm_logger.info("Sphere Tool: Click radius")
        elif self.state == 1:
            self.state = 2
            self._finalize_object("Sphere")

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
        pts = [self.center, self.current_point]
        while len(self.dm_points) < 2:
            self.dm_points.append(DMPoint(pts[len(self.dm_points)]))
        
        r = self._compute_handle_radius(ref_pt=self.center)
        for i, p in enumerate(pts):
            self.dm_points[i].position = p
            if self.dm_points[i]._point_sep is None:
                self.dm_points[i].draw_point(self.points_root, radius=r, color=(1.0, 0.5, 0.0))
            else:
                self.dm_points[i].update_draw(radius=r)

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
        return SdfSphereField(loc_center, radius, placement=getattr(self, "working_plane", None))

    def _get_final_field(self):
        return self._get_preview_field()

    def _get_final_points(self):
        if self.center is None or self.current_point is None:
            return None
        radius = (self.current_point - self.center).Length
        return [self.center, self.center + FreeCAD.Vector(radius, 0, 0)]


class CylinderCreator(PrimitiveCreatorBase):
    _last_working_plane = None

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_base = None
        # Pre-load a workplane

        if not self.working_plane:
            if CylinderCreator._last_working_plane is not None:
                self.working_plane = CylinderCreator._last_working_plane
            else:
                visible_wps = self.get_visible_workplanes()
                if visible_wps:
                    self.working_plane = visible_wps[0].getGlobalPlacement() if hasattr(visible_wps[0], "getGlobalPlacement") else visible_wps[0].Placement

        dm_logger.info("Cylinder Tool: Click base center")

    def _do_terminate(self):
        super()._do_terminate()

    def on_button1_down(self, event_dict):
        result = self.projector.get_mouse_plane_pt(
            event_dict,
            place_on_geometry=False,
            working_plane=getattr(self, "working_plane", None)
        )
        if isinstance(result, tuple):
            pos, wp_hit = result
        else:
            pos, wp_hit = result, None

        if pos is None:
            return True

        if self.state == 0:
            if wp_hit is not None:
                self.working_plane = (
                    wp_hit.getGlobalPlacement()
                    if hasattr(wp_hit, "getGlobalPlacement")
                    else wp_hit.Placement
                )
            CylinderCreator._last_working_plane = self.working_plane

            self.points.append(pos)
            self.state = 1

            dm_pt = DMPoint(pos)
            dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
            self.dm_points.append(dm_pt)

            dm_logger.info("Cylinder Tool: Click radius")
        elif self.state == 1:
            self.points.append(pos)
            self.state = 2
            self._height_drag_base = pos

            dm_pt = DMPoint(pos)
            dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
            self.dm_points.append(dm_pt)

            dm_logger.info("Cylinder Tool: Click height")
        elif self.state == 2:
            self.state = 3
            self._finalize_object("Cylinder")

        return True

    def _update_ghost_visuals(self):
        if not self.points or self.current_point is None:
            return

        import math
        loc_base = self.to_local(self.points[0])
        loc_cur = self.to_local(self.current_point)
        
        if self.state == 1:
            radius = (loc_cur - loc_base).Length
            height = 0.1
        else:
            loc_p1 = self.to_local(self.points[1])
            radius = (loc_p1 - loc_base).Length
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
        while len(self.dm_points) < len(pts):
            self.dm_points.append(DMPoint(pts[len(self.dm_points)]))
        
        r = self._compute_handle_radius(ref_pt=self.points[0])
        for i, p in enumerate(pts):
            self.dm_points[i].position = p
            if self.dm_points[i]._point_sep is None:
                self.dm_points[i].draw_point(self.points_root, radius=r, color=(1.0, 0.5, 0.0))
            else:
                self.dm_points[i].update_draw(radius=r)

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_plane_pt(event_dict)

    def on_move_state_2(self, event_dict):
        """Height drag: move current_point along workplane normal."""
        if self._height_drag_base is None:
            return

        wp = getattr(self, "working_plane", None)
        # Explicit normal from workplane rotation
        normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)

        self.current_point = DMInputManager.get_instance().get_axis_point(
            self.view, self._height_drag_base, normal, event_dict
        )

    def _get_preview_field(self):
        if not self.points or self.current_point is None:
            return None
            
        wp = getattr(self, "working_plane", None)
        loc_base = self.to_local(self.points[0])
        loc_current = self.to_local(self.current_point)
        
        # Axis in local space is always Z (0,0,1) for this tool's logic
        loc_axis = FreeCAD.Vector(0, 0, 1)
        
        if self.state == 1:
            radius = (loc_current - loc_base).Length
            height = 1.0  # minimal placeholder
        else:
            loc_p1 = self.to_local(self.points[1])
            radius = (loc_p1 - loc_base).Length
            height = (loc_current - loc_base).z # Project onto local Z
            
        if radius < 0.01:
            return None
            
        return SdfCylinderField(loc_base, loc_axis, radius, max(abs(height), 0.01), placement=wp)

    def _get_final_field(self):
        if len(self.points) < 3:
            return None
            
        wp = getattr(self, "working_plane", None)
        loc_base = self.to_local(self.points[0])
        loc_p_rad = self.to_local(self.points[1])
        loc_p_height = self.to_local(self.points[2])
        
        loc_axis = FreeCAD.Vector(0, 0, 1)
        radius = (loc_p_rad - loc_base).Length
        height = (loc_p_height - loc_base).z
        
        return SdfCylinderField(loc_base, loc_axis, radius, abs(height), placement=wp)

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        return list(self.points)
