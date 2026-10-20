# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import math
import FreeCAD
import numpy as np
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf_constants import SURFACE_ID_UNSET

AXES = {"X": 0, "Y": 1, "Z": 2}
_INF = 1e30


def axis_extent(field, axis):
    """(lo, hi) of `field`'s bounding box along `axis` ("X"/"Y"/"Z")."""
    lo, hi = field.bounding_box()
    ai = AXES.get(axis, 2)
    return (lo.x, lo.y, lo.z)[ai], (hi.x, hi.y, hi.z)[ai]


def measured_extent(field, axis, coarse_res=48, fine_res=64, coarse_slices=32, steps=10):
    """The part's real extent (lo, hi) along axis where material is present (inside points, d <= 0).

    Uses a cheap coarse grid to bracket each end and a finer grid to refine by bisection.
    Note: A feature thinner than one grid cell in cross-section can be missed and is then
    trimmed by the clip. Not a conservative bound.
    """
    from freecad.fields.core import fld_logger
    lo_box, hi_box = axis_extent(field, axis)
    if hi_box <= lo_box:
        return lo_box, hi_box

    lo_full, hi_full = field.bounding_box()
    ai = AXES.get(axis, 2)
    other_axes = [i for i in (0, 1, 2) if i != ai]
    ax0, ax1 = other_axes[0], other_axes[1]

    min_coords = (lo_full.x, lo_full.y, lo_full.z)
    max_coords = (hi_full.x, hi_full.y, hi_full.z)

    u0, u1 = min_coords[ax0], max_coords[ax0]
    v0, v1 = min_coords[ax1], max_coords[ax1]

    def make_grid(res):
        u_lin = np.linspace(u0, u1, res, dtype=np.float32)
        v_lin = np.linspace(v0, v1, res, dtype=np.float32)
        uu, vv = np.meshgrid(u_lin, v_lin)
        uu_flat = uu.ravel()
        vv_flat = vv.ravel()
        pts = np.empty((len(uu_flat), 3), dtype=np.float32)
        pts[:, ax0] = uu_flat
        pts[:, ax1] = vv_flat
        return pts

    coarse_grid = make_grid(coarse_res)
    fine_grid = make_grid(fine_res)

    def inside(a_val, grid):
        grid[:, ai] = a_val
        dists = field.evaluate_grid(grid)
        return bool(np.any(dists <= 0.0))

    # Coarse scan at coarse_slices evenly spaced samples
    coarse_a = np.linspace(lo_box, hi_box, coarse_slices)
    occ = [inside(a, coarse_grid) for a in coarse_a]
    if not any(occ):
        fld_logger.debug(f"measured_extent: no inside slice found along {axis}; keeping box extent")
        return lo_box, hi_box

    first_occ_idx = occ.index(True)
    last_occ_idx = len(occ) - 1 - occ[::-1].index(True)

    # Refine low end
    if first_occ_idx == 0:
        lo_m = lo_box
    else:
        empty_a = coarse_a[first_occ_idx - 1]
        occ_a = coarse_a[first_occ_idx]
        if not inside(occ_a, fine_grid):
            # Coarse-only hit: expand bracket to next coarse sample if available
            fld_logger.debug(f"measured_extent: coarse-only hit at low end for {axis}")
            if first_occ_idx + 1 < len(coarse_a) and occ[first_occ_idx + 1]:
                occ_a = coarse_a[first_occ_idx + 1]
        for _ in range(steps):
            mid = 0.5 * (empty_a + occ_a)
            if inside(mid, fine_grid):
                occ_a = mid
            else:
                empty_a = mid
        lo_m = occ_a

    # Refine high end
    if last_occ_idx == len(coarse_a) - 1:
        hi_m = hi_box
    else:
        occ_a = coarse_a[last_occ_idx]
        empty_a = coarse_a[last_occ_idx + 1]
        if not inside(occ_a, fine_grid):
            # Coarse-only hit: expand bracket to previous coarse sample if available
            fld_logger.debug(f"measured_extent: coarse-only hit at high end for {axis}")
            if last_occ_idx - 1 >= 0 and occ[last_occ_idx - 1]:
                occ_a = coarse_a[last_occ_idx - 1]
        for _ in range(steps):
            mid = 0.5 * (occ_a + empty_a)
            if inside(mid, fine_grid):
                occ_a = mid
            else:
                empty_a = mid
        hi_m = occ_a

    return max(lo_box, min(lo_m, hi_box)), max(lo_box, min(hi_m, hi_box))


