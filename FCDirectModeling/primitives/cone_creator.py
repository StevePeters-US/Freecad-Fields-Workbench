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
        pt = self.get_point_on_plane(event_dict)
        if self.state == 0:
            self.center = pt
            self.state = 1
        elif self.state == 1:
            self.state = 2
        elif self.state == 2:
            self.finish()

    def handle_move(self, event_dict):
        if self.state == 1:
            pt = self.get_point_on_plane(event_dict)
            self.radius = max(0.001, (pt - self.center).Length)
            self.update_preview()
        elif self.state == 2:
            axis = FreeCAD.Vector(0, 0, 1)
            pt_on_axis = self.get_closest_point_on_axis(event_dict, self.center, axis)
            self.height = (pt_on_axis - self.center).dot(axis)
            if abs(self.height) < 0.001:
                self.height = 0.001 if self.height >= 0 else -0.001
            self.update_preview()

    def update_preview(self):
        cx, cy, cz = self.center.x, self.center.y, self.center.z
        self.update_sdf_preview("cone", {
            "center": [cx, cy, cz],
            "radius": abs(self.radius),
            "height": self.height,
        })


