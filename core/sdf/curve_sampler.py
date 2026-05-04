import math
import FreeCAD


def sample_closed_curve_3d(obj, n_samples=256):
    """
    Sample a closed curve DMObject into local-space (x, y, z) tuples.
    Z is in working-plane local space — non-zero for 3D (non-planar) curves.
    """
    from core.dm_point import DMPoint
    from core.dm_curve import DMCurve

    pts   = list(getattr(obj, "Points", []))
    h_in  = list(getattr(obj, "HandleIn", []))
    h_out = list(getattr(obj, "HandleOut", []))

    dm_pts = []
    for i, p in enumerate(pts):
        hi = h_in[i]  if i < len(h_in)  else p
        ho = h_out[i] if i < len(h_out) else p
        dp = DMPoint(p)
        dp.handle_in  = hi if (hi - p).Length > 0.001 else None
        dp.handle_out = ho if (ho - p).Length > 0.001 else None
        dm_pts.append(dp)

    curve = DMCurve(points=dm_pts, is_closed=True)
    bs = curve.bspline
    if bs is None:
        raise ValueError("Could not build BSpline from curve object")

    sampled = bs.discretize(Number=n_samples)
    return [(v.x, v.y, v.z) for v in sampled]


def sample_curve_world_pts(obj, n_samples=256):
    """
    Sample a closed curve DMObject in WORLD space by applying obj.Placement.
    Returns list of FreeCAD.Vector in world coordinates.
    """
    from core.dm_point import DMPoint
    from core.dm_curve import DMCurve

    pts   = list(getattr(obj, "Points", []))
    h_in  = list(getattr(obj, "HandleIn", []))
    h_out = list(getattr(obj, "HandleOut", []))
    wp = obj.Placement

    dm_pts = []
    for i, p in enumerate(pts):
        hi = h_in[i]  if i < len(h_in)  else p
        ho = h_out[i] if i < len(h_out) else p
        dp = DMPoint(p)
        dp.handle_in  = hi if (hi - p).Length > 0.001 else None
        dp.handle_out = ho if (ho - p).Length > 0.001 else None
        dm_pts.append(dp)

    curve = DMCurve(points=dm_pts, is_closed=True)
    bs = curve.bspline
    if bs is None:
        raise ValueError("Could not build BSpline from curve object")

    sampled_local = bs.discretize(Number=n_samples)
    return [wp.multVec(v) for v in sampled_local]


def compute_best_fit_placement(pts_world, fallback_placement=None):
    """
    Compute a FreeCAD.Placement whose Z axis is the best-fit plane normal
    for the given world-space points, using the Newell method.

    The X/Y axes are chosen to be consistent with fallback_placement's X
    axis (projected onto the fit plane) when provided, or a standard
    perpendicular otherwise.

    Returns a FreeCAD.Placement with Base = centroid of points, or
    fallback_placement if the fit fails.
    """
    n = len(pts_world)
    if n < 3:
        return fallback_placement

    cx = sum(p.x for p in pts_world) / n
    cy = sum(p.y for p in pts_world) / n
    cz = sum(p.z for p in pts_world) / n
    centroid = FreeCAD.Vector(cx, cy, cz)

    # Newell's method: area-weighted polygon normal
    nx, ny, nz = 0.0, 0.0, 0.0
    for i in range(n):
        j = (i + 1) % n
        pi, pj = pts_world[i], pts_world[j]
        nx += (pi.y - pj.y) * (pi.z + pj.z)
        ny += (pi.z - pj.z) * (pi.x + pj.x)
        nz += (pi.x - pj.x) * (pi.y + pj.y)

    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-10:
        return fallback_placement

    normal = FreeCAD.Vector(nx / length, ny / length, nz / length)

    # Ensure normal is in the same hemisphere as the fallback Z axis
    if fallback_placement is not None:
        wp_z = fallback_placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        if normal.dot(wp_z) < 0:
            normal = FreeCAD.Vector(-normal.x, -normal.y, -normal.z)

    # Build X axis: project fallback X axis onto the fit plane, or use a default
    if fallback_placement is not None:
        wp_x = fallback_placement.Rotation.multVec(FreeCAD.Vector(1, 0, 0))
        dot_nx = wp_x.dot(normal)
        x_axis = FreeCAD.Vector(
            wp_x.x - normal.x * dot_nx,
            wp_x.y - normal.y * dot_nx,
            wp_x.z - normal.z * dot_nx,
        )
        if x_axis.Length < 1e-6:
            x_axis = None
    else:
        x_axis = None

    if x_axis is None:
        # Default: pick a vector not parallel to normal
        if abs(normal.z) < 0.9:
            candidate = FreeCAD.Vector(0, 0, 1)
        else:
            candidate = FreeCAD.Vector(1, 0, 0)
        x_axis = normal.cross(candidate)

    x_len = x_axis.Length
    if x_len < 1e-10:
        return fallback_placement
    x_axis = x_axis / x_len

    y_axis = normal.cross(x_axis)
    y_len = y_axis.Length
    if y_len < 1e-10:
        return fallback_placement
    y_axis = y_axis / y_len

    # Build 4x4 rotation+translation matrix (column-major rotation: columns = axes)
    m = FreeCAD.Matrix(
        x_axis.x, y_axis.x, normal.x, centroid.x,
        x_axis.y, y_axis.y, normal.y, centroid.y,
        x_axis.z, y_axis.z, normal.z, centroid.z,
        0.0,      0.0,      0.0,      1.0,
    )
    return FreeCAD.Placement(m)


