# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf_slicer.py

Fits smooth cross-section curves to the dense contour polygons that
`sdf_contour.slice_polygons` extracts, by iterative BSpline refinement.
Outputs native Part.BSplineCurve objects with the minimum number of control
points needed for the project accuracy target (0.1 mm; Fields Settings ->
Model Tolerance).
"""
import FreeCAD
import Part
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_constants import DEGENERATE_AXIS_EPS, DEFAULT_MODEL_TOLERANCE_MM
from freecad.fields.core.sdf.sdf_contour import slice_polygons
import math

# Handle type constants — matches Blender bezier control scheme.
# Used in SlicingBSplineCurveWrapper._handle_types and passed through to
# the Fields curve object's HandleTypes property (2 values per control point).
HANDLE_AUTO    = 0  # Catmull-Rom symmetric tangent (smooth section)
HANDLE_VECTOR  = 1  # Handle points toward adjacent control point (sharp corner)
HANDLE_ALIGNED = 2  # Collinear handles, asymmetric lengths (user-editable smooth)
HANDLE_FREE    = 3  # Fully independent in/out handles (user-editable free)

# How many inflection / curvature-extremum features may be seeded before the
# refinement loop has measured anything. See `_refine_to_min_points`: these are
# proxies for error, the loop measures error itself, and seeding all of them
# exhausted the point budget at iteration 0 on every deformed field.
_SOFT_SEED_LIMIT = 8

# Below this many failing segments the refinement stops batching and inserts one
# point per pass. See `_refine_to_min_points`: batching converges the hard contours
# and overspends on the easy ones, and this is the line between the two regimes.
_ENDGAME_SEGMENTS = 4

# What share of a candidate corner's windowed turn must fall on the sharpest
# adjacent PAIR of walk steps before it counts as a crease rather than a tight
# smooth bend. Two steps, not one: a crease whose vertex does not land on a walk
# point splits its turn across the two either side of it, and at one step a
# rotated box read 0.555 and lost all four of its corners.
#
# 0.88 is the highest value that keeps every one of the 39 real creases in the
# case set -- eight box, rotated-box, off-grid-box, non-square-box, CSG and
# displaced-box sections -- and it is chosen on that constraint, not on the false
# positive count. Rounding an edge the part is supposed to have is a worse failure
# than an extra corner on a noise field: the first destroys designed geometry, the
# second lands where the contour is already below tolerance. Raising it to 0.95
# would drop the false positives from 49 to 26 and cost two real corners.
_CREASE_STEP_SHARE = 0.88


class SlicingBSplineCurveWrapper:
    """Wrapper around Part.BSplineCurve that carries Python-side slicer metadata.

    `_is_closed` is the tracer's own verdict on whether the contour is a loop, and
    it is the only thing callers may ask. The BSpline cannot answer: the curve is
    built clamped (`buildFromPolesMultsKnots(..., periodic=False, ...)`), so OCC
    reports `isPeriodic() == False` for a closed contour whose first pole equals
    its last. Deriving closedness from the curve therefore turns every closed slice
    into an open one.

    `_handle_in` / `_handle_out` are the absolute handle positions the fit solved
    for, and they are carried rather than recomputed for the same reason: the
    lengths are a least-squares result (`_solve_bezier_handles`), and no local rule
    reproduces them. `tools/sdf_slice_tool.curve_params_from_slice` stores these
    straight onto the Fields curve object, so what the user gets is what was measured.
    """

    def __init__(self, bspline, interpolation_points=None, handle_types=None,
                 is_closed=False, handle_in=None, handle_out=None):
        object.__setattr__(self, "_bspline", bspline)
        object.__setattr__(self, "_interpolation_points", interpolation_points or [])
        object.__setattr__(self, "_handle_types", handle_types or [])
        object.__setattr__(self, "_is_closed", bool(is_closed))
        object.__setattr__(self, "_handle_in", handle_in or [])
        object.__setattr__(self, "_handle_out", handle_out or [])

    def __getattr__(self, name):
        return getattr(self._bspline, name)

    def __setattr__(self, name, value):
        if name in ("_bspline", "_interpolation_points", "_handle_types",
                    "_is_closed", "_handle_in", "_handle_out") or name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._bspline, name, value)

    def __repr__(self):
        return f"SlicingBSplineCurveWrapper({self._bspline!r})"


def _bezier_chain_to_bspline(pts, h_in, h_out, is_closed):
    """The exact cubic BSpline of the piecewise-Bezier through pts.

    Segment i runs pts[i] -> h_out[i] -> h_in[i+1] -> pts[i+1], so the Bezier
    control polygon is already the pole list; interior knots take multiplicity 3
    (C0 at the joins, which is what a Vector handle at a corner means). The
    result is the curve `_sample_bezier_sdf_error` measured, not a re-fit of it.
    """
    n = len(pts)
    if n < 2:
        return None
    poles = []
    if is_closed:
        for i in range(n):
            poles.extend([pts[i], h_out[i], h_in[(i + 1) % n]])
        poles.append(pts[0])
        knots = [float(i) for i in range(n + 1)]
        mults = [4] + [3] * (n - 1) + [4]
    else:
        for i in range(n - 1):
            poles.extend([pts[i], h_out[i], h_in[i + 1]])
        poles.append(pts[n - 1])
        knots = [float(i) for i in range(n)]
        mults = [4] + [3] * (n - 2) + [4]
    bs = Part.BSplineCurve()
    bs.buildFromPolesMultsKnots(poles, mults, knots, False, 3)
    return bs


def _polyline_arc(source_xyz, is_closed):
    """Cumulative arc length along the dense polyline, and the vertices it indexes.

    A closed contour gets its first vertex appended, so the closing edge is a real
    edge like any other and a query anywhere in [0, total] has somewhere to land.

    Returns:
        (ext, arc) -- the (N+1, 3) or (N, 3) vertex array and the arc length at
        each of its rows. `arc[-1]` is the whole length.
    """
    import numpy as np

    xyz = np.asarray(source_xyz, dtype=np.float64)
    ext = np.vstack((xyz, xyz[:1])) if is_closed else xyz
    steps = np.sqrt(((ext[1:] - ext[:-1]) ** 2).sum(axis=1))
    return ext, np.concatenate(([0.0], np.cumsum(steps)))


def _contour_tangents(source_xyz, indices, is_closed, arc_data=None):
    """Unit tangents read off the DENSE contour, not off the control polygon.

    This is the difference between a fit that converges and one that thrashes. The
    Catmull-Rom tangent this replaces is a function of where the *neighbouring
    control points* happen to sit, so it is exact only while they sit
    symmetrically -- and the refinement loop destroys that symmetry the moment it
    splits one segment and not its neighbour. Measured on a radius-20 circle:
    six evenly spaced control points give a tangent error of 0.000 degrees at
    every one of them, and inserting a single point puts **7.5 degrees** of error
    on the two either side of it.

    Nothing downstream can recover from that. `_solve_bezier_handles` fits handle
    *lengths* and takes these directions as given, so a wrong tangent is a floor
    under the segment's error, and the loop's only response to error is to insert
    another point -- which tilts two more tangents. That is the whole reason a
    circle cost 48 control points at 0.01 mm: error rose from 0.023 mm at 6 points
    to 0.090 mm at 20 before falling again, and the fit only escaped by subdividing
    until the spacing was near-uniform once more. Doubling every failing segment at
    once, which is what the loop does, is not an optimisation -- it is the only
    move that restores the symmetry the tangent rule silently required.

    The dense polyline has no such preference. The tangent at a control point is
    the secant between the two contour positions an equal arc length either side of
    it, which for any circle is exactly parallel to the true tangent regardless of
    where the control points are, and second-order accurate elsewhere. The window
    is half the distance to the nearer neighbouring control point, so it can never
    reach past one: a corner is always a control point, so the point next to a
    crease reads its own side of it and nothing else.

    Endpoints of an open contour fall out of the same expression -- the backward
    query clips to the start of the polyline, leaving a one-sided forward secant of
    half the gap, which is still tighter than the full-gap chord Catmull-Rom used.

    Args:
        indices:  positions in `source_xyz` of the control points, ascending.
        arc_data: the `_polyline_arc` result, when the caller already has it.

    Returns:
        (tangents, valid) -- an (m, 3) unit array and a boolean mask of the rows
        worth using -- or None if the polyline is degenerate.
    """
    import numpy as np

    m = len(indices)
    if m < 2:
        return None
    ext, arc = arc_data if arc_data is not None else _polyline_arc(source_xyz, is_closed)
    total = float(arc[-1])
    if not (total > 1e-9):
        return None
    idx = np.asarray(indices, dtype=np.int64)
    if idx.min() < 0 or idx.max() >= len(arc):
        return None

    s = arc[idx]
    if is_closed:
        gap_next = (np.roll(s, -1) - s) % total
        gap_prev = (s - np.roll(s, 1)) % total
    else:
        d = np.diff(s)
        if len(d) == 0:
            return None
        gap_next = np.concatenate((d, d[-1:]))
        gap_prev = np.concatenate((d[:1], d))

    w = 0.5 * np.minimum(gap_prev, gap_next)

    def at(q):
        q = (q % total) if is_closed else np.clip(q, 0.0, total)
        return np.stack([np.interp(q, arc, ext[:, k]) for k in range(3)], axis=1)

    vec = at(s + w) - at(s - w)
    length = np.sqrt((vec * vec).sum(axis=1))
    valid = length > 1e-12
    if not valid.any():
        return None
    return vec / np.where(valid, length, 1.0)[:, None], valid


def _handle_directions(fit_pts, is_closed, handle_types=None, tangents=None):
    """Unit directions from each control point toward its two handles, and chords.

    Split out of `_compute_bezier_handles` because a handle's *direction* and its
    *length* are decided by different things. The direction is geometry -- a
    centripetal Catmull-Rom tangent at a smooth point, the neighbouring chord at a
    corner -- and a local rule gets it right. The length is a fit, and the local
    rule this file used for it (chord/3) is a constant nobody ever fitted: the
    optimum for a quarter circle is 0.5523*r, which is 0.390 of its chord, so
    chord/3 leaves 0.43 mm on a 4-segment circle of radius 10 where the optimum
    leaves 0.004 mm. `_solve_bezier_handles` solves the lengths and reuses these
    directions untouched, which is what keeps corners sharp and smooth joins G1.

    Returns numpy rather than vectors -- (n, 3), (n, 3), (n,), (n,) -- because
    both callers want arrays: the solver does its whole 2x2 fit in numpy, and
    building n FreeCAD.Vectors here only to unpack them again per segment was 3.4
    of the 13.5 seconds a noisy contour took to fit.

    `tangents` is the `_contour_tangents` result, and it supersedes the Catmull-Rom
    tangent wherever it is valid. Read its docstring before removing it: the local
    rule is exact only for evenly spaced control points, which is a condition the
    refinement loop violates on purpose every time it splits a segment.

    Returns:
        (d_in, d_out, chord_in, chord_out) -- unit directions from the point
        toward each handle, and the neighbouring chord lengths that give the
        chord/3 fallback.
    """
    import numpy as np

    pts = _as_xyz(fit_pts)
    n = len(pts)
    ix = np.arange(n)
    if is_closed:
        prev_i, next_i = (ix - 1) % n, (ix + 1) % n
    else:
        prev_i, next_i = np.clip(ix - 1, 0, n - 1), np.clip(ix + 1, 0, n - 1)
    prev_pt, next_pt = pts[prev_i], pts[next_i]

    to_next = next_pt - pts
    to_prev = prev_pt - pts
    chord_out = np.sqrt((to_next * to_next).sum(axis=1))
    chord_in = np.sqrt((to_prev * to_prev).sum(axis=1))

    def unit(v):
        length = np.sqrt((v * v).sum(axis=1))
        return v / np.where(length > 1e-12, length, 1.0)[:, None]

    # AUTO / ALIGNED / FREE: centripetal Catmull-Rom tangent, shared by both
    # handles so the point stays G1.
    ok = (chord_out > 1e-6) & (chord_in > 1e-6)
    r_in = np.sqrt(np.where(ok, chord_in, 1.0))
    r_out = np.sqrt(np.where(ok, chord_out, 1.0))
    tangent = (-to_prev / r_in[:, None] + to_next / r_out[:, None]
               - (next_pt - prev_pt) / (r_in + r_out)[:, None])
    tangent = unit(np.where(ok[:, None], tangent, next_pt - prev_pt))

    # The contour's own tangent where we have it. Signed against the local rule
    # rather than trusted outright, so a control list that runs against the walk
    # direction cannot flip a handle end for end.
    if tangents is not None:
        dense, dense_ok = tangents
        if len(dense) == n:
            sign = np.sign((dense * tangent).sum(axis=1))
            dense = dense * np.where(sign == 0.0, 1.0, sign)[:, None]
            tangent = np.where(dense_ok[:, None], dense, tangent)

    d_out = tangent
    d_in = -tangent
    if handle_types is not None and len(handle_types) >= n:
        corner = np.array([handle_types[i] == HANDLE_VECTOR for i in range(n)])
        if corner.any():
            d_out = np.where(corner[:, None], unit(to_next), d_out)
            d_in = np.where(corner[:, None], unit(to_prev), d_in)
    return d_in, d_out, chord_in, chord_out


def _compute_bezier_handles(fit_pts, is_closed, handle_types=None):
    """Bezier handle positions from the local chord/3 rule.

    The fallback, and the reconstruction `tools/sdf_slice_tool.py` uses when it has
    nothing but points and types to work from (it imports this as
    `_compute_catmull_rom_handles`). The slicer itself calls
    `_solve_bezier_handles`, which keeps these directions and fits the lengths.

    Returns:
        (h_in, h_out) -- two lists of FreeCAD.Vector, one per control point.
    """
    d_in, d_out, chord_in, chord_out = _handle_directions(fit_pts, is_closed,
                                                          handle_types)
    h_in, h_out = [], []
    for i, p in enumerate(fit_pts):
        h_out.append(p + _as_vector(d_out[i]) * float(chord_out[i] / 3.0))
        h_in.append(p + _as_vector(d_in[i]) * float(chord_in[i] / 3.0))
    return h_in, h_out


def _segment_spans(indices, is_closed, handle_types=None):
    """Per-segment fitting data: where its polyline chunk lives, and a cache key.

    `indices` are positions in the dense polyline of the chosen control points, so
    the points between two consecutive entries are exactly the data one segment
    has to fit. A span is the half-open-ish pair `(a, b)` naming that stretch,
    inclusive of both ends; `b <= a` marks the wrap-around segment of a loop. The
    stretch is named rather than copied because decimation asks this question once
    per trial removal, and copying every chunk each time is O(polyline) work to
    answer a question about four segments.

    The key is what makes the refinement loop affordable. A solved segment depends
    on its own two endpoints, on the two neighbours whose positions set the tangent
    *directions* at those endpoints, and on the two handle types -- and on nothing
    else. Inserting or removing one control point therefore invalidates four
    segments and leaves the rest untouched, so re-solving all of them every
    iteration (which is what a keyless solve does) costs O(points x iterations) for
    no new information. On a 4486-point contour that was 11.7 s of the 12 s spent
    fitting it.
    """
    idx = sorted(indices)
    m = len(idx)
    if m < 2:
        return [], []
    at = (lambda k: idx[k % m]) if is_closed else (lambda k: idx[min(max(k, 0), m - 1)])
    ht = lambda k: (handle_types[k % m] if (handle_types and len(handle_types) == m)
                    else HANDLE_AUTO)
    spans, keys = [], []
    for j in range(m if is_closed else m - 1):
        a, b = idx[j], idx[(j + 1) % m]
        spans.append((a, b))
        keys.append((at(j - 1), a, b, at(j + 2), ht(j), ht(j + 1)))
    return spans, keys


def _as_xyz(points):
    """`points` as an (n, 3) float array, whatever kind of vector it holds."""
    import numpy as np
    if isinstance(points, np.ndarray):
        return points
    return np.array([[p.x, p.y, p.z] for p in points], dtype=np.float64)


def _as_vector(xyz):
    """A row of an (n, 3) array back as a FreeCAD.Vector of plain floats.

    The `float()` calls are not decoration. Everything from `_handle_directions`
    down is numpy, so these components arrive as `numpy.float64`, and handing
    those straight to OCC's bindings is the kind of thing that works perfectly
    under `mock_freecad` and raises in FreeCAD.
    """
    return FreeCAD.Vector(float(xyz[0]), float(xyz[1]), float(xyz[2]))


def _bezier_points(p0, p1, c0, c1, us):
    """The cubic at parameters `us`, all arguments and the result (n, 3) arrays."""
    import numpy as np
    mu = 1.0 - us
    return (np.outer(mu * mu * mu, p0) + np.outer(3.0 * mu * mu * us, c0)
            + np.outer(3.0 * mu * us * us, c1) + np.outer(us * us * us, p1))


def _lsq_handle_lengths(p0, p1, t0, t1, q, us):
    """The (a, b) that minimise squared distance from `q` to the segment.

    The two Bezier control points are pinned to `p0 + a*t0` and `p1 + b*t1`, so
    with the directions fixed the curve is *linear* in the two unknowns and the
    normal equations are a 2x2 system with a closed form -- no iteration, no
    initial guess. This is `GenerateBezier` from Schneider's curve fitter.

    Returns (a, b), or None when the system is singular.
    """
    import numpy as np
    mu = 1.0 - us
    b1 = 3.0 * mu * mu * us
    b2 = 3.0 * mu * us * us
    base = np.outer(mu * mu * mu + b1, p0) + np.outer(b2 + us * us * us, p1)
    resid = q - base
    a1 = np.outer(b1, t0)
    a2 = np.outer(b2, t1)
    c00 = float((a1 * a1).sum())
    c01 = float((a1 * a2).sum())
    c11 = float((a2 * a2).sum())
    x0 = float((a1 * resid).sum())
    x1 = float((a2 * resid).sum())
    det = c00 * c11 - c01 * c01
    if abs(det) < 1e-12:
        return None
    return ((x0 * c11 - x1 * c01) / det, (c00 * x1 - c01 * x0) / det)


def _reparameterize(p0, p1, c0, c1, q, us):
    """Move each data point to the parameter where the curve is actually nearest.

    Chord-length parameterisation assumes the curve advances at the same rate as
    the polyline it is fitting, and it does not. That mismatch, not the handle
    lengths, is most of what the least-squares residual is made of: solving once
    against chord-length parameters left a radius-10 circle needing **6** control
    points where two Newton passes bring it to **4**, the analytic optimum for a
    cubic. So the loop is solve -> reparameterise -> solve, as Schneider has it.

    One Newton step on `(B(u) - q) . B'(u) = 0`, the stationary condition for the
    distance. A step that leaves [0, 1] is dropped rather than clamped, and the
    result is forced monotone: a crossed parameterisation is worse than a stale
    one. The two endpoints are pinned, because letting Newton drift them turns a
    length fit into a (wrong) endpoint fit.
    """
    import numpy as np
    d0 = (c0 - p0) * 3.0
    d1 = (c1 - c0) * 3.0
    d2 = (p1 - c1) * 3.0
    mu = 1.0 - us
    pos = _bezier_points(p0, p1, c0, c1, us)
    vel = (np.outer(mu * mu, d0) + np.outer(2.0 * mu * us, d1)
           + np.outer(us * us, d2))
    acc = np.outer(mu, (d1 - d0) * 2.0) + np.outer(us, (d2 - d1) * 2.0)
    diff = pos - q
    denom = (vel * vel).sum(axis=1) + (diff * acc).sum(axis=1)
    safe = np.abs(denom) > 1e-12
    step = np.where(safe, (diff * vel).sum(axis=1) / np.where(safe, denom, 1.0), 0.0)
    out = us - step
    out = np.where((out >= 0.0) & (out <= 1.0), out, us)
    out = np.maximum.accumulate(out)
    out[0] = 0.0
    out[-1] = 1.0
    return out


def _seg_max_gap(p0, p1, c0, c1, q, n_curve=24):
    """Hausdorff distance in mm between the segment and the polyline under it.

    Symmetric, and it has to be. Measuring only how far each polyline point sits
    from the curve says nothing about a curve that bulges off into space between
    two data points, and measuring only the other direction says nothing about a
    curve that cuts a corner -- a fit can be wrong either way and both show up in
    the viewport.

    Neither is a parameter-matched distance, which was the first version of this
    and was not good enough to decide anything: reparameterisation can bunch the
    data points onto a short stretch and leave the rest of the segment unwatched,
    so a drifting fit still scored well.

    Sampling the curve at `n_curve` points over-states slightly, and does so for
    both candidates equally, which is all the comparison needs.
    """
    import numpy as np
    us = np.linspace(0.0, 1.0, n_curve + 1)
    samples = _bezier_points(p0, p1, c0, c1, us)
    dist = np.sqrt(((q[:, None, :] - samples[None, :, :]) ** 2).sum(axis=-1))
    return max(float(dist.min(axis=1).max()), float(dist.min(axis=0).max()))


def _solve_bezier_handles(fit_pts, is_closed, handle_types, source_xyz, spans,
                          seg_keys=None, cache=None, max_extent=1.0,
                          reparam_iters=12, arc_data=None):
    """Bezier handles whose lengths are least-squares fitted to the polyline.

    Directions come from `_handle_directions` and are not touched, so corners stay
    sharp and smooth points stay G1; only the two magnitudes of each segment move,
    and they move to the optimum for those directions. That optimum is a 2x2 solve
    (Schneider, *Graphics Gems*, 1990) rather than anything iterative: writing the
    segment as

        B(u) = base(u) + a * T0 * B1(u) + b * T1 * B2(u)

    with `base` the part fixed by the two endpoints, the residual is linear in the
    two unknown magnitudes, so the normal equations are 2x2 and closed-form. That
    solve is then alternated with `_reparameterize`, because chord-length
    parameters are an assumption about the curve and not a measurement of it, and
    it is alternated *to convergence* rather than a fixed number of times. The
    fixed count was two, and two is not enough: on a radius-20 circle the handle
    ratio is still 0.3897 after two passes against an analytic optimum of 0.3905,
    and closing that 0.2% takes the segment from 0.0036 mm off the contour to
    0.00018 mm -- a factor of twenty, for iterations that cost no field
    evaluations at all. A straight span converges on the first pass and pays
    nothing.

    This is what replaces chord/3. It is *exact* on a straight span -- a box slice
    comes back byte-identical, because for a straight chord-length-parameterised
    run the optimum is chord/3 -- and on curvature it is the whole difference
    between 0.43 mm and 0.004 mm on a 4-segment circle of radius 10. The
    refinement loop was previously buying that accuracy with control points, four
    times more of them than the shape needs.

    `source_xyz` is the dense polyline as an (N, 3) array and `spans[j]` names the
    stretch of it that segment j has to fit (`_segment_spans`); slicing an array is
    a view, so no chunk is copied unless the solve for it actually runs. A segment with fewer than three points, a degenerate
    direction, or a singular or runaway solve falls back to chord/3 -- for that
    segment alone, not for the curve. So does a solve that simply fits worse than
    chord/3 does, which is not a theoretical case: least squares minimises the
    summed square residual, and where the refinement has run out of control points
    and one segment is spanning a fold the L2 optimum can swing further from the
    contour at its worst point than the conservative chord/3 curve does. The
    comparison (`_seg_max_gap`) costs no field evaluations, and it means this can
    never return a curve further from the polyline than the rule it replaced.

    Handle directions come off the dense polyline (`_contour_tangents`), which the
    control-point indices in `spans` are enough to locate. That is deliberate and
    load-bearing: with the directions taken from the control polygon instead, this
    solve is fitting lengths along tangents that are wrong by several degrees
    whenever the control points are unevenly spaced, and no length can make that up.

    `seg_keys` and `cache` are optional and must be supplied together; see
    `_segment_data` for what the key covers and why it matters. The cache survives
    the change of tangent source because a segment's two tangents depend on the
    same four control indices the key already names.

    `arc_data` is the `_polyline_arc` result; pass it when fitting the same
    polyline repeatedly, as the refinement and decimation loops do.

    Returns:
        (h_in, h_out) -- absolute positions, the same shape
        `_compute_bezier_handles` returns.
    """
    import numpy as np

    n = len(fit_pts)
    pts_xyz = _as_xyz(fit_pts)
    tangents = None
    if spans is not None and len(source_xyz) > 2:
        if is_closed and len(spans) == n:
            ctrl_idx = [sp[0] for sp in spans]
        elif (not is_closed) and len(spans) == n - 1 and n >= 2:
            ctrl_idx = [sp[0] for sp in spans] + [spans[-1][1]]
        else:
            ctrl_idx = None
        if ctrl_idx is not None:
            tangents = _contour_tangents(source_xyz, ctrl_idx, is_closed, arc_data)
    d_in, d_out, chord_in, chord_out = _handle_directions(fit_pts, is_closed,
                                                          handle_types,
                                                          tangents=tangents)
    # Start every handle at the chord/3 fallback; a solved segment overwrites its
    # own two and leaves the rest alone.
    len_out = [float(c) / 3.0 for c in chord_out]
    len_in = [float(c) / 3.0 for c in chord_in]

    segs = n if is_closed else (n - 1)
    for j in range(segs):
        i0, i1 = j, (j + 1) % n
        if not spans or j >= len(spans):
            continue
        lo, hi = spans[j]
        n_chunk = (hi - lo + 1) if hi > lo else (len(source_xyz) - lo + hi + 1)
        if n_chunk < 3:
            continue
        p0, p1 = pts_xyz[i0], pts_xyz[i1]
        t0, t1 = d_out[i0], d_in[i1]
        chord = float(np.sqrt(((p1 - p0) ** 2).sum()))
        if (t0 * t0).sum() < 0.25 or (t1 * t1).sum() < 0.25 or chord < 1e-9:
            continue

        key = seg_keys[j] if (seg_keys and cache is not None and j < len(seg_keys)) else None
        if key is not None and key in cache:
            hit = cache[key]
            if hit is not None:
                len_out[i0], len_in[i1] = hit
            continue

        # The inner math is numpy throughout: a chunk is tens of points and the
        # solve/reparameterise/measure cycle walks it about ten times, which as
        # FreeCAD.Vector arithmetic was 14.9M temporary vectors and 75% of the
        # whole fit.
        q = (source_xyz[lo:hi + 1] if hi > lo
             else np.concatenate((source_xyz[lo:], source_xyz[:hi + 1])))

        # Chord-length parameterisation of the chunk. Uniform parameterisation
        # would bunch the fit wherever the polyline is dense, which on a
        # marching-squares contour is wherever the cells happened to cut.
        steps = np.sqrt(((q[1:] - q[:-1]) ** 2).sum(axis=1))
        us = np.concatenate(([0.0], np.cumsum(steps)))
        if us[-1] < 1e-9:
            continue
        us = us / us[-1]

        solved = _lsq_handle_lengths(p0, p1, t0, t1, q, us)
        for _ in range(reparam_iters):
            if solved is None:
                break
            a, b = solved
            if a <= 1e-9 or b <= 1e-9:
                break
            us = _reparameterize(p0, p1, p0 + t0 * a, p1 + t1 * b, q, us)
            again = _lsq_handle_lengths(p0, p1, t0, t1, q, us)
            if again is None:
                break
            solved = again
            # Converged: the lengths have stopped moving, so further passes only
            # cost time. This is the loop's normal exit -- `reparam_iters` is the
            # runaway guard, not the schedule.
            if (abs(again[0] - a) <= 1e-5 * chord
                    and abs(again[1] - b) <= 1e-5 * chord):
                break

        accepted = None
        # A non-positive magnitude reverses a handle and a runaway one loops the
        # segment back on itself; neither describes the contour, so chord/3 is the
        # honest answer in both cases.
        if solved is not None:
            a, b = solved
            if (a > 1e-9 and b > 1e-9
                    and a <= max_extent * chord and b <= max_extent * chord):
                fit_err = _seg_max_gap(p0, p1, p0 + t0 * a, p1 + t1 * b, q)
                base_err = _seg_max_gap(
                    p0, p1, p0 + t0 * (chord_out[i0] / 3.0),
                    p1 + t1 * (chord_in[i1] / 3.0), q)
                if fit_err <= base_err:
                    accepted = (a, b)

        if cache is not None and key is not None:
            cache[key] = accepted
        if accepted is not None:
            len_out[i0], len_in[i1] = accepted

    h_in = [fit_pts[i] + _as_vector(d_in[i]) * float(len_in[i]) for i in range(n)]
    h_out = [fit_pts[i] + _as_vector(d_out[i]) * float(len_out[i]) for i in range(n)]
    return h_in, h_out


def _surface_distance_grid(field, samples, normal, iters=3):
    """In-plane distance in MILLIMETRES from each sample to the zero isoline.

    `field.evaluate` does not return millimetres. Every field that normalises by a
    Lipschitz bound returns value/L -- `SdfNoiseField` divides by `1 + amp*freq*k`,
    which is 13.0 at amp=3/freq=2 -- and a domain-warping field returns the base
    field's value at the warped point. Only an unmodified primitive is a true
    distance field. Treating the raw value as a deviation understated the real error
    by 2.1x to 25.9x on measured noise fields, so the refinement stopped inserting
    control points while the curve was still millimetres off the surface.

    The fix is Newton's step, `|f| / |grad f|`, which is the distance to the isoline
    and reduces to `|f|` wherever the field is a true distance field -- so smooth
    primitives measure exactly as they did. It divides by the LOCAL gradient, not the
    field's global Lipschitz constant, and that distinction is not academic: in two of
    five measured cases the understatement was LARGER than the constant (25.9x against
    L=7.0), so a constant divisor would not have fixed it.

    The measurement stays ON the slice plane -- the gradient has its normal
    component removed, exactly as `sdf_contour._snap_to_surface` does. The
    unconstrained 3D distance is the wrong quantity for a slice: where the surface
    meets the plane obliquely (a cut across the top of a dome, say) a point can be
    a fraction of a millimetre from the surface and millimetres from the contour
    the slice is supposed to follow.

    Newton is iterated a few times rather than taken to first order, because a
    single step underestimates wherever the isoline curves inside the deviation
    being measured -- which is precisely the noise case this exists for.

    Batched through `evaluate_grid`/`gradient_grid`: the gradient costs six extra
    evaluations per sample scalar-wise, but vectorised it is 3.7 us/point against
    9.3 us for the scalar `evaluate` this replaced.

    Thin wrapper over the shared Newton-projection engine in
    `sdf_field._project_to_isosurface` -- see that docstring for the algorithm this,
    `sdf_contour._snap_to_surface`, and `SdfField.to_patch_cage()`'s vertex projection
    all now share. `tol=None` here (the default) is deliberate: this measures a
    fixed-iteration Newton distance for every sample, so there is no early exit to
    take -- unlike `_snap_to_surface`, which stops a point the moment it converges.
    """
    import numpy as np
    from freecad.fields.core.sdf.sdf_field import _project_to_isosurface

    nrm = np.array([normal.x, normal.y, normal.z], dtype=np.float64)
    start = np.array([[p.x, p.y, p.z] for p in samples], dtype=np.float64)
    pos = _project_to_isosurface(field, start, iters=iters, tol=None, plane_normal=nrm)

    delta = pos - start
    return np.sqrt((delta * delta).sum(axis=1))


def _span_sample_count(span, n_src, is_closed, base=16, cap=64):
    """How many places along one segment are worth measuring.

    A fixed count per segment measures a long segment and a short one to wildly
    different resolutions, and the long one is where the error is. Measured on a
    rounded box at 0.2 mm, six control points: nine samples per segment reported
    0.1721 mm and an independent 400-sample pass over the same curve reported
    0.2113 -- so the fit "converged" at 0.17 against a 0.2 tolerance while
    actually sitting outside it. On a Perlin sphere the same reading was 0.1798
    against 0.3789, a factor of 2.1.

    So the count follows the span: one sample for each vertex of the dense
    polyline the segment has to cover. That is the resolution the contour was
    extracted at, and nothing narrower than it exists to be found. It also makes
    the total roughly the length of the polyline no matter how many control points
    the fit ended up with, instead of growing with them.

    The floor is not decoration either. A Perlin-displaced box fitted to 80 control
    points reads 0.0454 mm at nine samples a segment and 0.0640 at seventeen, and
    every count above seventeen agrees on 0.0640 -- so nine was wrong by 29% on a
    curve whose segments each cover only about a dozen contour vertices, which the
    span rule alone would have left at a dozen samples.

    The cap is what keeps the pathological case affordable -- four control points
    across a four-thousand-vertex contour is a curve nobody is about to accept
    anyway, and it will be split long before the cap matters.
    """
    a, b = span
    count = (b - a) if b > a else (n_src - a + b)
    return int(min(cap, max(base, count)))


def _sample_bezier_sdf_error(field, pts, h_in, h_out, is_closed, normal, n_per_seg=8,
                             return_seg_errors=False, spans=None, n_src=0):
    """Sample the piecewise-cubic bezier and return (max_deviation_mm, worst_point).

    Uses the actual handle positions (not a smooth interpolating BSpline) so
    error at sharp-corner Vector-handle segments is correctly low, preventing
    the refinement loop from over-inserting control points near corners.

    Pass `spans` and `n_src` and each segment is measured at a resolution matched
    to how much contour it covers (`_span_sample_count`); without them every
    segment gets `n_per_seg`, which is only safe when they are all short.

    The returned deviation is in millimetres -- see `_surface_distance_grid`.
    """
    n = len(pts)
    segs = n if is_closed else (n - 1)
    if segs < 1 or not pts:
        if return_seg_errors:
            return 0.0, pts[0] if pts else FreeCAD.Vector(0, 0, 0), []
        return 0.0, pts[0] if pts else FreeCAD.Vector(0, 0, 0)

    samples = []
    seg_ranges = []
    for seg in range(segs):
        i0 = seg
        i1 = (seg + 1) % n
        p0, p1 = pts[i0], pts[i1]
        cp0, cp1 = h_out[i0], h_in[i1]

        k = (_span_sample_count(spans[seg], n_src, is_closed)
             if (spans and n_src and seg < len(spans)) else n_per_seg)
        start_idx = len(samples)
        for j in range(k + 1):
            t  = j / k
            mt = 1.0 - t
            # Cubic bezier: B(t) = mt^3·P0 + 3·mt^2·t·CP0 + 3·mt·t^2·CP1 + t^3·P1
            samples.append(p0  * (mt * mt * mt) +
                           cp0 * (3.0 * mt * mt * t) +
                           cp1 * (3.0 * mt * t  * t) +
                           p1  * (t  * t  * t))
        end_idx = len(samples)
        seg_ranges.append((start_idx, end_idx))

    dists = _surface_distance_grid(field, samples, normal)
    worst_i = int(dists.argmax())
    max_err = float(dists[worst_i])
    worst_pt = samples[worst_i]

    if return_seg_errors:
        seg_errors = [float(dists[s:e].max()) for s, e in seg_ranges]
        return max_err, worst_pt, seg_errors

    return max_err, worst_pt


def _sample_specific_bezier_segments(field, pts, h_in, h_out, seg_indices, is_closed,
                                     normal, n_per_seg=8, spans=None, n_src=0):
    """Sample only the specified segments of a piecewise-cubic Bezier curve.

    Same resolution rule as `_sample_bezier_sdf_error`, and it has to be the same
    one: this is what decimation and respacing judge a candidate by, and a
    candidate measured more loosely than the curve it replaces gets accepted for
    being measured, not for being better.
    """
    n = len(pts)
    samples = []
    seg_ranges = []
    for seg in seg_indices:
        i0 = seg
        i1 = (seg + 1) % n
        p0, p1 = pts[i0], pts[i1]
        cp0, cp1 = h_out[i0], h_in[i1]

        k = (_span_sample_count(spans[seg], n_src, is_closed)
             if (spans and n_src and seg < len(spans)) else n_per_seg)
        start_idx = len(samples)
        for j in range(k + 1):
            t  = j / k
            mt = 1.0 - t
            samples.append(p0  * (mt * mt * mt) +
                           cp0 * (3.0 * mt * mt * t) +
                           cp1 * (3.0 * mt * t  * t) +
                           p1  * (t  * t  * t))
        end_idx = len(samples)
        seg_ranges.append((start_idx, end_idx))

    dists = _surface_distance_grid(field, samples, normal)
    return {seg: float(dists[s:e].max()) for seg, (s, e) in zip(seg_indices, seg_ranges)}


def _span_midpoint_index(source_xyz, i0, i1, n_src, is_closed, used):
    """The unused source vertex nearest the arc-length midpoint of the span i0..i1.

    Where a failing segment gets divided. Halving it is deliberate, and it beat every
    error-guided alternative that was measured against it: splitting at the segment's
    worst sample, at its error argmax, and at its error centroid all cost a sphere at
    0.01 mm between 40 and 44 control points where halving costs 12. Off-centre
    splitting is self-amplifying -- an uneven pair of chords makes the next error
    profile more lopsided, which splits more unevenly again -- while halving is
    self-correcting under the same feedback. The error-guided rules bought about 10%
    fewer points on noisy contours and each needed a tuned constant to do it.

    Splitting at the *midpoint* rather than at the worst sample is also what stops
    insertions duplicating a point: the loop used to insert the walked vertex
    physically nearest the worst error sample, which on a tight curve sits a fraction
    of a millimetre from a control point already in the set -- measured, six
    consecutive insertions with the error frozen at 0.070632 mm.

    Returns None when the span holds no interior vertex left to take.
    """
    import numpy as np


    if is_closed:
        span_len = (i1 - i0) % n_src
        span = [(i0 + k) % n_src for k in range(span_len + 1)]
    else:
        span = list(range(i0, i1 + 1))
    if len(span) < 3:
        return None

    pts = source_xyz[span]
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate(([0.0], np.cumsum(seg)))
    if cum[-1] <= 1e-12:
        return None

    # Interior only -- the endpoints are the segment's own control points.
    order = np.argsort(np.abs(cum - 0.5 * cum[-1]))
    for k in order:
        k = int(k)
        if k == 0 or k == len(span) - 1:
            continue
        idx = span[k]
        if idx not in used:
            return idx
    return None


def _decimate_points(field, source, source_xyz, indices, is_closed, tolerance, normal, angle_tolerance_deg=180.0, known_corners=None, cache=None, arc_data=None):
    """Greedily remove redundant control points after refinement convergence.

    Points are candidates for removal as long as they are not protected (e.g. sharp
    corners). Removal is accepted only when the localized SDF error after removal
    stays strictly within tolerance.

    Localizes error sampling to the segments altered by candidate point removal,
    avoiding re-sampling the entire curve on every trial removal.

    Works in index space rather than on a bare point list because the handle
    lengths are fitted to the dense polyline (`_solve_bezier_handles`), and a
    removal changes which stretch of that polyline each surviving segment has to
    cover. Measuring a trial removal against chord/3 handles would reject removals
    the solved curve absorbs easily, which is the whole point of decimating.

    Args:
        source:     list[FreeCAD.Vector] -- the dense polyline.
        source_xyz: the same polyline as an (N, 3) array, for the handle solve.
        indices:    list[int] -- positions in `source` of the current control points.

    Returns:
        list[int] -- the surviving indices, ascending.
    """
    idx = sorted(indices)
    pts = [source[i] for i in idx]
    if len(pts) <= 3:
        return idx

    htypes = _detect_handle_types(pts, is_closed, known_corners=known_corners)
    spans, keys = _segment_spans(idx, is_closed, htypes)
    h_in, h_out = _solve_bezier_handles(pts, is_closed, htypes, source_xyz, spans,
                                        seg_keys=keys, cache=cache, arc_data=arc_data)
    err, _, seg_errors = _sample_bezier_sdf_error(field, pts, h_in, h_out, is_closed,
                                                  normal, return_seg_errors=True,
                                                  spans=spans, n_src=len(source_xyz))

    while len(pts) > 3:
        removed = False
        n = len(pts)
        check_range = range(n) if is_closed else range(1, n - 1)
        for i in check_range:
            if len(pts) <= 3:
                break
            n = len(pts)

            # Prevent decimating a known corner
            if known_corners:
                p = pts[i]
                is_known = False
                for kc in known_corners:
                    if (p - kc).Length < 1e-3:
                        is_known = True
                        break
                if is_known:
                    continue

            prev_i = (i - 1) % n
            next_i = (i + 1) % n
            v_in  = pts[i] - pts[prev_i]
            v_out = pts[next_i] - pts[i]
            lin, lout = v_in.Length, v_out.Length
            if lin > 1e-10 and lout > 1e-10:
                cos_a = max(-1.0, min(1.0, (v_in * (1.0 / lin)).dot(v_out * (1.0 / lout))))
                angle_i = math.degrees(math.acos(cos_a))
            else:
                angle_i = 0.0

            if angle_i > angle_tolerance_deg:
                continue

            test_idx = [idx[j] for j in range(n) if j != i]
            test_pts = [source[j] for j in test_idx]
            m = len(test_pts)
            htypes_t = _detect_handle_types(test_pts, is_closed, known_corners=known_corners)
            spans_t, keys_t = _segment_spans(test_idx, is_closed, htypes_t)
            h_in_t, h_out_t = _solve_bezier_handles(
                test_pts, is_closed, htypes_t, source_xyz, spans_t,
                seg_keys=keys_t, cache=cache, arc_data=arc_data)

            if m < 8:
                test_err, _, new_seg_errors = _sample_bezier_sdf_error(
                    field, test_pts, h_in_t, h_out_t, is_closed, normal,
                    return_seg_errors=True, spans=spans_t, n_src=len(source_xyz)
                )
                if test_err <= tolerance:
                    pts, idx = test_pts, test_idx
                    seg_errors = new_seg_errors
                    removed = True
                    break
            else:
                # Identify affected segments in test_pts
                idx_in_test = (i - 1) % m if is_closed else max(0, i - 1)
                num_test_segs = m if is_closed else (m - 1)
                affected_set = set()
                for offset in (-2, -1, 0, 1, 2):
                    s_idx = (idx_in_test + offset) % num_test_segs if is_closed else (idx_in_test + offset)
                    if 0 <= s_idx < num_test_segs:
                        affected_set.add(s_idx)

                # Check maximum error among unaffected segments from existing cached seg_errors
                unaffected_err = 0.0
                num_old_segs = len(seg_errors)
                for s_test in range(num_test_segs):
                    if s_test in affected_set:
                        continue
                    s_old = s_test if s_test < i else ((s_test + 1) % num_old_segs if is_closed else (s_test + 1))
                    if s_old < num_old_segs:
                        unaffected_err = max(unaffected_err, seg_errors[s_old])

                if unaffected_err > tolerance:
                    continue

                # Sample only affected segments
                affected_list = sorted(affected_set)
                eval_segs = _sample_specific_bezier_segments(
                    field, test_pts, h_in_t, h_out_t, affected_list, is_closed, normal,
                    spans=spans_t, n_src=len(source_xyz)
                )
                max_affected = max(eval_segs.values()) if eval_segs else 0.0

                if max(unaffected_err, max_affected) <= tolerance:
                    pts, idx = test_pts, test_idx
                    new_seg_err_list = []
                    for s_test in range(num_test_segs):
                        if s_test in eval_segs:
                            new_seg_err_list.append(eval_segs[s_test])
                        else:
                            s_old = s_test if s_test < i else ((s_test + 1) % num_old_segs if is_closed else (s_test + 1))
                            new_seg_err_list.append(seg_errors[s_old] if s_old < num_old_segs else 0.0)
                    seg_errors = new_seg_err_list
                    removed = True
                    break

        if not removed:
            break

    return idx


def _plane_basis(normal):
    """Two orthonormal in-plane axes for `normal`, as (3,) arrays."""
    import numpy as np

    nrm = np.array([normal.x, normal.y, normal.z], dtype=np.float64)
    nl = float(np.sqrt(nrm.dot(nrm)))
    nrm = nrm / nl if nl > DEGENERATE_AXIS_EPS else np.array([0.0, 0.0, 1.0])
    seed = np.array([1.0, 0.0, 0.0]) if abs(nrm[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = seed - nrm * float(seed.dot(nrm))
    u = u / float(np.sqrt(u.dot(u)))
    return u, np.cross(nrm, u), nrm


def _sharpen_crease_vertices(field, points, corner_indices, is_closed, normal,
                             point_spacing, window=16, skip=1, max_move=4.0,
                             on_surface=0.25):
    """Move each detected crease vertex onto the apex its two flanks actually meet at.

    Marching squares puts vertices where the contour crosses a cell edge, and a
    crease apex is almost never on a cell edge -- it is somewhere inside the cell,
    and no vertex is generated for it. The nearest walked vertex is up to a full
    grid pitch away along one of the two flanks, and that gap is the entire error
    of a fitted box: measured on a box rotated 20 degrees, the achieved deviation
    was 0.0216 / 0.0150 / 0.0047 mm at pitches of 0.05 / 0.02 / 0.005 and the
    distance from the true corner to the nearest control point was 0.0247 / 0.0157
    / 0.0047. Same number, three times over. The fit is not rounding the corner;
    it is placing it correctly on a polyline whose corner is in the wrong place.

    So put it in the right place. Each flank is a straight run of vertices that
    *are* exact, being ordinary crossings of an ordinary edge, and two lines fitted
    through them cross at the apex to well under a pitch. Total least squares, not
    a two-point chord: a chord through the first and last vertex of a flank is as
    good as its two endpoints, and averaging the whole run is what buys the
    sub-pitch accuracy.

    Nothing here fires on a smooth contour -- `_detect_contour_features` reports
    creases, and on smooth fields it now reports none. Guards throughout: the
    nearest vertex either side is skipped as the one whose cell may contain the
    apex, the window never reaches into a neighbouring crease, flanks that meet at
    a shallow angle are left alone because their intersection is ill-conditioned,
    and a correction longer than a few pitches is refused rather than trusted.

    The last guard is the one that matters, and it is not a guard at all but the
    premise being checked: the apex of a real crease is ON the surface, so if the
    computed apex is not, the two flanks were not two straight lines meeting at a
    corner and the intersection means nothing. Without that check a false crease
    on a noisy field -- where the "flanks" are two arcs of the same lump -- had its
    control point moved up to the `max_move` limit clean off the contour, and no
    amount of subdivision afterwards could recover, because it is the control point
    itself that is in the wrong place. Measured on a Perlin sphere at amplitude 3:
    the fitted curve sat 0.265 mm off a 0.05 mm target and stayed there through
    three refinement passes. All the candidates are checked in one batched pass,
    so the check costs one field evaluation per crease.

    Returns:
        (points, moved) -- the polyline with sharpened creases (a copy, only if any
        moved) and how many were.
    """
    import numpy as np

    n = len(points)
    if not corner_indices or n < 8:
        return points, 0
    u, v, _nrm = _plane_basis(normal)
    xyz = _as_xyz(points)
    uv = np.stack((xyz.dot(u), xyz.dot(v)), axis=1)
    ordered = sorted(set(int(i) % n for i in corner_indices))
    limit = max_move * max(point_spacing, 1e-9)

    def flank(start, step, room):
        """Up to `window` vertices walking away from a crease, as (m, 2)."""
        span = min(window, max(0, room))
        if span < 3:
            return None
        rows = []
        for t in range(span):
            k = start + step * t
            if is_closed:
                k %= n
            elif k < 0 or k >= n:
                break
            rows.append(uv[k])
        return np.array(rows) if len(rows) >= 3 else None

    def fit_line(q):
        """(point, unit direction) of the total-least-squares line through `q`."""
        c = q.mean(axis=0)
        d = q - c
        # The dominant singular vector is the direction; SVD on an (m, 2) block.
        _uu, _sv, vt = np.linalg.svd(d, full_matrices=False)
        return c, vt[0]

    proposed = []          # (index, shifted uv position)
    for pos, i in enumerate(ordered):
        # Half the way to the neighbouring crease, either side. Half, so two
        # adjacent creases can never read each other's flank.
        if len(ordered) == 1:
            back_room = fwd_room = n // 3
        elif is_closed:
            prev_c, next_c = ordered[pos - 1], ordered[(pos + 1) % len(ordered)]
            back_room = ((i - prev_c) % n) // 2 - skip
            fwd_room = ((next_c - i) % n) // 2 - skip
        else:
            # No wrap on an open contour: the outermost creases are bounded by
            # the ends of the polyline instead of by a neighbour.
            prev_c = ordered[pos - 1] if pos > 0 else None
            next_c = ordered[pos + 1] if pos + 1 < len(ordered) else None
            back_room = ((i - prev_c) // 2 if prev_c is not None else i) - skip
            fwd_room = ((next_c - i) // 2 if next_c is not None else n - 1 - i) - skip
        a = flank(i - skip, -1, back_room)
        b = flank(i + skip, +1, fwd_room)
        if a is None or b is None:
            continue
        ca, da = fit_line(a)
        cb, db = fit_line(b)
        cross = float(da[0] * db[1] - da[1] * db[0])
        if abs(cross) < 0.05:          # flanks within ~3 degrees of parallel
            continue
        diff = cb - ca
        t = (diff[0] * db[1] - diff[1] * db[0]) / cross
        apex = ca + da * t
        shift = apex - uv[i]
        if float(np.hypot(shift[0], shift[1])) > limit:
            continue
        proposed.append((i, xyz[i] + u * float(shift[0]) + v * float(shift[1])))

    if not proposed:
        return points, 0

    # An apex that is not on the zero isoline was never a crease apex.
    keep = [True] * len(proposed)
    if field is not None:
        try:
            dists = _surface_distance_grid(
                field, [_as_vector(q) for _i, q in proposed], normal)
            bound = on_surface * max(point_spacing, 1e-9)
            keep = [bool(d <= bound) for d in dists]
        except Exception as exc:
            fld_logger.debug(f"_sharpen_crease_vertices: apex check failed, "
                            f"leaving creases where they are: {exc}")
            keep = [False] * len(proposed)

    if not any(keep):
        return points, 0
    out = list(points)
    moved = 0
    for ok, (i, q) in zip(keep, proposed):
        if ok:
            out[i] = _as_vector(q)
            moved += 1
    return out, moved


def _max_segment_error(field, pts, h_in, h_out, is_closed, normal, seg_keys,
                       err_cache, limit=None, spans=None, n_src=0):
    """Worst per-segment deviation, re-measuring only the segments never seen before.

    `seg_keys[j]` names everything segment j's shape depends on -- its two
    endpoints, the two control points that set the tangents there, and the two
    handle types (`_segment_spans`). So a segment that turns up again under a
    different index is the same curve, and the trial that produced it does not
    have to pay for it again. That is what makes trying several candidate layouts
    per span affordable: the layouts differ in a handful of segments and agree on
    all the rest.

    `limit` short-circuits: once a cached segment already exceeds it the candidate
    is rejected whatever the unmeasured segments do.
    """
    n = len(pts)
    segs = n if is_closed else (n - 1)
    if segs < 1:
        return 0.0
    worst = 0.0
    unknown = []
    for j in range(segs):
        key = seg_keys[j] if (seg_keys and j < len(seg_keys)) else None
        hit = err_cache.get(key) if (err_cache is not None and key is not None) else None
        if hit is None:
            unknown.append(j)
        else:
            worst = max(worst, hit)
    if limit is not None and worst > limit:
        return worst
    if unknown:
        measured = _sample_specific_bezier_segments(field, pts, h_in, h_out, unknown,
                                                   is_closed, normal, spans=spans,
                                                   n_src=n_src)
        for j, e in measured.items():
            if err_cache is not None and seg_keys and j < len(seg_keys):
                err_cache[seg_keys[j]] = e
            worst = max(worst, e)
    return worst


def _redistribute_points(field, source, source_xyz, indices, is_closed, tolerance,
                         normal, known_corners=None, cache=None, arc_data=None,
                         err_cache=None):
    """Re-space the free control points inside each fixed span, at the fewest that hold.

    `_decimate_points` can only delete, and deleting is the one move that cannot
    reach the answer here. Take a circle of radius 20 fitted with six evenly spaced
    points: five evenly spaced points sit 0.0096 mm off it and four sit 0.0112 mm
    off, both comfortably inside a 0.05 mm tolerance -- but *dropping* one of the
    six leaves a 120-degree gap next to two 60-degree ones and the error jumps to
    0.0313 mm, which fails. So every single removal is rejected, decimation reports
    "no further removals possible", and a shape whose exact answer is four control
    points ships with six. The trap is that the rejected configuration really is
    worse; it is only the *count* that was affordable, never that particular
    arrangement of it.

    So this asks the other question: at t points, evenly spaced by arc length,
    does the span hold? Corners are what a span is delimited by and they never
    move -- an even respacing that walked a control point off a crease would round
    the crease. A closed contour with no corners at all has one arbitrary point
    pinned, because the spacing of the rest is the whole question and where the
    seam falls is not.

    The count is found by bisection rather than by counting down, so a span with
    forty free points costs six trials instead of forty; if bisection finds
    nothing it still tries one point fewer, which is the case where the error is
    not quite monotone in the count.

    Returns:
        list[int] -- indices into `source`, ascending. The input list unchanged if
        no span could be thinned.
    """
    import numpy as np

    idx = sorted(indices)
    m = len(idx)
    min_total = 4 if is_closed else 3
    if m <= min_total:
        return idx
    n_src = len(source)
    ext, arc = arc_data if arc_data is not None else _polyline_arc(source_xyz, is_closed)
    total = float(arc[-1])
    if not (total > 1e-9):
        return idx
    if err_cache is None:
        err_cache = {}

    fixed = set()
    if known_corners:
        for j, i in enumerate(idx):
            p = source[i]
            for kc in known_corners:
                if (p - kc).Length < 1e-3:
                    fixed.add(j)
                    break
    if not is_closed:
        fixed.add(0)
        fixed.add(m - 1)
    elif not fixed:
        fixed.add(0)
    fixed_list = sorted(fixed)
    n_fixed = len(fixed_list)

    # Spans are named by the DENSE index of the two points that bound them, not by
    # their position in the control list: accepting one span's respacing renumbers
    # every position after it, and a fixed point's dense index is the one label
    # that survives that.
    fixed_src = [idx[j] for j in fixed_list]
    if is_closed:
        bounds = [(fixed_src[a], fixed_src[(a + 1) % n_fixed]) for a in range(n_fixed)]
    else:
        bounds = [(fixed_src[a], fixed_src[a + 1]) for a in range(n_fixed - 1)]

    changed = False
    for a0, a1 in bounds:
        m = len(idx)
        try:
            f0, f1 = idx.index(a0), idx.index(a1)
        except ValueError:
            continue
        k = (m - 1) if (is_closed and n_fixed == 1) else (
            (f1 - f0 - 1) % m if is_closed else (f1 - f0 - 1))
        if k < 1:
            continue
        interior = set((f0 + 1 + i) % m for i in range(k))
        keep = [idx[j] for j in range(m) if j not in interior]
        s0 = float(arc[idx[f0]])
        span = float(arc[idx[f1]]) - s0
        if is_closed:
            span = span % total
            if span <= 1e-9:
                span = total
        if span <= 1e-9:
            continue

        def candidate(t):
            """The index list with this span's interior replaced by t even points."""
            fresh = []
            for i in range(t):
                q = s0 + (i + 1) * span / (t + 1.0)
                q = (q % total) if is_closed else min(max(q, 0.0), total)
                fresh.append(int(np.searchsorted(arc, q)) % n_src)
            cand = sorted(set(keep) | set(fresh))
            return cand if len(cand) == len(keep) + t else None

        def holds(t):
            cand = candidate(t)
            if cand is None or len(cand) < min_total:
                return None
            cpts = [source[i] for i in cand]
            chts = _detect_handle_types(cpts, is_closed, known_corners=known_corners)
            cspans, ckeys = _segment_spans(cand, is_closed, chts)
            ch_in, ch_out = _solve_bezier_handles(cpts, is_closed, chts, source_xyz,
                                                  cspans, seg_keys=ckeys, cache=cache,
                                                  arc_data=(ext, arc))
            err = _max_segment_error(field, cpts, ch_in, ch_out, is_closed, normal,
                                     ckeys, err_cache, limit=tolerance,
                                     spans=cspans, n_src=len(source_xyz))
            return cand if err <= tolerance else None

        lo = max(0, min_total - len(keep))
        hi = k - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            got = holds(mid)
            if got is not None:
                best, hi = got, mid - 1
            else:
                lo = mid + 1
        if best is None and k - 1 >= max(0, min_total - len(keep)):
            best = holds(k - 1)
        if best is not None:
            fld_logger.debug(f"_redistribute_points: span of {k} free points respaced "
                            f"to {len(best) - len(keep)}.")
            idx = best
            changed = True

    return idx if changed else sorted(indices)


