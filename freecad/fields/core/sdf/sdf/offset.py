# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np
from freecad.fields.core.sdf.sdf_field import SdfField


class SdfOffsetField(SdfField):
    """Dilate (distance > 0) or erode (distance < 0) a field by a constant.

    Outward offset of an exact SDF is exact -- the offset surface is the set of
    points at `distance` from the source surface, which is what "push the
    surface along its own normal" means for a distance field. Inward offset is
    only exact until the erosion self-intersects; the extrude path never uses
    negative values.
    """

    def __init__(self, source: SdfField, distance: float):
        super().__init__()
        self.source = source
        self.distance = float(distance)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self.source.evaluate(point) - self.distance

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return (self.source.evaluate_grid(points) - self.distance).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        inner = self.source.to_glsl(ctx, point_var)
        u_dist = ctx.uniform("float", self.distance)
        return f"(({inner}) - {u_dist})"

    def to_glsl_sample(self, ctx, point_var="p"):
        s = self.source.to_glsl_sample(ctx, point_var)
        u_dist = ctx.uniform("float", self.distance)
        return f"fld_offset({s}, {u_dist})"

    def gradient(self, point: FreeCAD.Vector, h: float = 1e-4) -> FreeCAD.Vector:
        # Offsetting moves the surface along the normal; it does not rotate it.
        return self.source.gradient(point, h)

    def max_erosion(self) -> float:
        # A dilation by `distance` has already rounded every convex edge to that
        # radius, so eroding back by up to `distance` costs nothing.
        return self.source.max_erosion() + self.distance

    def eroded(self, distance: float):
        d = float(distance)
        if d <= 0.0:
            return None
        if d <= self.distance:
            return SdfOffsetField(self.source, self.distance - d)
        return self.source.eroded(d - self.distance)

    def bounding_box(self):
        bmin, bmax = self.source.bounding_box()
        d = FreeCAD.Vector(self.distance, self.distance, self.distance)
        return (bmin - d, bmax + d)

    def lipschitz(self) -> float:
        return self.source.lipschitz()