def project_pts_to_2d(pts_world, placement):
    """
    Project world-space points onto the XY plane of the given placement.
    Returns list of (u, v) tuples in the placement's local space.
    """
    inv = placement.inverse()
    result = []
    for p in pts_world:
        local = inv.multVec(p)
        result.append((local.x, local.y))
    return result


def extract_bezier_segments_2d(obj):
    """
    Extract cubic Bezier segments from a closed curve object in 2D local space.
    Returns list of (p0, p1, p2, p3) tuples, each point as (x, y).
    Handles are absolute positions; missing/coincident handles fall back to straight-line CPs.
    """
    pts   = list(getattr(obj, "Points",    []))
    h_in  = list(getattr(obj, "HandleIn",  []))
    h_out = list(getattr(obj, "HandleOut", []))
    n = len(pts)
    segs = []
    for i in range(n):
        j = (i + 1) % n
        p0 = (pts[i].x, pts[i].y)
        p3 = (pts[j].x, pts[j].y)
        if i < len(h_out) and (h_out[i] - pts[i]).Length > 0.001:
            p1 = (h_out[i].x, h_out[i].y)
        else:
            p1 = (p0[0] + (p3[0] - p0[0]) / 3.0, p0[1] + (p3[1] - p0[1]) / 3.0)
        if j < len(h_in) and (h_in[j] - pts[j]).Length > 0.001:
            p2 = (h_in[j].x, h_in[j].y)
        else:
            p2 = (p0[0] + 2 * (p3[0] - p0[0]) / 3.0, p0[1] + 2 * (p3[1] - p0[1]) / 3.0)
        segs.append((p0, p1, p2, p3))
    return segs


def extract_bezier_segments_in_placement(obj, placement):
    """
    Extract Bezier control points from obj (stored in obj.Placement local space),
    convert to world space, then project into `placement`'s local XY.
    Returns list of (p0, p1, p2, p3) tuples, each point (x, y).
    """
    pts   = list(getattr(obj, "Points",    []))
    h_in  = list(getattr(obj, "HandleIn",  []))
    h_out = list(getattr(obj, "HandleOut", []))
    obj_pl = obj.Placement
    inv    = placement.inverse()

    def to_2d(p):
        world = obj_pl.multVec(p)
        local = inv.multVec(world)
        return (local.x, local.y)

    n = len(pts)
    segs = []
    for i in range(n):
        j = (i + 1) % n
        p0 = to_2d(pts[i])
        p3 = to_2d(pts[j])
        if i < len(h_out) and (h_out[i] - pts[i]).Length > 0.001:
            p1 = to_2d(h_out[i])
        else:
            p1 = (p0[0] + (p3[0] - p0[0]) / 3.0, p0[1] + (p3[1] - p0[1]) / 3.0)
        if j < len(h_in) and (h_in[j] - pts[j]).Length > 0.001:
            p2 = to_2d(h_in[j])
        else:
            p2 = (p0[0] + 2.0 * (p3[0] - p0[0]) / 3.0, p0[1] + 2.0 * (p3[1] - p0[1]) / 3.0)
        segs.append((p0, p1, p2, p3))
    return segs


def compute_inflection_limit(pts_3d, z_threshold=1.0):
    """
    Return the max valid extrusion half-height for a 3D closed curve, or None if planar.

    The limit is max(z_i) of sampled points in local working-plane space.
    Extruding past this point would push the solid's ceiling above the curve boundary.
    Returns None for planar curves (max Z < z_threshold mm).
    """
    z_max = max(p[2] for p in pts_3d)
    if z_max < z_threshold:
        return None
    return z_max
