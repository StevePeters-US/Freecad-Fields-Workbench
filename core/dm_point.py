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

        # Coin3D visual state (set by draw_point, cleared by undraw)
        self._point_sep = None
        self._point_mat = None
        self._point_xf = None
        self._point_sphere = None
        self._point_parent = None

    # ------------------------------------------------------------------
    # Coin3D sphere visualization
    # ------------------------------------------------------------------

    def draw_point(self, parent_node, radius=5.0, color=(1.0, 0.5, 0.0)):
        """
        Attach a Coin3D sphere to parent_node at this point's world position.

        Follows the project sphere-handle pattern:
          SoSeparator > SoMaterial + SoTransform + SoSphere
        Call undraw() to remove. Safe to call again (undrawss first).
        """
        from pivy import coin
        if self._point_sep is not None:
            self.undraw()

        self._point_parent = parent_node
        self._point_sep = coin.SoSeparator()

        self._point_mat = coin.SoMaterial()
        self._point_mat.diffuseColor.setValue(*color)
        self._point_mat.specularColor.setValue(0.8, 0.8, 0.8)
        self._point_mat.shininess.setValue(0.7)

        self._point_xf = coin.SoTransform()
        self._point_xf.translation.setValue(
            self.position.x, self.position.y, self.position.z
        )

        self._point_sphere = coin.SoSphere()
        self._point_sphere.radius = radius

        self._point_sep.addChild(self._point_mat)
        self._point_sep.addChild(self._point_xf)
        self._point_sep.addChild(self._point_sphere)
        parent_node.addChild(self._point_sep)

    def update_draw(self, radius=None):
        """Sync the sphere's world position to self.position, and optionally update radius."""
        if self._point_xf is None:
            return
        self._point_xf.translation.setValue(
            self.position.x, self.position.y, self.position.z
        )
        if radius is not None and self._point_sphere is not None:
            self._point_sphere.radius = radius

    def set_color(self, color):
        """Set the sphere diffuse color. color is an (r, g, b) tuple."""
        if self._point_mat is not None:
            self._point_mat.diffuseColor.setValue(*color)

    def undraw(self):
        """Remove the sphere from its parent node and clear visual state."""
        try:
            if self._point_parent is not None and self._point_sep is not None:
                self._point_parent.removeChild(self._point_sep)
        except Exception:
            pass
        self._point_sep = None
        self._point_mat = None
        self._point_xf = None
        self._point_sphere = None
        self._point_parent = None

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def to_vector(self):
        """Return the position as a FreeCAD.Vector."""
        return FreeCAD.Vector(self.position)

    def is_sharp(self):
        """Returns True if the point has no handles or handles are at the position (G0)."""
        if self.handle_in is None and self.handle_out is None:
            return True
        dist_in = (self.handle_in - self.position).Length if self.handle_in else 0
        dist_out = (self.handle_out - self.position).Length if self.handle_out else 0
        return dist_in < 1e-4 and dist_out < 1e-4

    def __repr__(self):
        return f"DMPoint({self.position.x:.2f}, {self.position.y:.2f}, {self.position.z:.2f})"
