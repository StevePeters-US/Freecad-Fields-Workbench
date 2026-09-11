# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf2d.bezier_curve import Sdf2dBezierCurve
from freecad.fields.core.sdf.field_eval import eval_grid
import copy



from freecad.fields.core.sdf.sdf_prism import (
    SdfProfilePrismField,  # noqa: F401
    _GLSL_BOX2D,
    extrude_frame,
    project_bezier_ring,
)
from freecad.fields.core.sdf.sdf2d.bezier_curve import (
    _GLSL_CUBIC_BEZ_2D,
    _GLSL_CUBIC_WINDING,
    _GLSL_SOLVE_CUBIC,
)


# The stack machine's leaves, read from two SSBOs instead of emitted per lobe.
#
# `Sdf2dBezierCurve.to_glsl_2d` unrolls its ring: one block of GLSL per segment,
# each with the cubic-Bezier distance and winding solves inlined, and the control
# points baked in as uniforms. One profile that way is fine, and every other
# consumer of a 2D profile still uses it. N stacked lobes is not: the machine
# calls the wall profile from one site, but that site expands to N unrolled
# rings, so the SHADER grows as N * segments even though the source text stays
# linear. Measured on the GUI at depth 3 that cost a 4.9 s driver compile against
# 92 ms at depth 2, and it is the same wall as the ~65k-instruction cap the cage
# hit at 30 faces.
#
# Here the ring is a loop over buffer data, so there is exactly ONE copy of the
# cubic machinery in the shader no matter how many lobes are stacked or how many
# segments each ring has, and a drag re-uploads the buffer rather than
# recompiling. `_ring_records` is the single writer of the layout that
# `RingMeta` / `RingSeg` in glsl_compiler.py declare -- change one and change
# both, and `test_extrusion_stack_cost.py` re-runs this loop on the CPU against
# the packed bytes to say they still agree.
_GLSL_RING = """
vec2 {uv}(int j, vec3 p) {{
    vec3 d = p - {meta}[j].org.xyz;
    return vec2(dot(d, {meta}[j].e1.xyz), dot(d, {meta}[j].e2.xyz));
}}

float {bound}(int j, vec3 p) {{
    return sd_box2d({uv}(j, p), {meta}[j].box.xy, {meta}[j].box.zw);
}}

float {walls}(int j, vec3 p) {{
    vec2 q = {uv}(j, p);
    int lo  = int({meta}[j].org.w);
    int cnt = int({meta}[j].e1.w);
    float d = 1e18;
    int w = 0;
    for (int i = 0; i < cnt; ++i) {{
        vec4 ab = {segs}[lo + i].ab;
        vec4 cd = {segs}[lo + i].cd;
        vec2 a = ab.xy, b = ab.zw, c = cd.xy, e = cd.zw;
        vec2 blo = min(min(a, b), min(c, e));
        vec2 bhi = max(max(a, b), max(c, e));
        if (length(q - clamp(q, blo, bhi)) < d) d = min(d, sd_cubic_bez_2d(q, a, b, c, e));
        if (q.y >= blo.y - 1e-6 && q.y <= bhi.y + 1e-6) w += sd_cubic_winding(q, a, b, c, e);
    }}
    return (w == 0 ? 1.0 : -1.0) * d;
}}

float {slab}(int j, vec3 p) {{
    // A lobe with no slab contributes nothing to `min(cap, slab)`, so 1e18 is
    // its absence. See `lobe_grid`: without this term a lobe swept further than
    // its source is thick detaches.
    if ({meta}[j].flags.y < 0.5) return 1e18;
    float h  = dot(p - {meta}[j].slab.xyz, {meta}[j].slab2.xyz);
    float h0 = {meta}[j].slab.w;
    return max(h - (h0 + {meta}[j].slab2.w), h0 - h);
}}
"""


def lobe_grid(ext, q, s, walls=None, cap=None):
    """ONE extruded lobe at points `q`. The definition of the operation.

        max( -(s + EMBED),  walls,  min(s(q - dir*L), slab) )

    Read it as a slice of the source, swept: outside the source (less an EMBED
    of overlap so the union's zero set is not a coincident 2-D patch), inside
    the face's prism, and inside the region the source sweeps out.

    The swept region is NOT `s(q - dir*L)` alone. That is the source's final
    position, and the lobe is the union of every position along the way. The two
    agree only while the source, translated by L, still overlaps itself -- i.e.
    while L is under the source's thickness along the sweep. Past that the two
    copies separate and the lobe becomes a cap floating over a hole. The slab
    `h0 <= dot(q - org, dir) <= h0 + L` is the filled middle, exact and
    1-Lipschitz, with h0 the lowest point of the source's front surface under
    the footprint (`face_height_datum`). Dropping it is what commit 641b0fb did
    -- measured: an extrusion on a 20 mm sphere detaches from L = 25 mm up.

    `s` is `ext.source` already evaluated at `q` -- a parameter, not something
    this computes, because that is exactly what the two callers cannot agree on.
    `SdfExtrusionStackField` carries a memoised running prefix and must not
    re-evaluate it; a standalone `SdfFaceExtrusionField` has to evaluate its own.
    Everything downstream of `s` is here, once, so neither caller owns it.

    `walls` and `cap` are the same escape hatch for the stack's short-circuits:
    it proves term by term when a partial `max` already exceeds the prefix and
    the rest cannot matter, and passes what it has. Both default to being
    evaluated here, which is the plain unconditional formula.
    """
    q = np.asarray(q, dtype=np.float64)
    if walls is None:
        walls = ext.profile.evaluate_grid(q)
    out = np.maximum(-(s + ext.EMBED), walls)

    if ext.mode == "surface":
        # An offset cap is the source moved along its own normals, which is
        # `s - L` at every point: it sweeps continuously by construction, so
        # there is no gap for a slab to fill.
        return np.maximum(out, s - ext.sweep_len).astype(np.float32)

    if cap is None:
        cap = ext.source.evaluate_grid(q - ext.sweep_dir * ext.sweep_len)
    if ext.cap_slab is not None:
        cap = np.minimum(cap, ext.cap_slab.evaluate_grid(q))
    return np.maximum(out, cap).astype(np.float32)