def _detect_contour_features(walked_points, is_closed, normal, point_spacing,
                             corner_threshold_deg=30.0,
                             min_inflection_bend_deg=3.0,
                             min_extremum_turn_deg=4.0):
    """Detect sharp corners, inflection points, and curvature extrema along the walked contour.

    Uses in-plane signed curvature kappa(s) = d(theta)/ds to identify:
      1. Sharp corners: turns concentrated on one or two walk steps, which is what
         separates a crease from a tight smooth bend (marked HANDLE_VECTOR).
      2. Inflection points: zero-crossings of kappa where the curve flips between convex and concave.
      3. Curvature extrema: local peaks of |kappa| (crests and troughs of bends).

    Returns:
        tuple(list[int], list[int], list[int]) — (corner_indices, inflection_indices, extremum_indices).
    """
    n = len(walked_points)
    if n < 8:
        return [], [], []

    # Window k spans ~3-5 walk steps to smooth out discrete stepping jitter
    k = max(2, min(6, int(round(0.35 / max(point_spacing, 1e-4)))))

    angles = [0.0] * n
    step_angles = [0.0] * n
    signed_angles = [0.0] * n
    curvatures = [0.0] * n

    norm = FreeCAD.Vector(normal).normalize()

    # The turn across a single walk step, for the crease test below. A tangent
    # discontinuity puts the whole of its window's turn on one or two vertices;
    # a smooth bend spreads the turn over all 2k steps in the window.
    for i in range(n):
        prev_idx = (i - 1) % n if is_closed else max(0, i - 1)
        next_idx = (i + 1) % n if is_closed else min(n - 1, i + 1)
        v_in = walked_points[i] - walked_points[prev_idx]
        v_out = walked_points[next_idx] - walked_points[i]
        lin, lout = v_in.Length, v_out.Length
        if lin > 1e-6 and lout > 1e-6:
            t_in = v_in * (1.0 / lin)
            t_out = v_out * (1.0 / lout)
            dot = max(-1.0, min(1.0, t_in.dot(t_out)))
            cross_n = (t_in.cross(t_out)).dot(norm)
            step_angles[i] = abs(math.degrees(math.atan2(cross_n, dot)))

    def sharpest_pair(i):
        """Turn over the sharper of the two adjacent step-pairs centred near i."""
        if not is_closed and (i < 1 or i > n - 2):
            return step_angles[i]
        lo = step_angles[(i - 1) % n] + step_angles[i]
        hi = step_angles[i] + step_angles[(i + 1) % n]
        return max(lo, hi)

    for i in range(n):
        if not is_closed and (i < k or i >= n - k):
            continue
        prev_idx = (i - k) % n if is_closed else max(0, i - k)
        next_idx = (i + k) % n if is_closed else min(n - 1, i + k)
        v_in = walked_points[i] - walked_points[prev_idx]
        v_out = walked_points[next_idx] - walked_points[i]
        lin, lout = v_in.Length, v_out.Length
        if lin > 1e-6 and lout > 1e-6:
            t_in = v_in * (1.0 / lin)
            t_out = v_out * (1.0 / lout)
            dot = max(-1.0, min(1.0, t_in.dot(t_out)))
            cross_n = (t_in.cross(t_out)).dot(norm)
            d_theta = math.atan2(cross_n, dot)
            ds = (lin + lout) * 0.5
            signed_angles[i] = math.degrees(d_theta)
            angles[i] = abs(signed_angles[i])
            curvatures[i] = d_theta / max(ds, 1e-6)

    # 1D smoothing of curvature and angles using triangular kernel over radius k
    smooth_curv = [0.0] * n
    smooth_ang = [0.0] * n

    for i in range(n):
        if not is_closed and (i < k or i >= n - k):
            smooth_curv[i] = curvatures[i]
            smooth_ang[i] = signed_angles[i]
            continue
        c_acc = 0.0
        a_acc = 0.0
        w_acc = 0.0
        for m in range(-k, k + 1):
            idx = (i + m) % n if is_closed else i + m
            if 0 <= idx < n:
                w = float(k + 1 - abs(m))
                c_acc += curvatures[idx] * w
                a_acc += signed_angles[idx] * w
                w_acc += w
        smooth_curv[i] = c_acc / max(w_acc, 1e-6)
        smooth_ang[i] = a_acc / max(w_acc, 1e-6)

    # 1. Detect sharp corners: local maxima in angle exceeding corner_threshold_deg
    #
    # The angle threshold alone cannot do this job. `angles[i]` is the turn over a
    # ~0.7 mm window, so 30 degrees of it means a local radius near 1.3 mm -- and a
    # displaced or noisy field has no shortage of smooth bends that tight. Reading
    # those as creases is not cosmetic: a corner becomes HANDLE_VECTOR, which aims
    # the two handles at their neighbours and puts a real tangent break in the
    # machined curve. Measured on a 20 mm sphere under Perlin displacement, this
    # test alone reported 36 corners at amplitude 2 and 206 at amplitude 3, on
    # contours whose level set is smooth everywhere.
    #
    # The discriminator is where in the window the turn happens, not how much of it
    # there is. A crease is a tangent break, so its turn falls on the one or two
    # walk steps at the break and the ratio below is ~1; a smooth bend spreads the
    # same total over all 2k steps, so the ratio is ~2/2k. Measured over the
    # candidate peaks at 0.05 mm pitch: 1.000 to 1.110 on every crease of eight
    # box, rotated-box, off-grid-box, box-minus-sphere, cylinder-minus-box and
    # displaced-box sections, against 0.24 to 0.65 on a Perlin sphere at amplitude
    # 2. A tight bend that fails this stays HANDLE_AUTO, where the handle solver
    # fits it and the curve stays G1.
    #
    # The test is deliberately alone. Pairing it with a second measure -- the
    # crease's turn against the curvature its own flanks carry -- was tried and
    # rejected: it removes the last few false positives on extreme noise but also
    # loses a real corner on a Perlin-displaced box, and dropping a genuine crease
    # rounds an edge the part is supposed to have.
    corner_indices = []
    suppress_r = k * 2
    for i in range(n):
        if angles[i] < corner_threshold_deg:
            continue
        if sharpest_pair(i) < _CREASE_STEP_SHARE * angles[i]:
            continue
        is_max = True
        for r in range(-suppress_r, suppress_r + 1):
            idx = (i + r) % n if is_closed else i + r
            if 0 <= idx < n:
                if angles[idx] > angles[i] or (angles[idx] == angles[i] and idx < i):
                    is_max = False
                    break
        if is_max:
            corner_indices.append(i)

    corner_set = set(corner_indices)

    # 2. Detect inflection points: zero-crossings of smooth_curv between significant lobes
    inflection_indices = []
    lobe_steps = max(8, int(round(3.0 / max(point_spacing, 1e-3))))
    for i in range(n):
        if i in corner_set:
            continue
        prev_i = (i - 1) % n if is_closed else max(0, i - 1)
        c_prev = smooth_curv[prev_i]
        c_curr = smooth_curv[i]
        if (c_prev > 0 and c_curr <= 0) or (c_prev < 0 and c_curr >= 0) or (c_prev * c_curr < 0):
            # Check adjacent lobes: both preceding and succeeding regions must have curvature
            lobe_back_max = max(abs(smooth_ang[(i - b) % n if is_closed else max(0, i - b)]) for b in range(1, lobe_steps + 1))
            lobe_fwd_max = max(abs(smooth_ang[(i + f) % n if is_closed else min(n - 1, i + f)]) for f in range(1, lobe_steps + 1))
            lobe_back_bend = sum(abs(smooth_ang[(i - b) % n if is_closed else max(0, i - b)]) for b in range(1, lobe_steps + 1))
            lobe_fwd_bend = sum(abs(smooth_ang[(i + f) % n if is_closed else min(n - 1, i + f)]) for f in range(1, lobe_steps + 1))

            if (lobe_back_max >= 0.5 or lobe_back_bend >= 1.5) and (lobe_fwd_max >= 0.5 or lobe_fwd_bend >= 1.5):
                inflection_indices.append(i)

    # Non-maximum suppression / deduplication for inflection points within 2*k
    filtered_inflections = []
    for idx in inflection_indices:
        too_close = False
        for existing in filtered_inflections:
            diff = min(abs(idx - existing), n - abs(idx - existing)) if is_closed else abs(idx - existing)
            if diff < k * 2:
                too_close = True
                break
        if not too_close:
            filtered_inflections.append(idx)

    # 3. Detect curvature extrema: local peaks of |smooth_curv| with significant prominence
    extremum_indices = []
    avoid_set = set(corner_indices) | set(filtered_inflections)
    for i in range(n):
        if i in avoid_set:
            continue
        c_mag = abs(smooth_curv[i])
        # Check if local peak
        is_peak = True
        for r in range(-k, k + 1):
            if r == 0:
                continue
            idx = (i + r) % n if is_closed else i + r
            if 0 <= idx < n:
                if abs(smooth_curv[idx]) > c_mag or (abs(smooth_curv[idx]) == c_mag and idx < i):
                    is_peak = False
                    break
        if is_peak:
            # Check prominence: peak curvature must rise above local baseline
            c_min = min(abs(smooth_curv[(i + r) % n if is_closed else max(0, min(n - 1, i + r))]) for r in range(-k * 3, k * 3 + 1))
            prominence = c_mag - c_min
            if prominence >= 0.005:
                extremum_indices.append(i)

    # Non-maximum suppression / deduplication for extrema within 2*k
    filtered_extrema = []
    for idx in extremum_indices:
        too_close = False
        for existing in filtered_extrema:
            diff = min(abs(idx - existing), n - abs(idx - existing)) if is_closed else abs(idx - existing)
            if diff < k * 2:
                too_close = True
                break
        if not too_close:
            filtered_extrema.append(idx)

    return corner_indices, filtered_inflections, filtered_extrema


