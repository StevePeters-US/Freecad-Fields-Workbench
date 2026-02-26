"""
Torus creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from PySide import QtCore
from .base import SDFMeshPrimitiveCreator
from FCDirectModeling import sdf_logger

class TorusCreator(SDFMeshPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.R = 1.0   # Major radius
        self.r = 0.5   # Minor radius

    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
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
        pt = self.get_point_on_plane(event_dict)
        if self.state == 1:
            self.R = max(0.1, (pt - self.center).Length)
            self.update_preview()
        elif self.state == 2:
            dist = (pt - self.center).Length
            self.r = max(0.01, abs(dist - self.R))
            self.update_preview()

    def update_preview(self):
        cx, cy, cz = self.center.x, self.center.y, self.center.z
        self.update_sdf_preview("torus", {
            "center": [cx, cy, cz],
            "major_r": self.R,
            "minor_r": self.r,
        })


