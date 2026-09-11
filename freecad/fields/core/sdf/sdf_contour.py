# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf_contour.py

The SDF's zero set on a slice plane, as dense polygons: sample the field on a
grid, run marching squares on the sign array, snap every vertex onto the
surface, and subdivide any chord that still sags off it.

The grid is sampled in tiles and only the tiles the surface can reach are
evaluated at all -- the field's own value at a tile centre says whether the zero
set can get inside it. So the cost follows the *length* of the contour rather
than the area of the bounding box, and the pitch stops being a function of how
big the part is. The lattice itself is never materialised; tiles carry their
node offsets into one shared edge graph, so a contour crossing a tile border
links up without a stitching pass.

This is the one contour extractor. `sdf_slicer.trace_sdf` fits Beziers to what
comes out of here, and `sdf_slice_body` builds a `Part` body from it.

Topology comes from the grid and accuracy comes from the snap, and separating
the two is the point. The cell-edge graph cannot lap, cannot leave its contour
and cannot return a fragment, so closure stops being a heuristic; a subdivided
midpoint sits between two *known* contour points, so it cannot wander onto a
neighbouring fold the way a free walk step can. The gradient walk this replaced
(`_find_seeds` + `_walk_contour`, deleted 2026-08-22) had neither property: its
seed grid was pitched at four walk steps -- 0.78 mm on a 45 mm part, because the
walk step came from the bounding box -- so on a Perlin-deformed sphere 21 of 30
contours fell inside a single cell and were never seeded at all.

