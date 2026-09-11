# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from .sdf_field import SdfField
from .sdf2d.sdf2d_field import Sdf2dField
from freecad.fields.core.sdf.sdf_prism import SdfProfilePrismField, SdfPrismExtrusionField, SdfPlaneCap
from freecad.fields.core import fld_logger
import numpy as np


class SdfExtrusionField(SdfField):
    """
    Extrudes a 2D profile along the local Z axis by the given height.
    The profile lives in the local XY plane; the solid extends ±height/2 along Z.

    Delegates to SdfPrismExtrusionField with SdfProfilePrismField walls and two SdfPlaneCap caps.
    """

    def __init__(self, profile: Sdf2dField, height: float, placement: FreeCAD.Placement = None):
        super().__init__()
        self.profile = profile
        self.height = height
        self.placement = placement
        self.inv_matrix = self._compute_inv_matrix(self.placement)

        if self.placement is not None:
            origin = np.array([self.placement.Base.x, self.placement.Base.y, self.placement.Base.z], dtype=np.float64)
            rot = self.placement.Rotation
            v1 = rot.multVec(FreeCAD.Vector(1, 0, 0))
            v2 = rot.multVec(FreeCAD.Vector(0, 1, 0))
            v3 = rot.multVec(FreeCAD.Vector(0, 0, 1))
            e1 = np.array([v1.x, v1.y, v1.z], dtype=np.float64)
            e2 = np.array([v2.x, v2.y, v2.z], dtype=np.float64)
            axis = np.array([v3.x, v3.y, v3.z], dtype=np.float64)
        else:
            origin = np.array([0.0, 0.0, 0.0], dtype=np.float64)
            e1 = np.array([1.0, 0.0, 0.0], dtype=np.float64)
            e2 = np.array([0.0, 1.0, 0.0], dtype=np.float64)
            axis = np.array([0.0, 0.0, 1.0], dtype=np.float64)

        half_h = float(height * 0.5)
        walls = SdfProfilePrismField(self.profile, origin, e1, e2)
        bottom = SdfPlaneCap(origin, -axis, offset=half_h)
        top = SdfPlaneCap(origin, axis, offset=half_h)

        self._prism = SdfPrismExtrusionField(walls, bottom, top, exact_corner=True)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self._prism.evaluate(point)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return self._prism.evaluate_grid(points)

    def to_glsl(self, ctx, point_var: str = "p") -> str:
        return self._prism.to_glsl(ctx, point_var)

    def bounding_box(self):
        half_h = self.height * 0.5
        if hasattr(self.profile, 'bbox_2d'):
            minx, miny, maxx, maxy = self.profile.bbox_2d()
        else:
            try:
                r = _profile_radial_extent(self.profile)
            except Exception as e:
                fld_logger.debug(f"SdfExtrusionField.bounding_box radial extent estimation failed, defaulting to 1000.0: {e}")
                r = 1000.0
            minx, miny, maxx, maxy = -r, -r, r, r

        all_corners = [
            FreeCAD.Vector(x, y, z)
            for x in (minx, maxx) for y in (miny, maxy) for z in (-half_h, half_h)
        ]
        if self.placement is not None:
            world = [self.placement.multVec(c) for c in all_corners]
            return (
                FreeCAD.Vector(min(p.x for p in world), min(p.y for p in world), min(p.z for p in world)),
                FreeCAD.Vector(max(p.x for p in world), max(p.y for p in world), max(p.z for p in world)),
            )
        return (FreeCAD.Vector(minx, miny, -half_h), FreeCAD.Vector(maxx, maxy, half_h))

    def lipschitz(self) -> float:
        return self._prism.lipschitz()

    def max_erosion(self) -> float:
        h_cap = self.height * 0.5
        prof_cap = getattr(self.profile, 'max_erosion', lambda: h_cap)()
        return min(h_cap, prof_cap)

    def eroded(self, distance: float):
        d = float(distance)
        if d <= 0.0 or d > self.max_erosion():
            return None
        new_h = self.height - 2.0 * d
        if new_h <= 0.0:
            return None
        prof_eroded = getattr(self.profile, 'eroded', lambda dist: None)(d)
        if prof_eroded is None:
            prof_eroded = self.profile
        return SdfExtrusionField(prof_eroded, height=new_h, placement=self.placement)


def _profile_radial_extent(profile: Sdf2dField, samples: int = 32, search_range: float = 2000.0) -> float:
    """Estimate the radial bounding radius of a 2D profile via binary search along axes."""
    max_r = 0.0
    for angle_i in range(samples):
        import math
        a = math.pi * 2.0 * angle_i / samples
        dx, dy = math.cos(a), math.sin(a)
        lo, hi = 0.0, search_range
        for _ in range(32):
            mid = (lo + hi) * 0.5
            if profile.evaluate_2d(dx * mid, dy * mid) < 0:
                lo = mid
            else:
                hi = mid
        max_r = max(max_r, hi)
    return max_r
