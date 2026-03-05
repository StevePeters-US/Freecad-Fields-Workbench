import FreeCAD
import FreeCADGui
from PySide import QtCore
from core import dm_logger
from core.dm_object import create_dm_object, get_frep_storage_type
from core.frep_mesher import mesh_timer
from tools.dm_base import DMBase

# Ensure we import the right storage classes. For now, defaulting to MarchingCubes
from core.frep.marching_cubes.box import MCBoxField
from core.frep.marching_cubes.sphere import MCSphereField
from core.frep.marching_cubes.cylinder import MCCylinderField

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
        dm_logger.info("Box Tool: Click 1st corner")

    def on_button1_down(self, event_dict):
        pos = self.get_mouse_world_pos(event_dict)
        if pos is None:
            return True

        if len(self.points) == 0:
            self.points.append(pos)
            dm_logger.info("Box Tool: Click 2nd corner")
        elif len(self.points) == 1:
            self.points.append(pos)
            dm_logger.info("Box Tool: Click height")
        elif len(self.points) == 2:
            self.points.append(pos)
            self.current_point = pos
            self._finalize_object("Box")

        return True

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_world_pos(event_dict)

    def on_move_state_2(self, event_dict):
        self.current_point = self.get_mouse_world_pos(event_dict)

    def _get_preview_field(self):
        if not self.points or self.current_point is None:
            return None
        if len(self.points) == 1:
            p1 = self.points[0]
            p2 = FreeCAD.Vector(self.current_point.x, self.current_point.y, p1.z)
            return self._make_field(p1, p2, p1)
        elif len(self.points) == 2:
            return self._make_field(self.points[0], self.points[1], self.current_point)
        return None

    def _get_final_field(self):
        if len(self.points) == 3:
            return self._make_field(*self.points)
        return None

    def _make_field(self, p1, p2, p3):
        size_x = abs(p2.x - p1.x)
        size_y = abs(p2.y - p1.y)
        size_z = abs(p3.z - p2.z)
        if size_x < 0.01 or size_y < 0.01:
            return None
        cx = (p1.x + p2.x) / 2.0
        cy = (p1.y + p2.y) / 2.0
        cz = (p2.z + p3.z) / 2.0
        return MCBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(size_x, size_y, max(size_z, 0.01)))

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        p1, p2, p3 = self.points
        size_x = abs(p2.x - p1.x)
        size_y = abs(p2.y - p1.y)
        size_z = abs(p3.z - p2.z)
        cx = (p1.x + p2.x) / 2.0
        cy = (p1.y + p2.y) / 2.0
        cz = (p2.z + p3.z) / 2.0
        return [
            FreeCAD.Vector(cx - size_x/2, cy - size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy - size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy + size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy + size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy - size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy - size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy + size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy + size_y/2, cz + size_z/2),
        ]


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
        return MCSphereField(self.center, radius)

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
        return MCCylinderField(center, n, radius, max(abs(height), 0.01))

    def _get_final_field(self):
        if len(self.points) < 3:
            return None
        c_base, p_rad, p_height = self.points
        n, _ = self.get_base_plane()
        radius = (p_rad - c_base).Length
        height = (p_height - c_base).dot(n)
        center = c_base + n * (height / 2.0)
        return MCCylinderField(center, n, radius, abs(height))

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        return list(self.points)
