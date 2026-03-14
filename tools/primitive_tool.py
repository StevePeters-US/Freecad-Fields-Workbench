import FreeCAD
from PySide import QtCore
from pivy import coin
from core import dm_logger
from core.input_manager import DMInputManager
from core.dm_point import DMPoint
from core.dm_object import create_dm_object, get_meshing_cell_size
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
        # allow default DMBase placing on geometry, since projection fallback is fixed

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
        if not self._update_pending:
            self._update_pending = True
            QtCore.QTimer.singleShot(0, lambda: self._apply_preview_field(field))

    def _apply_preview_field(self, field):
        self._update_pending = False
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
        self.dm_points = []
        self.sg = self.view.getSceneGraph() if self.view else None
        self.points_root = coin.SoSeparator()
        if self.sg:
            self.sg.addChild(self.points_root)
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
        try:
            if self.sg and self.points_root:
                self.sg.removeChild(self.points_root)
        except Exception as e:
            dm_logger.debug(f"BoxCreator._do_terminate: {e}")
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
            # Note: Right click implicitly finishes the tool as handled by DMBase

        return True

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
        self.dm_points = []
        self.sg = self.view.getSceneGraph() if self.view else None
        self.points_root = coin.SoSeparator()
        if self.sg:
            self.sg.addChild(self.points_root)

        if not self.working_plane:
            if SphereCreator._last_working_plane is not None:
                self.working_plane = SphereCreator._last_working_plane
            else:
                visible_wps = self.get_visible_workplanes()
                if visible_wps:
                    self.working_plane = visible_wps[0].getGlobalPlacement() if hasattr(visible_wps[0], "getGlobalPlacement") else visible_wps[0].Placement

        dm_logger.info("Sphere Tool: Click center")

    def _do_terminate(self):
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()
        try:
            if self.sg and self.points_root:
                self.sg.removeChild(self.points_root)
        except Exception as e:
            dm_logger.debug(f"SphereCreator._do_terminate: {e}")
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
            self.current_point = pos
            self.state = 2

            dm_pt = DMPoint(pos)
            dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
            self.dm_points.append(dm_pt)

            self._finalize_object("Sphere")

        return True

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
        self.dm_points = []
        self._height_drag_base = None
        self.sg = self.view.getSceneGraph() if self.view else None
        self.points_root = coin.SoSeparator()
        if self.sg:
            self.sg.addChild(self.points_root)

        if not self.working_plane:
            if CylinderCreator._last_working_plane is not None:
                self.working_plane = CylinderCreator._last_working_plane
            else:
                visible_wps = self.get_visible_workplanes()
                if visible_wps:
                    self.working_plane = visible_wps[0].getGlobalPlacement() if hasattr(visible_wps[0], "getGlobalPlacement") else visible_wps[0].Placement

        dm_logger.info("Cylinder Tool: Click base center")

    def _do_terminate(self):
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()
        try:
            if self.sg and self.points_root:
                self.sg.removeChild(self.points_root)
        except Exception as e:
            dm_logger.debug(f"CylinderCreator._do_terminate: {e}")
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
            self.points.append(self.current_point)
            self.state = 3

            if self.current_point:
                dm_pt = DMPoint(self.current_point)
                dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=self.current_point))
                self.dm_points.append(dm_pt)

            self._finalize_object("Cylinder")

        return True

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
