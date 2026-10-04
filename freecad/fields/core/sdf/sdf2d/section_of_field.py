# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""A 2D profile that is a planar cross-section of a 3D SdfField."""
import numpy as np
import FreeCAD
from .sdf2d_field import Sdf2dField


class Sdf2dSectionOfField(Sdf2dField):
    """The (u, v) slice of a 3D field through `origin`, spanned by `ex` and `ey`.

    This is what "use an existing solid as the profile" has to mean. A 3D field
    is in world coordinates, so sampling it at (u, v, 0) -- which is what the
    sweep used to do -- slices through the world origin plane and ignores where
    the solid actually is: a solid parked away from z=0 gives an empty profile,
    and a large one centred near the origin gives a slice that is entirely
    interior and so has no boundary to sweep at all. Taking the section through
    the solid's own placement, in its own frame, is both well defined and what
    the user picking the solid is asking for.
    """

    @staticmethod
    def _xyz(v, default=(0.0, 0.0, 0.0)):
        """Accept a FreeCAD.Vector, a tuple, or a numpy row alike."""
        if v is None:
            return np.array(default, dtype=np.float64)
        if hasattr(v, "x") and hasattr(v, "y") and hasattr(v, "z"):
            return np.array([v.x, v.y, v.z], dtype=np.float64)
        return np.array([v[0], v[1], v[2]], dtype=np.float64)

    def __init__(self, field, origin=None, ex=None, ey=None, half_extent=None):
        self.field = field
        self.origin = self._xyz(origin)
        self.ex = self._unit(ex, (1.0, 0.0, 0.0))
        self.ey = self._unit(ey, (0.0, 1.0, 0.0))
        # Re-orthogonalise so a caller passing a sloppy pair cannot shear the section.
        self.ey = self.ey - np.dot(self.ey, self.ex) * self.ex
        n = np.linalg.norm(self.ey)
        self.ey = self.ey / n if n > 1e-9 else np.array([0.0, 1.0, 0.0])
        self.half_extent = half_extent if half_extent is not None else self._derive_half_extent()

    @classmethod
    def _unit(cls, v, default):
        a = cls._xyz(v, default)
        n = np.linalg.norm(a)
        return a / n if n > 1e-9 else np.array(default, dtype=np.float64)

    def _derive_half_extent(self):
        """How far out the section can reach, from the source solid's own bounds."""
        try:
            bmin, bmax = self.field.bounding_box()
            diag = np.linalg.norm([bmax.x - bmin.x, bmax.y - bmin.y, bmax.z - bmin.z])
            return float(max(1e-3, 0.5 * diag))
        except Exception:
            return 100.0

    @classmethod
    def from_object(cls, obj, field):
        """Section a document object's field through its own placement frame."""
        pl = getattr(obj, "Placement", None)
        if pl is None:
            return cls(field)
        rot = pl.Rotation
        return cls(field,
                   origin=pl.Base,
                   ex=rot.multVec(FreeCAD.Vector(1, 0, 0)),
                   ey=rot.multVec(FreeCAD.Vector(0, 1, 0)))

    def _to_world(self, pts_2d: np.ndarray) -> np.ndarray:
        u = pts_2d[:, 0:1]
        v = pts_2d[:, 1:2]
        return self.origin[None, :] + u * self.ex[None, :] + v * self.ey[None, :]

    def bbox_2d(self):
        h = self.half_extent
        return (-h, -h, h, h)

    def evaluate_2d(self, x: float, y: float) -> float:
        return float(self.evaluate_2d_grid(np.array([[x, y]], dtype=np.float64))[0])

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        world = self._to_world(np.asarray(pts, dtype=np.float64))
        return np.asarray(self.field.evaluate_grid(world), dtype=np.float32)

    def to_glsl_2d(self, ctx, pvar: str = "q") -> str:
        o = ctx.uniform("vec3", list(self.origin))
        ex = ctx.uniform("vec3", list(self.ex))
        ey = ctx.uniform("vec3", list(self.ey))
        return self.field.to_glsl(ctx, f"({o} + {ex} * {pvar}.x + {ey} * {pvar}.y)")
