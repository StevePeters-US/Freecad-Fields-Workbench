# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField, swept_rotation_bounds
import numpy as np

AXES = {"X": 0, "Y": 1, "Z": 2}


def _slab_swept_bounds(u_lo, u_hi, v_lo, v_hi, centre_u, centre_v, th_lo, th_hi):
    """In-plane AABB of a rectangle swept through each of N angular ranges.

    `th_lo`/`th_hi` are (N,) arrays; slab i sweeps the rectangle through every
    rotation in [th_lo[i], th_hi[i]] about (centre_u, centre_v). Returns four (N,)
    arrays. This is `swept_rotation_bounds` vectorised over many narrow arcs -- the
    scalar one is exact and this keeps that: the extreme of a coordinate over a
    rotated rectangle is attained at a rotated corner, so tracing the four corners
    across the arc covers the swept solid. A corner's coordinate peaks at an arc
    endpoint unless the arc sweeps through the direction where that coordinate is
    extremal, which is the case the `_covers` term adds back.
    """
    cu = np.array([u_lo, u_lo, u_hi, u_hi], dtype=np.float64) - centre_u
    cv = np.array([v_lo, v_hi, v_lo, v_hi], dtype=np.float64) - centre_v
    r = np.hypot(cu, cv)
    phi = np.arctan2(cv, cu)

    a0 = phi[None, :] + np.asarray(th_lo, dtype=np.float64)[:, None]
    a1 = phi[None, :] + np.asarray(th_hi, dtype=np.float64)[:, None]
    a0, a1 = np.minimum(a0, a1), np.maximum(a0, a1)

    two_pi = 2.0 * np.pi

    def _covers(target):
        """Does the arc [a0, a1] pass through `target` (mod 2*pi)?"""
        k = np.ceil((a0 - target) / two_pi)
        return (target + two_pi * k) <= a1

    cos0, cos1 = r * np.cos(a0), r * np.cos(a1)
    sin0, sin1 = r * np.sin(a0), r * np.sin(a1)

    u_max = np.where(_covers(0.0), r, np.maximum(cos0, cos1))
    u_min = np.where(_covers(np.pi), -r, np.minimum(cos0, cos1))
    v_max = np.where(_covers(0.5 * np.pi), r, np.maximum(sin0, sin1))
    v_min = np.where(_covers(1.5 * np.pi), -r, np.minimum(sin0, sin1))

    return (u_min.min(axis=1) + centre_u, u_max.max(axis=1) + centre_u,
            v_min.min(axis=1) + centre_v, v_max.max(axis=1) + centre_v)


