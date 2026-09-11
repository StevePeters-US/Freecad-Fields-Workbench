# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np
import math
import os
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf_constants import DEGENERATE_AXIS_EPS

_GLSL_BOX2D = """
float sd_box2d(vec2 p, vec2 lo, vec2 hi) {
    vec2 d = max(lo - p, p - hi);
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}
"""


class SdfProfilePrismField(SdfField):
    """An infinite prism: a 2D profile swept along `direction`.

    The 2D field does all the work. This class only projects the query point
    into the profile's plane. `Sdf2dField.evaluate_2d` is a true distance, so
    |grad| <= 1 and lipschitz() is exactly 1.0.
    """

    def __init__(self, profile2d, origin, e1, e2):
        super().__init__()
        self.profile = profile2d
        self.origin = np.asarray(origin, dtype=np.float64)
        self.e1 = np.asarray(e1, dtype=np.float64)
        self.e2 = np.asarray(e2, dtype=np.float64)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        v = np.array([point.x, point.y, point.z]) - self.origin
        return float(self.profile.evaluate_2d(float(v @ self.e1), float(v @ self.e2)))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        v = np.asarray(points, dtype=np.float64) - self.origin
        uv = np.column_stack([v @ self.e1, v @ self.e2]).astype(np.float32)
        return self.profile.evaluate_2d_grid(uv).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        u_o = ctx.uniform("vec3", self.origin.tolist())
        u_1 = ctx.uniform("vec3", self.e1.tolist())
        u_2 = ctx.uniform("vec3", self.e2.tolist())
        uv = f"vec2(dot({point_var} - {u_o}, {u_1}), dot({point_var} - {u_o}, {u_2}))"
        return self.profile.to_glsl_2d(ctx, uv)

    def _bbox_terms(self, points):
        """(u, v, box) -- the projected point and the profile's 2D bounds."""
        d = np.asarray(points, dtype=np.float64) - self.origin
        return d @ self.e1, d @ self.e2, self.profile.bbox_2d()

    def lower_bound_grid(self, points: np.ndarray) -> np.ndarray:
        """A sound, cheap lower bound on `evaluate_grid` -- the box distance.

        The curve lies inside its control hull and so inside the 2D bounding
        box, which nests the two boundaries: outside the box the box distance
        cannot exceed the distance to the curve, and inside it the box's own
        (negative) interior distance cannot exceed the profile's, since any
        path out of the box crosses the profile first. So it never reports
        more than the exact field does, in either sign -- which is what lets a
        caller skip the exact cubic-Bezier solve on the strength of it.
        """
        u, v, (x0, y0, x1, y1) = self._bbox_terms(points)
        dx = np.maximum(x0 - u, u - x1)
        dy = np.maximum(y0 - v, v - y1)
        return (np.hypot(np.maximum(dx, 0.0), np.maximum(dy, 0.0))
                + np.minimum(np.maximum(dx, dy), 0.0))

    def lipschitz(self) -> float:
        return 1.0


def extrude_frame(axis, origin):
    """(origin, e1, e2) — an orthonormal basis of the plane normal to `axis`.

    Deterministic: the seed vector is +Z unless `axis` is within ~25 deg of it, in which
    case +Y. Two calls with the same axis always return the same frame, which matters
    because the frame is baked into GLSL uniforms — a frame that flips between rebuilds
    would churn the shader program cache.
    """
    ax = np.asarray(axis, dtype=np.float64)
    norm_ax = np.linalg.norm(ax)
    if norm_ax < DEGENERATE_AXIS_EPS:
        ax = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    else:
        ax = ax / norm_ax

    org = np.asarray(origin, dtype=np.float64)

    # Seed vector: +Z unless axis is within ~25 deg of it (cos(25deg) ≈ 0.9063), in which case +Y
    seed = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    if abs(float(seed @ ax)) > 0.9:
        seed = np.array([0.0, 1.0, 0.0], dtype=np.float64)

    e1 = np.cross(ax, seed)
    norm_e1 = np.linalg.norm(e1)
    if norm_e1 < 1e-12:
        seed = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        e1 = np.cross(ax, seed)
        norm_e1 = np.linalg.norm(e1)
    e1 /= norm_e1
    e2 = np.cross(ax, e1)
    e2 /= np.linalg.norm(e2)

    return org, e1, e2


