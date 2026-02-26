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
        pt = self.get_point_on_plane(event_dict)

        if self.state == 0:
            self.center = pt
            self.state = 1
        elif self.state == 1:
            self.handle_move(event_dict)
            self.finish()
            return True

    def handle_move(self, event_dict):
        if self.state == 1:
            pt = self.get_point_on_plane(event_dict)
            self.radius = max(0.01, (pt - self.center).Length)
            self.update_preview()

    def update_preview(self):
        cx, cy, cz = self.center.x, self.center.y, self.center.z
        r = max(0.01, abs(self.radius))
        self.update_sdf_preview("sphere", {"center": [cx, cy, cz], "radius": r})

from PySide import QtCore
