"""
Cone creator — no Coin3D, uses Mesh::Feature preview.
"""

import FreeCAD
from PySide import QtCore
from .primitive_base import NURBSPrimitiveCreator
from FCDirectModeling import dm_logger

class ConeCreator(NURBSPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.radius = 0.1

    def on_move_state_1(self, event_dict):
        pt = self.get_mouse_plane_pt(event_dict)
        self.radius = max(0.001, (pt - self.start_point).Length)

    def update_preview(self, debug_pt=None):
        if not self.start_point:
            return
        n = FreeCAD.Vector(0,0,1)
        if self.working_plane:
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
        
        rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
        
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
            
        self.update_nurbs_preview("cone", params, placement=final_placement)



