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
            
            r = abs(self.radius)
            if r < 0.001: r = 0.001
            
            # Construct from 8 triangular pieces (octants)
            wedges = []
            for u in [0, 90, 180, 270]:
                for v_pairs in [(0, 90), (-90, 0)]:
                    # makeSphere(radius, center, dir, angle1, angle2, angle3)
                    # angle1/angle2 are V angles (-90 to 90)
                    # angle3 is U sweep
                    wedge = Part.makeSphere(r, FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), v_pairs[0], v_pairs[1], 90)
                    
                    rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), u)
                    pl = FreeCAD.Placement(FreeCAD.Vector(0,0,0), rot)
                    wedge.transformShape(pl.toMatrix())
                    
                    wedges.append(wedge)
            
            # Fuse the 8 wedges to form the final solid
            sphere_shape = wedges[0]
            for w in wedges[1:]:
                sphere_shape = sphere_shape.fuse(w)
            
            from FCDirectModeling.dm_part import create_dm_part
            obj = create_dm_part("Sphere")
            obj.Shape = sphere_shape
            obj.Placement.Base = self.center
            
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.Deviation = 0.05
            
            doc.recompute()
            log_to_file("SphereCreator: Object created successfully.")
        except Exception as e:
            msg = f"SphereCreator: Error creating object: {e}"
            log_to_file(msg)
            FreeCAD.Console.PrintError(msg + "\n")
            
        super().finish()
