"""
core/frep/sdf_slicer.py

Extracts smooth cross-section curves from F-Rep SDFs using marching squares.
Outputs DM-curve-compatible dicts for create_dm_object().
"""
import math
import numpy as np
import FreeCAD

# ── Marching squares edge table ──────────────────────────────────────────────
# For each of the 16 cell configurations, list of edge pairs to connect.
# Edges: 0=bottom, 1=right, 2=top, 3=left
_MS_EDGES = [
    [],             # 0000
    [(3, 0)],       # 0001
    [(0, 1)],       # 0010
    [(3, 1)],       # 0011
    [(1, 2)],       # 0100
    [(1, 0), (3, 2)],  # 0101 (ambiguous — use average)
    [(0, 2)],       # 0110
    [(3, 2)],       # 0111
    [(2, 3)],       # 1000
    [(2, 0)],       # 1001
    [(0, 1), (2, 3)],  # 1010 (ambiguous)
    [(2, 1)],       # 1011
    [(1, 3)],       # 1100
    [(1, 0)],       # 1101
    [(0, 3)],       # 1110
    [],             # 1111
]


def slice_sdf(field, origin, normal, resolution=1.0, extent=None):
    """
    Extract zero-crossing contours of an SDF field on a plane.

    Args:
        field:      Any SdfField subclass.
        origin:     FreeCAD.Vector — point on the slice plane.
        normal:     FreeCAD.Vector — plane normal (will be normalized).
        resolution: float — grid spacing in mm on the plane.
        extent:     float or None — half-size of the sampling grid. If None,
                    computed from field.bounding_box().

    Returns:
        list[list[FreeCAD.Vector]] — one list of ordered 3D points per contour.
        Closed contours have first == last point.
    """
    normal = FreeCAD.Vector(normal)
    normal.normalize()

    # Build orthonormal basis on the plane
    up = FreeCAD.Vector(0, 0, 1)
    if abs(normal.dot(up)) > 0.99:
        up = FreeCAD.Vector(1, 0, 0)
    u_axis = normal.cross(up)
    u_axis.normalize()
    v_axis = normal.cross(u_axis)
    v_axis.normalize()

    # Determine grid center and extent from field's bounding box
    mn, mx = field.bounding_box()
    center = (mn + mx) * 0.5
    
    # Project center onto the plane
    v = center - origin
    dist = v.dot(normal)
    projected_center = center - normal * dist
    
    if extent is None:
        diag = (mx - mn).Length
        extent = diag * 0.7 # slightly larger margin

    n = max(1, int(math.ceil(2 * extent / resolution)))
    half = extent

    # Sample SDF on the plane grid centered at projected_center
    us = np.linspace(-half, half, n + 1).astype(np.float32)
    vs = np.linspace(-half, half, n + 1).astype(np.float32)
    U, V = np.meshgrid(us, vs, indexing='ij')

    # Convert 2D grid to 3D world points
    pts_3d = np.zeros((U.size, 3), dtype=np.float32)
    pts_3d[:, 0] = projected_center.x + U.ravel() * u_axis.x + V.ravel() * v_axis.x
    pts_3d[:, 1] = projected_center.y + U.ravel() * u_axis.y + V.ravel() * v_axis.y
    pts_3d[:, 2] = projected_center.z + U.ravel() * u_axis.z + V.ravel() * v_axis.z

    vals = field.evaluate_grid(pts_3d).reshape(n + 1, n + 1)

    # ── Marching squares ──
    segments = []
    for i in range(n):
        for j in range(n):
            # Corner values: bottom-left, bottom-right, top-right, top-left
            v0 = vals[i, j]
            v1 = vals[i + 1, j]
            v2 = vals[i + 1, j + 1]
            v3 = vals[i, j + 1]

            idx = 0
            if v0 < 0: idx |= 1
            if v1 < 0: idx |= 2
            if v2 < 0: idx |= 4
            if v3 < 0: idx |= 8

            edges = _MS_EDGES[idx]
            if not edges:
                continue

            # Edge midpoints with linear interpolation
            corners_u = [us[i], us[i + 1], us[i + 1], us[i]]
            corners_v = [vs[j], vs[j], vs[j + 1], vs[j + 1]]
            corner_vals = [v0, v1, v2, v3]

            def edge_point(e):
                a = e
                b = (e + 1) % 4
                va = corner_vals[a]
                vb = corner_vals[b]
                denom = va - vb
                if abs(denom) < 1e-12:
                    frac = 0.5
                else:
                    frac = va / denom
                eu = corners_u[a] + frac * (corners_u[b] - corners_u[a])
                ev = corners_v[a] + frac * (corners_v[b] - corners_v[a])
                return (eu, ev)

            for e0, e1 in edges:
                p0 = edge_point(e0)
                p1 = edge_point(e1)
                segments.append((p0, p1))

    # ── Chain segments into contours ──
    contours_2d = _chain_segments(segments)

    # ── Convert to 3D ──
    contours_3d = []
    for contour in contours_2d:
        pts = []
        for (eu, ev) in contour:
            p = FreeCAD.Vector(
                origin.x + eu * u_axis.x + ev * v_axis.x,
                origin.y + eu * u_axis.y + ev * v_axis.y,
                origin.z + eu * u_axis.z + ev * v_axis.z,
            )
            pts.append(p)
        if pts:
            contours_3d.append(pts)

    return contours_3d


