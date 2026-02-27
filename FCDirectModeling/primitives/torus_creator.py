"""
Torus creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from PySide import QtCore
from .primitive_base import NURBSPrimitiveCreator
from FCDirectModeling import dm_logger

class TorusCreator(NURBSPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.R = 1.0   # Major radius
        self.r = 0.5   # Minor radius

    def on_move_state_1(self, event_dict):
        pt = self.get_mouse_plane_pt(event_dict)
        self.R = max(0.1, (pt - self.start_point).Length)

    def on_move_state_2(self, event_dict):
        pt = self.get_mouse_plane_pt(event_dict)
        dist = (pt - self.start_point).Length
        self.r = max(0.01, abs(dist - self.R))

    def update_preview(self, debug_pt=None):
        if not self.start_point:
            return
        rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
        final_placement = FreeCAD.Placement(self.start_point, rot)
        
        params = {
            "center": [0, 0, 0],
            "major_r": self.R,
            "minor_r": self.r,
        }
        if debug_pt:
            params["debug_pt"] = debug_pt
        self.update_nurbs_preview("torus", params, placement=final_placement)



