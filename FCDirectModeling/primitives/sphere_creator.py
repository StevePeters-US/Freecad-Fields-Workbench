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

    def _do_finish(self):
        sdf_logger.debug("SphereCreator: Finishing object...")
        try:
            cx, cy, cz = self.center.x, self.center.y, self.center.z
            r = max(0.01, abs(self.radius))
            if self._preview_obj is not None:
                self._preview_obj.Label = "Sphere"
                self._preview_obj.Proxy.sdf_type = "sphere"
                self._preview_obj.Proxy.params = {"center": [cx, cy, cz], "radius": r}
                self._preview_obj.Proxy.is_preview = False
                self._preview_obj.touch()
                FreeCAD.activeDocument().recompute()
                self._preview_obj = None   # detach so terminate() doesn't delete it
            else:
                from ..sdf_object import create_sdf_object
                create_sdf_object("Sphere", "sphere", {"center": [cx, cy, cz], "radius": r})
        except Exception as e:
            FreeCAD.Console.PrintError(f"SphereCreator finish error: {e}\n")
        
        super()._do_finish()


from PySide import QtCore