def lobe_glsl(ctx, ext, s_expr, walls_expr, cap_expr=None, point_var="p"):
    """`lobe_grid` as GLSL, given already-built expressions for its terms.

    Kept beside the numpy form so the two cannot drift apart unnoticed; the
    stack machine spells the same formula a third time inline
    (`SdfExtrusionStackField.to_glsl`) because it interleaves it with an
    explicit frame stack, and `test_one_lobe_definition.py` is what pins all
    three together.
    """
    u_embed = ctx.uniform("float", float(ext.EMBED))
    out = f"max((-(({s_expr}) + {u_embed})), {walls_expr})"
    if ext.mode == "surface":
        u_len = ctx.uniform("float", float(ext.sweep_len))
        return f"max({out}, ({s_expr}) - {u_len})"
    if ext.cap_slab is not None:
        cap_expr = f"min({cap_expr}, {ext.cap_slab.to_glsl(ctx, point_var)})"
    return f"max({out}, {cap_expr})"


def ring_surface_normals(source, ring_pts):
    """Outward unit surface normals at each ring vertex. (n, 3) float64.

    ONE batched gradient_grid() call, never a per-vertex gradient() loop:
    SdfCageDeformField has no analytic gradient, so each scalar call costs six
    numeric samples and therefore six MVC solves.
    """
    g = np.asarray(source.gradient_grid(np.asarray(ring_pts, dtype=np.float64)),
                   dtype=np.float64)
    n = np.linalg.norm(g, axis=1, keepdims=True)
    return g / np.maximum(n, 1e-12)


def newell_normal(ring_pts):
    """Unit Newell normal of a closed (possibly non-planar) ring, CCW-positive."""
    P = np.asarray(ring_pts, dtype=np.float64)
    Q = np.roll(P, -1, axis=0)
    n = np.array([
        np.sum((P[:, 1] - Q[:, 1]) * (P[:, 2] + Q[:, 2])),
        np.sum((P[:, 2] - Q[:, 2]) * (P[:, 0] + Q[:, 0])),
        np.sum((P[:, 0] - Q[:, 0]) * (P[:, 1] + Q[:, 1])),
    ])
    L = np.linalg.norm(n)
    if L < 1e-12:
        raise ValueError("degenerate ring: Newell normal is zero")
    return n / L


from freecad.fields.core import fld_logger


class SdfSlabField(SdfField):
    """The layer h0 <= dot(p - origin, axis) <= h0 + length. Exact, 1-Lipschitz."""

    def __init__(self, origin, axis, h0, length):
        super().__init__()
        self.origin = np.asarray(origin, dtype=np.float64)
        self.axis = np.asarray(axis, dtype=np.float64)
        self.h0 = float(h0)
        self.length = float(length)

    def _h(self, pts):
        return (np.asarray(pts, dtype=np.float64) - self.origin) @ self.axis

    def evaluate(self, point: FreeCAD.Vector) -> float:
        h = float(self._h([[point.x, point.y, point.z]])[0])
        return max(h - (self.h0 + self.length), self.h0 - h)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        h = self._h(points)
        return np.maximum(h - (self.h0 + self.length), self.h0 - h).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        u_o = ctx.uniform("vec3", self.origin.tolist())
        u_a = ctx.uniform("vec3", self.axis.tolist())
        u_h = ctx.uniform("float", self.h0)
        u_l = ctx.uniform("float", self.length)
        hh = f"dot({point_var} - {u_o}, {u_a})"
        return f"max({hh} - ({u_h} + {u_l}), {u_h} - {hh})"

    def lipschitz(self) -> float:
        return 1.0


def _source_reach(source, ring):
    """How far a probe may have to travel before it is certainly outside."""
    try:
        bmin, bmax = source.bounding_box()
        diag = np.linalg.norm([bmax.x - bmin.x, bmax.y - bmin.y, bmax.z - bmin.z])
        if np.isfinite(diag) and diag > 0.0:
            return float(diag) * 1.5
    except (NotImplementedError, AttributeError, TypeError) as e:
        fld_logger.debug(
            f"_source_reach: {type(source).__name__}.bounding_box() unavailable "
            f"({e}); falling back to the ring-extent estimate, which is coarser."
        )
    return float(np.linalg.norm(np.ptp(np.asarray(ring, dtype=np.float64), axis=0))) * 20.0 + 10.0


def _scan_exit_pair(source, pts, direction, reach, n_coarse=257, n_bisect=12):
    """Distance from each point to where it leaves the solid along `direction` and `-direction`.

    Tolerance guarantee per scan is `reach / (n_coarse - 1) / 2**n_bisect`.
    With n_coarse=257 and n_bisect=12, a reach of ~50mm gives ~5e-5 mm precision,
    four orders of magnitude finer than CAD tolerance (0.05 mm).

    Both forward and reverse direction scans are batched into single evaluate_grid
    calls per coarse pass and per bisection step to halve call overhead.

    Returns ((t_f, ok_f), (t_r, ok_r)).
    """
    P = np.asarray(pts, dtype=np.float64)
    d_f = np.asarray(direction, dtype=np.float64)
    d_r = -d_f
    N = len(P)
    ts = np.linspace(0.0, float(reach), n_coarse)

    Q_f = P[None, :, :] + ts[:, None, None] * d_f[None, None, :]
    Q_r = P[None, :, :] + ts[:, None, None] * d_r[None, None, :]
    Q_cat = np.vstack([Q_f.reshape(-1, 3), Q_r.reshape(-1, 3)])
    V_cat = np.asarray(eval_grid(source, Q_cat), dtype=np.float64)
    V_f = V_cat[:n_coarse * N].reshape(n_coarse, N)
    V_r = V_cat[n_coarse * N:].reshape(n_coarse, N)

    out_f = V_f > 0.0
    found_f = out_f.any(axis=0)
    first_f = np.argmax(out_f, axis=0)
    lo_f = ts[np.maximum(first_f - 1, 0)]
    hi_f = ts[first_f]

    out_r = V_r > 0.0
    found_r = out_r.any(axis=0)
    first_r = np.argmax(out_r, axis=0)
    lo_r = ts[np.maximum(first_r - 1, 0)]
    hi_r = ts[first_r]

    for _ in range(n_bisect):
        mid_f = 0.5 * (lo_f + hi_f)
        mid_r = 0.5 * (lo_r + hi_r)
        P_mid_f = P + d_f * mid_f[:, None]
        P_mid_r = P + d_r * mid_r[:, None]
        P_mid_cat = np.vstack([P_mid_f, P_mid_r])
        inside_cat = np.asarray(eval_grid(source, P_mid_cat), dtype=np.float64) <= 0.0
        inside_f = inside_cat[:N]
        inside_r = inside_cat[N:]
        lo_f = np.where(inside_f, mid_f, lo_f)
        hi_f = np.where(inside_f, hi_f, mid_f)
        lo_r = np.where(inside_r, mid_r, lo_r)
        hi_r = np.where(inside_r, hi_r, mid_r)

    res_f = np.where(found_f, 0.5 * (lo_f + hi_f), np.inf), found_f
    res_r = np.where(found_r, 0.5 * (lo_r + hi_r), np.inf), found_r
    return res_f, res_r


