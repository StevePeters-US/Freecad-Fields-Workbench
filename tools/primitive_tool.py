import FreeCAD
from PySide import QtCore
from core import dm_logger
from core.dm_object import create_dm_object, get_frep_storage_type
from tools.dm_base import DMBase

# Ensure we import the right storage classes. For now, defaulting to MarchingCubes
from core.frep.marching_cubes.box import MCBoxField
from core.frep.marching_cubes.sphere import MCSphereField
from core.frep.marching_cubes.cylinder import MCCylinderField

class PrimitiveCreatorBase(DMBase):
    """Base class for F-Rep primitive creator tools."""
    def __init__(self):
        super().__init__()
        # Visual feedback nodes would go here if we want Coin3D previews

    def update_preview(self):
        pass
        
    def _create_frep_object(self, name, field):
        """Helper to create the FreeCAD object and assign the field."""
        obj = create_dm_object(name=name, shape_type="frep")
        # Attach the field securely to the proxy
        obj.Proxy.FRepField = field
        # Update the standard property text to trigger a recompute
        if hasattr(obj, "ShapeType"):
            obj.ShapeType = "frep" 
        return obj

class BoxCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.points = []
        dm_logger.info("Box Tool: Click 1st corner")

    def on_button1_down(self, event_dict):
        # 1. P1: First corner
        # 2. P2: Opposite corner on plane
        # 3. P3: Height
        x, y = event_dict["Position"]
        
        pos = self.get_mouse_world_pos(event_dict)
        
        if len(self.points) == 0:
            self.points.append(pos)
            dm_logger.info("Box Tool: Click 2nd corner")
        elif len(self.points) == 1:
            self.points.append(pos)
            dm_logger.info("Box Tool: Click height")
        elif len(self.points) == 2:
            self.points.append(pos)
            self._finalize_box()

        return True

    def _finalize_box(self):
        p1, p2, p3 = self.points
        
        # We need a center and a size.
        # size X/Y from p1 to p2.
        # size Z from p2 to p3.
        size_x = abs(p2.x - p1.x)
        size_y = abs(p2.y - p1.y)
        size_z = abs(p3.z - p2.z)

        # Center is midpoint
        cx = (p1.x + p2.x) / 2.0
        cy = (p1.y + p2.y) / 2.0
        cz = (p2.z + p3.z) / 2.0
        
        center = FreeCAD.Vector(cx, cy, cz)
        size = FreeCAD.Vector(size_x, size_y, size_z)

        field = MCBoxField(center, size)
        
        QtCore.QTimer.singleShot(0, lambda: self.__do_create(field))
        self.finish()

    def __do_create(self, field):
        self._create_frep_object("Box", field)
        FreeCAD.activeDocument().recompute()

class SphereCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.center = None
        dm_logger.info("Sphere Tool: Click center")

    def on_button1_down(self, event_dict):
        pos = self.get_mouse_world_pos(event_dict)
        
        if self.center is None:
            self.center = pos
            dm_logger.info("Sphere Tool: Click radius")
        else:
            radius = (pos - self.center).Length
            field = MCSphereField(self.center, radius)
            QtCore.QTimer.singleShot(0, lambda: self.__do_create(field))
            self.finish()

        return True

    def __do_create(self, field):
        self._create_frep_object("Sphere", field)
        FreeCAD.activeDocument().recompute()


class CylinderCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.points = []
        dm_logger.info("Cylinder Tool: Click base center")

    def on_button1_down(self, event_dict):
        pos = self.get_mouse_world_pos(event_dict)
        
        if len(self.points) == 0:
            self.points.append(pos)
            dm_logger.info("Cylinder Tool: Click radius")
        elif len(self.points) == 1:
            self.points.append(pos)
            dm_logger.info("Cylinder Tool: Click height")
        elif len(self.points) == 2:
            self.points.append(pos)
            self._finalize_cylinder()

        return True

    def _finalize_cylinder(self):
        c_base, p_rad, p_height = self.points
        
        radius = (p_rad - c_base).Length
        # By default assume Z-axis up from the workplane.
        n, _ = self.get_base_plane()
        axis = n

        # Height is projection of p_height-c_base onto axis
        height = (p_height - c_base).dot(axis)
        
        # MC Cylinder takes the center point halfway up, axis, radius, and total height
        center = c_base + axis * (height / 2.0)
        
        field = MCCylinderField(center, axis, radius, abs(height))
        
        QtCore.QTimer.singleShot(0, lambda: self.__do_create(field))
        self.finish()

    def __do_create(self, field):
        self._create_frep_object("Cylinder", field)
        FreeCAD.activeDocument().recompute()