def _chain_segments(segments, tol=1e-6):
    """Chain unordered line segments into ordered polylines."""
    if not segments:
        return []

    remaining = list(segments)
    contours = []

    while remaining:
        seg = remaining.pop(0)
        chain = [seg[0], seg[1]]

        changed = True
        while changed:
            changed = False
            for k in range(len(remaining) - 1, -1, -1):
                s = remaining[k]
                d0_end = abs(chain[-1][0] - s[0][0]) + abs(chain[-1][1] - s[0][1])
                d1_end = abs(chain[-1][0] - s[1][0]) + abs(chain[-1][1] - s[1][1])
                d0_start = abs(chain[0][0] - s[1][0]) + abs(chain[0][1] - s[1][1])
                d1_start = abs(chain[0][0] - s[0][0]) + abs(chain[0][1] - s[0][1])

                if d0_end < tol:
                    chain.append(s[1])
                    remaining.pop(k)
                    changed = True
                elif d1_end < tol:
                    chain.append(s[0])
                    remaining.pop(k)
                    changed = True
                elif d0_start < tol:
                    chain.insert(0, s[0])
                    remaining.pop(k)
                    changed = True
                elif d1_start < tol:
                    chain.insert(0, s[1])
                    remaining.pop(k)
                    changed = True

        contours.append(chain)

    return contours


def fit_dm_curve(contour_points, closed=False, smooth_factor=0.33):
    """
    Fit a DM curve through a list of 3D points.

    Args:
        contour_points: list[FreeCAD.Vector] — ordered 3D points.
        closed:         bool — whether the contour is closed.
        smooth_factor:  float — handle length as fraction of chord.

    Returns:
        dict with keys: Points, HandleIn, HandleOut, Closed
        Compatible with create_dm_object(name, "curve", params=result).
    """
    pts = list(contour_points)

    # Remove near-duplicate last point if closed
    if closed and len(pts) > 2:
        if (pts[0] - pts[-1]).Length < 0.01:
            pts = pts[:-1]

    # Decimate: skip points that are too close together
    if len(pts) > 3:
        decimated = [pts[0]]
        avg_dist = (pts[0] - pts[-1]).Length / max(len(pts), 1)
        min_dist = max(0.1, avg_dist * 0.5)
        for p in pts[1:]:
            if (p - decimated[-1]).Length >= min_dist:
                decimated.append(p)
        pts = decimated

    n = len(pts)
    if n < 2:
        return {"Points": pts, "HandleIn": pts[:], "HandleOut": pts[:],
                "Closed": closed, "is_closed": closed}

    handles_in = []
    handles_out = []

    for i in range(n):
        prev_p = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        next_p = pts[(i + 1) % n] if (closed or i < n - 1) else pts[i]

        tangent = next_p - prev_p
        chord_prev = (pts[i] - prev_p).Length
        chord_next = (next_p - pts[i]).Length

        if tangent.Length > 1e-6:
            tangent.normalize()
        else:
            tangent = FreeCAD.Vector(1, 0, 0)

        handle_in = pts[i] - tangent * (chord_prev * smooth_factor)
        handle_out = pts[i] + tangent * (chord_next * smooth_factor)

        handles_in.append(handle_in)
        handles_out.append(handle_out)

    return {
        "Points": pts,
        "HandleIn": handles_in,
        "HandleOut": handles_out,
        "Closed": closed,
        "is_closed": closed,
    }