def face_height_datum(source, ring, handles, normals, direction, origin,
                      n_edge=8, n_interior=2):
    """(h0, ok, h1): where the parent's FRONT surface sits under the face.

    h0 and h1 are the lowest and highest points of that surface over the face
    footprint, measured as dot(x - origin, direction). `ok` is False when the
    parent's back surface reaches above h0 somewhere under the face -- a parent
    thinner than the face's own relief -- in which case this construction cannot
    be used.

    h0 is the slab datum: the height the cap must start from to stay embedded
    everywhere under the face. h1 is what `bounding_box` needs, because the lobe
    cannot reach further along the axis than the surface it grew from plus the
    sweep, and on a curved parent that crown sits well above the ring corners.

    Probes are seeded STRICTLY INSIDE. A cage point is only on the source's zero
    set while the cage is undeformed; after a deformation it drifts off by an
    arbitrary amount, and a probe that starts outside would otherwise report
    front == rear and veto the whole face.
    """
    probes = bezier_ring_samples(ring, handles, n_subdiv=n_edge)[0]
    c = np.asarray(ring, dtype=np.float64).mean(axis=0)
    # The centroid is probed on its own: h1 bounds a crown, and on a convex face
    # the crown is at the middle, which no ring-to-centroid fraction below 1 hits.
    interior = np.vstack([probes + (c - probes) * f
                          for f in np.linspace(0.25, 0.75, n_interior)]
                         + [c.reshape(1, 3)])
    P = np.vstack([probes, interior])

    eps = 1e-3 * max(1.0, float(np.linalg.norm(np.ptp(ring, axis=0))))
    n_probe = ring_surface_normals(source, P)
    s0 = np.asarray(eval_grid(source, P), dtype=np.float64)
    # S is a distance, so stepping back along -n by (S + eps) lands inside.
    P_in = P - n_probe * (np.maximum(s0, 0.0) + eps)[:, None]
    inside = np.asarray(eval_grid(source, P_in), dtype=np.float64) < 0.0

    if not inside.any():
        fld_logger.warn("face_height_datum: no footprint probe is inside the source")
        return 0.0, False, 0.0
    P_in = P_in[inside]

    d = np.asarray(direction, dtype=np.float64)
    reach = _source_reach(source, ring)
    (t_f, ok_f), (t_r, ok_r) = _scan_exit_pair(source, P_in, d, reach)

    keep = ok_f & ok_r
    if not keep.any():
        fld_logger.warn("face_height_datum: source appears unbounded under the face")
        return 0.0, False, 0.0

    h = (P_in - np.asarray(origin, dtype=np.float64)) @ d
    front = (h + t_f)[keep]
    h0, h1 = float(front.min()), float(front.max())
    ok = bool(len(front) > 0)
    if not ok:
        fld_logger.warn("face_height_datum: no valid front probes found under face footprint")
    return h0, ok, h1


def bezier_ring_samples(ring_pts, ring_handles=None, n_subdiv=8, tol=1e-6):
    """Sample one cage face's boundary curve. Returns (samples, params).

    Ring edge i runs ring_pts[i] -> ring_pts[i+1] as a cubic Bezier with control
    points ring_handles[2i] (near i) and ring_handles[2i+1] (near i+1). A cage
    face is bounded by those curves, not by the chords between its vertices --
    sampling them is what stops an extruded sphere face from being a flat-sided
    triangle sitting inside the bulged spherical one.

    An edge whose handles sit on the chord thirds is already straight and gets a
    single sample: subdividing it would emit n identical wall planes, and a box
    or cylinder face is straight-edged throughout. Without handles the whole ring
    degrades to that case, i.e. the polygon itself.

    params[i] is (edge index, t in [0,1)) for samples[i], so a caller can carry
    per-corner data along the boundary.
    """
    P = np.asarray(ring_pts, dtype=np.float64)
    k = len(P)
    H = (np.asarray(ring_handles, dtype=np.float64).reshape((-1, 3))
         if ring_handles is not None else None)
    if H is None or len(H) < 2 * k or n_subdiv < 2:
        return P, np.column_stack([np.arange(k, dtype=np.float64), np.zeros(k)])

    pts, params = [], []
    for i in range(k):
        p0, p3 = P[i], P[(i + 1) % k]
        p1, p2 = H[2 * i], H[2 * i + 1]
        chord = p3 - p0
        straight = (np.allclose(p1, p0 + chord / 3.0, atol=tol)
                    and np.allclose(p2, p0 + 2.0 * chord / 3.0, atol=tol))
        # endpoint excluded, so consecutive edges do not duplicate a vertex
        t = np.linspace(0.0, 1.0, 1 if straight else n_subdiv,
                        endpoint=False).reshape((-1, 1))
        u = 1.0 - t
        pts.append(u * u * u * p0 + 3.0 * u * u * t * p1
                   + 3.0 * u * t * t * p2 + t * t * t * p3)
        params.append(np.column_stack([np.full(len(t), float(i)), t.ravel()]))
    return np.vstack(pts), np.vstack(params)


