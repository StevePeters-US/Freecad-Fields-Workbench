"""
Torus creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from PySide import QtCore
from .primitive_base import DMPrimitiveCreator
from FCDirectModeling import dm_logger

class TorusCreator(DMPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.R = 1.0   # Major radius
        self.r = 0.5   # Minor radius

    def handle_click(self, event_dict):
        handled = super().handle_click(event_dict)
        if handled:
            print(f"Pin location {self.state}: {self.current_point.x:.2f}, {self.current_point.y:.2f}, {self.current_point.z:.2f}")
        return handled

    def handle_move(self, event_dict):
        super().handle_move(event_dict)

        if self.state == 1:
            pt = self.get_mouse_plane_pt(event_dict)
            self.R = max(0.1, (pt - self.start_point).Length)
            self.update_preview(debug_pt=pt)
        elif self.state == 2:
            pt = self.get_mouse_plane_pt(event_dict)
            dist = (pt - self.start_point).Length
            self.r = max(0.01, abs(dist - self.R))
            self.update_preview(debug_pt=pt)

    def update_preview(self, debug_pt=None):
        rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
        final_placement = FreeCAD.Placement(self.start_point, rot)
        
        params = {
            "center": [0, 0, 0],
            "major_r": self.R,
            "minor_r": self.r,
        }
        if debug_pt:
            params["debug_pt"] = debug_pt
        self.update_dm_preview("torus", params, placement=final_placement)
        
        self._last_sdf_params = params
        self._last_placement = final_placement


