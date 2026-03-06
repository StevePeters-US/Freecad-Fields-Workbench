import FreeCAD

class DMPoint:
    """3D point with optional control handles for NURBS curves."""
    def __init__(self, position, handle_in=None, handle_out=None, weight=1.0):
        if isinstance(position, DMPoint):
            self.position = FreeCAD.Vector(position.position)
            self.handle_in = FreeCAD.Vector(position.handle_in) if position.handle_in else None
            self.handle_out = FreeCAD.Vector(position.handle_out) if position.handle_out else None
            self.weight = position.weight
        else:
            self.position = FreeCAD.Vector(position)
            self.handle_in = FreeCAD.Vector(handle_in) if handle_in is not None else None
            self.handle_out = FreeCAD.Vector(handle_out) if handle_out is not None else None
            self.weight = weight

    def to_vector(self):
        """Return the position as a FreeCAD.Vector."""
        return FreeCAD.Vector(self.position)

    def is_sharp(self):
        """Returns True if the point has no handles or handles are at the position (G0)."""
        if self.handle_in is None and self.handle_out is None:
            return True
        # Check distance to avoid "near-zero" handle issues
        dist_in = (self.handle_in - self.position).Length if self.handle_in else 0
        dist_out = (self.handle_out - self.position).Length if self.handle_out else 0
        return dist_in < 1e-4 and dist_out < 1e-4

    def __repr__(self):
        return f"DMPoint({self.position.x:.2f}, {self.position.y:.2f}, {self.position.z:.2f})"