class SdfFaceExtrusionField(SdfField):
    """One extruded cage face: max(-source, cap, walls).

    mode is "surface" | "control" | "defined" -- see direction-mode documentation.
    `direction` is used only in "defined" mode.
    """

    EMBED = 0.05   # mm. The extrusion base sits this far INSIDE the parent, so
                   # the union's zero set is not a coincident 2-D patch.

    AXIAL_PAD = 0.25   # mm of slack on the front surface `_axial_box` measures,
                       # which is sampled at the ring, two interior rings and the
                       # centroid -- dense on a crown, not a proof of one.

    def __init__(self, source, base_ring, top_ring, mode="surface", direction=None,
                 base_handles=None, n_subdiv=8):
        super().__init__()
        self.source = source
        self.base_ring = np.asarray(base_ring, dtype=np.float64)
        self.top_ring = np.asarray(top_ring, dtype=np.float64)
        self.mode = mode
        self.direction = direction
        self.base_handles = (np.asarray(base_handles, dtype=np.float64).reshape((-1, 3))
                             if base_handles is not None and len(base_handles) else None)
        self.n_subdiv = int(n_subdiv)

        n_ring = ring_surface_normals(source, self.base_ring)

        if mode == "surface":
            sweep_dir = None
        elif mode == "control":
            # Newell is CCW-positive w.r.t. the ring winding, and cage face
            # winding carries no outward guarantee. An inward sweep would flip
            # the cap half-space and hollow the extrusion out, so agree with the
            # source's own outward normals.
            sweep_dir = newell_normal(self.top_ring)
            if np.dot(sweep_dir, n_ring.mean(axis=0)) < 0.0:
                sweep_dir = -sweep_dir
        elif mode == "defined":
            d_arr = (np.asarray([direction[0], direction[1], direction[2]], dtype=np.float64)
                     if direction is not None else None)
            norm_d = np.linalg.norm(d_arr) if d_arr is not None else 0.0
            if norm_d > 1e-12:
                sweep_dir = d_arr / norm_d      # user's vector is authoritative
            else:
                sweep_dir = newell_normal(self.top_ring)
                if np.dot(sweep_dir, n_ring.mean(axis=0)) < 0.0:
                    sweep_dir = -sweep_dir
        else:
            raise ValueError(f"Unknown mode: {mode}")

        self.sweep_dir = sweep_dir
        # In surface mode there is no sweep_dir, but the prism still has an
        # axis, and `bounding_box` needs to know which way it runs.
        self._n_ring_mean = n_ring.mean(axis=0)
        self._n_ring_mean = self._n_ring_mean / max(
            float(np.linalg.norm(self._n_ring_mean)), 1e-12)
        self._org = self.base_ring.mean(axis=0)

        # Where the source's front surface sits under the footprint. Depends on
        # the BASE ring only, never on the top ring, which is what lets
        # `with_top_ring` reuse it through a drag -- it costs a bracketed scan
        # per probe. Surface mode sweeps continuously and needs no slab.
        if mode == "surface":
            self._datum = (0.0, False, 0.0)
        else:
            self._datum = face_height_datum(source, self.base_ring, self.base_handles,
                                            n_ring, sweep_dir, self._org)
            if not self._datum[1]:
                fld_logger.debug(
                    "face_height_datum ok=False for face; the lobe falls back to the "
                    "translated-source cap alone and may detach on a long sweep")

        # Orthonormal frame with `sweep_dir` (or average surface normal) as the prism axis.
        effective_axis = sweep_dir if sweep_dir is not None else n_ring.mean(axis=0)
        org, e1, e2 = extrude_frame(effective_axis, self.base_ring.mean(axis=0))

        self._apply_top_ring(self.top_ring)

        segs = project_bezier_ring(self.base_ring, self.base_handles, org, e1, e2)
        self.profile = SdfProfilePrismField(Sdf2dBezierCurve(segs), org, e1, e2)

    def _apply_top_ring(self, top_ring):
        """Set the state that depends on the top ring: sweep_len and the slab."""
        self.top_ring = np.asarray(top_ring, dtype=np.float64).reshape((-1, 3))

        if self.mode == "surface":
            # Offset is already topology-preserving and has no length limit.
            self.sweep_len = float(np.mean(
                np.linalg.norm(self.top_ring - self.base_ring, axis=1)))
            self.cap_slab = None
        else:
            self.sweep_len = float(np.mean((self.top_ring - self.base_ring) @ self.sweep_dir))
            h0, ok, _ = self._datum
            # The slab is the only part of the lobe that moves with the top
            # ring; h0 does not, so a drag re-makes four floats and nothing else.
            self.cap_slab = (SdfSlabField(self._org, self.sweep_dir, h0, self.sweep_len)
                             if ok else None)

    def with_top_ring(self, top_ring):
        """This extrusion swept to a new top ring, reusing the base-ring analysis.

        The base ring fixes the profile prism and, in control mode, nothing else
        that the top ring can move; only `sweep_len` changes under a rigid
        translation.

        Rebuilding instead re-runs `face_height_datum`, whose bracketed scan
        evaluates the whole stack *underneath* this lobe -- so it gets dearer
        with every extrusion already on the cage. Measured 2026-08-18 on a
        sphere cage, per drag tick, at 1/2/3 lobes (CX-006):

            shortcut       0.359 / 0.342 / 0.361 ms
            full rebuild   1.117 / 25.005 / 39.624 ms

        One datum scan runs per tick either way; it alone is 0.64 / 21.2 / 34.5
        ms of that, i.e. ~94% of the rebuild. `ring_surface_normals` -- one MVC
        solve per ring vertex against a deformed-cage source -- is the rest, at
        0.08 / 0.87 / 1.55 ms. So the shortcut is what keeps a two-lobe drag
        inside the 16 ms tick budget at all, and the margin widens with depth.

        The rigid-translation guard is required, not conservative: `sweep_dir`
        is the TOP ring's Newell normal, so a non-uniform offset turns the ring
        and the frame with it.
        """
        top = np.asarray(top_ring, dtype=np.float64).reshape((-1, 3))
        offsets = top - self.base_ring if len(top) == len(self.base_ring) else None
        if (self.mode == "surface" or offsets is None
                or not np.allclose(offsets, offsets[0], atol=1e-9)):
            return SdfFaceExtrusionField(
                self.source, self.base_ring, top, mode=self.mode,
                direction=self.direction, base_handles=self.base_handles,
                n_subdiv=self.n_subdiv)

        swept = copy.copy(self)
        swept._apply_top_ring(top)
        return swept

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return float(self.evaluate_grid(
            np.array([[point.x, point.y, point.z]], dtype=np.float64))[0])

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64)
        s = np.asarray(self.source.evaluate_grid(pts), dtype=np.float64)
        return lobe_grid(self, pts, s)

    def to_glsl(self, ctx, point_var="p"):
        # The source is named once, as a helper, and called for `s` and again
        # for the shifted cap. Inlining it twice would double every helper the
        # source itself registers -- and the source may be a whole extrusion
        # stack or an MVC deform cage.
        src_fn = ctx.get_unique_name("lobe_src")
        ctx.add_custom_helper(
            src_fn, f"float {src_fn}(vec3 p) {{ return {self.source.to_glsl(ctx, 'p')}; }}")

        walls = self.profile.to_glsl(ctx, point_var)
        cap = None
        if self.mode != "surface":
            u_v = ctx.uniform("vec3", (self.sweep_dir * self.sweep_len).tolist())
            cap = f"{src_fn}({point_var} - {u_v})"
        return lobe_glsl(ctx, self, f"{src_fn}({point_var})", walls, cap, point_var)

    def _axial_box(self, foot):
        """Bound the lobe along the prism axis, or None if it cannot be bounded.

        The cap box on its own is a bad bound. It is the PARENT's box translated,
        so it says nothing about which part of the parent the face sits on, and
        `bounding_box` can only intersect it with a footprint stretched by
        `|axis| * reach` -- which on a diagonal face normal touches all three
        coordinates and loses the footprint's tightness in every one of them.
        Measured on the 1..5-lobe cage that left a drag region covering 66-77%
        of the scene bbox when the set that actually changed was 12-19%, and a
        region bake at depth 4-5 cost as much as a full one.

        In the prism's own frame the lobe is small and easy to describe. The
        parent's front surface under the footprint runs between heights h0 and
        h1 (`face_height_datum` walks it with a bracketed sign scan, so this
        needs the parent's sign, never its distance). The lobe grows out of that
        surface and stops `sweep_len` past it, and the base term -(S+EMBED) holds
        it to EMBED below. So h in [h0 - EMBED, h1 + sweep_len], swept over the
        footprint's own transverse extent.

        What this deliberately excludes: the cap `S(p - v)` also admits a second
        branch where the prism leaves the parent's BACK surface, and that branch
        is real -- it appears on a short sweep, measured at h = -16.8 on a 5-lobe
        cage at sweep 3. It cannot move a surface. The branch lives at
        h >= rear + sweep_len, i.e. strictly inside the parent's own solid for
        any positive sweep, so the composite `min` is negative there with or
        without the lobe; dropping it changes a stored interior distance and
        nothing that is drawn. Bounding it instead would mean carrying the rear
        surface too and giving back most of the tightening, since on a sphere
        rear + sweep_len sits ~13 mm below the face.
        """
        if self.mode == "surface" or not self._datum[1]:
            return None            # no scan was run; the caller's box stands

        d = np.asarray(self.sweep_dir, dtype=np.float64)
        h0, _, h1 = self._datum
        # sweep_len is signed: an inward sweep swaps which end the cap bounds.
        lo = min(h0 - self.EMBED, h0 + self.sweep_len) - self.AXIAL_PAD
        hi = max(h1 + self.sweep_len, h1) + self.AXIAL_PAD

        hf = (foot - self._org) @ d
        trans = foot - np.outer(hf, d)                    # footprint, axis removed
        ends = trans[:, None, :] + np.outer([lo, hi], d)[None, :, :]
        ends = ends.reshape(-1, 3)
        return ends.min(axis=0), ends.max(axis=0)

    def bounding_box(self):
        """The lobe is `max(-(S+E), walls, cap)`, so it is contained in the
        intersection of the profile prism with the region the cap admits.

        Too large is slow; too small deletes geometry -- the voxel bake writes a
        field only inside this box, and anything outside it is simply never
        written. So the prism is bounded by the cap's box rather than by the
        swept ring: on a curved source the surface under the footprint bulges
        past both rings, and a box drawn through the ring corners alone cuts
        the cap off.

        That leaves the box far too long along the prism axis, which `_axial_box`
        then cuts back -- see there for why the cap's box alone is a poor bound
        and what the tighter one is allowed to drop.
        """
        pts = self.base_ring
        if self.base_handles is not None and len(self.base_handles) > 0:
            pts = np.vstack([pts, self.base_handles])
        foot_min, foot_max = pts.min(axis=0), pts.max(axis=0)

        try:
            smin, smax = self.source.bounding_box()
            cap_min = np.array([smin.x, smin.y, smin.z], dtype=np.float64)
            cap_max = np.array([smax.x, smax.y, smax.z], dtype=np.float64)
        except (NotImplementedError, AttributeError, TypeError):
            # An unbounded source bounds nothing, so neither does the cap. Fall
            # back to the old swept-ring box rather than to something wrong.
            d = abs(self.sweep_len)
            return (FreeCAD.Vector(*(foot_min - d - 0.05)),
                    FreeCAD.Vector(*(foot_max + d + 0.05)))

        if self.mode == "surface":
            # cap = S - L: the source grown by L in every direction, and the
            # prism axis is the face's average surface normal.
            d = abs(self.sweep_len)
            cap_min, cap_max = cap_min - d, cap_max + d
            axis = self._n_ring_mean
        else:
            # cap = S(p - v): the source's own box, translated along the sweep.
            v = self.sweep_dir * self.sweep_len
            cap_min, cap_max = cap_min + v, cap_max + v
            axis = self.sweep_dir

        # The prism is INFINITE along its axis, so the footprint box bounds only
        # the transverse directions; along the axis the cap is what bounds the
        # lobe. Stretch the footprint far enough along +/-axis that it cannot
        # clip the cap, then intersect. Stretching by |axis_k| * reach touches
        # every component the axis has a share of, which is conservative and
        # still leaves the transverse extents at the ring's own.
        span = np.linalg.norm(cap_max - cap_min)
        offs = np.linalg.norm(0.5 * (cap_min + cap_max) - 0.5 * (foot_min + foot_max))
        reach = np.abs(axis) * (span + offs)

        bmin_arr = np.maximum(foot_min - reach, cap_min) - 0.05
        bmax_arr = np.minimum(foot_max + reach, cap_max) + 0.05

        tight = self._axial_box(pts)
        if tight is not None:
            bmin_arr = np.maximum(bmin_arr, tight[0])
            bmax_arr = np.minimum(bmax_arr, tight[1])

        if np.any(bmax_arr <= bmin_arr):
            # Too large is slow; too small silently deletes solid, because the
            # voxel bake writes a field only inside this box.
            fld_logger.warn(
                "SdfFaceExtrusionField.bounding_box: prism and cap boxes do not "
                "meet; falling back to their union rather than an empty box")
            bmin_arr = np.minimum(foot_min, cap_min) - 0.05
            bmax_arr = np.maximum(foot_max, cap_max) + 0.05

        return (FreeCAD.Vector(*bmin_arr), FreeCAD.Vector(*bmax_arr))

    def lipschitz(self) -> float:
        # Don't assert 1.0. Every term is built from `source` -- the base side is
        # -(source + EMBED) and the cap is the source translated -- so an
        # extrusion is only 1-Lipschitz when its source is. Extrude a face of a
        # deformed cage and the source is an MVC warp measuring |grad| ~20;
        # claiming 1.0 makes sdf_octree cull cells that do contain surface (its
        # test is |d(centre)| <= half_diag * L), which pits the mesh exactly
        # where the deformation is strongest. Still 1.0 for every primitive.
        return max(self.source.lipschitz(), self.profile.lipschitz())


