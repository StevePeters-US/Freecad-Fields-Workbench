"""
Cone creator (BRep).
"""

import FreeCAD
import FreeCADGui
from pivy import coin
import numpy as np
from .base import BRepPrimitiveCreator, log_to_file

class ConeCreator(BRepPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.radius = 0.1
        self.height = 0.1
        log_to_file("ConeCreator: Initialized")

    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        
        if self.state == 0:  # Start: Center point
            self.center = pt
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
            import Part
            import FreeCAD
            
            r = abs(self.radius)
            h = abs(self.height)
            
            shape = Part.makeCone(r, 0.0, h)
            
            pl = FreeCAD.Placement()
            pl.Base = self.center
            if self.height < 0:
                pl.Rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
                
            super().update_preview(shape, pl)
        except Exception as e:
            log_to_file(f"Cone preview error: {e}")

    def finish(self):
        log_to_file("ConeCreator: Creating loft object...")
        try:
            import Part
            import FreeCAD
            
            doc = FreeCAD.activeDocument()
            if not doc:
                doc = FreeCAD.newDocument()
            
            r = abs(self.radius)
            h = abs(self.height)
            
            # Loft: Base circle and Top circle/vertex
            base_circle = Part.makeCircle(r, FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1))
            top_circle = Part.makeCircle(1e-5, FreeCAD.Vector(0,0,h), FreeCAD.Vector(0,0,1)) # 0 radius circle approximating vertex
            
            # Create Wires
            base_wire = Part.Wire(base_circle)
            top_wire = Part.Wire(top_circle)
            
            # Loft them (ruled solid)
            loft_shape = Part.makeLoft([base_wire, top_wire], True, True) 
            
            obj = doc.addObject("Part::Feature", "Cone")
            obj.Shape = loft_shape
            
            pl = FreeCAD.Placement()
            pl.Base = self.center
            if self.height < 0:
                pl.Rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
            
            obj.Placement = pl
            FreeCAD.activeDocument().recompute()
            log_to_file("ConeCreator: Object created successfully.")
            
        except Exception as e:
            msg = f"ConeCreator: Error creating object: {e}"
            log_to_file(msg)
            FreeCAD.Console.PrintError(msg + "\n")
            
        super().finish()