The polygons are dense by construction (one vertex per grid cell crossed, plus
midpoint subdivision). They are the reference a fitted curve approximates, not
a CAM path in themselves.
"""
import math

import numpy as np
import FreeCAD

from freecad.fields.core import fld_logger

V = FreeCAD.Vector


def _snap_to_surface(field, pos, normal, iters=4):
    """Snap a 3D point back to the SDF zero-crossing, constrained to the slice plane.

    Thin wrapper over the shared Newton-projection engine in
    `sdf_field._project_to_isosurface` -- see that docstring for the algorithm this,
    `sdf_slicer._surface_distance_grid`, and `SdfField.to_patch_cage()`'s vertex
    projection all now share.

    Args:
        field:  SdfField to evaluate.
        pos:    FreeCAD.Vector — current approximate position.
        normal: FreeCAD.Vector — slice plane normal (point stays on this plane).
        iters:  int — number of Newton refinement steps.

    Returns:
        FreeCAD.Vector — refined position on the zero isoline.
    """
    from freecad.fields.core.sdf.sdf_field import _project_to_isosurface

    pts = np.array([[pos.x, pos.y, pos.z]], dtype=np.float64)
    nrm = np.array([normal.x, normal.y, normal.z], dtype=np.float64)
    result = _project_to_isosurface(field, pts, iters=iters, tol=1e-7, plane_normal=nrm)
    return V(result[0, 0], result[0, 1], result[0, 2])


# Marching-squares segment table, oriented so the interior (f < 0) is on the
# left of travel: outer boundaries come out CCW and holes CW, which is the
# winding the rest of the slicer already assumes.
#   e0 = bottom, e1 = right, e2 = top, e3 = left
_MS = {
    0:  (),            1:  ((0, 3),),     2:  ((1, 0),),     3:  ((1, 3),),
    4:  ((2, 1),),     6:  ((2, 0),),     7:  ((2, 3),),     8:  ((3, 2),),
    9:  ((0, 2),),     11: ((1, 2),),     12: ((3, 1),),     13: ((0, 1),),
    14: ((3, 0),),     15: (),
}
# The two saddles need the cell centre to decide which way the band runs.
_MS_5_JOINED = ((0, 1), (2, 3))     # centre inside: c0 and c2 are one region
_MS_5_SPLIT = ((0, 3), (2, 1))
_MS_10_JOINED = ((3, 0), (1, 2))    # centre inside: c1 and c3 are one region
_MS_10_SPLIT = ((1, 0), (3, 2))


class SlabCompositeField:
    """The union of a field's cross-sections over a slab of thickness `t`.

    `min` over the slab: the sign is inside-at-any-height, so the zero set is
    the outline of everything the layer contains, and a feature thinner than
    the layer cannot fall between two sample planes and vanish. `min` of
    1-Lipschitz functions is 1-Lipschitz, so snapping still converges.

    A slab's zero set is deliberately not the plane's -- it is the layer's
    silhouette. Score it against its own definition, never against `f(x, y, z0)`.
    """

    def __init__(self, base, normal, thickness, n=3):
        self.base = base
        self.n = max(1, int(n))
        if self.n > 1:
            dists = np.linspace(-0.5, 0.5, self.n) * float(thickness)
        else:
            dists = [0.0]
        self.offsets = [FreeCAD.Vector(normal) * float(d) for d in dists]

    def bounding_box(self):
        return self.base.bounding_box()

    def lipschitz(self) -> float:
        # A min of L-Lipschitz functions is L-Lipschitz, and every branch here
        # is the same field at a different offset, so the base's bound carries
        # over unchanged. Needed because the tile skip below asks for it.
        try:
            return float(self.base.lipschitz())
        except AttributeError:
            return 1.0

    def _branch(self, pos):
        best, best_o = None, self.offsets[0]
        for o in self.offsets:
            v = self.base.evaluate(pos + o)
            if best is None or v < best:
                best, best_o = v, o
        return best, best_o

    def evaluate(self, pos):
        return self._branch(pos)[0]

    def gradient(self, pos):
        # The gradient of a min is the gradient of whichever branch won, taken
        # at that branch's own offset -- not at the query point.
        return self.base.gradient(pos + self._branch(pos)[1])

    def _branch_grid(self, pts):
        best, which = None, None
        for k, o in enumerate(self.offsets):
            v = np.asarray(self.base.evaluate_grid(
                pts + np.array([o.x, o.y, o.z])), dtype=np.float64)
            if best is None:
                best, which = v, np.zeros(len(v), dtype=np.int64)
            else:
                take = v < best
                best = np.where(take, v, best)
                which = np.where(take, k, which)
        return best, which

    def evaluate_grid(self, pts):
        return self._branch_grid(np.asarray(pts, dtype=np.float64))[0]

    def gradient_grid(self, pts):
        pts = np.asarray(pts, dtype=np.float64)
        which = self._branch_grid(pts)[1]
        offs = np.array([[o.x, o.y, o.z] for o in self.offsets])
        return np.asarray(self.base.gradient_grid(pts + offs[which]),
                          dtype=np.float64)


def slice_frame(normal):
    """The (u, v) axes this module measures a slice plane in."""
    normal = FreeCAD.Vector(normal)
    normal.normalize()
    up = V(0, 0, 1)
    if abs(normal.dot(up)) > 0.99:
        up = V(1, 0, 0)
    u_axis = normal.cross(up)
    u_axis.normalize()
    v_axis = normal.cross(u_axis)
    v_axis.normalize()
    return normal, u_axis, v_axis


def _edge_graph(vals, us, vs, i0, j0, nxt, prv, pos):
    """Fold one block of the sign grid into a shared marching-squares edge graph.

    `i0`/`j0` are the block's node offsets in the whole-plane lattice, so a
    crossing on the node column two blocks share gets the *same* key from both
    and a contour running out of one block and into the next links up with no
    stitching pass. That is what makes it safe to sample the plane in pieces:
    the graph is global even though no two blocks are ever in memory together.

    `pos` carries each crossing's (u, v) as it is found, so the tracer never has
    to look at `vals` again -- which is the other half of why a block can be
    thrown away as soon as it has been marched.
    """
    neg = vals < 0
    case = (neg[:-1, :-1].astype(np.uint8)
            | (neg[1:, :-1].astype(np.uint8) << 1)
            | (neg[1:, 1:].astype(np.uint8) << 2)
            | (neg[:-1, 1:].astype(np.uint8) << 3))
    active = np.argwhere((case != 0) & (case != 15))

    def _pt(kind, i, j):
        if kind == 0:   # horizontal: between (i, j) and (i+1, j)
            a, b = vals[i, j], vals[i + 1, j]
            t = 0.5 if a == b else a / (a - b)
            return (us[i] + min(1.0, max(0.0, t)) * (us[i + 1] - us[i]), vs[j])
        a, b = vals[i, j], vals[i, j + 1]
        t = 0.5 if a == b else a / (a - b)
        return (us[i], vs[j] + min(1.0, max(0.0, t)) * (vs[j + 1] - vs[j]))

    for iu, iv in active:
        i, j = int(iu), int(iv)
        c = int(case[i, j])
        if c == 5 or c == 10:
            centre = 0.25 * (vals[i, j] + vals[i + 1, j]
                             + vals[i + 1, j + 1] + vals[i, j + 1])
            if c == 5:
                segs = _MS_5_JOINED if centre < 0 else _MS_5_SPLIT
            else:
                segs = _MS_10_JOINED if centre < 0 else _MS_10_SPLIT
        else:
            segs = _MS[c]
        local = ((0, i, j), (1, i + 1, j), (0, i, j + 1), (1, i, j))
        for a, b in segs:
            ends = []
            for e in (a, b):
                kind, li, lj = local[e]
                key = (kind, i0 + li, j0 + lj)
                if key not in pos:
                    pos[key] = _pt(kind, li, lj)
                ends.append(key)
            nxt[ends[0]] = ends[1]
            prv[ends[1]] = ends[0]


def _trace_loops(nxt, prv, pos):
    """Walk an edge graph into contours -> list of (list[(u, v)], is_closed)."""
    loops, seen = [], set()
    # Open chains first, from every edge nothing leads into: these run off the
    # sampled region's border, and starting one mid-chain would report it as two
    # fragments.
    for start in [k for k in nxt if k not in prv]:
        if start in seen:
            continue
        chain, k = [start], start
        seen.add(k)
        while k in nxt:
            k = nxt[k]
            if k in seen:
                break
            seen.add(k)
            chain.append(k)
        loops.append(([pos[k] for k in chain], False))
    for start in nxt:
        if start in seen:
            continue
        chain, k = [start], start
        seen.add(k)
        while True:
            k = nxt.get(k)
            if k is None or k == start:
                break
            if k in seen:
                break
            seen.add(k)
            chain.append(k)
        if len(chain) >= 3:
            loops.append(([pos[k] for k in chain], True))
    return loops


def _march(vals, us, vs):
    """Marching squares on one whole sign grid -> list of (list[(u, v)], is_closed)."""
    nxt, prv, pos = {}, {}, {}
    _edge_graph(vals, us, vs, 0, 0, nxt, prv, pos)
    return _trace_loops(nxt, prv, pos)


def _subdivide(field, pts, normal, is_closed, sag_tol, max_rounds=8):
    """Insert the chord midpoint wherever it does not already lie on the contour.

    Bounded refinement: both endpoints are known points of the true contour, so
    an inserted midpoint cannot wander onto a neighbouring fold the way a free
    walk step can.
    """
    for _ in range(max_rounds):
        out, inserted = [], 0
        n = len(pts)
        last = n if is_closed else n - 1
        for i in range(last):
            a, b = pts[i], pts[(i + 1) % n]
            out.append(a)
            mid = (a + b) * 0.5
            snapped = _snap_to_surface(field, mid, normal)
            if (snapped - mid).Length > sag_tol:
                out.append(snapped)
                inserted += 1
        if not is_closed:
            out.append(pts[-1])
        pts = out
        if not inserted:
            break
    return pts


def _drop_near_duplicates(pts, is_closed, min_span):
    """Remove vertices that sit on top of their predecessor.

    Marching squares puts a vertex wherever the contour crosses a cell edge, so a
    contour that clips a cell corner emits two vertices a hair apart -- 12 spans of
    exactly zero and 96 under 0.005 mm in a 1760-point loop, measured. Zero-length
    spans make the turn angle at that vertex undefined, and the fitter's feature
    detection reads those angles directly, so the fit chases grid jitter instead of
    the shape. Dropping a vertex within `min_span` of the one kept before it moves
    the polyline by at most `min_span`, which is why the threshold is a small
    fraction of tolerance rather than a fraction of the cell.
    """
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for p in pts[1:]:
        if (p - out[-1]).Length >= min_span:
            out.append(p)
    if is_closed:
        while len(out) > 3 and (out[-1] - out[0]).Length < min_span:
            out.pop()
    return out


# Cells per side of a sampling tile. The tile is the unit of empty-space
# skipping, so it sets how wide a band around the contour actually gets
# evaluated: samples go as `perimeter * TILE / pitch` instead of the full grid's
# `area / pitch^2`. Smaller tiles mean a tighter band but a bigger share of nodes
# duplicated on shared tile borders -- (T+1)^2/T^2 is 13% at 16 and 6% at 32 --
# and 16 is where that overhead is still small against the band it saves.
_TILE = 16


def _lattice_size(lo, hi, cell, pad):
    """How many nodes one axis needs, without building them.

    Separate from `_lattice` so the budget can be checked before anything is
    allocated: a pitch fine enough to overrun it is also fine enough to make the
    node array itself expensive, and the whole point is not to pay for lattice
    that will be thrown away.
    """
    return max(8, int(math.ceil((hi - lo + 2.0 * pad) / cell)) + 1)


def _lattice(lo, hi, cell, pad):
    """The node coordinates along one axis, padded and pitched at `cell`."""
    return np.linspace(lo - pad, hi + pad, _lattice_size(lo, hi, cell, pad))


def _tile_count(n, tile=_TILE):
    """Tiles along an axis of `n` nodes -- `_tile_boxes`' count, without the list."""
    return max(1, math.ceil((n - 1) / tile))