def project_bezier_ring(ring_pts, ring_handles, origin, e1, e2):
    """Closed ring of 3D cubic Bezier edges -> 2D segs for Sdf2dBezierCurve.

    The projection of a cubic Bezier is the cubic Bezier of the projected control points,
    so this is EXACT: a k-edge ring yields k segments, never k * n_subdiv chords. With no
    handles the chord thirds make each cubic degenerate to the straight edge, which is
    exactly right for a box or cylinder face.
    """
    P = np.asarray(ring_pts, dtype=np.float64)
    k = len(P)
    H = (np.asarray(ring_handles, dtype=np.float64).reshape((-1, 3))
         if ring_handles is not None and len(ring_handles) else None)
    org = np.asarray(origin, dtype=np.float64)
    e1_arr = np.asarray(e1, dtype=np.float64)
    e2_arr = np.asarray(e2, dtype=np.float64)

    segs = []
    for i in range(k):
        j = (i + 1) % k
        if H is not None and len(H) >= 2 * k:
            quad = (P[i], H[2 * i], H[2 * i + 1], P[j])
        else:
            ch = P[j] - P[i]
            quad = (P[i], P[i] + ch / 3.0, P[i] + 2.0 * ch / 3.0, P[j])
        segs.append(tuple((float((x - org) @ e1_arr), float((x - org) @ e2_arr))
                          for x in quad))
    return segs


