# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np
from freecad.fields.core.sdf.sdf_field import SdfField


class SdfTranslateField(SdfField):
    """Rigidly translate a field. Exact: S_t(p) = S(p - offset).

    Unlike SdfOffsetField (which dilates, changing curvature), this preserves
    the source surface exactly -- same shape, moved. That is what makes it the
    right cap for an extrusion: the new face IS the old face.
    """

    def __init__(self, source: SdfField, offset):
        super().__init__()
        self.source = source
        self.offset = np.asarray(
            [offset.x, offset.y, offset.z] if hasattr(offset, "x")
            else [offset[0], offset[1], offset[2]], dtype=np.float64)

    def _shift(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        return FreeCAD.Vector(point.x - self.offset[0],
                              point.y - self.offset[1],
                              point.z - self.offset[2])

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self.source.evaluate(self._shift(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64) - self.offset
        return self.source.evaluate_grid(pts).astype(np.float32)

    def gradient(self, point: FreeCAD.Vector, h: float = 1e-4) -> FreeCAD.Vector:
        # Translation moves the surface; it does not rotate it.
        return self.source.gradient(self._shift(point), h)

    def to_glsl(self, ctx, point_var="p"):
        u_off = ctx.uniform("vec3", self.offset.tolist())
        return self.source.to_glsl(ctx, f"({point_var} - {u_off})")

    def to_glsl_sample(self, ctx, point_var="p"):
        u_off = ctx.uniform("vec3", self.offset.tolist())
        return self.source.to_glsl_sample(ctx, f"({point_var} - {u_off})")

    def max_erosion(self) -> float:
        return self.source.max_erosion()

    def eroded(self, distance: float):
        core = self.source.eroded(distance)
        if core is None:
            return None
        return SdfTranslateField(core, FreeCAD.Vector(*self.offset))

    def bounding_box(self):
        bmin, bmax = self.source.bounding_box()
        d = FreeCAD.Vector(*self.offset)
        return (bmin + d, bmax + d)

    def lipschitz(self) -> float:
        return self.source.lipschitz()
