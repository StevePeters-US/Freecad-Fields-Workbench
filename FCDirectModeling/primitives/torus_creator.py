"""
Torus creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from PySide import QtCore
from .base import DMPrimitiveCreator
from FCDirectModeling import dm_logger

class TorusCreator(DMPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.R = 1.0   # Major radius
        self.r = 0.5   # Minor radius

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

        pt = self.get_point_on_plane(event_dict, n, o)
        if self.state == 1:
            self.R = max(0.1, (pt - self.center).Length)
            self.update_preview()
        elif self.state == 2:
            dist = (pt - self.center).Length
            self.r = max(0.01, abs(dist - self.R))
            self.update_preview()

    def update_preview(self):
        # We use [0,0,0] locally.
        # Orientation is centered at the clicked center, with Z normal matching the face.
        rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
        final_placement = FreeCAD.Placement(self.center, rot)
        
        params = {
            "center": [0, 0, 0],
            "major_r": self.R,
            "minor_r": self.r,
        }
        self.update_dm_preview("torus", params, placement=final_placement)
        
        self._last_sdf_params = params
        self._last_placement = final_placement


