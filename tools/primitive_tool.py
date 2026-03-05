import FreeCAD
import FreeCADGui
from PySide import QtCore
from core import dm_logger
from core.dm_object import create_dm_object, get_frep_storage_type
from core.frep_mesher import mesh_timer
from tools.dm_base import DMBase

# Ensure we import the right storage classes. For now, defaulting to MarchingCubes
from core.frep.analytic.box import AnalyticBoxField
from core.frep.analytic.sphere import AnalyticSphereField
from core.frep.analytic.cylinder import AnalyticCylinderField

# Resolution for interactive preview (lower = faster updates)
_PREVIEW_RES = 10
# Resolution for final committed mesh (higher = more detail)
_FINAL_RES = 20


class PrimitiveCreatorBase(DMBase):
    """Base class for F-Rep primitive creator tools with live mesh preview."""
    def __init__(self):
        super().__init__()
        self._preview_obj = None     # Live FreeCAD object for preview
        self._update_pending = False  # Throttle rapid updates
        # Reset the shared timer so preview calls for this tool session are isolated
        mesh_timer.reset()
        # Primitive creation must snap to workplane, not arbitrary geometry surfaces.
        # Individual instances shadow the class variable to avoid changing global state.
        self.place_on_geometry = False

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
        QtCore.QTimer.singleShot(0, lambda: self.__do_commit(name, field, points))
        self.finish()

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
        proxy._final_resolution = _FINAL_RES
        obj.touch()
        obj.Document.recompute([obj])
        # Print accumulated timer summary now that the tool is accepted
        mesh_timer.summary(f"{primitive_name} preview ({_PREVIEW_RES} res) + final ({_FINAL_RES} res)")
        self._preview_obj = None  # Severed; the object is now the user's

    def _do_terminate(self):
        # If tool exits without committing, clean up the preview object
        if self._preview_obj:
            try:
                doc = self._preview_obj.Document
                if doc:
                    doc.removeObject(self._preview_obj.Name)
            except Exception:
                pass
            self._preview_obj = None
        super()._do_terminate()

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
    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_screen_y = None
        self._height_drag_base = None
        # Pre-load the active workplane so preview is correct before the 1st click
        visible_wps = self.get_visible_workplanes()
        if visible_wps:
            self.working_plane = visible_wps[0].getGlobalPlacement() if hasattr(visible_wps[0], "getGlobalPlacement") else visible_wps[0].Placement
        dm_logger.info("Box Tool: Click 1st corner")

    def on_button1_down(self, event_dict):
        pos = self.get_mouse_plane_pt(event_dict)
        if pos is None:
            return True

        if len(self.points) == 0:
            self.points.append(pos)
            # Lock working_plane from the active workplane for consistent transforms
            if not getattr(self, "working_plane", None):
                visible_wps = self.get_visible_workplanes()
                if visible_wps:
                    self.working_plane = visible_wps[0].getGlobalPlacement() if hasattr(visible_wps[0], "getGlobalPlacement") else visible_wps[0].Placement
            wp = getattr(self, "working_plane", None)
            loc = wp.inverse().multVec(pos) if wp else pos
            dm_logger.info(f"Box P0 (1st corner): world=({pos.x:.2f},{pos.y:.2f},{pos.z:.2f})  local=({loc.x:.2f},{loc.y:.2f},{loc.z:.2f})")
            if wp:
                dm_logger.debug(f"  Working plane Base=({wp.Base.x:.2f},{wp.Base.y:.2f},{wp.Base.z:.2f})  Normal={wp.Rotation.multVec(FreeCAD.Vector(0,0,1))}")
            dm_logger.info("Box Tool: Click 2nd corner")
        elif len(self.points) == 1:
            self.points.append(pos)
            self.state = 2  # Switch to height-drag mode
            # Record screen Y for height drag
            self._height_drag_screen_y = event_dict["Position"][1]
            self._height_drag_base = pos  # world point where height drag starts
            wp = getattr(self, "working_plane", None)
            loc = wp.inverse().multVec(pos) if wp else pos
            loc0 = wp.inverse().multVec(self.points[0]) if wp else self.points[0]
            dm_logger.info(f"Box P1 (2nd corner): world=({pos.x:.2f},{pos.y:.2f},{pos.z:.2f})  local=({loc.x:.2f},{loc.y:.2f},{loc.z:.2f})")
            dm_logger.info(f"  Footprint local size: dx={abs(loc.x-loc0.x):.2f}  dy={abs(loc.y-loc0.y):.2f}")
            dm_logger.info("Box Tool: Click height")
        elif len(self.points) == 2:
            self.points.append(self.current_point)
            wp = getattr(self, "working_plane", None)
            hp = self.current_point
            hp_loc = wp.inverse().multVec(hp) if wp else hp
            p0_loc = wp.inverse().multVec(self.points[0]) if wp else self.points[0]
            dm_logger.info(f"Box P2 (height): world=({hp.x:.2f},{hp.y:.2f},{hp.z:.2f})  local=({hp_loc.x:.2f},{hp_loc.y:.2f},{hp_loc.z:.2f})")
            dm_logger.info(f"  Height (local Z delta from P0): {abs(hp_loc.z - p0_loc.z):.2f}")
            self._finalize_object("Box")

        return True

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_plane_pt(event_dict)

    def on_move_state_2(self, event_dict):
        """Height drag: move current_point along workplane normal proportional to screen Y delta."""
        if not hasattr(self, "_height_drag_screen_y") or self._height_drag_base is None:
            return
        screen_y = event_dict["Position"][1]
        delta_px = screen_y - self._height_drag_screen_y
        # Scale: how many mm of height per pixel (rough, view-distance based)
        # Positive screen Y goes down → negative normal direction → invert
        height_mm = -delta_px / 4.0
        
        wp = getattr(self, "working_plane", None)
        if wp is not None:
            normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        else:
            normal = FreeCAD.Vector(0, 0, 1)
        
        # current_point = base point of height drag + normal * height
        self.current_point = self._height_drag_base + normal * height_mm

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
        
        def to_local(p):
            if wp is not None:
                return wp.inverse().multVec(p)
            return p
        
        loc_p1 = to_local(p1)
        loc_p2 = to_local(p2)
        loc_p3 = to_local(p3)
        
        size_x = abs(loc_p2.x - loc_p1.x)
        size_y = abs(loc_p2.y - loc_p1.y)
        # Height = distance of 3rd point along local Z from the base plane (local Z midpoint of p1 and p3)
        size_z = abs(loc_p3.z - loc_p1.z)
        if size_x < 0.01 or size_y < 0.01:
            return None
            
        cx = (loc_p1.x + loc_p2.x) / 2.0
        cy = (loc_p1.y + loc_p2.y) / 2.0
        cz = (loc_p1.z + loc_p3.z) / 2.0
        
        return AnalyticBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(size_x, size_y, max(size_z, 0.01)), placement=wp)

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        wp = getattr(self, "working_plane", None)
        def to_local(p):
            return wp.inverse().multVec(p) if wp is not None else p
        def to_global(p):
            return wp.multVec(p) if wp is not None else p

        loc_p1 = to_local(self.points[0])
        loc_p2 = to_local(self.points[1])
        loc_p3 = to_local(self.points[2])
        
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
        return [to_global(pt) for pt in pts_local]


class SphereCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.center = None
        self.current_point = None
        dm_logger.info("Sphere Tool: Click center")

    def on_button1_down(self, event_dict):
        pos = self.get_mouse_world_pos(event_dict)
        if pos is None:
            return True

        if self.center is None:
            self.center = pos
            dm_logger.info("Sphere Tool: Click radius")
        else:
            self.current_point = pos
            self._finalize_object("Sphere")

        return True

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_world_pos(event_dict)

    def _get_preview_field(self):
        if self.center is None or self.current_point is None:
            return None
        radius = (self.current_point - self.center).Length
        if radius < 0.01:
            return None
        return AnalyticSphereField(self.center, radius)

    def _get_final_field(self):
        return self._get_preview_field()

    def _get_final_points(self):
        if self.center is None or self.current_point is None:
            return None
        radius = (self.current_point - self.center).Length
        return [self.center, self.center + FreeCAD.Vector(radius, 0, 0)]


class CylinderCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        dm_logger.info("Cylinder Tool: Click base center")

    def on_button1_down(self, event_dict):
        pos = self.get_mouse_world_pos(event_dict)
        if pos is None:
            return True

        if len(self.points) == 0:
            self.points.append(pos)
            dm_logger.info("Cylinder Tool: Click radius")
        elif len(self.points) == 1:
            self.points.append(pos)
            dm_logger.info("Cylinder Tool: Click height")
        elif len(self.points) == 2:
            self.points.append(pos)
            self.current_point = pos
            self._finalize_object("Cylinder")

        return True

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_world_pos(event_dict)

    def on_move_state_2(self, event_dict):
        self.current_point = self.get_mouse_world_pos(event_dict)

    def _get_preview_field(self):
        if not self.points or self.current_point is None:
            return None
        c_base = self.points[0]
        n, _ = self.get_base_plane()
        if len(self.points) == 1:
            radius = (self.current_point - c_base).Length
            height = 1.0  # minimal placeholder
        else:
            radius = (self.points[1] - c_base).Length
            height = (self.current_point - c_base).dot(n)
        if radius < 0.01:
            return None
        center = c_base + n * (height / 2.0)
        return AnalyticCylinderField(center, n, radius, max(abs(height), 0.01))

    def _get_final_field(self):
        if len(self.points) < 3:
            return None
        c_base, p_rad, p_height = self.points
        n, _ = self.get_base_plane()
        radius = (p_rad - c_base).Length
        height = (p_height - c_base).dot(n)
        center = c_base + n * (height / 2.0)
        return AnalyticCylinderField(center, n, radius, abs(height))

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        return list(self.points)