class SdfPrismExtrusionField(SdfField):
    """A 2D profile swept between two cap fields: max(bottom, top, walls).

    Unified backing implementation for all direct prismatic extrusions in the workbench.
    `walls` is an infinite prism (SdfProfilePrismField); `bottom` and `top` are any
    SdfFields that are negative inside the solid. The direct extrude paths (curve extrude,
    surface patch extrude, cage face extrude) delegate to this class, differing ONLY in
    their cap and profile configurations:

      curve extrude : two half-space planes
      patch extrude : two heightmap caps
      cage face     : -(source + embed) and (translated source U slab)

    `exact_corner` selects the rounded IQ combine
    `min(max(a,b),0) + length(max(vec2(a,b),0))` instead of a bare `max`. That form is the
    true Euclidean distance outside the corner ring and traces measurably faster, but it is
    only valid when `bottom`/`top` are opposing planes of one slab. Plain `max` is
    conservative (it understates), which is correct but slower -- and is what the cage path
    must use, since its caps are curved and its source is not a tight distance anyway.
    """

    def __init__(self, walls, bottom, top, exact_corner=False):
        super().__init__()
        self.walls = walls
        self.bottom = bottom
        self.top = top
        self.exact_corner = bool(exact_corner)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        w = self.walls.evaluate(point)
        b = self.bottom.evaluate(point)
        t = self.top.evaluate(point)
        if self.exact_corner:
            dz = max(b, t)
            return float(min(max(w, dz), 0.0) + (max(w, 0.0) ** 2 + max(dz, 0.0) ** 2) ** 0.5)
        return float(max(w, b, t))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        w = self.walls.evaluate_grid(points)
        b = self.bottom.evaluate_grid(points)
        t = self.top.evaluate_grid(points)
        if self.exact_corner:
            dz = np.maximum(b, t)
            inner = np.minimum(np.maximum(w, dz), 0.0)
            outer = np.sqrt(np.maximum(w, 0.0) ** 2 + np.maximum(dz, 0.0) ** 2)
            return (inner + outer).astype(np.float32)
        return np.maximum.reduce([w, b, t]).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        func_name = ctx.get_unique_name("sdf_prism_extrude")
        walls_glsl = self.walls.to_glsl(ctx, "p")
        bottom_glsl = self.bottom.to_glsl(ctx, "p")
        top_glsl = self.top.to_glsl(ctx, "p")

        if self.exact_corner:
            body = (
                f"float {func_name}(vec3 p) {{\n"
                f"    float _d2 = {walls_glsl};\n"
                f"    float _dz = max({bottom_glsl}, {top_glsl});\n"
                f"    vec2 _w = vec2(_d2, _dz);\n"
                f"    return min(max(_w.x, _w.y), 0.0) + length(max(_w, 0.0));\n"
                f"}}"
            )
        else:
            body = (
                f"float {func_name}(vec3 p) {{\n"
                f"    float _w = {walls_glsl};\n"
                f"    float _b = {bottom_glsl};\n"
                f"    float _t = {top_glsl};\n"
                f"    return max(_w, max(_b, _t));\n"
                f"}}"
            )
        ctx.add_custom_helper(func_name, body)
        return f"{func_name}({point_var})"

    def bounding_box(self):
        b_bot = None
        b_top = None
        b_wall = None
        try:
            b_bot = self.bottom.bounding_box()
        except (NotImplementedError, AttributeError) as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug(
                f"SdfPrismField.bounding_box: bottom component "
                f"({type(self.bottom).__name__}) has no bounding box ({e}); "
                f"the prism bbox falls back to the remaining components."
            )
        try:
            b_top = self.top.bounding_box()
        except (NotImplementedError, AttributeError) as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug(
                f"SdfPrismField.bounding_box: top component "
                f"({type(self.top).__name__}) has no bounding box ({e}); "
                f"the prism bbox falls back to the remaining components."
            )
        try:
            b_wall = self.walls.bounding_box()
        except (NotImplementedError, AttributeError) as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug(
                f"SdfPrismField.bounding_box: wall component "
                f"({type(self.walls).__name__}) has no bounding box ({e}); "
                f"the prism bbox falls back to the remaining components."
            )

        if b_bot is not None and b_top is not None:
            from freecad.fields.core.sdf.sdf_composer import _bbox_union
            b_caps = _bbox_union(b_bot, b_top)
            if b_wall is not None:
                from freecad.fields.core.sdf.sdf_composer import _bbox_intersect
                return _bbox_intersect(b_caps, b_wall)
            return b_caps
        if b_wall is not None:
            return b_wall
        # Walls are an infinite prism and the caps are half-spaces, so none of the three
        # parts is bounded on its own even though their intersection is. Any subclass
        # that knows its own frame MUST override this -- the fallback below is a 2 m cube
        # that defeats render-AABB culling and bloats every octree built over it.
        from freecad.fields.core import fld_logger
        fld_logger.warn(
            "SdfPrismExtrusionField.bounding_box: no part reported a box; falling back to "
            "a 2000mm cube. The owning field should override bounding_box().")
        return (FreeCAD.Vector(-1000, -1000, -1000), FreeCAD.Vector(1000, 1000, 1000))

    def lipschitz(self) -> float:
        return float(max(self.walls.lipschitz(), self.bottom.lipschitz(), self.top.lipschitz()))


class SdfPlaneCap(SdfField):
    """Half-space dot(p - origin, normal) - offset. Exact, 1-Lipschitz."""

    def __init__(self, origin, normal, offset=0.0):
        super().__init__()
        self.origin = np.asarray(origin, dtype=np.float64)
        n = np.asarray(normal, dtype=np.float64)
        L = np.linalg.norm(n)
        self.normal = n / L if L > 1e-12 else np.array([0.0, 0.0, 1.0], dtype=np.float64)
        self.offset = float(offset)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        v = np.array([point.x, point.y, point.z], dtype=np.float64) - self.origin
        return float(v @ self.normal - self.offset)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        v = np.asarray(points, dtype=np.float64) - self.origin
        return (v @ self.normal - self.offset).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        u_o = ctx.uniform("vec3", self.origin.tolist())
        u_n = ctx.uniform("vec3", self.normal.tolist())
        u_off = ctx.uniform("float", self.offset)
        return f"(dot({point_var} - {u_o}, {u_n}) - {u_off})"

    def lipschitz(self) -> float:
        return 1.0


