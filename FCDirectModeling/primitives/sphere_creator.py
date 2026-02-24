"""
Sphere creator (BRep).
"""

import FreeCAD
import numpy as np
from .base import BRepPrimitiveCreator, log_to_file

class SphereCreator(BRepPrimitiveCreator):
    def __init__(self):
        super().__init__()
        log_to_file("SphereCreator: Initializing...")
        self.radius = 1.0

    def handle_click(self, event_dict):
        log_to_file(f"SphereCreator: Click! State={self.state}")
        pt = self.get_point_on_plane(event_dict)

        if self.state == 0:
            self.center = pt
            self.state = 1
        elif self.state == 1:
            self.handle_move(event_dict)
            log_to_file("SphereCreator: Finishing interaction...")
            self.finish()
            return True

    def handle_move(self, event_dict):
        if self.state == 1:
            pt = self.get_point_on_plane(event_dict)
            self.radius = max(0.01, (pt - self.center).Length)
            self.update_preview()
            
    def update_preview(self):
        try:
            import Part
            import FreeCAD
            shape = Part.makeSphere(self.radius)
            pl = FreeCAD.Placement()
            pl.Base = self.center
            super().update_preview(shape, pl)
        except Exception as e:
            log_to_file(f"Sphere preview error: {e}")

    def finish(self):
        log_to_file("SphereCreator: Creating object...")
        try:
            import Part
            import FreeCAD
            
            doc = FreeCAD.activeDocument()
            if not doc:
                doc = FreeCAD.newDocument()
            
            # Loft/Revolve Approach:
            # Create a semi-circle arc from -Z to +Z
            p1 = FreeCAD.Vector(0, 0, -self.radius)
            p2 = FreeCAD.Vector(self.radius, 0, 0)
            p3 = FreeCAD.Vector(0, 0, self.radius)
            
            arc = Part.Arc(p1, p2, p3).toShape()
            wire = Part.Wire([arc])
            face = Part.Face(wire)
            
            # Revolve around Z axis 360 degrees
            sphere_shape = face.revolve(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 360)
            
            obj = doc.addObject("Part::Feature", "Sphere")
            obj.Shape = sphere_shape
            obj.Placement.Base = self.center
            
            doc.recompute()
            log_to_file("SphereCreator: Object created successfully.")
        except Exception as e:
            msg = f"SphereCreator: Error creating object: {e}"
            log_to_file(msg)
            FreeCAD.Console.PrintError(msg + "\n")
            
        super().finish()
