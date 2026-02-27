import FreeCAD

class NurbsPoint:
    """3D point with optional control handles for NURBS curves."""
    def __init__(self, position, handle_in=None, handle_out=None, weight=1.0):
        self.position = FreeCAD.Vector(position)
        self.handle_in = FreeCAD.Vector(handle_in) if handle_in is not None else None
        self.handle_out = FreeCAD.Vector(handle_out) if handle_out is not None else None
        self.weight = weight

    def to_vector(self):
        """Return the position as a FreeCAD.Vector."""
        return FreeCAD.Vector(self.position)

    def is_sharp(self):
        """Returns True if the point has no handles (G0 continuity)."""
        return self.handle_in is None and self.handle_out is None

    def __repr__(self):
        return f"NurbsPoint({self.position.x:.2f}, {self.position.y:.2f}, {self.position.z:.2f})"
