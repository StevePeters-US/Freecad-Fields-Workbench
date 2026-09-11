# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Curve-fill / patch-mesh geometry helpers.

Split out of fld_object_proxy.py (CR-061) -- the geometry-math half of that module,
unrelated to Proxy lifecycle plumbing: fitting a height field over a curve's best-fit
plane, meshing it as a disk for picking/preview, and extracting Coons-patch corner/handle
data from a ShapeType=="surface" object or a pool of boundary curves. `FldObjectProxy`
itself still calls most of these (its `build_shape()`/`_planar_extension_shape()`), so
this module has no dependency on it -- only the reverse.
"""
import numpy as np
import FreeCAD

from freecad.fields.core import fld_logger


def _triangles_to_preview_shape(flat_verts, flat_idx):
    """Convert Coin3D-format triangle arrays to a preview Part.Shape."""
    import Mesh
    import Part
    if len(flat_idx) == 0:
        return Part.Shape()

    # Extract triangle vertex indices (skip -1 sentinels)
    idx = flat_idx.reshape(-1, 4)[:, :3]  # (N_tris, 3)

    # Vectorized lookup of triangle vertices in C
    tri_verts = flat_verts[idx]  # shape (N_tris, 3, 3)

    mesh = Mesh.Mesh(tri_verts.tolist())
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, 0.1)
    return shape


def _curve_plane_and_local_points(source_curve, n_samples=64):
    """(best-fit plane, boundary points in that plane's frame) for a closed curve.

    Both the height-field solve and the picking mesh need exactly this, and neither
    the curve sampling nor the PCA fit is free, so it lives in one place rather than
    being repeated per rebuild.
    """
    if source_curve is None:
        return FreeCAD.Placement(), []

    from freecad.fields.core.sdf.curve_sampler import sample_curve_world_pts, compute_best_fit_placement
    try:
        world_pts = sample_curve_world_pts(source_curve, n_samples=n_samples)
    except Exception as e:
        from freecad.fields.core import fld_logger
        fld_logger.warn(
            f"sample_curve_world_pts failed on {getattr(source_curve, 'Label', '?')} "
            f"({e}); falling back to raw control points, which do not lie on the "
            f"curve -- the fitted plane will be approximate."
        )
        world_pts = [source_curve.Placement.multVec(p)
                     for p in list(getattr(source_curve, "Points", []))]

    fallback = getattr(source_curve, "Placement", FreeCAD.Placement())
    best_fit = compute_best_fit_placement(world_pts, fallback_placement=fallback)
    plane = best_fit if best_fit is not None else fallback

    # UX-008: the best-fit plane minimises out-of-plane deviation, which is not the
    # same as being a projection the boundary is simple on. Settle that here, once,
    # because this plane is what the height solve, the disk mesh and
    # `_to_local_frame` all work in -- they must not disagree about it.
    from freecad.fields.core.sdf.sdf_curve_fill_extrusion import simple_projection_placement
    simple = simple_projection_placement(
        world_pts, plane, label=getattr(source_curve, "Name", None))
    if simple is not None:
        plane = simple

    m_inv = plane.inverse()
    return plane, [m_inv.multVec(p) for p in world_pts]


# How far off its own best-fit plane a boundary may sit and still be filled as flat,
# as a fraction of the boundary's in-plane extent. ~7um on a 70mm curve.
PLANARITY_REL_TOL = 1e-4


def fill_is_planar(lp_pts):
    """Does this boundary's fill take the planar fast path?

    One answer, because three callers need it: the proxy's `get_or_solve_height_field`,
    the SDF extrusion's choice between an exact plane cap and a heightmap cap, and the
    face task panel, which greys Tension out on exactly the curves whose fill ignores
    it. The first two were carrying their own copy of the threshold. A fourth copy in
    the panel would be the one that drifts, and a control greyed out while the solver
    still reads it -- or live while it does not -- is worse than never greying it.

    `lp_pts` are the boundary points in the frame of their own best-fit plane, so z IS
    the out-of-plane deviation. An empty boundary is planar; there is nothing to be out
    of plane.
    """
    if not lp_pts:
        return True
    xs = [p.x for p in lp_pts]
    ys = [p.y for p in lp_pts]
    extent = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    return max(abs(p.z) for p in lp_pts) < (PLANARITY_REL_TOL * extent)


def curve_fill_is_planar(source_curve):
    """`fill_is_planar` for a caller holding only the curve -- i.e. the task panel.

    Returns False if the curve cannot be sampled or fitted: the panel uses this to
    decide whether to grey a control out, and leaving a live control enabled is the
    harmless failure.
    """
    if source_curve is None:
        return True
    try:
        _, lp_pts = _curve_plane_and_local_points(source_curve)
    except Exception as e:
        fld_logger.debug(f"curve_fill_is_planar: no plane for "
                         f"{getattr(source_curve, 'Label', '?')}: {e}")
        return False
    return fill_is_planar(lp_pts)


def _disk_mesh_over_height_field(lp_pts, height_of, u_res, v_res):
    """Triangulate the curve's interior as a disk: a centre point plus v_res rings.

    The outer ring is the curve itself, so the mesh is bounded by the curve exactly.
    Sampling the height field on its own rectangular grid instead is what produced a
    sheet spilling far past the boundary: that grid carries a 10% margin and fills
    everything outside the curve with nearest-boundary values, none of which is part
    of the surface.

    `height_of(x, y)` returns local z for arrays of interior coordinates; the outer
    ring ignores it and uses the curve's own z, so the boundary is exact rather than
    interpolated.

    Returns (verts, tris, u_res) in the plane frame -- float arrays ready for
    `_triangles_to_preview_shape`, plus the angular count actually used, which the
    caller needs to index the rings.
    """
    bx = np.array([p.x for p in lp_pts], dtype=np.float64)
    by = np.array([p.y for p in lp_pts], dtype=np.float64)
    bz = np.array([p.z for p in lp_pts], dtype=np.float64)

    # The curve sampler closes the polyline by repeating the first point. Left in, that
    # duplicate becomes a zero-length ring edge and a strip of degenerate triangles all
    # the way from the boundary to the centre.
    if len(bx) > 3 and abs(bx[-1] - bx[0]) + abs(by[-1] - by[0]) + abs(bz[-1] - bz[0]) < 1e-9:
        bx, by, bz = bx[:-1], by[:-1], bz[:-1]
    n = len(bx)

    # The outer ring is the visible silhouette, so it is never coarser than the curve
    # samples: a 12-gon through a lumpy boundary cuts the lobes off, which reads as the
    # patch not reaching its own curve. The requested count only raises it.
    u_res = max(int(u_res), n)

    # Resample the polyline to u_res points, evenly in index space.
    t = np.linspace(0.0, n, u_res, endpoint=False)
    i0 = np.floor(t).astype(int) % n
    i1 = (i0 + 1) % n
    f = t - np.floor(t)
    ring_x = bx[i0] * (1.0 - f) + bx[i1] * f
    ring_y = by[i0] * (1.0 - f) + by[i1] * f
    ring_z = bz[i0] * (1.0 - f) + bz[i1] * f

    # Orient the ring counter-clockwise so the fan and quads below always wind the
    # same way; a clockwise curve would otherwise flip every normal on the patch.
    if np.sum(ring_x * np.roll(ring_y, -1) - np.roll(ring_x, -1) * ring_y) < 0.0:
        ring_x, ring_y, ring_z = ring_x[::-1], ring_y[::-1], ring_z[::-1]

    cx, cy = float(np.mean(ring_x)), float(np.mean(ring_y))
    cz = float(height_of(np.array([cx]), np.array([cy]))[0])

    verts = [[cx, cy, cz]]
    for j in range(1, v_res + 1):
        frac = j / float(v_res)
        rx = cx + (ring_x - cx) * frac
        ry = cy + (ring_y - cy) * frac
        rz = ring_z if j == v_res else height_of(rx, ry)
        verts.extend(np.column_stack([rx, ry, rz]).tolist())

    tris = []
    for i in range(u_res):  # centre fan
        tris.append([0, 1 + i, 1 + (i + 1) % u_res, -1])
    for j in range(v_res - 1):  # ring j -> ring j+1
        a, b = 1 + j * u_res, 1 + (j + 1) * u_res
        for i in range(u_res):
            k = (i + 1) % u_res
            tris.append([a + i, b + i, a + k, -1])
            tris.append([a + k, b + i, b + k, -1])

    return (np.array(verts, dtype=np.float32),
            np.array(tris, dtype=np.int32),
            u_res)


def _patch_mesh_resolution(fp):
    """(u_res, v_res) for the patch's display/picking mesh.

    `makeShapeFromMesh` turns every triangle into its own planar face, so FreeCAD shades
    each one with a single normal: the patch reads as facets no matter how smooth the
    height field underneath it is, and the only lever a triangle mesh has is how small
    the facets are. The angular direction is already fine -- `_disk_mesh_over_height_field`
    raises u_res to the curve's own sample count -- so the visible banding is radial,
    where the default of 8 rings put a 12-degree kink between neighbours on a curved
    patch. 16 halves that for ~2x the triangles, which is a few ms against a height-field
    solve measured in hundreds.

    This is mitigation, not a fix. The fix is one smooth face instead of a triangle soup;
    see the SDF-face smoothing task (UX-007).
    """
    return (max(12, int(getattr(fp, "UCount", 0) or 12)),
            max(3, int(getattr(fp, "VCount", 0) or 16)))


def _wire_winding_normal(wire, n_samples=96):
    """Right-hand-rule normal of a closed planar wire, by Newell's method.

    Newell rather than a cross product of two edges: it averages over the whole loop,
    so a concave corner or a near-collinear pair cannot pick the normal on its own.
    """
    import Part  # noqa: F401  (kept local, as everywhere else in this module)
    try:
        pts = wire.discretize(Number=int(n_samples))
    except Exception as e:
        fld_logger.debug(f"_wire_winding_normal: cannot discretize wire: {e}")
        return None
    if len(pts) < 3:
        return None
    n = FreeCAD.Vector(0.0, 0.0, 0.0)
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        n.x += (p.y - q.y) * (p.z + q.z)
        n.y += (p.z - q.z) * (p.x + q.x)
        n.z += (p.x - q.x) * (p.y + q.y)
    if n.Length <= 1e-12:
        return None
    return n.normalize()


def _planar_face_from_wire(wire):
    """A flat fill, built flat: `Part.Face` gives a true `Part.Plane`.

    `Part.makeFilledFace` was here, and it is the wrong primitive for a flat boundary.
    It solves a plate energy minimisation and hands back a `BSplineSurface` whose
    flatness is only ever as good as that solve converged. Measured against exactly
    planar curves it did come back flat to ~1e-14 -- but it amplified any residual
    non-planarity in the boundary by about 11x into the interior, and it cost 80-95 ms
    on a plain square or octagon against 0.1 ms here, paid again on every recompute.
    A `Plane` cannot be non-flat; it is not a tighter tolerance, it is no tolerance.

    It was also building the fill inside-out. `makeFilledFace` returned a -Z normal for
    BOTH windings, so every counter-clockwise curve -- the ordinary case -- got a face
    whose normal opposed its own boundary.

    The plate stays as the fallback because OCC's planarity test is far tighter than
    this workbench's `is_planar`, which admits a boundary up to 1e-4*extent off-plane:
    0.67um of deviation on a 5mm curve is already enough for `Part.Face` to refuse,
    while `get_or_solve_height_field` still calls it planar and routes it here. Without
    the fallback such a curve would get no face at all -- which is the bug this whole
    branch was just fixed for.

    Returns the face, or None if neither route produced one.
    """
    import Part
    try:
        return Part.Face(wire)
    except Exception as e:
        fld_logger.debug(f"Planar fill: not planar enough for Part.Face ({e}); "
                         "falling back to the filled-plate face.")

    try:
        face = Part.makeFilledFace(wire.OrderedEdges)
    except Exception as e:
        fld_logger.debug(f"Planar fill: makeFilledFace failed: {e}")
        return None
    if face is None or face.isNull():
        return None

    # Match Part.Face's convention -- the normal follows the winding -- so the fill does
    # not flip over when a curve crosses the planarity threshold by half a micron.
    try:
        want = _wire_winding_normal(wire)
        f0 = face.Faces[0] if face.Faces else None
        if want is not None and f0 is not None:
            u0, u1, v0, v1 = f0.ParameterRange
            if f0.normalAt(0.5 * (u0 + u1), 0.5 * (v0 + v1)).dot(want) < 0.0:
                face.reverse()
    except Exception as e:
        fld_logger.debug(f"Planar fill: could not orient the plate face: {e}")
    return face


def _fit_bspline_surface_to_height_field(grid, bounds, lp_pts, curve_plane, fp_placement, tol=0.05):
    """Fit a smooth Part.BSplineSurface over the height field and trim with boundary curve (UX-007).

    Avoids polar grid defects (degenerate apex row, unclosed U seam, and C2 approximation
    failures in OCC) by interpolating a regular tensor-product B-spline surface over
    the rectangular (x, y) height field domain and trimming with the closed 2D boundary
    polygon extruded along the local Z axis.

    Returns a trimmed Part.Shape (transformed into fp_placement frame), or None if fitting fails.
    """
    import Part
    if getattr(Part, "is_mock", False):
        return None

    if grid is None or bounds is None or len(lp_pts) < 3:
        return None

    try:
        min_x, max_x, min_y, max_y = bounds
        res_y, res_x = grid.shape

        # Downsample grid if very large (e.g. 256x256) to a fast fitting resolution (<= 32x32)
        fit_res = min(32, max(res_x, res_y))
        if res_x != fit_res or res_y != fit_res:
            from scipy.interpolate import RegularGridInterpolator
            interp = RegularGridInterpolator(
                (np.linspace(min_y, max_y, res_y), np.linspace(min_x, max_x, res_x)),
                grid, bounds_error=False, fill_value=0.0
            )
            f_xs = np.linspace(min_x, max_x, fit_res)
            f_ys = np.linspace(min_y, max_y, fit_res)
            f_X, f_Y = np.meshgrid(f_xs, f_ys)
            f_Z = interp(np.column_stack([np.ravel(f_Y), np.ravel(f_X)])).reshape(fit_res, fit_res)
            dx = (max_x - min_x) / float(fit_res - 1)
            dy = (max_y - min_y) / float(fit_res - 1)
            # In OpenCascade interpolate(zpoints, X0, dX, Y0, dY):
            # Axis 0 of zpoints corresponds to X (U parameter) and axis 1 corresponds to Y (V parameter).
            z_occ = f_Z.T.tolist()
        else:
            dx = (max_x - min_x) / float(res_x - 1)
            dy = (max_y - min_y) / float(res_y - 1)
            z_occ = grid.T.tolist()

        s = Part.BSplineSurface()
        s.interpolate(z_occ, float(min_x), float(dx), float(min_y), float(dy))
        face_untrimmed = s.toShape()
        if face_untrimmed.isNull() or not face_untrimmed.isValid():
            return None

        # Build 2D boundary polygon in curve_plane (XY) frame
        poly_2d = [FreeCAD.Vector(float(p.x), float(p.y), 0.0) for p in lp_pts]
        if (poly_2d[0] - poly_2d[-1]).Length > 1e-6:
            poly_2d.append(poly_2d[0])

        wire_2d = Part.makePolygon(poly_2d)
        face_2d = Part.Face(wire_2d)
        if face_2d.isNull() or not face_2d.isValid():
            return None

        # Extrude along local Z to form trimming prism
        min_z = float(np.min(grid))
        max_z = float(np.max(grid))
        z_span = (max_z - min_z) + 40.0
        face_2d.translate(FreeCAD.Vector(0.0, 0.0, min_z - 20.0))
        prism = face_2d.extrude(FreeCAD.Vector(0.0, 0.0, z_span))
        if prism.isNull() or not prism.isValid():
            return None

        # Trim untrimmed face by the boundary prism
        trimmed = face_untrimmed.common(prism)
        if trimmed.isNull() or not trimmed.isValid() or len(trimmed.Faces) == 0:
            return None

        # Take the resulting trimmed face
        result_face = trimmed.Faces[0].copy() if len(trimmed.Faces) == 1 else trimmed.copy()

        # Transform shape from curve_plane frame into fp_placement frame.
        #
        # This must BAKE the transform into the geometry. `transformShape` is free to
        # record a rigid transform as the shape's Location instead, which surfaces as a
        # non-identity `Placement` -- and the caller hands this face to `fp.Shape`, which
        # syncs the shape's placement with the object's own and so drops it. The patch
        # then sits in the curve plane's local frame instead of the object's: correctly
        # shaped, rigidly displaced from the curve it was fitted to, by exactly the
        # curve plane's offset and tilt. `transformGeometry` moves the poles themselves,
        # so the result carries no placement to lose.
        if curve_plane is not None and fp_placement is not None:
            M = fp_placement.inverse().multiply(curve_plane)
            result_face = result_face.transformGeometry(M.toMatrix())

        return result_face
    except Exception as e:
        fld_logger.warn(f"_fit_bspline_surface_to_height_field failed: {e}")
        return None


def _to_local_frame(verts, from_placement, to_placement):
    """(N,3) points carried from one placement's frame into another's."""
    inv = to_placement.inverse()
    out = np.empty((len(verts), 3), dtype=np.float32)
    for i, v in enumerate(verts):
        p = inv.multVec(from_placement.multVec(FreeCAD.Vector(float(v[0]), float(v[1]), float(v[2]))))
        out[i] = (p.x, p.y, p.z)
    return out


def _surface_to_patch_spec(patch):
    """Extract corner and boundary handles from a ShapeType=='surface' object.

    Fidelity note: A cage face is a cubic Coons patch (4 corners + 2 cubic handles per edge).
    This extracts the 4 corners and boundary curve handles at 1/3 and 2/3 parameter coordinates.
    """
    import numpy as np
    import FreeCAD

    u_count = getattr(patch, "UCount", 0)
    v_count = getattr(patch, "VCount", 0)
    grid = getattr(patch, "ControlGrid", None)
    if u_count <= 1 or v_count <= 1 or not grid:
        return None

    # Get placement
    gpl = patch.getGlobalPlacement() if hasattr(patch, "getGlobalPlacement") else patch.Placement
    def _to_world(p):
        return gpl.multVec(p) if gpl else p

    # Corner points
    A = _to_world(grid[0])
    B = _to_world(grid[u_count - 1])
    C = _to_world(grid[(v_count - 1) * u_count + u_count - 1])
    D = _to_world(grid[(v_count - 1) * u_count])

    corners = np.array([
        [A.x, A.y, A.z],
        [B.x, B.y, B.z],
        [C.x, C.y, C.z],
        [D.x, D.y, D.z]
    ], dtype=np.float64)

    # Extract boundary points
    i1 = int(round((u_count - 1) / 3.0))
    i2 = int(round(2 * (u_count - 1) / 3.0))
    j1 = int(round((v_count - 1) / 3.0))
    j2 = int(round(2 * (v_count - 1) / 3.0))

    C0 = [grid[0], grid[i1], grid[i2], grid[u_count-1]]
    C1 = [grid[(v_count-1)*u_count], grid[(v_count-1)*u_count + i1], grid[(v_count-1)*u_count + i2], grid[v_count*u_count-1]]
    D0 = [grid[0], grid[j1*u_count], grid[j2*u_count], grid[(v_count-1)*u_count]]
    D1 = [grid[u_count-1], grid[(j1+1)*u_count - 1], grid[(j2+1)*u_count - 1], grid[v_count*u_count-1]]

    # Convert to world coordinates
    C0_w = [_to_world(p) for p in C0]
    C1_w = [_to_world(p) for p in C1]
    D0_w = [_to_world(p) for p in D0]
    D1_w = [_to_world(p) for p in D1]

    # Map boundary curve segments to handles for quad edges:
    # Edge 0: A -> B. Handles: C0_w[1], C0_w[2]
    # Edge 1: B -> C. Handles: D1_w[1], D1_w[2]
    # Edge 2: C -> D. Handles: C1_w[2], C1_w[1] (reversed because C1 goes D -> C)
    # Edge 3: D -> A. Handles: D0_w[2], D0_w[1] (reversed because D0 goes A -> D)
    handles = np.array([
        [C0_w[1].x, C0_w[1].y, C0_w[1].z],
        [C0_w[2].x, C0_w[2].y, C0_w[2].z],

        [D1_w[1].x, D1_w[1].y, D1_w[1].z],
        [D1_w[2].x, D1_w[2].y, D1_w[2].z],

        [C1_w[2].x, C1_w[2].y, C1_w[2].z],
        [C1_w[1].x, C1_w[1].y, C1_w[1].z],

        [D0_w[2].x, D0_w[2].y, D0_w[2].z],
        [D0_w[1].x, D0_w[1].y, D0_w[1].z]
    ], dtype=np.float64)

    return {"corners": corners, "handles": handles}


def _chain_curves(curves, tol=1e-3):
    """Find ordering and directions to chain a list of curves into a closed loop."""
    import itertools
    n = len(curves)
    if n < 2 or n > 4:
        return None
    for p in itertools.permutations(curves):
        for dirs in itertools.product([False, True], repeat=n):
            valid = True
            for i in range(n):
                c_curr = p[i]
                c_next = p[(i + 1) % n]

                # If curve has placement, we must transform points to check connectivity
                pl_curr = c_curr.Placement
                pl_next = c_next.Placement

                v_curr_end = c_curr.value(0.0) if dirs[i] else c_curr.value(1.0)
                if pl_curr:
                    v_curr_end = pl_curr.multVec(v_curr_end)

                v_next_start = c_next.value(1.0) if dirs[(i+1)%n] else c_next.value(0.0)
                if pl_next:
                    v_next_start = pl_next.multVec(v_next_start)

                if (v_curr_end - v_next_start).Length > tol:
                    valid = False
                    break
            if valid:
                return list(zip(p, dirs))
    return None


def _find_curve_loops(curves, tol=1e-3):
    """Find all closed loops of 2-4 open curves in a pool of curves."""
    import itertools
    pool = list(curves)
    loops = []

    while len(pool) >= 2:
        found_loop = False
        for k in [2, 3, 4]:
            if k > len(pool):
                continue
            for subset in itertools.combinations(pool, k):
                chained = _chain_curves(subset, tol=tol)
                if chained:
                    loops.append(chained)
                    for c, _ in chained:
                        pool.remove(c)
                    found_loop = True
                    break
            if found_loop:
                break
        if not found_loop:
            break
    return loops