def _corner_turn_angles(points, is_closed, indices, k=4):
    """Turn angle in degrees at each of `indices`, over a +/-k window.
    Lets a caller rank corners by sharpness."""
    n = len(points)
    out = []
    for i in indices:
        prev_idx = (i - k) % n if is_closed else max(0, i - k)
        next_idx = (i + k) % n if is_closed else min(n - 1, i + k)
        v_in = points[i] - points[prev_idx]
        v_out = points[next_idx] - points[i]
        lin, lout = v_in.Length, v_out.Length
        if lin > 1e-6 and lout > 1e-6:
            cos_a = max(-1.0, min(1.0, (v_in * (1.0 / lin)).dot(v_out * (1.0 / lout))))
            out.append(math.degrees(math.acos(cos_a)))
        else:
            out.append(0.0)
    return out


def _detect_handle_types(fit_pts, is_closed, known_corners=None):
    """Classify each control point as HANDLE_AUTO or HANDLE_VECTOR.

    A point is HANDLE_VECTOR only where it coincides with one of `known_corners`,
    the creases `_detect_contour_features` found on the dense walked contour.

    Nothing is inferred from the fit points themselves, and that is the point. The
    chord-turn angle at fit-point spacing cannot separate a crease from a tight
    smooth bend -- at ~1.5 mm spacing a smooth bend of 1.5 mm radius turns ~57
    degrees, indistinguishable from a real corner -- and the local-maximum test
    this function used to apply does not rescue it, because on a noisy contour the
    tight bends are exactly the local maxima. Measured on a Perlin sphere, that
    rule marked 20 to 114 control points HANDLE_VECTOR on contours whose level set
    is smooth everywhere, and HANDLE_VECTOR is not cosmetic: it aims the two
    handles at their neighbours and leaves a real tangent break in the machined
    curve, up to 149 degrees.

    Only the dense walk has the resolution to make that call, and it makes it by
    asking where in the window the turning happens rather than how much of it there
    is. Every corner therefore arrives here already decided.

    Args:
        fit_pts:       list[FreeCAD.Vector] -- interpolation control points.
        is_closed:     bool.
        known_corners: list[FreeCAD.Vector] or None -- crease locations from the
                       dense walk.

    Returns:
        list[int] -- HANDLE_AUTO or HANDLE_VECTOR, one value per control point.
    """
    n = len(fit_pts)
    types = [HANDLE_AUTO] * n
    if n < 3 or not known_corners:
        return types

    for i in range(n):
        if not is_closed and (i == 0 or i == n - 1):
            continue
        p = fit_pts[i]
        for kc in known_corners:
            if (p - kc).Length < 1e-3:
                types[i] = HANDLE_VECTOR
                break

    return types


