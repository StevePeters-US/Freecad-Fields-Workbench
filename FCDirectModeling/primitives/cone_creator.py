"""
Cone creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from PySide import QtCore
from .base import SDFMeshPrimitiveCreator
from FCDirectModeling import sdf_logger

class ConeCreator(SDFMeshPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.radius = 0.1
        self.height = 0.1
        sdf_logger.debug("ConeCreator: Initialized")

    def handle_click(self, event_dict):
        n = FreeCAD.Vector(0,0,1)
        o = FreeCAD.Vector(0,0,0)
        if self.working_plane:
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = self.working_plane.Base
            
        pt = self.get_point_on_plane(event_dict, n, o)
        
        if self.state == 0:
            self.center = pt
            self.state = 1
        elif self.state == 1:
            self.state = 2
        elif self.state == 2:
            self.finish()
            return True
        
        return False

    def handle_move(self, event_dict):
        if self.state == 0:
            # Detect face under mouse
            obj, subname = self.get_face_under_mouse(event_dict)
            if obj and subname and "Face" in subname:
                try:
                    face = obj.Shape.getElement(subname)
                    if hasattr(face, "Surface") and "GeomPlane" in face.Surface.TypeId:
                        self.working_plane = face.Surface.Position
                        self.snap_face = (obj, subname)
                    else:
                        self.working_plane = None
                        self.snap_face = None
                except Exception:
                    self.working_plane = None
                    self.snap_face = None
            else:
                self.working_plane = None
                self.snap_face = None
            return

        n = FreeCAD.Vector(0,0,1)
        o = FreeCAD.Vector(0,0,0)
        if self.working_plane:
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = self.working_plane.Base

        if self.state == 1:
            pt = self.get_point_on_plane(event_dict, n, o)
            self.radius = max(0.001, (pt - self.center).Length)
            self.update_preview()
        elif self.state == 2:
            # Axis must be aligned with world normal of the face
            axis = n
            pt_on_axis = self.get_closest_point_on_axis(event_dict, self.center, axis)
            self.height = (pt_on_axis - self.center).dot(axis)
            if abs(self.height) < 0.001:
                self.height = 0.001 if self.height >= 0 else -0.001
            self.update_preview()

    def update_preview(self):
        # We use [0,0,0] locally. The base of the cone is at z = -height/2 effectively?
        # Actually, our SDF cone logic might expect z=0 as base or center.
        # Let's assume it's centered at [0,0,0] locally.
        # So we need to translate it by center + axis*(height/2)
        
        n = FreeCAD.Vector(0,0,1)
        if self.working_plane:
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
        
        # Local offset to move [0,0,0] to the midpoint of the height
        # If we want the base to be at self.center, and it's centered locally:
        mid_pt = self.center + n * (self.height / 2.0)
        
        # Construct placement using the world midpoint and face rotation
        rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
        final_placement = FreeCAD.Placement(mid_pt, rot)
        
        params = {
            "center": [0, 0, 0],
            "radius": abs(self.radius),
            "height": self.height,
        }
        self.update_sdf_preview("cone", params, placement=final_placement)
        
        self._last_sdf_params = params
        self._last_placement = final_placement


