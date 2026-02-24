"""
Torus creator (BRep).
"""

import FreeCAD
import numpy as np
from .base import BRepPrimitiveCreator, log_to_file

class TorusCreator(BRepPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.R = 1.0  # Major radius
        self.r = 0.5  # Minor radius

    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 0:  # Set center
            self.center = pt
            self.state = 1
        elif self.state == 1:  # Lock major radius
            self.state = 2
        elif self.state == 2:  # Finish
            self.finish()

    def handle_move(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 1:
            self.R = max(0.1, (pt - self.center).Length)
            self.update_preview()

        elif self.state == 2:
            dist = (pt - self.center).Length
            self.r = max(0.01, abs(dist - self.R))
            self.update_preview()

    def update_preview(self):
        try:
            import Part
            import FreeCAD
            
            shape = Part.makeTorus(self.R, self.r)
            pl = FreeCAD.Placement()
            pl.Base = self.center
            
            super().update_preview(shape, pl)
        except Exception as e:
            log_to_file(f"Torus preview error: {e}")

    def finish(self):
        log_to_file("TorusCreator: Creating object...")
        try:
            import Part
            import FreeCAD
            
            doc = FreeCAD.activeDocument()
            if not doc:
                doc = FreeCAD.newDocument()
            
            # Revolve Approach: Revolve a minor circle around the Z axis
            # Place minor circle at distance R on X axis, extending in XZ plane
            minor_circle = Part.makeCircle(self.r, FreeCAD.Vector(self.R, 0, 0), FreeCAD.Vector(0, 1, 0))
            wire = Part.Wire(minor_circle)
            face = Part.Face(wire)
            
            # Revolve around Z axis 360 degrees
            torus_shape = face.revolve(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 360)
            
            from FCDirectModeling.dm_part import create_dm_part
            obj = create_dm_part("Torus")
            obj.Shape = torus_shape
            
            obj.Placement.Base = self.center
            
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.Deviation = 0.05
                
            FreeCAD.activeDocument().recompute()
            log_to_file("TorusCreator: Object created successfully.")
            
        except Exception as e:
            msg = f"TorusCreator: Error creating object: {e}"
            log_to_file(msg)
            FreeCAD.Console.PrintError(msg + "\n")
            
        super().finish()