def _tile_boxes(nu, nv, tile=_TILE):
    """Node index ranges (i0, i1, j0, j1) covering every cell exactly once.

    Ranges are inclusive of both end nodes and so overlap their neighbours by one
    node column; the *cells* they cover do not overlap, which is what keeps every
    crossing marched once.
    """
    out = []
    for i0 in range(0, nu - 1, tile):
        i1 = min(i0 + tile, nu - 1)
        for j0 in range(0, nv - 1, tile):
            j1 = min(j0 + tile, nv - 1)
            out.append((i0, i1, j0, j1))
    return out


def _reachable_tiles(field, us, vs, o, ua, va, boxes, lip, margin):
    """The tiles the zero set can reach, by the field's own Lipschitz bound.

    A tile whose centre reads `|f| > L * half_diagonal` cannot contain a zero:
    the field would have to change faster than `L` to get from that value to
    zero inside the tile. This is the octree's empty-space skip applied to a
    plane, and it is the whole reason the pitch no longer has to be traded
    against the size of the bounding box -- the cost follows the length of the
    contour, not the area it sits in.

    Every centre is evaluated in one batch, so the scan costs one sample per
    tile: at pitch 0.02 mm on a 44 mm part that is 7.5k samples to decide the
    fate of 4.8M.

    It is exactly as good as `lipschitz()`. A field that *under*-reports its
    bound does not come out coarse here, it comes out missing whole contours, so
    `lip` is floored at 1.0 and the reach carries `margin` (one cell) on top.
    A contour that vanishes when nothing else changed is the symptom to suspect
    a wrong bound first -- see the noise CPU/GPU divergence for precedent.
    """
    cu = np.array([0.5 * (us[i0] + us[i1]) for i0, i1, _, _ in boxes])
    cv = np.array([0.5 * (vs[j0] + vs[j1]) for _, _, j0, j1 in boxes])
    half = np.array([math.hypot(0.5 * (us[i1] - us[i0]), 0.5 * (vs[j1] - vs[j0]))
                     for i0, i1, j0, j1 in boxes])
    pts = (o[None, :] + cu[:, None] * ua[None, :] + cv[:, None] * va[None, :])
    vals = np.abs(np.asarray(field.evaluate_grid(pts), dtype=np.float64))
    keep = vals <= lip * half + margin
    return [b for b, k in zip(boxes, keep) if k]