class SdfExtrusionStackField(SdfField):
    """A base solid plus N stacked face extrusions, evaluated as ONE flat stack.

    Same value as the nested spine `UnionField(UnionField(base, E0), E1)` where
    each `Ek.source` is the union below it -- only the evaluation order differs.

    That nested form costs 3^n. Every level evaluates its source three times:
    once for the base-side term `-(source + EMBED)`, once inside the cap (which
    IS the source, translated -- see design_analytic_face_extrude), and once
    more as the union's own left operand. Measured on a sphere at depths 1..4:
    20k-point evaluate 151 / 541 / 1783 / 5423 ms, shader source 424 / 1722 /
    6099 / 20434 chars, uniform count 33 / 126 / 405 / 1242.

    Flattening removes two of the three. The running prefix S_k is computed once
    per level and handed to both the base-side term and the union, leaving only
    the cap's evaluation of the prefix at `p - sweep_dir * sweep_len`. Those
    shifted evaluations are memoised by offset, so a straight tower -- every
    lobe swept the same vector -- collapses to O(n^2); lobes in different
    directions still branch, but at 2^n rather than 3^n.

    The remaining 2^n is then paid only where it changes the answer. A lobe is
    consumed as `min(s, lobe)`, and `lobe` is a `max` that the cap can only
    raise, so once the cheap terms alone reach `s` the cap cannot affect the
    union and is skipped -- exactly, not approximately. That short-circuit is
    self-terminating: a cap samples its source at points that lie in or near
    the material below, which is precisely where the base-side term dominates,
    so the recursion collapses instead of doubling. On a 3-lobe sphere it cuts
    base evaluations along a screenful of rays by ~5x.

    `to_glsl` emits the same recursion as a STACK MACHINE -- one loop over an
    explicit frame stack -- not as nested helper calls. That is the whole of
    UX-010, and it is about compile time rather than frame time.

    The obvious emission, one `float f(vec3)` helper per level, keeps the shader
    TEXT linear but not the shader. GLSL has no recursion, so drivers inline the
    whole call graph, and what they expand is the number of root-to-leaf paths:
    level k reaches level k-1 once directly and once through the lobe's cap, so
    the inlined graph is 2^n even though the text is linear. Measured that way:
    9/12/15/18/21/24 emitted helpers at 1..6 lobes against 17/47/107/227/467/947
    nodes after inlining, and driver `compile_compute` times of 0.74 s / 4.9 s /
    28.7 s at 1/2/3 lobes. Emitted source length is not a proxy for compile cost.

    The machine has one copy of the lobe body, so the base solid -- which may be
    an MVC deform cage -- is named at exactly one call site and the inlined graph
    is linear. The loop bound is a uniform precisely so the driver cannot unroll
    it back into a path tree.

    Linear was not enough. The lobe body called a per-lobe if-chain, and each
    arm of it was that lobe's ring unrolled one block per Bezier segment with
    the cubic distance and winding solves inlined -- so the SHADER still grew as
    lobes x segments while the text grew as lobes. A GUI run at depth 3 measured
    a 4858 ms driver compile against 92 ms at depth 2: 53x for 20% more source.
    Every per-lobe term now comes out of two SSBOs (`_ring_records`) read by ONE
    ring loop, which makes the emitted shader the same size at one lobe and at
    six -- and a drag re-uploads the buffer rather than recompiling.
    """

    SHIFT_DP = 9    # decimal places the memo keys offsets to -- 1 pm

    def __init__(self, base, extrusions):
        super().__init__()
        self.base = base
        self.extrusions = list(extrusions)

    SPARSE_FRAC = 0.5   # descend into the cap on a subset below this fill rate

    def _prefix(self, pts, k, shift, memo):
        """S_k -- base plus lobes 0..k-1 -- evaluated at `pts + shift`."""
        vals, _ = memo
        # `pts` is in the key because a lobe may descend on a subset of the
        # points; entries for different subsets must not collide. The subsets
        # are held alive in memo[1] so their ids stay unique.
        key = (k, shift.tobytes(), id(pts))
        hit = vals.get(key)
        if hit is not None:
            return hit
        if k == 0:
            val = np.asarray(self.base.evaluate_grid(pts + shift))
        else:
            prev = self._prefix(pts, k - 1, shift, memo)
            val = np.minimum(
                prev, self._lobe(pts, k - 1, shift, memo, prev)).astype(np.float32)
        vals[key] = val
        return val

    def _lobe(self, pts, j, shift, memo, s_j):
        """Lobe j at `pts + shift`; `s_j` is its source already evaluated there.

        The formula is `lobe_grid`'s and lives there; this is only the decision
        of WHICH terms have to be evaluated. It returns the true lobe distance
        wherever it is below `s_j`, and some value that is still >= `s_j`
        elsewhere -- enough for the `min` this feeds, and it buys skipping the
        cap's evaluation of the prefix.
        """
        ext = self.extrusions[j]
        q = pts + shift
        b = -(s_j + ext.EMBED)

        # The profile's bounding box bounds its exact field from below, so
        # points it already lifts to s_j never need the cubic-Bezier solve.
        e = np.maximum(b, ext.profile.lower_bound_grid(q))
        near = e < s_j
        if near.any():
            walls = ext.profile.evaluate_grid(q[near] if not near.all() else q)
            if near.all():
                e = np.maximum(b, walls)
            else:
                e[near] = np.maximum(b[near], walls)

        # `e` is now max(b, walls) wherever that is below s_j, and a valid
        # over-estimate elsewhere -- so it can stand in for `walls` below:
        # max(b, e) == e, since e >= b everywhere.
        if ext.mode == "surface":
            return lobe_grid(ext, q, s_j, walls=e)

        need = e < s_j
        n_need = int(np.count_nonzero(need))
        if n_need == 0:
            return e.astype(np.float32)
        # Rounded so a tower of lobes swept the same way shares one memo entry.
        # sweep_dir is a Newell normal, which is only translation invariant to
        # within rounding, so the raw offsets miss by ~1e-16 and every level
        # would branch again.
        cap_shift = np.round(
            shift - ext.sweep_dir * ext.sweep_len, self.SHIFT_DP)
        if n_need >= self.SPARSE_FRAC * len(need):
            return lobe_grid(ext, q, s_j, walls=e,
                             cap=self._prefix(pts, j, cap_shift, memo))

        sub = np.ascontiguousarray(pts[need])
        memo[1].append(sub)
        out = e.astype(np.float32)
        out[need] = lobe_grid(ext, q[need], s_j[need], walls=e[need],
                              cap=self._prefix(sub, j, cap_shift, memo))
        return out

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return float(self.evaluate_grid(
            np.array([[point.x, point.y, point.z]], dtype=np.float64))[0])

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64)
        return self._prefix(pts, len(self.extrusions),
                            np.zeros(3, dtype=np.float64),
                            ({}, [pts])).astype(np.float32)

    # RingMeta is 8 vec4 and RingSeg is 2; both are mirrored in the struct
    # declarations in glsl_compiler.py.
    RING_META_VEC4 = 8
    RING_SEG_VEC4 = 2

    def _ring_records(self):
        """(meta, segs) -- the two SSBO images, float32 and C-contiguous.

        `meta` is (n, 8, 4) and `segs` is (total_segments, 2, 4); the field order
        is the comment block on `RingMeta` / `RingSeg` in glsl_compiler.py, and
        `_GLSL_RING` is the only reader. Everything a lobe needs is here --
        frame, profile bbox, EMBED, sweep, slab -- so the emitted source depends
        on nothing but the lobe COUNT, and a drag re-uploads rather than
        recompiling.
        """
        n = len(self.extrusions)
        meta = np.zeros((max(n, 1), self.RING_META_VEC4, 4), dtype=np.float32)
        segs = []

        for j, ext in enumerate(self.extrusions):
            prism = ext.profile
            curve = getattr(prism, "profile", None)
            ring = getattr(curve, "segments", None)
            if ring is None:
                raise TypeError(
                    f"lobe {j}'s profile is {type(prism).__name__}, which carries no "
                    f"Bezier ring; the extrusion stack's GLSL reads rings from a buffer "
                    f"and has no per-lobe emission to fall back on")

            x0, y0, x1, y1 = prism.profile.bbox_2d()
            meta[j, 0, :3] = prism.origin
            meta[j, 0, 3] = len(segs)
            meta[j, 1, :3] = prism.e1
            meta[j, 1, 3] = len(ring)
            meta[j, 2, :3] = prism.e2
            meta[j, 2, 3] = ext.EMBED
            meta[j, 3] = (x0, y0, x1, y1)

            surface = ext.mode == "surface"
            v = np.zeros(3) if surface else ext.sweep_dir * ext.sweep_len
            meta[j, 4, :3] = v
            meta[j, 4, 3] = ext.sweep_len

            if ext.cap_slab is not None:
                meta[j, 5, :3] = ext.cap_slab.origin
                meta[j, 5, 3] = ext.cap_slab.h0
                meta[j, 6, :3] = ext.cap_slab.axis
                meta[j, 6, 3] = ext.cap_slab.length
            meta[j, 7, 0] = 1.0 if surface else 0.0
            meta[j, 7, 1] = 0.0 if ext.cap_slab is None else 1.0

            for p0, p1, p2, p3 in ring:
                segs.append(((p0[0], p0[1], p1[0], p1[1]),
                             (p2[0], p2[1], p3[0], p3[1])))

        seg_arr = (np.asarray(segs, dtype=np.float32)
                   if segs else np.zeros((1, self.RING_SEG_VEC4, 4), dtype=np.float32))
        return np.ascontiguousarray(meta), np.ascontiguousarray(seg_arr)

    def ssbo_packer(self, name):
        """The buffer contents for SSBO `name`, as the renderer/evaluator ask."""
        meta, segs = self._ring_records()
        return meta if "ring_meta" in name else segs

    def to_glsl(self, ctx, point_var="p"):
        n = len(self.extrusions)
        if n == 0:
            return self.base.to_glsl(ctx, point_var)

        base_fn = ctx.get_unique_name("stack_base")
        base_body = f"float {base_fn}(vec3 p) {{ return {self.base.to_glsl(ctx, 'p')}; }}"
        ctx.add_custom_helper(base_fn, base_body)

        # Every per-lobe leaf -- wall profile, its bound, the slab, the sweep,
        # EMBED, the surface flag -- comes out of these two buffers, so the
        # machine below is the whole of the per-lobe source text and a drag
        # re-uploads instead of recompiling. `_ring_records` writes them.
        ctx.add_custom_helper("sd_solve_cubic", _GLSL_SOLVE_CUBIC)
        ctx.add_custom_helper("sd_cubic_bez_2d", _GLSL_CUBIC_BEZ_2D)
        ctx.add_custom_helper("sd_cubic_winding", _GLSL_CUBIC_WINDING)
        ctx.add_custom_helper("sd_box2d", _GLSL_BOX2D)

        meta = ctx.ssbo("ring_meta", self, struct_type="RingMeta")
        segs = ctx.ssbo("ring_segs", self, struct_type="RingSeg")
        uv = ctx.get_unique_name("ring_uv")
        bound = ctx.get_unique_name("ring_bound")
        walls = ctx.get_unique_name("ring_walls")
        slab = ctx.get_unique_name("ring_slab")
        ctx.add_custom_helper(walls, _GLSL_RING.format(
            meta=meta, segs=segs, uv=uv, bound=bound, walls=walls, slab=slab))

        # Worst case is 2^n sub-problems of <= n lobe steps plus one pop each --
        # the count the early-outs below collapse, not a budget. It is a uniform
        # for two reasons: it must not be a compile-time constant, or the driver
        # unrolls the loop straight back into the 2^n path tree this exists to
        # remove; and truncating it would silently delete solid rather than
        # error, so it is set to a bound that is never reached.
        iters = (1 << n) * (n + 2) + 4
        if iters > (1 << 20):
            iters = 1 << 20
            fld_logger.warn(
                f"extrusion stack: {n} lobes exceeds the exact GLSL iteration "
                f"bound; the shader may drop geometry at grazing angles")
        u_it = ctx.uniform("int", iters)

        name = ctx.get_unique_name("stack_eval")
        body = f"""float {name}(vec3 p_in) {{
    float f_s[{n}];
    float f_e[{n}];
    ivec2 f_mt[{n}];
    vec3  q     = p_in;
    int   sp    = 0;
    int   top   = {n};
    int   m     = 1;
    float s     = 0.0;
    bool  fresh = true;
    for (int it = 0; it < {u_it}; ++it) {{
        if (fresh) {{ s = {base_fn}(q); m = 1; fresh = false; }}
        if (m > top) {{
            if (sp == 0) return s;
            sp--;
            int mm = f_mt[sp].x;
            int jj = mm - 1;
            q += {meta}[jj].sweep.xyz;
            float cap = min(s, {slab}(jj, q));
            s   = min(f_s[sp], max(f_e[sp], cap));
            top = f_mt[sp].y;
            m   = mm + 1;
            continue;
        }}
        // Lobe j, given s = S_j(q). The caller takes min(s, lv) and every term
        // here can only raise a max, so any value already >= s settles the
        // union and the rest is irrelevant -- these three skips are exact, not
        // approximate. Cheapest first, they drop: the wall profile deep inside
        // the material below (which is exactly where a cap above samples), its
        // exact cubic-Bezier solve for anything outside the profile's bounding
        // box, and the cap descent, which is the branch that used to cost 2^n.
        int   j  = m - 1;
        float b  = -(s + {meta}[j].e2.w);
        float lv;
        if (b >= s) {{
            lv = b;
        }} else {{
            float bv = {bound}(j, q);
            float lb = max(b, bv);
            if (bv > 0.0 && lb >= s) {{
                lv = lb;
            }} else {{
                float e = max(b, {walls}(j, q));
                if (e >= s) {{
                    lv = e;
                }} else if ({meta}[j].flags.x > 0.5) {{
                    lv = max(e, s - {meta}[j].sweep.w);
                }} else {{
                    f_s[sp]  = s;
                    f_e[sp]  = e;
                    f_mt[sp] = ivec2(m, top);
                    sp++;
                    q   -= {meta}[j].sweep.xyz;
                    top  = j;
                    fresh = true;
                    continue;
                }}
            }}
        }}
        s = min(s, lv);
        m++;
    }}
    return s;
}}"""
        ctx.add_custom_helper(name, body)
        return f"{name}({point_var})"

    def bounding_box(self):
        from freecad.fields.core.sdf.sdf_composer import _bbox_union

        box = self.base.bounding_box()
        for ext in self.extrusions:
            box = _bbox_union(box, ext.bounding_box())
        return box

    def changed_region(self, prev):
        """World box containing every point where this field differs from `prev`.

        `None` means "cannot narrow it" -- the caller must fall back to the two
        bounding boxes, which for a cage is the whole object.

        This exists because dragging a lobe's top ring rewrites one extrusion and
        leaves the rest of the solid alone, while the *field's* bbox says the
        whole object changed. A drag tick that rebakes only the moving lobe is
        the difference between a full volume bake and a small one.

        Soundness: extrusion j only ever lowers the running min inside its own
        bounding box -- outside it the lobe's value is large and positive, so the
        composite is whatever it was without it. Its cap samples the prefix at a
        shifted point, but the cap is intersected with the lobe, so it too is
        confined to the lobe's box. Points outside the union of the old and new
        boxes therefore have an unchanged value. The band padding a region bake
        needs on top of this is added by SceneVolume.bake(), which knows the band.
        """
        from freecad.fields.core.sdf.sdf_composer import _bbox_union

        if not isinstance(prev, SdfExtrusionStackField) or prev.base is not self.base:
            return None

        a, b = prev.extrusions, self.extrusions
        k = 0
        while k < min(len(a), len(b)) and a[k] is b[k]:
            k += 1

        # A record is rebuilt against the prefix below it, so a change at index k
        # makes every later record a new object too -- taking the whole suffix on
        # both sides is what keeps that honest rather than optimistic.
        boxes = [e.bounding_box() for e in a[k:]] + [e.bounding_box() for e in b[k:]]
        if not boxes:
            return None          # identical stacks; say nothing rather than "empty"
        box = boxes[0]
        for other in boxes[1:]:
            box = _bbox_union(box, other)
        return box

    def lipschitz(self) -> float:
        return max([self.base.lipschitz()]
                   + [e.lipschitz() for e in self.extrusions])
