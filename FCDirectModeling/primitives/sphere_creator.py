"""
Sphere creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from .primitive_base import NURBSPrimitiveCreator
from FCDirectModeling import dm_logger

class SphereCreator(NURBSPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.radius = 1.0

    def on_move_state_1(self, event_dict):
        pt = self.get_mouse_plane_pt(event_dict)
        if self.start_point:
            self.radius = (pt - self.start_point).Length
            # Ensure a minimum radius for visibility
            if self.radius < 0.01:
                self.radius = 0.01

    def update_preview(self, debug_pt=None):
        if not self.start_point:
            return
        r = max(0.01, abs(self.radius))
        final_placement = FreeCAD.Placement(self.start_point, FreeCAD.Rotation())
        
        params = {"center": [0, 0, 0], "radius": r}
        if debug_pt:
            params["debug_pt"] = debug_pt
        self.update_nurbs_preview("sphere", params, placement=final_placement)


from PySide import QtCore