def _march_tiles(field, us, vs, o, ua, va, boxes):
    """Sample and march each tile in turn -> (loops, samples evaluated)."""
    nxt, prv, pos = {}, {}, {}
    evaluated = 0
    for i0, i1, j0, j1 in boxes:
        su, sv = us[i0:i1 + 1], vs[j0:j1 + 1]
        U, Vv = np.meshgrid(su, sv, indexing="ij")
        pts = (o[None, :] + U.ravel()[:, None] * ua[None, :]
               + Vv.ravel()[:, None] * va[None, :])
        block = np.asarray(field.evaluate_grid(pts),
                           dtype=np.float64).reshape(len(su), len(sv))
        evaluated += block.size
        _edge_graph(block, su, sv, i0, j0, nxt, prv, pos)
    return _trace_loops(nxt, prv, pos), evaluated


def slice_polygons(field, origin, normal, tolerance=0.05, cell=None,
                   thickness=0.0, max_samples=1_000_000, subdivide=True):
    """The SDF's zero set on the slice plane, as dense snapped polygons.

    Returns a list of `(points, is_closed)`. No curve fitting: these points are
    on the surface to within `_snap_to_surface`'s Newton tolerance, so they are
    the reference the fitted curves are supposed to approximate.

    `cell` is the sampling pitch and it must be tied to `tolerance`, not to the
    bounding box: at a coarse pitch the failure is a *wildly* wrong contour, not
    a slightly coarse one (measured at 16.4 mm off on a 44 mm part at 0.40 mm
    pitch, against 0.118 mm at 0.05 mm pitch). Default is `tolerance`.

    Only the tiles the surface can reach are sampled (`_reachable_tiles`), so
    `max_samples` bounds the points actually *evaluated* rather than the side of
    the lattice. That distinction is the point: a side cap made the achievable
    pitch a function of how big the part is, and a 44 mm part at a 1024 cap could
    not be asked for anything finer than 0.044 mm -- every tolerance below that
    silently got the same grid. The budget here is spent on the contour instead
    of the empty space around it, and how much that buys depends on how much
    contour there is: a sphere section reaches about 10x finer for the same
    million samples, a Perlin slice that folds into 71 separate regions only
    about 1.7x. So it can still bite, and a budget that bites is reported,
    because silently coarsening the pitch is exactly how a wrong contour looks
    right.
    """
    normal, u_axis, v_axis = slice_frame(normal)
    if thickness > 0.0:
        field = SlabCompositeField(field, normal, thickness)

    mn, mx = field.bounding_box()
    centre = (mn + mx) * 0.5
    projected = centre - normal * (centre - FreeCAD.Vector(origin)).dot(normal)

    u_lo = v_lo = float("inf")
    u_hi = v_hi = float("-inf")
    for cx in (mn.x, mx.x):
        for cy in (mn.y, mx.y):
            for cz in (mn.z, mx.z):
                d = V(cx, cy, cz) - projected
                du, dv = d.dot(u_axis), d.dot(v_axis)
                u_lo, u_hi = min(u_lo, du), max(u_hi, du)
                v_lo, v_hi = min(v_lo, dv), max(v_hi, dv)

    if cell is None:
        cell = max(tolerance, 1e-4)
    requested = cell
    try:
        lip = max(1.0, float(field.lipschitz()))
    except (AttributeError, TypeError, ValueError):
        lip = 1.0

    o = np.array([projected.x, projected.y, projected.z])
    ua = np.array([u_axis.x, u_axis.y, u_axis.z])
    va = np.array([v_axis.x, v_axis.y, v_axis.z])

    # Two ways to overrun the budget and both are handled the same way, by
    # coarsening and re-scanning: the scan itself can cost more than a quarter of
    # the budget -- it is overhead, not the work -- or the tiles the surface
    # reaches can. Tile count falls as 1/pitch^2 and sampled nodes as 1/pitch, so
    # one corrective step usually lands and eight are more than enough.
    coarsened = False
    scan_budget = max(1, max_samples // 4)
    last = 7
    for attempt in range(last + 1):
        pad = cell * 2.0
        n_tiles = (_tile_count(_lattice_size(u_lo, u_hi, cell, pad))
                   * _tile_count(_lattice_size(v_lo, v_hi, cell, pad)))
        if n_tiles > scan_budget and attempt < last:
            cell *= math.sqrt(n_tiles / scan_budget) * 1.05
            coarsened = True
            continue
        us = _lattice(u_lo, u_hi, cell, pad)
        vs = _lattice(v_lo, v_hi, cell, pad)
        boxes = _tile_boxes(len(us), len(vs))
        kept = _reachable_tiles(field, us, vs, o, ua, va, boxes, lip, cell)
        predicted = len(boxes) + sum((i1 - i0 + 1) * (j1 - j0 + 1)
                                     for i0, i1, j0, j1 in kept)
        if predicted <= max_samples or attempt == last:
            break
        cell *= (predicted / max_samples) * 1.05
        coarsened = True

    actual = max((us[-1] - us[0]) / (len(us) - 1),
                 (vs[-1] - vs[0]) / (len(vs) - 1))
    if coarsened:
        fld_logger.warn(
            f"SliceContour: {max_samples} sample budget reached -- pitch "
            f"coarsened {requested:.4f} -> {actual:.4f} mm; contour may be "
            f"badly wrong, not merely coarse")

    loops, evaluated = _march_tiles(field, us, vs, o, ua, va, kept)

    polys = []
    for uvs, is_closed in loops:
        pts = [projected + u_axis * float(u) + v_axis * float(v) for u, v in uvs]
        pts = [_snap_to_surface(field, p, normal) for p in pts]
        pts = _drop_near_duplicates(pts, is_closed, tolerance * 0.1)
        if subdivide:
            pts = _subdivide(field, pts, normal, is_closed, tolerance * 0.5)
            pts = _drop_near_duplicates(pts, is_closed, tolerance * 0.1)
        if len(pts) >= (3 if is_closed else 2):
            polys.append((pts, is_closed))
    fld_logger.info(
        f"SliceContour: {len(us)}x{len(vs)} lattice at {actual:.4f} mm, "
        f"{len(kept)}/{len(boxes)} tiles sampled, "
        f"{evaluated + len(boxes)} sample(s) -> {len(polys)} polygon(s), "
        f"{sum(len(p) for p, _ in polys)} point(s), "
        f"{sum(1 for _, c in polys if c)} closed")
    return polys
