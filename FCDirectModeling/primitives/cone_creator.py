"""
SDF Cone creator.
"""

import FreeCAD
import FreeCADGui
from pivy import coin
import numpy as np
from .base import SDFPrimitiveCreator, log_to_file
import FCDirectModeling.sdf_lib as sdf_lib
from FCDirectModeling.primitives.cone import SDFCone
import FCDirectModeling.sdf_utils as sdf_utils

class ConeCreator(SDFPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.radius = 0.1
        self.height = 0.1
        log_to_file("ConeCreator: Initialized")

    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        
        if self.state == 0:  # Start: Center point
            self.center = pt
            self.sdf_trans.translation.setValue(pt.x, pt.y, pt.z)
            self.state = 1
            log_to_file("ConeCreator: Center set")
            
        elif self.state == 1:  # Radius set, start Height
            self.state = 2
            log_to_file("ConeCreator: Radius set")
            
        elif self.state == 2:  # Height set, Finish
            self.finish()

    def handle_move(self, event_dict):
        if self.state == 1: # Dragging Radius
            pt = self.get_point_on_plane(event_dict)
            self.radius = (pt - self.center).Length
            if self.radius < 0.001: self.radius = 0.001
            
            # Preview with default height
            self.update_preview()
            
        elif self.state == 2: # Dragging Height
            axis = FreeCAD.Vector(0, 0, 1) # Local Z
            pt_on_axis = self.get_closest_point_on_axis(event_dict, self.center, axis)
            
            # vector from center to mouse proj
            diff = pt_on_axis - self.center
            
            # dot product to get signed height along Z
            self.height = diff.dot(axis)
            
            if abs(self.height) < 0.001:
                self.height = 0.001 if self.height >= 0 else -0.001
                
            self.update_preview()
            
    def update_preview(self):
        try:
            # Create temporary SDF for preview
            # Note: SDFCone expects (radius, height)
            # With updated SDFCone, height is absolute height from base (0) to tip (h).
            sdf = SDFCone(self.radius, self.height)
            self.update_sdf_preview(sdf)
            self.view.redraw()
        except Exception as e:
            log_to_file(f"ConeCreator Preview Error: {e}")

    def create_object(self):
        log_to_file("ConeCreator: Creating object...")
        try:
            # Ensure positive dimensions where appropriate
            # SDFCone handles signed height, but for property consistency:
            r = abs(self.radius)
            h = self.height # Signed height is useful for direction
            
            obj = self.create_generic_sdf(
                "SDF_Cone",
                [
                    ("App::PropertyLength", "Radius", "SDF", "Cone Radius"),
                    ("App::PropertyLength", "Height", "SDF", "Cone Height"),
                ],
                {"Radius": r, "Height": abs(h)},
            )
            
            # Handle orientation if height was negative
            if h < 0:
                # Flip 180 deg around X
                pl = obj.Placement
                pl.Rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
                obj.Placement = pl
                
            log_to_file("ConeCreator: Object created successfully.")
            
        except Exception as e:
            msg = f"ConeCreator: Error creating object: {e}"
            log_to_file(msg)
            import traceback
            traceback.print_exc()

