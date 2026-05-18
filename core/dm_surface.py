class DMSurface:
    """NURBS surface built from a grid of control points or 4 boundary curves."""

    def __init__(self, grid):
        # grid: list of rows, each row a list of FreeCAD.Vector
        self.grid = grid

    @staticmethod
    def from_boundaries(c1, c2, d1, d2, res=10):
        """
        Bilinear Coons patch sampled to a res×res grid.
        c1/c2 are the u-direction boundary curves (v=0 and v=1).
        d1/d2 are the v-direction boundary curves (u=0 and u=1).
        Each must expose .value(t) → FreeCAD.Vector for t in [0, 1].
        """
        import FreeCAD
        n = max(res, 2)
        c1_0 = c1.value(0.0)
        c1_1 = c1.value(1.0)
        c2_0 = c2.value(0.0)
        c2_1 = c2.value(1.0)

        grid = []
        for vi in range(n):
            v = vi / (n - 1)
            row = []
            for ui in range(n):
                u = ui / (n - 1)
                p_c1 = c1.value(u)
                p_c2 = c2.value(u)
                p_d1 = d1.value(v)
                p_d2 = d2.value(v)
                w00 = (1 - u) * (1 - v)
                w10 = u * (1 - v)
                w01 = (1 - u) * v
                w11 = u * v
                x = ((1-v)*p_c1.x + v*p_c2.x + (1-u)*p_d1.x + u*p_d2.x
                     - (w00*c1_0.x + w10*c1_1.x + w01*c2_0.x + w11*c2_1.x))
                y = ((1-v)*p_c1.y + v*p_c2.y + (1-u)*p_d1.y + u*p_d2.y
                     - (w00*c1_0.y + w10*c1_1.y + w01*c2_0.y + w11*c2_1.y))
                z = ((1-v)*p_c1.z + v*p_c2.z + (1-u)*p_d1.z + u*p_d2.z
                     - (w00*c1_0.z + w10*c1_1.z + w01*c2_0.z + w11*c2_1.z))
                row.append(FreeCAD.Vector(x, y, z))
            grid.append(row)
        return DMSurface(grid)

    def to_shape(self):
        import Part
        import FreeCAD
        if not self.grid or not self.grid[0]:
            return Part.Shape()
        poles = [
            [p if isinstance(p, FreeCAD.Vector) else FreeCAD.Vector(*p) for p in row]
            for row in self.grid
        ]
        try:
            bs = Part.BSplineSurface()
            bs.interpolate(poles)
            return bs.toShape()
        except Exception:
            return Part.Shape()
