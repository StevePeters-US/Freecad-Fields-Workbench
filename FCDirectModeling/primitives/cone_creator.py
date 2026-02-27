"""
Cone creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from PySide import QtCore
from .primitive_base import DMPrimitiveCreator
from FCDirectModeling import dm_logger

class ConeCreator(DMPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.radius = 0.1

    def handle_click(self, event_dict):
        handled = super().handle_click(event_dict)
        if handled:
            print(f"Pin location {self.state}: {self.current_point.x:.2f}, {self.current_point.y:.2f}, {self.current_point.z:.2f}")
        return handled

    def apply_height(self, height):
        self.height = height
        if abs(self.height) < 0.001:
            self.height = 0.001 if self.height >= 0 else -0.001

    def handle_move(self, event_dict):
        super().handle_move(event_dict)

        if self.state == 1:
            pt = self.get_mouse_plane_pt(event_dict)
            self.radius = max(0.001, (pt - self.start_point).Length)
            self.update_preview(debug_pt=pt)

    def update_preview(self, debug_pt=None):
        n = FreeCAD.Vector(0,0,1)
        if self.working_plane:
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
        
        # Local offset to move [0,0,0] to the base (start_point)
        # Note: build_cone starts at 0 and goes up Z.
        # Direction handled by working_plane.
        rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
        
        # If height is negative, we want the cone to point "down" from the start point.
        # We can achieve this by flipping the orientation or offsetting.
        # Easiest: keep orientation, use abs(height) for shape, and translate base.
        # But wait, makeCone(r, 0, h) always goes along +Z.
        # If we use the face rotation, it goes along +Normal.
        # If height is negative, it should go along -Normal.
        
        h = self.height
        if h < 0:
            # Flip rotation by 180 around X or Y locally
            rot = rot * FreeCAD.Rotation(FreeCAD.Vector(1,0,0), 180)
            h = abs(h)
            
        final_placement = FreeCAD.Placement(self.start_point, rot)
        
        params = {
            "center": [0, 0, 0],
            "radius": abs(self.radius),
            "height": h,
        }
        if debug_pt:
            params["debug_pt"] = debug_pt
            
        self.update_dm_preview("cone", params, placement=final_placement)


