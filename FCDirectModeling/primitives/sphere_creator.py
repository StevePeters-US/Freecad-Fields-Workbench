"""
Sphere creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from .base import SDFMeshPrimitiveCreator
from FCDirectModeling import sdf_logger

class SphereCreator(SDFMeshPrimitiveCreator):
    def __init__(self):
        super().__init__()
        sdf_logger.debug("SphereCreator: Initializing...")
        self.radius = 1.0

    def handle_click(self, event_dict):
        sdf_logger.debug(f"SphereCreator: Click! State={self.state}")
        
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
            self.handle_move(event_dict)
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

        if self.state == 1:
            n = FreeCAD.Vector(0,0,1)
            o = FreeCAD.Vector(0,0,0)
            if self.working_plane:
                n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
                o = self.working_plane.Base
            
            pt = self.get_point_on_plane(event_dict, n, o)
            self.radius = max(0.01, (pt - self.center).Length)
            self.update_preview()

    def update_preview(self):
        # We use [0,0,0] locally for the sphere creator in the SDf engine,
        # and manage the position via Placement.
        r = max(0.01, abs(self.radius))
        
        # Placement is just the translation to the center point.
        # Note: rotation is less critical for a sphere, but we keep the face rotation for consistency.
        # However, many times users want the sphere centered ON the clicked point.
        # Since we are centering it at [0,0,0], we just use the center pt as placement.
        final_placement = FreeCAD.Placement(self.center, FreeCAD.Rotation())
        
        params = {"center": [0, 0, 0], "radius": r}
        self.update_sdf_preview("sphere", params, placement=final_placement)
        
        self._last_sdf_params = params
        self._last_placement = final_placement

from PySide import QtCore