class SdfBendField(SdfField):
    """Bend deformation wrapping a source SDF field.

    Curves the geometry around an axis by bending the coordinate space.
    CPU evaluate() and GPU to_glsl() are stubs.
    """

    def __init__(self, source: SdfField, axis: str = "Z", bend_angle: float = 45.0,
                 bend_origin: float = 0.0):
        super().__init__()
        self.source = source
        self.axis = axis              # axis around which to bend: "X", "Y", or "Z"
        self.bend_angle = bend_angle  # total bend angle in degrees
        self.bend_origin = bend_origin  # position along axis where bend centre sits

    # ------------------------------------------------------------------
    # SdfField interface
    # ------------------------------------------------------------------

    # Per axis: the two coordinate indices the bend rotates in, which of that pair
    # carries the bend origin, and the sign of the solid's rotation relative to
    # +bend_angle. Unlike twist, the rotation plane contains the bend axis.
    _PLANE = {"X": (0, 1, 0, +1.0), "Y": (1, 2, 0, +1.0), "Z": (0, 2, 1, -1.0)}

    def bounding_box(self) -> tuple:
        """Conservative axis-aligned bounding box of the bent solid.

        Enumerated in *query* space, not source space, and that is the whole point.
        `evaluate` maps a query point q to a source point p by rotating q about the
        bend centre through an angle that depends on q's own axis coordinate, so the
        solid is the pre-image of the source box. Chasing that pre-image forwards --
        picking a source point and solving for the q that lands on it -- means solving
        `theta = rate * r * cos(theta + phi)`, which has **more than one root as soon
        as |rate * r| > 1**: the bend folds over itself and one point of the source has
        several pre-images. A single Newton root per source point silently drops the
        other branches, and which branch it lands on flips with the sign of the angle,
        so the box came out short on one side (measured: 13 mm of solid outside the
        box at -180 degrees about Z, and no mirror symmetry against +180).

        Slicing by q instead removes the fold entirely. Hold q's axis coordinate
        fixed and the rotation angle is fixed with it, so that slice of the map is a
        plain rigid rotation and `swept_rotation_bounds`' exactness argument applies
        unchanged. Partition the axis into slabs, bound each slab by the sweep over
        its own narrow angular range, intersect that with the slab itself, and union.
        Every slab's bound is a superset of its slice, so the union contains the
        solid whatever the angle does; refining the slabs only tightens it.
        """
        src_min, src_max = self.source.bounding_box()
        if abs(self.bend_angle) < 1e-12:
            return src_min, src_max

        cache_key = (
            getattr(self.source, "geometry_version", None),
            (src_min.x, src_min.y, src_min.z),
            (src_max.x, src_max.y, src_max.z),
            self.axis,
            float(self.bend_angle),
            float(self.bend_origin),
        )
        if getattr(self, "_bbox_cache_key", None) == cache_key and getattr(self, "_bbox_cache_val", None) is not None:
            return self._bbox_cache_val

        import math
        lo = [src_min.x, src_min.y, src_min.z]
        hi = [src_max.x, src_max.y, src_max.z]

        # The bend rotates in the (axis, next-axis) plane and leaves the third
        # coordinate alone -- x bends into y, y into z, z into x, which is what
        # `evaluate` does for each of the three cases.
        u_i = AXES.get(self.axis, 2)
        v_i = (u_i + 1) % 3

        extent = hi[u_i] - lo[u_i]
        if extent < 1e-6:
            return src_min, src_max
        rate = math.radians(self.bend_angle) / extent
        if abs(rate) < 1e-12:
            return src_min, src_max

        centre_u, centre_v = float(self.bend_origin), 0.0

        # A rotation preserves radius, so a query point's distance from the bend
        # centre equals its source point's. Nothing outside that radius can be in
        # the solid, which bounds the slab sweep without assuming anything about
        # the angle.
        r_max = max(math.hypot(u - centre_u, v - centre_v)
                    for u in (lo[u_i], hi[u_i])
                    for v in (lo[v_i], hi[v_i]))
        if r_max < 1e-12:
            return src_min, src_max

        n_slabs = 256
        edges = np.linspace(-r_max, r_max, n_slabs + 1)
        s0, s1 = edges[:-1], edges[1:]
        su_lo, su_hi, sv_lo, sv_hi = _slab_swept_bounds(
            lo[u_i], hi[u_i], lo[v_i], hi[v_i],
            centre_u, centre_v, rate * s0, rate * s1)

        # Slab i only contributes the part of its swept box that actually lies
        # inside slab i -- that intersection is what makes this tighter than one
        # sweep over the whole angular range.
        a_lo = np.maximum(su_lo, centre_u + s0)
        a_hi = np.minimum(su_hi, centre_u + s1)
        live = a_lo <= a_hi

        if live.any():
            lo[u_i], hi[u_i] = float(a_lo[live].min()), float(a_hi[live].max())
            lo[v_i], hi[v_i] = float(sv_lo[live].min()), float(sv_hi[live].max())
        else:
            # Unreachable for a real box, but a bound that is merely loose beats
            # one that is missing: fall back to the whole-range sweep.
            u0, u1, v0, v1 = swept_rotation_bounds(
                src_min, src_max, u_i, v_i, centre_u, centre_v,
                rate * -r_max, rate * r_max)
            lo[u_i], hi[u_i] = u0, u1
            lo[v_i], hi[v_i] = v0, v1

        result = (FreeCAD.Vector(*lo), FreeCAD.Vector(*hi))
        self._bbox_cache_key = cache_key
        self._bbox_cache_val = result
        return result

    def lipschitz(self) -> float:
        try:
            bbox = self.source.bounding_box()
            min_c, max_c = bbox[0], bbox[1]
            if self.axis == "X":
                axis_extent = max_c.x - min_c.x
            elif self.axis == "Y":
                axis_extent = max_c.y - min_c.y
            else:
                axis_extent = max_c.z - min_c.z
        except Exception:
            return self.source.lipschitz()
            
        if axis_extent < 1e-6:
            return self.source.lipschitz()
            
        import math
        kappa = math.radians(self.bend_angle) / axis_extent
        
        corners = [
            [min_c.x, min_c.y, min_c.z],
            [min_c.x, min_c.y, max_c.z],
            [min_c.x, max_c.y, min_c.z],
            [min_c.x, max_c.y, max_c.z],
            [max_c.x, min_c.y, min_c.z],
            [max_c.x, min_c.y, max_c.z],
            [max_c.x, max_c.y, min_c.z],
            [max_c.x, max_c.y, max_c.z],
        ]
        
        r_max = 0.0
        for x, y, z in corners:
            if self.axis == "X":
                r = math.sqrt((x - self.bend_origin)**2 + y*y)
            elif self.axis == "Y":
                r = math.sqrt((y - self.bend_origin)**2 + z*z)
            else: # "Z"
                r = math.sqrt((z - self.bend_origin)**2 + x*x)
            if r > r_max:
                r_max = r
                
        return self.source.lipschitz() * (1.0 + abs(kappa) * r_max)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        import math
        try:
            bbox = self.source.bounding_box()
            min_c, max_c = bbox[0], bbox[1]
            if self.axis == "X":
                L = max_c.x - min_c.x
            elif self.axis == "Y":
                L = max_c.y - min_c.y
            else:
                L = max_c.z - min_c.z
        except Exception:
            L = 100.0
        if L < 1e-6:
            L = 100.0
            
        rate = math.radians(self.bend_angle) / L
        x, y, z = point.x, point.y, point.z
        
        if self.axis == "X":
            x_local = x - self.bend_origin
            theta = x_local * rate
            c = math.cos(theta)
            s = math.sin(theta)
            p_rot = FreeCAD.Vector(c * x_local + s * y + self.bend_origin, -s * x_local + c * y, z)
        elif self.axis == "Y":
            y_local = y - self.bend_origin
            theta = y_local * rate
            c = math.cos(theta)
            s = math.sin(theta)
            p_rot = FreeCAD.Vector(x, c * y_local + s * z + self.bend_origin, -s * y_local + c * z)
        else: # "Z"
            z_local = z - self.bend_origin
            theta = z_local * rate
            c = math.cos(theta)
            s = math.sin(theta)
            p_rot = FreeCAD.Vector(-s * z_local + c * x, y, c * z_local + s * x + self.bend_origin)
            
        return self.source.evaluate(p_rot)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        try:
            bbox = self.source.bounding_box()
            min_c, max_c = bbox[0], bbox[1]
            if self.axis == "X":
                L = max_c.x - min_c.x
            elif self.axis == "Y":
                L = max_c.y - min_c.y
            else:
                L = max_c.z - min_c.z
        except Exception:
            L = 100.0
        if L < 1e-6:
            L = 100.0
            
        rate = np.radians(self.bend_angle) / L
        x = points[:, 0]
        y = points[:, 1]
        z = points[:, 2]
        
        pts_rot = np.empty_like(points)
        
        if self.axis == "X":
            x_local = x - self.bend_origin
            theta = x_local * rate
            c = np.cos(theta)
            s = np.sin(theta)
            pts_rot[:, 0] = c * x_local + s * y + self.bend_origin
            pts_rot[:, 1] = -s * x_local + c * y
            pts_rot[:, 2] = z
        elif self.axis == "Y":
            y_local = y - self.bend_origin
            theta = y_local * rate
            c = np.cos(theta)
            s = np.sin(theta)
            pts_rot[:, 0] = x
            pts_rot[:, 1] = c * y_local + s * z + self.bend_origin
            pts_rot[:, 2] = -s * y_local + c * z
        else: # "Z"
            z_local = z - self.bend_origin
            theta = z_local * rate
            c = np.cos(theta)
            s = np.sin(theta)
            pts_rot[:, 0] = -s * z_local + c * x
            pts_rot[:, 1] = y
            pts_rot[:, 2] = c * z_local + s * x + self.bend_origin
            
        return self.source.evaluate_grid(pts_rot).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        import math
        try:
            bbox = self.source.bounding_box()
            min_c, max_c = bbox[0], bbox[1]
            if self.axis == "X":
                L = max_c.x - min_c.x
            elif self.axis == "Y":
                L = max_c.y - min_c.y
            else:
                L = max_c.z - min_c.z
        except Exception:
            L = 100.0
        if L < 1e-6:
            L = 100.0
            
        rate_val = math.radians(self.bend_angle) / L
        rate_u = ctx.uniform("float", rate_val)
        origin_u = ctx.uniform("float", self.bend_origin)
        
        if self.axis == "X":
            ctx.add_custom_helper("bend_x", _GLSL_BEND_X)
            deformed_var = f"bend_x({point_var}, {rate_u}, {origin_u})"
        elif self.axis == "Y":
            ctx.add_custom_helper("bend_y", _GLSL_BEND_Y)
            deformed_var = f"bend_y({point_var}, {rate_u}, {origin_u})"
        else: # Z
            ctx.add_custom_helper("bend_z", _GLSL_BEND_Z)
            deformed_var = f"bend_z({point_var}, {rate_u}, {origin_u})"
            
        return self.source.to_glsl(ctx, deformed_var)

    def to_glsl_sample(self, ctx, point_var="p"):
        import math
        try:
            bbox = self.source.bounding_box()
            min_c, max_c = bbox[0], bbox[1]
            if self.axis == "X":
                L = max_c.x - min_c.x
            elif self.axis == "Y":
                L = max_c.y - min_c.y
            else:
                L = max_c.z - min_c.z
        except Exception:
            L = 100.0
        if L < 1e-6:
            L = 100.0
            
        rate_val = math.radians(self.bend_angle) / L
        rate_u = ctx.uniform("float", rate_val)
        origin_u = ctx.uniform("float", self.bend_origin)
        
        if self.axis == "X":
            ctx.add_custom_helper("bend_x", _GLSL_BEND_X)
            deformed_var = f"bend_x({point_var}, {rate_u}, {origin_u})"
        elif self.axis == "Y":
            ctx.add_custom_helper("bend_y", _GLSL_BEND_Y)
            deformed_var = f"bend_y({point_var}, {rate_u}, {origin_u})"
        else: # Z
            ctx.add_custom_helper("bend_z", _GLSL_BEND_Z)
            deformed_var = f"bend_z({point_var}, {rate_u}, {origin_u})"
            
        return self.source.to_glsl_sample(ctx, deformed_var)


_GLSL_BEND_X = """
vec3 bend_x(vec3 p, float rate, float origin) {
    float x_local = p.x - origin;
    float theta = x_local * rate;
    float c = cos(theta);
    float s = sin(theta);
    return vec3(c * x_local + s * p.y + origin, -s * x_local + c * p.y, p.z);
}
"""

_GLSL_BEND_Y = """
vec3 bend_y(vec3 p, float rate, float origin) {
    float y_local = p.y - origin;
    float theta = y_local * rate;
    float c = cos(theta);
    float s = sin(theta);
    return vec3(p.x, c * y_local + s * p.z + origin, -s * y_local + c * p.z);
}
"""

_GLSL_BEND_Z = """
vec3 bend_z(vec3 p, float rate, float origin) {
    float z_local = p.z - origin;
    float theta = z_local * rate;
    float c = cos(theta);
    float s = sin(theta);
    return vec3(-s * z_local + c * p.x, p.y, c * z_local + s * p.x + origin);
}
"""