def board_layout(lo, hi, thickness, locked, count, spacing):
    """(offset, pitch, count, gap) of boards filling [lo, hi] along the axis.

    The first board is flush with lo and the last with hi. `locked` is "Count" or
    "Spacing": the input the user edited last; the other is derived. `gap` is the clear
    space between boards (None for a single board); `pitch` = thickness + gap is the
    centre distance SdfPanelField takes.
    """
    t = max(float(thickness), 1e-6)
    extent = max(float(hi) - float(lo), 0.0)
    n_max = max(1, int(math.floor(extent / t + 1e-9)))
    if locked == "Spacing":
        g = max(float(spacing), 0.0)
        # N is the count whose gap is nearest the request (never < 1, never past the cap)
        n = int(round((extent + g) / (t + g))) if t + g > 0 else 1
        n = min(max(1, n), n_max)
        if n_max >= 2:
            # Check adjacent integers around n for the closest achievable gap
            best_n = n if n >= 2 else 2
            best_err = abs((extent - best_n * t) / (best_n - 1) - g)
            for cand in (n - 1, n + 1):
                if 2 <= cand <= n_max:
                    err = abs((extent - cand * t) / (cand - 1) - g)
                    if err < best_err:
                        best_err = err
                        best_n = cand
            n = best_n
    else:
        n = int(count)
    n = min(max(1, n), n_max)
    if n == 1:
        return 0.5 * (lo + hi), t, 1, None
    gap = (extent - n * t) / (n - 1)
    return lo + 0.5 * t, t + gap, n, gap


