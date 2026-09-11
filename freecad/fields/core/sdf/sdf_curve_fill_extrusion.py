# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf_curve_fill_extrusion.py

SdfCurveFillExtrusionField — extrudes a filled SDF face (fill-curve result)
along the normal of its boundary plane into a solid. Provides both the
height-field solver (solve_boundary_height_field) and the SdfField subclass
that drives the GPU ray-march renderer and CPU octree evaluator.

The module was renamed from sdf_surface_extrusion.py as part of the RPF
(Rename Patch Face) task series.
"""
import FreeCAD
from .sdf_field import SdfField
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_prism import (
    SdfProfilePrismField,
    SdfPrismExtrusionField,
    SdfPlaneCap,
    SdfHeightmapCap,
)
import math
import os
import numpy as np


def _thin_plate_field(boundary_pts, control_pts, grid_x, grid_y, max_centres=250):
    """Thin-plate spline through the boundary (and control) points, on the given grid.

    This is the exact minimiser of the bending energy that interpolates those points --
    the tension = 0 end of the fill. It is an RBF fit rather than a relaxation because
    relaxing the plate is not affordable here: the 13-point biharmonic stencil converges
    like the 4th power of the mode number, so at 256^2 it still had visible low-frequency
    error after thousands of sweeps (measured), while a dense fit through ~250 centres is
    one small solve and a vectorised evaluation. Returns None if the fit is degenerate.
    """
    pts = np.asarray(boundary_pts, dtype=np.float64)
    if control_pts is not None and len(control_pts):
        pts = np.vstack([pts, np.asarray(control_pts, dtype=np.float64)])
    if len(pts) < 4:
        return None

    # Boundary samples are dense in CELLS (see the caller); as RBF centres they would be
    # near-duplicates and the system would be singular. Thin them evenly instead.
    if len(pts) > max_centres:
        pts = pts[np.round(np.linspace(0, len(pts) - 1, max_centres)).astype(int)]
    # Exact duplicates survive the thinning when the curve doubles back on itself.
    _, keep = np.unique(np.round(pts[:, :2], 9), axis=0, return_index=True)
    pts = pts[np.sort(keep)]
    n = len(pts)
    if n < 4:
        return None

    def phi(r2):
        # r^2 log r, written on r^2 so the log is one call and r = 0 stays finite.
        return np.where(r2 > 1e-24, 0.5 * r2 * np.log(np.maximum(r2, 1e-24)), 0.0)

    d2 = ((pts[:, None, 0] - pts[None, :, 0]) ** 2 + (pts[:, None, 1] - pts[None, :, 1]) ** 2)
    K = phi(d2)
    P = np.column_stack([np.ones(n), pts[:, 0], pts[:, 1]])
    A = np.zeros((n + 3, n + 3), dtype=np.float64)
    A[:n, :n] = K
    A[:n, n:] = P
    A[n:, :n] = P.T
    # Smoothing term: the fit stays an interpolant to plotting accuracy but the system
    # stops being singular when the thinned centres are still nearly collinear.
    A[:n, :n] += np.eye(n) * (1e-8 * max(np.abs(K).max(), 1.0))
    rhs = np.concatenate([pts[:, 2], np.zeros(3)])
    try:
        sol = np.linalg.solve(A, rhs)
    except np.linalg.LinAlgError:
        sol, *_ = np.linalg.lstsq(A, rhs, rcond=None)
    if not np.all(np.isfinite(sol)):
        return None
    w, a = sol[:n], sol[n:]

    out = a[0] + a[1] * grid_x + a[2] * grid_y
    # Chunked over grid rows: the full (cells x centres) distance table is hundreds of
    # MB at 256^2 x 250.
    for y0 in range(0, grid_x.shape[0], 32):
        gx = grid_x[y0:y0 + 32, :, None]
        gy = grid_y[y0:y0 + 32, :, None]
        r2 = (gx - pts[None, None, :, 0]) ** 2 + (gy - pts[None, None, :, 1]) ** 2
        out[y0:y0 + 32] += phi(r2) @ w
    return out


_RING_EPS = 1e-9
_MAX_RING_SEGMENTS = 512


def _ring_points(bx, by):
    """(n, 2) vertices of the closed polygon through (bx, by), duplicates removed.

    The ring is closed by wrap-around everywhere in this module, so a point list
    that already repeats its first point at the end -- which is exactly what
    `curve_sampler.sample_curve_world_pts` returns for a closed curve -- has to
    have that duplicate dropped. Leaving it in puts a zero-length segment on the
    ring and leaves segments 0 and n-2 sharing the vertex p0, which any
    crossing test reads as a self-intersection.
    """
    pts = np.column_stack([np.asarray(bx, dtype=np.float64),
                           np.asarray(by, dtype=np.float64)])
    if len(pts) > 1:
        keep = np.ones(len(pts), dtype=bool)
        keep[1:] = np.any(np.abs(np.diff(pts, axis=0)) > _RING_EPS, axis=1)
        pts = pts[keep]
    if len(pts) > 1 and np.all(np.abs(pts[0] - pts[-1]) <= _RING_EPS):
        pts = pts[:-1]
    if len(pts) > _MAX_RING_SEGMENTS:
        idx = np.unique(np.round(
            np.linspace(0, len(pts) - 1, _MAX_RING_SEGMENTS)).astype(int))
        pts = pts[idx]
    return pts


def _polyline_self_intersects(bx, by):
    """True when the closed polygon through (bx, by) properly crosses itself.

    UX-008: `solve_boundary_height_field` models the patch as h(x, y), which is
    only defined while the boundary projects to a SIMPLE closed curve. Where it
    crosses, the Dirichlet ring pins two different z in one cell and the fill
    flattens towards their mean while still passing every test.

    Only PROPER crossings count -- pairs of segments whose interiors meet. Two
    segments that merely touch at a shared vertex are what a closed ring is made
    of, and counting them is what made an earlier version of this return True
    for every convex polygon. False positives are much more expensive than false
    negatives here: they silently flatten a patch that was fine.

    O(n^2) but fully vectorised, so the ~130-point boundary costs microseconds
    and there is no need to thin the polyline first. Thinning would be wrong as
    well as unnecessary: replacing the ring with chords both hides real crossings
    and manufactures fake ones.
    """
    pts = _ring_points(bx, by)
    n = len(pts)
    if n < 4:
        return False

    a = pts[:, None, :]                      # segment i start
    b = np.roll(pts, -1, axis=0)[:, None, :]  # segment i end
    c = pts[None, :, :]                      # segment j start
    d = np.roll(pts, -1, axis=0)[None, :, :]  # segment j end

    def _orient(p, q, r):
        return np.sign((q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1])
                       - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0]))

    straddles = ((_orient(c, d, a) * _orient(c, d, b) < 0)
                 & (_orient(a, b, c) * _orient(a, b, d) < 0))

    i = np.arange(n)[:, None]
    j = np.arange(n)[None, :]
    adjacent = (i == j) | ((i + 1) % n == j) | ((j + 1) % n == i)
    return bool(np.any(straddles & ~adjacent))


def _placement_from_axes(base, ex, ey, ez):
    """A Placement whose local x/y/z map to the world directions ex/ey/ez."""
    return FreeCAD.Placement(FreeCAD.Matrix(
        ex.x, ey.x, ez.x, base.x,
        ex.y, ey.y, ez.y, base.y,
        ex.z, ey.z, ez.z, base.z,
        0.0, 0.0, 0.0, 1.0,
    ))


def simple_projection_placement(world_pts, plane, label=None):
    """`plane`, or a rotation of it that the boundary projects simply onto.

    UX-008 step 2. The best-fit plane minimises out-of-plane deviation, which is
    not the same as being a valid projection direction, so when the boundary
    crosses itself on the best fit the other two PCA axes are tried in turn.
    Returns None when none of the three works: that curve is knotted and is not
    a height field on any plane, and the caller must say so and fall back.

    The PLANE is rotated rather than the solver's coordinate arrays permuted.
    Every consumer -- the prism walls, both height-field caps, the picking mesh
    and `_to_local_frame` -- reads this one plane, so rotating it keeps them
    consistent. Permuting inside the solve instead leaves the grid indexed on one
    pair of axes and its `bounds` interpreted on another, which is silently wrong
    geometry rather than an honest fallback.
    """
    if plane is None or not world_pts:
        return plane

    rot = plane.Rotation
    ex = rot.multVec(FreeCAD.Vector(1, 0, 0))
    ey = rot.multVec(FreeCAD.Vector(0, 1, 0))
    ez = rot.multVec(FreeCAD.Vector(0, 0, 1))

    # Each candidate keeps a right-handed frame: local z is the projection
    # direction, i.e. the axis the height is measured along.
    candidates = (
        ("best-fit", plane),
        ("local x/z", _placement_from_axes(plane.Base, ex, ez, ey.negative())),
        ("local y/z", _placement_from_axes(plane.Base, ey, ez, ex)),
    )

    who = f" for {label}" if label else ""
    for name, cand in candidates:
        m_inv = cand.inverse()
        lp = [m_inv.multVec(p if isinstance(p, FreeCAD.Vector) else FreeCAD.Vector(*p))
              for p in world_pts]
        px = np.array([p.x for p in lp], dtype=np.float64)
        py = np.array([p.y for p in lp], dtype=np.float64)
        if len(px) == 0:
            return plane

        # A projection the curve is nearly edge-on to is simple for the trivial
        # reason that it has collapsed to a line. Its height field would be a
        # crease, so it is not an answer -- skip it and keep looking.
        span_x = float(px.max() - px.min())
        span_y = float(py.max() - py.min())
        if min(span_x, span_y) < 0.01 * max(span_x, span_y, 1e-12):
            continue

        if not _polyline_self_intersects(px, py):
            if name != "best-fit":
                fld_logger.info(
                    f"SDF face{who}: boundary crosses itself on the best-fit plane; "
                    f"projecting on {name} instead")
            return cand

    fld_logger.warn(
        f"SDF face{who}: the boundary crosses itself on all three PCA axes, so it is "
        f"not a height field on any plane -- falling back to the planar fill")
    return None


def solve_boundary_height_field(curve_obj, curve_plane, control_grid=None, resolution=None,
                                tension=1.0):
    """
    Solves the boundary height field directly on a 2D heightmap grid via relaxation.

    1. Samples boundary curve points and projects into local space of curve_plane -> (x, y, z).
    2. Rasterizes the closed 2D boundary polygon to an interior mask on a resolution^2 grid.
    3. Imposes Dirichlet boundary conditions along the boundary cells (and any interior control points).
    4. Relaxes the free cells via red-black SOR: h = 0.25 * (h_left + h_right + h_up + h_down).
    5. Blends towards a thin-plate spline through the same points, per `tension`.

    `tension` in [0, 1] runs from the thin plate (0) to the membrane (1). The membrane is
    the harmonic fill this has always produced: taut, minimal area, and it creases into a
    lumpy boundary. The plate is the minimum-bending-energy fill: it carries the boundary
    curvature inwards and bulges. Both interpolate the boundary points exactly, so every
    blend of them does too -- which is why the knob can be a blend of two solutions
    rather than a solve of the blended operator (that operator is affordable to write and
    not affordable to converge; see `_thin_plate_field`).

    Returns (grid_array, (min_x, max_x, min_y, max_y)).
    """
    if resolution is None:
        try:
            from freecad.fields.core.objects.fld_object import get_heightmap_resolution
            resolution = get_heightmap_resolution()
        except Exception:
            resolution = 256

    world_pts = []
    if curve_obj is not None:
        if isinstance(curve_obj, (list, tuple, np.ndarray)):
            world_pts = list(curve_obj)
        else:
            from freecad.fields.core.sdf.curve_sampler import sample_curve_world_pts
            try:
                world_pts = sample_curve_world_pts(curve_obj, n_samples=128)
            except Exception:
                world_pts = [
                    curve_obj.Placement.multVec(p)
                    for p in list(getattr(curve_obj, "Points", []))
                ]

    if not world_pts:
        raise ValueError("solve_boundary_height_field: no boundary points provided or extracted.")

    if hasattr(curve_plane, "inverse"):
        m_inv = curve_plane.inverse()
        lp_pts = [m_inv.multVec(pt) if isinstance(pt, FreeCAD.Vector) else FreeCAD.Vector(*pt) for pt in world_pts]
    else:
        lp_pts = [FreeCAD.Vector(*pt) for pt in world_pts]

    bx = np.array([p.x for p in lp_pts], dtype=np.float64)
    by = np.array([p.y for p in lp_pts], dtype=np.float64)
    bz = np.array([p.z for p in lp_pts], dtype=np.float64)

    # UX-008 steps 1 and 3. Choosing WHICH axis to project along is not this
    # function's job -- it belongs to whoever picks `curve_plane`, because the
    # walls and the picking mesh are built in that same frame (see
    # `simple_projection_placement`). All this can do is refuse to solve a
    # boundary condition that has no solution, so the caller falls back rather
    # than shipping a silently flattened patch.
    if _polyline_self_intersects(bx, by):
        raise ValueError(
            "solve_boundary_height_field: the boundary crosses itself when projected on "
            "this plane, so h(x, y) is not defined -- the Dirichlet ring would pin two "
            "different z in one cell. Pick a projection with simple_projection_placement().")

    min_x, max_x = float(np.min(bx)), float(np.max(bx))
    min_y, max_y = float(np.min(by)), float(np.max(by))

    margin_x = max((max_x - min_x) * 0.1, 1.0)
    margin_y = max((max_y - min_y) * 0.1, 1.0)
    min_x -= margin_x; max_x += margin_x
    min_y -= margin_y; max_y += margin_y

    res_x = int(resolution)
    res_y = int(resolution)
    dx = (max_x - min_x) / max(res_x - 1, 1)
    dy = (max_y - min_y) / max(res_y - 1, 1)

    # 2. Dirichlet boundary cells.
    #
    # No interior mask is rasterized. The relaxation runs over every unpinned cell and
    # the pinned ring keeps the two sides apart on its own: a chain of cells stepped at
    # most one cell at a time is 8-connected, and an 8-connected curve separates the
    # 4-connected neighbourhoods the Laplacian stencil reads. That is why the sampling
    # below is dense in CELLS rather than a fixed count -- a ring with a hole in it
    # would let the outside drag the fill down through the gap.
    is_dirichlet = np.zeros((res_y, res_x), dtype=bool)
    h = np.zeros((res_y, res_x), dtype=np.float64)

    k = len(bx)
    perimeter = float(np.sum(np.hypot(np.diff(bx, append=bx[0]), np.diff(by, append=by[0]))))
    cell = min(dx, dy)
    n_polyline_samples = max(int(perimeter / (0.5 * cell)) + 1, k * 10, 500)
    t_samples = np.linspace(0.0, float(k), n_polyline_samples, endpoint=False)
    seg_idx = np.floor(t_samples).astype(int) % k
    seg_frac = (t_samples - np.floor(t_samples))[:, None]

    P1 = np.column_stack([bx[seg_idx], by[seg_idx], bz[seg_idx]])
    P2 = np.column_stack([bx[(seg_idx + 1) % k], by[(seg_idx + 1) % k], bz[(seg_idx + 1) % k]])
    P_sampled = P1 + seg_frac * (P2 - P1)

    ix_b = np.clip(np.round((P_sampled[:, 0] - min_x) / dx).astype(int), 0, res_x - 1)
    iy_b = np.clip(np.round((P_sampled[:, 1] - min_y) / dy).astype(int), 0, res_y - 1)
    for iy, ix, z_val in zip(iy_b, ix_b, P_sampled[:, 2]):
        is_dirichlet[iy, ix] = True
        h[iy, ix] = z_val

    control_pts = []
    if control_grid:
        for p in control_grid:
            p_vec = FreeCAD.Vector(*p) if not isinstance(p, FreeCAD.Vector) else p
            lp = m_inv.multVec(p_vec) if hasattr(curve_plane, "inverse") else p_vec
            ix_c = int(round((lp.x - min_x) / dx))
            iy_c = int(round((lp.y - min_y) / dy))
            if 0 <= ix_c < res_x and 0 <= iy_c < res_y:
                is_dirichlet[iy_c, ix_c] = True
                h[iy_c, ix_c] = lp.z
                control_pts.append((lp.x, lp.y, lp.z))

    # 3. Nearest boundary fill as initial seed
    from scipy.interpolate import griddata
    grid_y_mesh, grid_x_mesh = np.mgrid[min_y:max_y:complex(0, res_y), min_x:max_x:complex(0, res_x)]
    boundary_y = min_y + iy_b * dy
    boundary_x = min_x + ix_b * dx
    h_seed = griddata((boundary_y, boundary_x), P_sampled[:, 2], (grid_y_mesh, grid_x_mesh), method='nearest')
    h[~is_dirichlet] = h_seed[~is_dirichlet]

    # Red-Black SOR (Successive Over-Relaxation) for fast O(N) convergence.
    # Every unpinned cell relaxes, outside the curve as well as inside it. The exterior
    # used to keep its frozen nearest-boundary seed, which is piecewise constant: its
    # Voronoi cell walls are creases in h, and the rim of the rendered cap reads a band
    # of them wherever the exact-Bezier wall runs outside the rasterized interior mask.
    # Interior and exterior stay independent regardless -- the pinned boundary ring
    # separates them.
    grid_y_idx, grid_x_idx = np.indices((res_y, res_x))
    free = ~is_dirichlet
    red = ((grid_y_idx + grid_x_idx) % 2 == 0) & free
    black = ((grid_y_idx + grid_x_idx) % 2 == 1) & free

    # Interior slice masks (excluding 1-cell border)
    red_int = red[1:-1, 1:-1]
    black_int = black[1:-1, 1:-1]

    extent = max(max_x - min_x, max_y - min_y)
    tolerance = 1e-4 * extent
    omega = 1.85  # Optimal SOR parameter for N ~ 128-256

    n_sweeps = 0
    delta = 0.0
    for sweep in range(500):
        n_sweeps += 1
        h_old = h.copy()

        # Red pass
        avg_red = 0.25 * (h[:-2, 1:-1] + h[2:, 1:-1] + h[1:-1, :-2] + h[1:-1, 2:])
        h[1:-1, 1:-1][red_int] += omega * (avg_red[red_int] - h[1:-1, 1:-1][red_int])

        # Black pass
        avg_black = 0.25 * (h[:-2, 1:-1] + h[2:, 1:-1] + h[1:-1, :-2] + h[1:-1, 2:])
        h[1:-1, 1:-1][black_int] += omega * (avg_black[black_int] - h[1:-1, 1:-1][black_int])

        delta = float(np.max(np.abs(h[free] - h_old[free]))) if np.any(free) else 0.0
        if delta < tolerance:
            break

    fld_logger.debug(f"solve_boundary_height_field: relaxed grid in {n_sweeps} sweeps (max delta {delta:.6f})")

    t = float(np.clip(tension, 0.0, 1.0))
    if t < 1.0:
        plate = _thin_plate_field(P_sampled, control_pts, grid_x_mesh, grid_y_mesh)
        if plate is not None:
            h = t * h + (1.0 - t) * plate
            fld_logger.debug(f"solve_boundary_height_field: blended thin plate at tension {t:.2f}")

    return h.astype(np.float64), (min_x, max_x, min_y, max_y)


class SdfCurveFillExtrusionField(SdfField):
    """
    Extrudes a 3D surface patch along the normal of its boundary plane.
    Delegates to SdfPrismExtrusionField with SdfProfilePrismField walls and heightmap/plane caps.
    """

    def __init__(self, source_surface, height: float = 0.1, placement: FreeCAD.Placement = None):
        super().__init__()
        self.source_surface = source_surface
        self._height = float(height)
        self.placement = placement
        self.inv_matrix = self._compute_inv_matrix(self.placement)

        # Retrieve boundary curve
        self.source_curve = None
        self.profile = None
        self.curve_plane = None

        if self.source_surface is not None:
            if hasattr(self.source_surface, "SourceCurve") and self.source_surface.SourceCurve is not None:
                self.source_curve = self.source_surface.SourceCurve
            else:
                self.source_curve = self.source_surface

        if self.source_curve is not None:
            from freecad.fields.core.sdf.curve_sampler import (
                sample_curve_world_pts, compute_best_fit_placement,
                extract_bezier_segments_in_placement,
            )
            from freecad.fields.core.sdf.sdf2d.bezier_curve import Sdf2dBezierCurve

            try:
                world_pts = sample_curve_world_pts(self.source_curve, n_samples=64)
            except Exception:
                world_pts = [
                    self.source_curve.Placement.multVec(p)
                    for p in list(getattr(self.source_curve, "Points", []))
                ]

            best_fit = compute_best_fit_placement(world_pts, fallback_placement=getattr(self.source_curve, "Placement", FreeCAD.Placement()))
            self.curve_plane = best_fit if best_fit is not None else getattr(self.source_curve, "Placement", FreeCAD.Placement())

            # UX-008: settle the projection direction before anything is built in
            # this frame -- the walls, both caps and the picking mesh all read it.
            simple = simple_projection_placement(
                world_pts, self.curve_plane,
                label=getattr(self.source_curve, "Name", None))
            self.projection_is_simple = simple is not None
            if simple is not None:
                self.curve_plane = simple

            segs = extract_bezier_segments_in_placement(self.source_curve, self.curve_plane)
            if segs:
                self.profile = Sdf2dBezierCurve(segs)

        if self.curve_plane is None:
            self.curve_plane = FreeCAD.Placement()

        # Build orthonormal frame for prism
        rot = self.curve_plane.Rotation
        v1 = rot.multVec(FreeCAD.Vector(1, 0, 0))
        v2 = rot.multVec(FreeCAD.Vector(0, 1, 0))
        v3 = rot.multVec(FreeCAD.Vector(0, 0, 1))
        origin = np.array([self.curve_plane.Base.x, self.curve_plane.Base.y, self.curve_plane.Base.z], dtype=np.float64)
        e1 = np.array([v1.x, v1.y, v1.z], dtype=np.float64)
        e2 = np.array([v2.x, v2.y, v2.z], dtype=np.float64)
        axis = np.array([v3.x, v3.y, v3.z], dtype=np.float64)

        walls = SdfProfilePrismField(self.profile, origin, e1, e2)

        # Check planar fast path (EX-009)
        m_inv = self.curve_plane.inverse()
        if self.source_curve is not None:
            try:
                from freecad.fields.core.sdf.curve_sampler import sample_curve_world_pts
                pts_w = sample_curve_world_pts(self.source_curve, n_samples=64)
            except Exception:
                pts_w = [self.source_curve.Placement.multVec(p) for p in list(getattr(self.source_curve, "Points", []))]
        else:
            pts_w = []

        lp_pts = [m_inv.multVec(pt) for pt in pts_w] if pts_w else []
        from freecad.fields.core.objects.fld_curve_fill_geometry import fill_is_planar
        is_planar = fill_is_planar(lp_pts)

        # UX-008: a knotted boundary is not a height field on any plane, so there
        # is nothing to solve. `simple_projection_placement` has already named the
        # object in the log; take the planar fill rather than a flattened patch.
        if not getattr(self, "projection_is_simple", True):
            is_planar = True

        self.is_planar = is_planar
        self.heightmap_path = ""
        self.hmap_bounds = None
        self.hmap_z_range = None

        grid = bounds = None
        if not is_planar:
            proxy = getattr(self.source_surface, "Proxy", None) if self.source_surface is not None else None
            if proxy and hasattr(proxy, "get_or_solve_height_field"):
                # Already returns (None, None, True) when the solve had no answer.
                grid, bounds, _ = proxy.get_or_solve_height_field(self.source_surface)
            else:
                control_grid = getattr(self.source_surface, "ControlGrid", None) if self.source_surface is not None else None
                tension = float(getattr(self.source_surface, "Tension", 1.0)) if self.source_surface is not None else 1.0
                try:
                    grid, bounds = solve_boundary_height_field(
                        self.source_curve, self.curve_plane,
                        control_grid=control_grid, tension=tension,
                    )
                    fld_logger.info(f"SDF face: solved height field grid {grid.shape}")
                except ValueError as e:
                    # UX-008 step 3: name it and take the planar fill.
                    fld_logger.warn(f"SDF face: no height field, using the planar fill -- {e}")

            # UX-008: no grid means no cap to build. Falling through with grid=None
            # would hand SdfHeightmapCap a None array and fail far from the cause.
            if grid is None or bounds is None:
                is_planar = True
                self.is_planar = True

        if is_planar:
            fld_logger.info("SDF face: planar fast path")
            bottom = SdfPlaneCap(origin, -axis, offset=0.0)
            top = SdfPlaneCap(origin, axis, offset=self._height)
            h_lo, h_hi = 0.0, 0.0
        else:
            self.hmap_bounds = bounds
            bottom = SdfHeightmapCap(grid, bounds, origin, e1, e2, axis=axis, offset=0.0, sign=-1.0)
            top = SdfHeightmapCap(grid, bounds, origin, e1, e2, axis=axis, offset=self._height, sign=1.0)
            h_lo, h_hi = float(np.min(grid)), float(np.max(grid))

        # Kept for bounding_box: the frame the caps live in, plus the range the height
        # field spans along the axis.
        self._frame = (origin, e1, e2, axis)
        self._h_range = (h_lo, h_hi)
        self._top_cap = top

        self._prism = SdfPrismExtrusionField(walls, bottom, top, exact_corner=False)

    @property
    def height(self):
        return self._height

    @height.setter
    def height(self, value):
        """Move the top cap, not just the number the bounding box is derived from.

        The caps are built once, with the top one carrying `offset=height`; a plain
        attribute here left the solid frozen at whatever height it was constructed with
        while `bounding_box` tracked the drag. Shrinking the box below the geometry is
        what produced the stream of "bounding box might be too tight ... inside solid"
        warnings on every extrude drag, and the culled render that goes with them.
        """
        self._height = float(value)
        self._top_cap.offset = self._height

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self._prism.evaluate(point)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return self._prism.evaluate_grid(points)

    def to_glsl(self, ctx, point_var: str = "p") -> str:
        return self._prism.to_glsl(ctx, point_var)

    def bounding_box(self):
        """Tight world AABB of the swept patch.

        Must be computed here rather than delegated: neither the infinite prism nor the
        caps is bounded alone, so `SdfPrismExtrusionField.bounding_box` can only fall
        back to a 2000mm cube, which turns off render-AABB culling and bloats any octree
        built over this field.
        """
        if self.profile is None or not hasattr(self.profile, "bbox_2d"):
            return self._prism.bounding_box()

        x0, y0, x1, y1 = self.profile.bbox_2d()
        origin, e1, e2, axis = self._frame
        h_lo, h_hi = self._h_range
        # The solid lies between h(x, y) and h(x, y) + height, so along the axis it spans
        # [min h, max h + height]; ordering survives a negative height.
        z_lo = min(h_lo, h_lo + self.height)
        z_hi = max(h_hi, h_hi + self.height)

        corners = np.array([
            origin + e1 * x + e2 * y + axis * z
            for x in (x0, x1) for y in (y0, y1) for z in (z_lo, z_hi)
        ], dtype=np.float64)
        mn = corners.min(axis=0)
        mx = corners.max(axis=0)
        return (FreeCAD.Vector(*mn), FreeCAD.Vector(*mx))

    def lipschitz(self) -> float:
        return self._prism.lipschitz()