def _refine_to_min_points(field, walked_points, is_closed, normal, tolerance=DEFAULT_MODEL_TOLERANCE_MM, max_iters=300, max_control_points=200, angle_tolerance_deg=180.0):
    """Find the minimum-point BSpline that approximates the SDF contour within tolerance.

    Algorithm:
        1. Pre-detect contour features (sharp corners, inflection points, curvature extrema).
        2. Seed initial control points at feature locations.
        3. Detect handle types (Auto vs Vector) for the current subset.
        4. Compute the actual piecewise-cubic bezier handles.
        5. Sample the bezier at N intermediate points per segment.
        6. Evaluate |field.evaluate(sample)| — in-plane distance from the true surface.
        7. If max error < tolerance, done.
        8. Otherwise, insert the walked point physically nearest the worst sample.
        9. Repeat from step 3.
        10. Decimate redundant intermediate points while strictly maintaining tolerance.

    Using the actual bezier (step 3–4) rather than a smooth interpolating BSpline
    is critical: Vector handles at corners are nearly linear and stay on the SDF
    surface, so the refinement converges at far fewer control points.

    Args:
        field:              SdfField — ground truth for error measurement.
        walked_points:      list[FreeCAD.Vector] — dense contour from
                            `sdf_contour.slice_polygons`.
        is_closed:          bool — whether the contour is closed.
        normal:             FreeCAD.Vector — slice plane normal; error is measured
                            in-plane, see `_surface_distance_grid`.
        tolerance:          float — maximum allowed deviation from the contour in mm.
        max_iters:          int — safety cap on refinement iterations.
        max_control_points: int — hard cap on control point count.

    Returns:
        SlicingBSplineCurveWrapper or None.
    """
    n_walked = len(walked_points)
    if n_walked < 3:
        fld_logger.warn(f"_refine_to_min_points: Walked points count is less than 3 ({n_walked})")
        return None

    # Remove duplicate endpoint for closed contours
    fit_source = list(walked_points)
    if is_closed and n_walked > 3:
        if (fit_source[0] - fit_source[-1]).Length < tolerance:
            fit_source = fit_source[:-1]

    n_src = len(fit_source)
    if n_src < 3:
        fld_logger.warn(f"_refine_to_min_points: Source points count after cleanup is less than 3 ({n_src})")
        return None

    # Point spacing, for the feature-detection windows below. Measure it off the
    # polyline in hand rather than re-deriving it from the bounding box: the
    # windows are specified as an arc length (0.35 mm of contour, 3.0 mm of lobe)
    # and converting that to a point count needs the spacing the caller actually
    # supplied. The old `diag / 400` guess was 4x too coarse once the contour
    # started arriving at the tolerance pitch, which silently shrank every window.
    spans = [(fit_source[i + 1] - fit_source[i]).Length for i in range(n_src - 1)]
    if is_closed:
        spans.append((fit_source[0] - fit_source[-1]).Length)
    positive = [d for d in spans if d > 1e-9]
    point_spacing = (sum(positive) / len(positive)) if positive else max(tolerance, 1e-4)

    # Pre-detect geometric features: corners, inflection points, curvature extrema
    corner_indices, inflection_indices, extremum_indices = _detect_contour_features(
        fit_source, is_closed, normal, point_spacing
    )

    # Put every crease vertex on its apex before anything is fitted to it. The
    # indices are untouched, so seeds, spans and `known_corners` below all still
    # mean what they meant; only the position of a handful of vertices improves.
    fit_source, n_sharpened = _sharpen_crease_vertices(
        field, fit_source, corner_indices, is_closed, normal, point_spacing)
    if n_sharpened:
        fld_logger.debug(f"_refine_to_min_points: {n_sharpened} crease vertex/vertices "
                        f"snapped to the intersection of their flanks.")

    # Build initial seed set from detected features:
    # If the total feature count exceeds max_control_points, prioritize the most significant features.
    ranked_seeds = []
    if corner_indices:
        c_angles = _corner_turn_angles(fit_source, is_closed, corner_indices)
        corners = sorted(zip(corner_indices, c_angles), key=lambda item: -item[1])
        # A deformed contour has no shortage of 30-degree turns: on a Perlin sphere
        # `_detect_contour_features` reported 31 and 38 corners, which seeded 39 and 46
        # control points before a single error sample and left a budget of 4. Worse,
        # HANDLE_VECTOR at a turn that is not really a crease sends the fit off the
        # contour entirely -- measured 12.6 mm at iteration 0 and 24.3 mm at iteration
        # 1 on a 20 mm sphere, before subdivision dragged it back to 0.53 mm.
        #
        # Keep the sharpest quarter-budget of them and let error-driven splitting
        # recover anything real that was dropped. Splitting halves the distance to a
        # crease every pass, so a genuine corner past the limit costs points rather
        # than accuracy; a spurious one costs nothing at all.
        corner_limit = max(4, max_control_points // 4)
        if len(corners) > corner_limit:
            fld_logger.debug(f"_refine_to_min_points: {len(corners)} corner features "
                            f"capped at {corner_limit} by turn angle "
                            f"(sharpest {corners[0][1]:.1f} deg, "
                            f"dropped from {corners[corner_limit][1]:.1f} deg down).")
            corners = corners[:corner_limit]
        for idx, ang in corners:
            ranked_seeds.append((idx, 1000.0 + ang))

    if not is_closed:
        ranked_seeds.append((0, 2000.0))
        ranked_seeds.append((n_src - 1, 2000.0))

    # Inflection and curvature-extremum seeds are RANKED PROXIES FOR ERROR, and the
    # loop below measures the real thing. Seeding all of them spends the entire point
    # budget before a single error sample is taken: on a Perlin sphere
    # `_detect_contour_features` proposed 267 and 882 features, the clamp below kept
    # the top `max_control_points`, and the refinement then exited immediately with
    # `Reached max_control_points at iteration 0` -- zero insertions, the error-driven
    # half of the algorithm never ran on the one shape class that needs it, and the
    # curve came back 9x outside tolerance (0.899 mm against 0.100) while the log said
    # only "raise Max Points". At cap 200 the same contour seeded 200 and decimated
    # back to 71, so the fit was produced by seeding and decimation with the
    # refinement contributing nothing.
    #
    # Corners get the far more generous budget above, because a cubic cannot discover
    # a G0 crease by subdivision and a box slice is 4 points at 0.0000 mm only because
    # its four corners are seeded. These two tiers are subsampled to a handful that
    # bracket the contour, and the loop re-derives the rest from measured error.
    soft_seeds = [(idx, 500.0) for idx in inflection_indices]
    soft_seeds += [(idx, 100.0) for idx in extremum_indices]
    if len(soft_seeds) > _SOFT_SEED_LIMIT:
        soft_seeds.sort(key=lambda item: item[0])
        stride = len(soft_seeds) / float(_SOFT_SEED_LIMIT)
        soft_seeds = [soft_seeds[int(i * stride)] for i in range(_SOFT_SEED_LIMIT)]
        fld_logger.debug(f"_refine_to_min_points: {len(inflection_indices)} inflection + "
                        f"{len(extremum_indices)} extremum features subsampled to "
                        f"{_SOFT_SEED_LIMIT} seeds; error drives the rest.")
    ranked_seeds.extend(soft_seeds)

    # Deduplicate by index keeping highest rank
    best_rank = {}
    for idx, rank in ranked_seeds:
        if idx not in best_rank or rank > best_rank[idx]:
            best_rank[idx] = rank

    sorted_ranked = sorted(best_rank.items(), key=lambda item: -item[1])
    if len(sorted_ranked) > max_control_points:
        chosen_indices = set(idx for idx, _ in sorted_ranked[:max_control_points])
        fld_logger.warn(f"_refine_to_min_points: {len(sorted_ranked)} total features detected but "
                       f"max_control_points={max_control_points}; keeping the "
                       f"{max_control_points} highest-priority features.")
    else:
        chosen_indices = set(best_rank.keys())

    # Keep only corners that were actually selected
    known_corners = [fit_source[idx] for idx in corner_indices if idx in chosen_indices]

    subset_indices = sorted(chosen_indices)

    # Ensure minimum initial count (4 for closed, 2 for open)
    min_initial = 4 if is_closed else 2
    if len(subset_indices) < min_initial and len(subset_indices) < max_control_points:
        initial_count = max(min_initial, min(6, n_src, max_control_points))
        if is_closed:
            offset = n_src // (2 * initial_count)
            candidates = [(int(i * n_src / initial_count) + offset) % n_src
                          for i in range(initial_count)]
        else:
            candidates = [int(i * (n_src - 1) / (initial_count - 1)) for i in range(initial_count)]

        for cand in candidates:
            too_close = False
            for existing in subset_indices:
                diff = min(abs(cand - existing), n_src - abs(cand - existing)) if is_closed else abs(cand - existing)
                if diff < n_src // (2 * initial_count):
                    too_close = True
                    break
            if not too_close and len(subset_indices) < max_control_points:
                subset_indices.append(cand)

        used_set = set(subset_indices)
        for cand in candidates:
            if cand not in used_set and len(subset_indices) < initial_count and len(subset_indices) < max_control_points:
                subset_indices.append(cand)
                used_set.add(cand)

    # Subdivide any very large spans between initial seeds
    if len(subset_indices) >= 2 and len(subset_indices) < max_control_points:
        sorted_sub = sorted(subset_indices)
        max_gap = n_src // 3 if is_closed else n_src // 2
        for idx in range(len(sorted_sub)):
            if len(subset_indices) >= max_control_points:
                break
            curr_i = sorted_sub[idx]
            next_i = sorted_sub[(idx + 1) % len(sorted_sub)] if is_closed else (sorted_sub[idx + 1] if idx + 1 < len(sorted_sub) else None)
            if next_i is None:
                continue
            gap = (next_i - curr_i) % n_src if is_closed else (next_i - curr_i)
            if gap > max_gap:
                mid_pt = (curr_i + gap // 2) % n_src
                if mid_pt not in subset_indices and len(subset_indices) < max_control_points:
                    subset_indices.append(mid_pt)

    used_set = set(subset_indices)
    # One handle-solve cache for the whole fit. Keyed on the four indices and two
    # handle types a segment's solve depends on (`_segment_data`), so it is shared
    # safely across refinement iterations *and* decimation trials -- a trial
    # removal that is rejected still leaves behind segments the next trial reuses.
    solve_cache = {}
    fit_source_xyz = _as_xyz(fit_source)
    # Arc length of the dense polyline, once. Every handle solve below reads
    # tangents off it (`_contour_tangents`) and there are thousands of solves.
    fit_source_arc = _polyline_arc(fit_source_xyz, is_closed)
    fld_logger.debug(f"_refine_to_min_points: Starting refinement with {n_src} source points. "
                    f"Initial subset: {len(subset_indices)}, indices: {sorted(subset_indices)}")

    worst_error = 0.0

    def _build_result(sub_indices):
        """Build the final SlicingBSplineCurveWrapper; called only at exit points."""
        # Delete, then re-space, then delete again. The two moves fail on
        # different shapes and neither subsumes the other: removal is the only one
        # that can drop a point a span genuinely does not need, and respacing is
        # the only one that can reach an arrangement no single deletion leads to
        # (see `_redistribute_points`). Respacing a span also tends to free up a
        # neighbour, which is what the repeat is for; it settles in two rounds on
        # everything measured, and the guard is there for the shape that does not.
        final_idx = sorted(sub_indices)
        redist_errs = {}
        for _round in range(4):
            before = len(final_idx)
            final_idx = _decimate_points(field, fit_source, fit_source_xyz,
                                         final_idx, is_closed,
                                         tolerance, normal, angle_tolerance_deg,
                                         known_corners=known_corners, cache=solve_cache,
                                         arc_data=fit_source_arc)
            final_idx = _redistribute_points(field, fit_source, fit_source_xyz,
                                             final_idx, is_closed, tolerance, normal,
                                             known_corners=known_corners,
                                             cache=solve_cache,
                                             arc_data=fit_source_arc,
                                             err_cache=redist_errs)
            if len(final_idx) >= before:
                break
        pts = [fit_source[i] for i in final_idx]
        htypes = _detect_handle_types(pts, is_closed, known_corners=known_corners)
        fld_logger.info(f"_refine_to_min_points: After decimation: {len(pts)} pts")
        spans, keys = _segment_spans(final_idx, is_closed, htypes)
        h_in, h_out = _solve_bezier_handles(pts, is_closed, htypes, fit_source_xyz,
                                            spans, seg_keys=keys, cache=solve_cache,
                                            arc_data=fit_source_arc)
        # A solved smooth point is HANDLE_ALIGNED, not HANDLE_AUTO. Both render the
        # same collinear pair, but `curve_tool._apply_handle_type` *recomputes*
        # chord/3 for Auto and keeps the stored positions for Aligned -- so leaving
        # these Auto would let the editor silently throw the fit away the first
        # time a handle type was re-applied. Corners stay Vector: their length is
        # chord/3 on a straight span anyway, and Vector is what marks a Split point.
        htypes = [HANDLE_ALIGNED if h == HANDLE_AUTO else h for h in htypes]
        try:
            bs_raw = _bezier_chain_to_bspline(pts, h_in, h_out, is_closed)
        except Exception as exc:
            fld_logger.warn(f"_refine_to_min_points: Bezier->BSpline build failed: {exc}")
            return None
        res = SlicingBSplineCurveWrapper(bs_raw, pts, htypes, is_closed,
                                         handle_in=h_in, handle_out=h_out)
        res._achieved_deviation, _ = _sample_bezier_sdf_error(
            field, pts, h_in, h_out, is_closed, normal,
            spans=spans, n_src=len(fit_source_xyz))
        res._tolerance = float(tolerance)
        res._tolerance_met = bool(res._achieved_deviation <= tolerance * 1.05)
        return res

    for iteration in range(max_iters):
        sorted_sub = sorted(subset_indices)
        subset_pts = [fit_source[i] for i in sorted_sub]
        if len(subset_pts) < 3:
            fld_logger.warn(f"_refine_to_min_points: Subset count < 3 ({len(subset_pts)}); aborting.")
            break

        # Detect handle types, solve the bezier, measure error.
        htypes = _detect_handle_types(subset_pts, is_closed, known_corners=known_corners)
        spans, keys = _segment_spans(sorted_sub, is_closed, htypes)
        h_in, h_out = _solve_bezier_handles(subset_pts, is_closed, htypes,
                                            fit_source_xyz, spans,
                                            seg_keys=keys, cache=solve_cache,
                                            arc_data=fit_source_arc)
        worst_error, _worst_pt, seg_errors = _sample_bezier_sdf_error(
            field, subset_pts, h_in, h_out, is_closed, normal, return_seg_errors=True,
            spans=spans, n_src=len(fit_source_xyz))

        if worst_error < tolerance:
            fld_logger.info(f"_refine_to_min_points: Converged at iteration {iteration}. "
                           f"Worst error {worst_error:.6f} mm < tolerance {tolerance:.6f} mm. "
                           f"Control points: {len(subset_pts)}")
            return _build_result(subset_indices)

        if len(subset_indices) >= max_control_points:
            fld_logger.warn(f"_refine_to_min_points: Reached max_control_points={max_control_points} "
                           f"at iteration {iteration}. Worst error: {worst_error:.6f} mm")
            return _build_result(subset_indices)

        # Split EVERY segment that misses tolerance, worst first, within the remaining
        # budget -- one pass of adaptive subdivision rather than one point per
        # iteration. The per-segment errors come free from the measurement just taken.
        #
        # One point per iteration was the second half of the convergence failure. Each
        # iteration re-solves the handles and re-samples the whole curve to buy a
        # single control point, so a contour needing 40 of them costs 40 full curve
        # measurements; batching turns that into ~log2(40) passes, because every
        # failing segment is halved at once. Measured on a Perlin sphere contour: 3.6 s
        # to converge before, and the run that did *not* converge spent 490 ms proving
        # it. Insertion order also stops mattering, which is what the frozen-error
        # plateau was: the loop kept choosing points near one hot spot while forty
        # other segments were over tolerance and untouched.
        n_sub = len(sorted_sub)
        failing = sorted(((err, seg) for seg, err in enumerate(seg_errors)
                          if err >= tolerance), key=lambda item: -item[0])

        # Batch while the curve is broadly wrong; go one point at a time once it is
        # nearly right. Batching is what rescued the deformed fields -- a contour with
        # forty bad segments cannot afford forty full curve measurements to buy forty
        # points -- but it overspends at the end, where splitting a near-miss buys
        # about 16x more accuracy than was asked for (a cubic's error falls with the
        # fourth power of segment length). Measured on the bench's Perlin rows at
        # 0.01 mm: 88 control points one at a time, 113 by batching throughout.
        # Switching on the count of failing segments rather than on how badly the
        # worst one misses is what makes this work; damping by error threshold was
        # tried first and moved the same number by one point.
        budget = max_control_points - len(subset_indices)
        want = 1 if len(failing) <= _ENDGAME_SEGMENTS else len(failing)
        want = min(want, budget)

        # Walk the whole failing list, not just the first `want` of it, and stop
        # once `want` points have actually been placed. The difference is what
        # happens when the chosen segment turns out to be two adjacent source
        # vertices with nothing in between: taking the first `want` entries and
        # giving up if none of them split abandons every other segment that is
        # also over tolerance, and in the endgame `want` is 1, so a single
        # unsplittable segment ended the fit outright. Measured on a Perlin-
        # displaced box at 0.05 mm: the fit stopped at 96 control points and
        # 0.0756 mm with 94 perfectly splittable segments still over tolerance.
        added = []
        for _err, seg in failing:
            if len(added) >= want:
                break
            i0 = sorted_sub[seg]
            i1 = sorted_sub[(seg + 1) % n_sub]
            cand = _span_midpoint_index(fit_source_xyz, i0, i1, n_src, is_closed, used_set)
            if cand is not None:
                subset_indices.append(cand)
                used_set.add(cand)
                added.append(cand)

        if added:
            fld_logger.info(f"_refine_to_min_points: Iteration {iteration}: error {worst_error:.6f} mm, "
                           f"{len(failing)} of {len(seg_errors)} segments over tolerance. "
                           f"Split {len(added)} (now {len(subset_indices)} pts).")
        else:
            # Every failing segment is down to two adjacent source vertices; there is
            # no interior point left to divide it at. More control points cannot help
            # -- the polyline itself is coarser than the tolerance here, which is what
            # a sub-millimetre noise speck looks like from inside the fitter.
            fld_logger.warn(f"_refine_to_min_points: No splittable segment left among "
                           f"{len(failing)} over tolerance ({n_src} source points). "
                           f"Worst error: {worst_error:.6f} mm")
            return _build_result(subset_indices)

    fld_logger.warn(f"_refine_to_min_points: Reached max_iters ({max_iters}). "
                   f"Final worst error: {worst_error:.6f} mm")
    return _build_result(subset_indices)


def trace_sdf(field, origin, normal, tolerance=DEFAULT_MODEL_TOLERANCE_MM, max_control_points=200, angle_tolerance_deg=5.0):
    """Trace zero-crossing contours of an SDF field on a plane.

    Extraction is marching squares on a tolerance-pitched grid
    (`sdf_contour.slice_polygons`); this function fits each polygon it returns,
    producing curves with the minimum number of control points needed for the
    given accuracy tolerance.

    Args:
        field:     SdfField — the field to slice.
        origin:    FreeCAD.Vector — point on the slice plane.
        normal:    FreeCAD.Vector — plane normal (will be normalized).
        tolerance: float — maximum allowed deviation from the true surface in mm.
                   Default 0.1 mm (project accuracy target; GUI callers pass
                   the global Model Tolerance from Fields Settings).

    Returns:
        list[Part.BSplineCurve] — one BSpline per contour, with minimal poles.
    """
    fld_logger.info(f"trace_sdf started: origin={origin}, normal={normal}, tolerance={tolerance}")
    normal = FreeCAD.Vector(normal)
    normal.normalize()

    # Phase 1: extract the zero set as dense polygons. One marching-squares pass
    # on a grid pitched at the tolerance replaces the seed scan and gradient walk
    # this used to do (`_find_seeds` + `_walk_contour`, deleted 2026-08-22). The
    # walk itself was accurate -- measured within 0.03 mm of the raster contour and
    # closing correctly -- but its seed grid was pitched at four walk steps, and the
    # walk step came from the bounding box (`diag / 400`), so on a 45 mm Perlin
    # sphere the seeds sat 0.78 mm apart and 21 of 30 contours fitted inside a
    # single cell and were never seeded. Nothing downstream can recover a contour
    # that was never found, and pitch has to follow tolerance, not part size.
    try:
        polys = slice_polygons(field, origin, normal, tolerance=tolerance)
    except Exception as e:
        fld_logger.error(f"trace_sdf: Contour extraction failed: {e}")
        return []
    if not polys:
        fld_logger.warn("trace_sdf: No contour on the slice plane.")
        return []

    effective_tolerance = tolerance
    curves = []

    # Phase 2: fit each polygon. `_refine_to_min_points` is input-agnostic -- it
    # wants a dense ordered polyline and a closed flag, which is exactly what
    # marching squares produces, and it is unchanged by this.
    for poly_idx, (raw_points, is_closed) in enumerate(polys):
        if len(raw_points) < 3:
            fld_logger.warn(f"trace_sdf: Contour {poly_idx} has too few points: "
                           f"{len(raw_points)}")
            continue

        # Iterative refinement: find minimum points for target tolerance
        fld_logger.debug(f"trace_sdf: Refining contour walked points (count={len(raw_points)}, is_closed={is_closed})")
        bs = _refine_to_min_points(field, raw_points, is_closed, normal, effective_tolerance, max_control_points=max_control_points, angle_tolerance_deg=angle_tolerance_deg)
        if bs is not None:
            curves.append(bs)
            fld_logger.info(f"trace_sdf: Successfully traced curve {len(curves)-1} with {len(bs.getPoles())} poles.")
        else:
            fld_logger.warn(f"trace_sdf: Refinement failed to produce a valid BSpline for contour {poly_idx}")

    fld_logger.info(f"trace_sdf finished: successfully traced {len(curves)} curve(s)")
    return curves