class SdfPanelField(SdfField):
    """`count` flat panels along `axis`: panel i is the source's cross-section at
    a = offset + i*pitch, extruded to `thickness` along the axis.

    d_i(p) = max(source(p | a := c_i), |a - c_i| - thickness/2); field = min_i d_i,
    evaluated over the two planes bracketing `a` and capped by the distance to the
    nearest plane outside that pair, so a closer neighbouring panel is never missed.

    The source value on a plane is the 3-D distance, not the 2-D profile distance. It is
    <= the true 2-D distance, so the zero set is exact and the value is only conservative.
    """

    def __init__(self, source: SdfField, axis: str = "Z", offset: float = 0.0,
                 pitch: float = 20.0, count: int = 5, thickness: float = 2.0,
                 clip_lo: float = -_INF, clip_hi: float = _INF):
        super().__init__()
        self.source = source
        self.axis = axis if axis in AXES else "Z"
        self.offset = float(offset)
        self.pitch = max(float(pitch), 1e-6)
        self.count = max(1, int(count))
        # Panels may touch but never overlap: the dz_out cap is a safe lower bound only
        # while it is positive, i.e. thickness < 2*pitch. Past that it is negative far
        # outside the part and draws a solid block (PNL-011).
        self.thickness = min(max(float(thickness), 0.0), self.pitch)
        self.clip_lo = float(clip_lo)
        self.clip_hi = float(clip_hi)

    @property
    def axis_index(self) -> int:
        return AXES[self.axis]

    # ------------------------------------------------------------------
    # CPU
    # ------------------------------------------------------------------

    def lipschitz(self) -> float:
        return self.source.lipschitz()

    def bounding_box(self):
        bb_min, bb_max = self.source.bounding_box()
        ai = self.axis_index
        lo = [bb_min.x, bb_min.y, bb_min.z]
        hi = [bb_max.x, bb_max.y, bb_max.z]
        half = self.thickness * 0.5
        new_lo = max(lo[ai], self.offset - half, self.clip_lo)
        new_hi = min(hi[ai], self.offset + (self.count - 1) * self.pitch + half, self.clip_hi)
        if new_lo > new_hi:
            new_lo = new_hi = self.offset
        lo[ai], hi[ai] = new_lo, new_hi
        return FreeCAD.Vector(*lo), FreeCAD.Vector(*hi)

    def _plane_indices(self, a):
        """(i0, i1, dz_out) for axis coordinate `a`."""
        n = self.count
        f = math.floor((a - self.offset) / self.pitch)
        i0 = min(max(f, 0), n - 1)
        i1 = min(max(f + 1, 0), n - 1)
        dz = _INF
        if i0 > 0:
            dz = min(dz, abs(a - (self.offset + (i0 - 1) * self.pitch)))
        if i1 < n - 1:
            dz = min(dz, abs((self.offset + (i1 + 1) * self.pitch) - a))
        return int(i0), int(i1), dz

    def evaluate(self, point: FreeCAD.Vector) -> float:
        ai = self.axis_index
        c = [point.x, point.y, point.z]
        a = c[ai]
        half = self.thickness * 0.5
        i0, i1, dz_out = self._plane_indices(a)

        def d_at(i):
            plane = self.offset + i * self.pitch
            q = list(c)
            q[ai] = plane
            return max(self.source.evaluate(FreeCAD.Vector(*q)), abs(a - plane) - half)

        best = d_at(i0)
        if i1 != i0:
            best = min(best, d_at(i1))
        return max(min(best, dz_out - half), self.clip_lo - a, a - self.clip_hi)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        ai = self.axis_index
        n = self.count
        half = self.thickness * 0.5
        a = points[:, ai].astype(np.float64)
        k = np.floor((a - self.offset) / self.pitch)
        i0 = np.clip(k, 0, n - 1)
        i1 = np.clip(k + 1, 0, n - 1)
        left = np.where(i0 > 0, np.abs(a - (self.offset + (i0 - 1) * self.pitch)), _INF)
        right = np.where(i1 < n - 1, np.abs((self.offset + (i1 + 1) * self.pitch) - a), _INF)
        dz_out = np.minimum(left, right)

        results = [dz_out - half]
        for idx in (i0, i1):
            plane = self.offset + idx * self.pitch
            pinned = points.copy()
            pinned[:, ai] = plane
            d = np.maximum(self.source.evaluate_grid(pinned), np.abs(a - plane) - half)
            results.append(d)
        d = np.minimum.reduce(results)
        return np.maximum(d, np.maximum(self.clip_lo - a, a - self.clip_hi)).astype(np.float32)

    # ------------------------------------------------------------------
    # GLSL
    # ------------------------------------------------------------------

    def _uniforms(self, ctx):
        return (
            ctx.uniform("int", self.axis_index),
            ctx.uniform("float", self.offset),
            ctx.uniform("float", self.pitch),
            ctx.uniform("int", self.count),
            ctx.uniform("float", self.thickness),
            ctx.uniform("float", self.clip_lo),
            ctx.uniform("float", self.clip_hi),
        )

    def to_glsl(self, ctx, point_var="p"):
        src_expr = self.source.to_glsl(ctx, "q")
        src_fn = ctx.get_unique_name("pnl_src")
        ctx.add_custom_helper(src_fn, f"float {src_fn}(vec3 q) {{ return {src_expr}; }}\n")
        fn = ctx.get_unique_name("pnl")
        ctx.add_custom_helper(fn, f"""
float {fn}(vec3 p, int ax, float off, float pitch, int n, float thick, float clo, float chi) {{
    float a = (ax == 0) ? p.x : ((ax == 1) ? p.y : p.z);
    float half_t = 0.5 * thick;
    float nm1 = float(n - 1);
    float k = floor((a - off) / pitch);
    float i0 = clamp(k, 0.0, nm1);
    float i1 = clamp(k + 1.0, 0.0, nm1);
    float dz = 1e30;
    if (i0 > 0.0) dz = min(dz, abs(a - (off + (i0 - 1.0) * pitch)));
    if (i1 < nm1) dz = min(dz, abs((off + (i1 + 1.0) * pitch) - a));
    float c0 = off + i0 * pitch;
    vec3 q0 = vec3(ax == 0 ? c0 : p.x, ax == 1 ? c0 : p.y, ax == 2 ? c0 : p.z);
    float best = max({src_fn}(q0), abs(a - c0) - half_t);
    if (i1 != i0) {{
        float c1 = off + i1 * pitch;
        vec3 q1 = vec3(ax == 0 ? c1 : p.x, ax == 1 ? c1 : p.y, ax == 2 ? c1 : p.z);
        best = min(best, max({src_fn}(q1), abs(a - c1) - half_t));
    }}
    return max(min(best, dz - half_t), max(clo - a, a - chi));
}}
""")
        ax_u, off_u, pitch_u, n_u, t_u, clo_u, chi_u = self._uniforms(ctx)
        return f"{fn}({point_var}, {ax_u}, {off_u}, {pitch_u}, {n_u}, {t_u}, {clo_u}, {chi_u})"

    def to_glsl_sample(self, ctx, point_var="p"):
        if getattr(self, "surface_id", SURFACE_ID_UNSET) < SURFACE_ID_UNSET and getattr(self.source, "surface_id", SURFACE_ID_UNSET) == SURFACE_ID_UNSET:
            setattr(self.source, "surface_id", self.surface_id)

        src_expr = self.source.to_glsl_sample(ctx, "q")
        src_fn = ctx.get_unique_name("pnl_srcs")
        ctx.add_custom_helper(src_fn, f"FldSample {src_fn}(vec3 q) {{ return {src_expr}; }}\n")
        fn = ctx.get_unique_name("pnls")
        ctx.add_custom_helper(fn, f"""
FldSample {fn}(vec3 p, int ax, float off, float pitch, int n, float thick, float clo, float chi) {{
    float a = (ax == 0) ? p.x : ((ax == 1) ? p.y : p.z);
    float half_t = 0.5 * thick;
    float nm1 = float(n - 1);
    float k = floor((a - off) / pitch);
    float i0 = clamp(k, 0.0, nm1);
    float i1 = clamp(k + 1.0, 0.0, nm1);
    float dz = 1e30;
    if (i0 > 0.0) dz = min(dz, abs(a - (off + (i0 - 1.0) * pitch)));
    if (i1 < nm1) dz = min(dz, abs((off + (i1 + 1.0) * pitch) - a));
    float c0 = off + i0 * pitch;
    vec3 q0 = vec3(ax == 0 ? c0 : p.x, ax == 1 ? c0 : p.y, ax == 2 ? c0 : p.z);
    FldSample best = {src_fn}(q0);
    best.d = max(best.d, abs(a - c0) - half_t);
    if (i1 != i0) {{
        float c1 = off + i1 * pitch;
        vec3 q1 = vec3(ax == 0 ? c1 : p.x, ax == 1 ? c1 : p.y, ax == 2 ? c1 : p.z);
        FldSample s1 = {src_fn}(q1);
        s1.d = max(s1.d, abs(a - c1) - half_t);
        best = fld_min(best, s1);
    }}
    best.d = max(min(best.d, dz - half_t), max(clo - a, a - chi));
    return best;
}}
""")
        ax_u, off_u, pitch_u, n_u, t_u, clo_u, chi_u = self._uniforms(ctx)
        return f"{fn}({point_var}, {ax_u}, {off_u}, {pitch_u}, {n_u}, {t_u}, {clo_u}, {chi_u})"