class SdfHeightmapCap(SdfField):
    """Signed distance to a height field h(x,y) in a local frame, slope-normalized.

    value = sign * (local_z - h(x, y) - offset) / sqrt(1 + dhdx^2 + dhdy^2)

    The division is what keeps this near-metric on a sloped surface; without it a 45-deg
    slope reports distances 1.41x too large and the sphere tracer overshoots.
    """

    def __init__(self, grid, bounds, origin, e1, e2, axis=None, offset=0.0, sign=1.0):
        super().__init__()
        self.grid = np.asarray(grid, dtype=np.float64)
        self.bounds = tuple(float(x) for x in bounds)  # (min_x, max_x, min_y, max_y)
        self.origin = np.asarray(origin, dtype=np.float64)
        self.e1 = np.asarray(e1, dtype=np.float64)
        self.e2 = np.asarray(e2, dtype=np.float64)
        if axis is None:
            ax = np.cross(self.e1, self.e2)
            norm_ax = np.linalg.norm(ax)
            self.axis = ax / norm_ax if norm_ax > 1e-12 else np.array([0.0, 0.0, 1.0], dtype=np.float64)
        else:
            self.axis = np.asarray(axis, dtype=np.float64)
        self.offset = float(offset)
        self.sign = float(sign)
        # Set content key for GPU texture tracking. The renderer garbage-collects any
        # heightmap texture whose key it cannot find on a registered field.
        import hashlib
        grid32 = np.ascontiguousarray(self.grid, dtype=np.float32)
        digest = hashlib.sha1(grid32.tobytes()).hexdigest()[:16]
        self.heightmap_path = f"hmap_{grid32.shape[0]}x{grid32.shape[1]}_{digest}"

        min_x, max_x, min_y, max_y = self.bounds
        res_y, res_x = self.grid.shape
        dx = (max_x - min_x) / max(res_x - 1, 1)
        dy = (max_y - min_y) / max(res_y - 1, 1)
        grad_y, grad_x = np.gradient(self.grid, dy, dx)
        self._dhdx = grad_x
        self._dhdy = grad_y

    def _sample(self, u: np.ndarray, v: np.ndarray):
        min_x, max_x, min_y, max_y = self.bounds
        res_y, res_x = self.grid.shape
        u_norm = np.clip((u - min_x) / max(max_x - min_x, 1e-9), 0.0, 1.0)
        v_norm = np.clip((v - min_y) / max(max_y - min_y, 1e-9), 0.0, 1.0)
        fx = u_norm * (res_x - 1)
        fy = v_norm * (res_y - 1)
        x0 = np.floor(fx).astype(np.int64)
        y0 = np.floor(fy).astype(np.int64)
        x1 = np.minimum(x0 + 1, res_x - 1)
        y1 = np.minimum(y0 + 1, res_y - 1)
        tx = fx - x0
        ty = fy - y0

        # Deliberately plain bilinear. A smoothstep fade on (tx, ty) makes the
        # interpolant C1, which looks like the fix for normal faceting, but measured on
        # a 45-degree ramp it costs 0.021mm of accuracy and swings the surface slope by
        # +/-50% of its local value at texel period. Bilinear's own normal jump at a
        # cell edge is the second difference of the height field over one cell -- ~4% on
        # anything as smooth as a relaxed fill -- so the fade trades small
        # discontinuities for a large continuous ripple. Bicubic would beat both, at 16
        # texture fetches per sample against this one.
        def _bilerp(arr):
            top = arr[y0, x0] * (1 - tx) + arr[y0, x1] * tx
            bot = arr[y1, x0] * (1 - tx) + arr[y1, x1] * tx
            return top * (1 - ty) + bot * ty

        return _bilerp(self.grid), _bilerp(self._dhdx), _bilerp(self._dhdy)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        pt = np.array([point.x, point.y, point.z], dtype=np.float64) - self.origin
        u = float(pt @ self.e1)
        v = float(pt @ self.e2)
        w = float(pt @ self.axis)
        h, dhdx, dhdy = self._sample(np.array([u]), np.array([v]))
        slope = math.sqrt(1.0 + float(dhdx[0]) ** 2 + float(dhdy[0]) ** 2)
        return float(self.sign * (w - float(h[0]) - self.offset) / slope)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64) - self.origin
        u = pts @ self.e1
        v = pts @ self.e2
        w = pts @ self.axis
        h, dhdx, dhdy = self._sample(u, v)
        slope = np.sqrt(1.0 + dhdx ** 2 + dhdy ** 2)
        return (self.sign * (w - h - self.offset) / slope).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        tex_name = ctx.sampler2d_data("heightmap", self.grid)
        min_x, max_x, min_y, max_y = self.bounds
        u_o = ctx.uniform("vec3", self.origin.tolist())
        u_1 = ctx.uniform("vec3", self.e1.tolist())
        u_2 = ctx.uniform("vec3", self.e2.tolist())
        u_a = ctx.uniform("vec3", self.axis.tolist())
        u_min_xy = ctx.uniform("vec2", [min_x, min_y])
        u_size_xy = ctx.uniform("vec2", [max_x - min_x, max_y - min_y])
        u_off = ctx.uniform("float", self.offset)
        u_sign = ctx.uniform("float", self.sign)
        res_y, res_x = self.grid.shape
        u_res = ctx.uniform("vec2", [float(res_x), float(res_y)])

        # Same interpolant as `_sample`, and on the same convention: grid value i sits at
        # texel CENTRE i, so the normalized coordinate scales by res-1 and lands on
        # (i + 0.5)/res. Sampling `texture(tex, uv)` directly -- the obvious reading of a
        # uv -- puts value i at i/(res-1) of the texture instead, which shifts the whole
        # height field half a texel and stretches it by res/(res-1) against the CPU field
        # it is supposed to be a copy of. One hardware bilinear fetch still does the
        # filtering; only the coordinate moves.
        #
        # TODO: central difference slope uses 4 extra texture fetches per sample; optimize in future pass.
        smp = ctx.get_unique_name("hmap_texel")
        smp_body = (
            f"float {smp}(sampler2D tex, vec2 uv, vec2 res) {{\n"
            f"    vec2 tc = clamp(uv, 0.0, 1.0) * (res - 1.0);\n"
            f"    return texture(tex, (tc + 0.5) / res).r;\n"
            f"}}"
        )
        ctx.add_custom_helper(smp, smp_body)

        fn = ctx.get_unique_name("sdf_hmap_cap")
        body = (
            f"float {fn}(vec3 p) {{\n"
            f"    vec3 d = p - {u_o};\n"
            f"    vec2 uv_local = vec2(dot(d, {u_1}), dot(d, {u_2}));\n"
            f"    float w = dot(d, {u_a});\n"
            f"    vec2 uv = clamp((uv_local - {u_min_xy}) / {u_size_xy}, 0.0, 1.0);\n"
            f"    float h = {smp}({tex_name}, uv, {u_res});\n"
            # One texel, not a fixed 1% of the extent: at 256^2 the old 0.01 spanned
            # 2.5 texels, so a ridge read back flatter than it is, the slope divisor
            # came out too small, and the sphere tracer overstepped it.
            f"    vec2 h_eps = 1.0 / ({u_res} - 1.0);\n"
            f"    float hr = {smp}({tex_name}, uv + vec2(h_eps.x, 0.0), {u_res});\n"
            f"    float hl = {smp}({tex_name}, uv - vec2(h_eps.x, 0.0), {u_res});\n"
            f"    float hu = {smp}({tex_name}, uv + vec2(0.0, h_eps.y), {u_res});\n"
            f"    float hd = {smp}({tex_name}, uv - vec2(0.0, h_eps.y), {u_res});\n"
            f"    float dhdx = (hr - hl) / (2.0 * h_eps.x * {u_size_xy}.x);\n"
            f"    float dhdy = (hu - hd) / (2.0 * h_eps.y * {u_size_xy}.y);\n"
            f"    float slope = sqrt(1.0 + dhdx * dhdx + dhdy * dhdy);\n"
            f"    return {u_sign} * (w - h - {u_off}) / slope;\n"
            f"}}"
        )
        ctx.add_custom_helper(fn, body)
        return f"{fn}({point_var})"

    def lipschitz(self) -> float:
        return 1.0
