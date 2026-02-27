"""
Sphere creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from .primitive_base import DMPrimitiveCreator
from FCDirectModeling import dm_logger

class SphereCreator(DMPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.radius = 1.0

    def handle_click(self, event_dict):
        handled = super().handle_click(event_dict)
        if handled and self.state == 1:
             print(f"Pin location 1: {self.start_point.x:.2f}, {self.start_point.y:.2f}, {self.start_point.z:.2f}")
        return handled

    def handle_move(self, event_dict):
        super().handle_move(event_dict)

        if self.state == 1:
            pt = self.get_mouse_plane_pt(event_dict)
            self.radius = max(0.01, (pt - self.start_point).Length)
            self.update_preview(debug_pt=pt)

    def update_preview(self, debug_pt=None):
        # We use [0,0,0] locally for the sphere creator in the SDf engine,
        # and manage the position via Placement.
        r = max(0.01, abs(self.radius))
        
        # Placement is just the translation to the center point.
        # Note: rotation is less critical for a sphere, but we keep the face rotation for consistency.
        # However, many times users want the sphere centered ON the clicked point.
        # Since we are centering it at [0,0,0], we just use the center pt as placement.
        final_placement = FreeCAD.Placement(self.start_point, FreeCAD.Rotation())
        
        params = {"center": [0, 0, 0], "radius": r}
        if debug_pt:
            params["debug_pt"] = debug_pt
        self.update_dm_preview("sphere", params, placement=final_placement)
        
        self._last_sdf_params = params
        self._last_placement = final_placement

from PySide import QtCore
