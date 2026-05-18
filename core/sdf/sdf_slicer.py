"""
core/sdf/sdf_slicer.py

Extracts smooth cross-section curves from SDF SDFs using marching squares.
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


def slice_sdf(field, origin, normal, resolution=1.0, extent=None, octree_cache=None):
    """
    Extract zero-crossing contours of an SDF field on a plane.

    Args:
        field:        Any SdfField subclass.
        origin:       FreeCAD.Vector — point on the slice plane.
        normal:       FreeCAD.Vector — plane normal (will be normalized).
        resolution:   float — grid spacing in mm on the plane.
        extent:       float or None — half-size of the sampling grid. If None,
                      computed from field.bounding_box().
        octree_cache: optional SdfOctreeCache instance for accelerated lookup.

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
        extent = diag * 0.8 # slightly larger margin

    n = max(1, int(math.ceil(2 * extent / resolution)))
    half = extent

    # Sample SDF on the plane grid centered at projected_center
    us = np.linspace(-half, half, n + 1).astype(np.float32)
    vs = np.linspace(-half, half, n + 1).astype(np.float32)
    U, V = np.meshgrid(us, vs, indexing='ij')

    # Convert 2D grid to 3D world points
    pts_3d_flat = np.zeros((U.size, 3), dtype=np.float32)
    pts_3d_flat[:, 0] = projected_center.x + U.ravel() * u_axis.x + V.ravel() * v_axis.x
    pts_3d_flat[:, 1] = projected_center.y + U.ravel() * u_axis.y + V.ravel() * v_axis.y
    pts_3d_flat[:, 2] = projected_center.z + U.ravel() * u_axis.z + V.ravel() * v_axis.z

    if octree_cache is not None:
        # Accelerated evaluation from cache
        all_pts = [FreeCAD.Vector(p[0], p[1], p[2]) for p in pts_3d_flat]
        vals = np.array([octree_cache.query(p) for p in all_pts], dtype=np.float32).reshape(n + 1, n + 1)
    else:
        # Standard analytical evaluation
        vals = field.evaluate_grid(pts_3d_flat).reshape(n + 1, n + 1)

    # ── 2D Dual Contouring (Pass 1: Find & Refine Crossings) ──
    crossings = {}
    active_cells = set()
    
    # Precise crossing finder (Newton steps on grid edges)
    def refine_crossing(pA, pB, field, iters=3, cache=None):
        if cache is not None:
            v0 = cache.query(pA)
            v1 = cache.query(pB)
        else:
            v0 = field.evaluate(pA)
            v1 = field.evaluate(pB)

        if (v0 < 0) == (v1 < 0): return None, None
        t = v0 / (v0 - v1)
        p = pA + (pB - pA) * t
        direction = (pB - pA)
        dlen = direction.Length
        if dlen < 1e-12: return p, t
        direction.normalize()
        
        for _ in range(iters):
            if cache is not None:
                fval = cache.query(p)
                eps = 1e-4
                df = (cache.query(p + direction * eps) - fval) / eps
            else:
                fval = field.evaluate(p)
                eps = 1e-4
                df = (field.evaluate(p + direction * eps) - fval) / eps
            
            if abs(df) > 1e-9:
                p = p - direction * (fval / df)
        
        # New t relative to original edge
        new_t = (p - pA).Length / dlen
        return p, new_t

    for i in range(n):
        for j in range(n):
            v0, v1, v2, v3 = vals[i, j], vals[i+1, j], vals[i+1, j+1], vals[i, j+1]
            p0 = _uv_to_3d(us[i], vs[j], projected_center, u_axis, v_axis)
            p1 = _uv_to_3d(us[i+1], vs[j], projected_center, u_axis, v_axis)
            p2 = _uv_to_3d(us[i+1], vs[j+1], projected_center, u_axis, v_axis)
            p3 = _uv_to_3d(us[i], vs[j+1], projected_center, u_axis, v_axis)

            # Right edge: (v1, v2) connects i,j and i+1,j
            if (v1 < 0) != (v2 < 0):
                p_ref, _ = refine_crossing(p1, p2, field, cache=octree_cache)
                if p_ref:
                    # Map p_ref back to UV
                    rel = p_ref - projected_center
                    u, v = rel.dot(u_axis), rel.dot(v_axis)
                    crossings[(i, j, 0)] = (u, v, p_ref)
                    active_cells.add((i, j))
                    active_cells.add((i+1, j))
                
            # Top edge: (v3, v2) connects i,j and i,j+1
            if (v3 < 0) != (v2 < 0):
                p_ref, _ = refine_crossing(p3, p2, field, cache=octree_cache)
                if p_ref:
                    rel = p_ref - projected_center
                    u, v = rel.dot(u_axis), rel.dot(v_axis)
                    crossings[(i, j, 1)] = (u, v, p_ref)
                    active_cells.add((i, j))
                    active_cells.add((i, j+1))

    if not crossings: return []

    # ── 2D Dual Contouring (Pass 2: Batch UV Gradients) ──
    crossing_keys = list(crossings.keys())
    pts_to_eval = []
    eps = 1e-4
    for key in crossing_keys:
        _, _, p3 = crossings[key]
        pts_to_eval.append([p3.x, p3.y, p3.z])
        pts_to_eval.append([p3.x + eps, p3.y, p3.z])
        pts_to_eval.append([p3.x, p3.y + eps, p3.z])
        pts_to_eval.append([p3.x, p3.y, p3.z + eps])

    all_pts_to_eval = np.array(pts_to_eval, dtype=np.float32)
    if octree_cache is not None:
        all_vals = np.array([octree_cache.query(FreeCAD.Vector(p[0],p[1],p[2])) for p in all_pts_to_eval], dtype=np.float32)
    else:
        all_vals = field.evaluate_grid(all_pts_to_eval)
        
    uv_grads = []
    for k in range(len(crossing_keys)):
        v0 = all_vals[k*4]
        g3d = FreeCAD.Vector((all_vals[k*4+1]-v0)/eps, (all_vals[k*4+2]-v0)/eps, (all_vals[k*4+3]-v0)/eps)
        gu, gv = g3d.dot(u_axis), g3d.dot(v_axis)
        uv_grad = np.array([gu, gv])
        gl = np.linalg.norm(uv_grad)
        if gl > 1e-8: uv_grad /= gl
        uv_grads.append(uv_grad)

    # ── 2D Dual Contouring (Pass 3: Solve QEF & Refine Vertex) ──
    cell_vertices = {}
    for cell in active_cells:
        i, j = cell
        if i < 0 or i >= n or j < 0 or j >= n: continue
        
        rel_uvs, rel_grads = [], []
        for ekey in [(i, j-1, 1), (i, j, 0), (i, j, 1), (i-1, j, 0)]:
            if ekey in crossings:
                idx = crossing_keys.index(ekey)
                u, v, _ = crossings[ekey]
                rel_uvs.append(np.array([u, v]))
                rel_grads.append(uv_grads[idx])
        
        if rel_uvs:
            A, b = np.zeros((2, 2)), np.zeros(2)
            for p, n_uv in zip(rel_uvs, rel_grads):
                A += np.outer(n_uv, n_uv)
                b += np.dot(p, n_uv) * n_uv
            
            # Regularize
            lambd = 1e-6
            avg_uv = np.mean(rel_uvs, axis=0)
            A += np.eye(2) * lambd
            b += lambd * avg_uv
            
            try:
                uv_res, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
                p_final = _uv_to_3d(uv_res[0], uv_res[1], projected_center, u_axis, v_axis)
                
                # Newton-Raphson refinement: snap vertex to surface
                for _ in range(3):
                    if octree_cache is not None:
                        fval = octree_cache.query(p_final)
                        g_x = (octree_cache.query(p_final + FreeCAD.Vector(eps,0,0)) - fval)/eps
                        g_y = (octree_cache.query(p_final + FreeCAD.Vector(0,eps,0)) - fval)/eps
                        g_z = (octree_cache.query(p_final + FreeCAD.Vector(0,0,eps)) - fval)/eps
                    else:
                        fval = field.evaluate(p_final)
                        g_x = (field.evaluate(p_final + FreeCAD.Vector(eps,0,0)) - fval)/eps
                        g_y = (field.evaluate(p_final + FreeCAD.Vector(0,eps,0)) - fval)/eps
                        g_z = (field.evaluate(p_final + FreeCAD.Vector(0,0,eps)) - fval)/eps
                    grad = FreeCAD.Vector(g_x, g_y, g_z)
                    gl2 = grad.Length ** 2
                    if gl2 > 1e-9:
                        p_final = p_final - grad * (fval / gl2)
                
                cell_vertices[cell] = p_final
            except:
                cell_vertices[cell] = _uv_to_3d(avg_uv[0], avg_uv[1], projected_center, u_axis, v_axis)
    # ── 2D Dual Contouring (Pass 4: Connectivity) ──
    edges_to_connect = []
    for (i, j, etype) in crossings:
        c1, c2 = (i, j), (i+1, j) if etype == 0 else (i, j+1)
        if c1 in cell_vertices and c2 in cell_vertices:
            edges_to_connect.append((cell_vertices[c1], cell_vertices[c2]))

    contours_3d = _chain_segments(edges_to_connect)
    
    final_contours_3d = []
    for contour in contours_3d:
        if len(contour) < 2: continue
        # Simplify using RDP — epsilon should be related to resolution.
        # DC is much cleaner than MS, so 0.1 * resolution is often enough
        # to remove tiny noise while keeping sharp corners.
        epsilon = resolution * 0.1
        simplified = _simplify_contour_rdp(contour, epsilon)
        final_contours_3d.append(simplified)

    return final_contours_3d


def _uv_to_3d(u, v, projected_center, u_axis, v_axis):
    """Helper to convert 2D grid coords (u,v) back to 3D world coords."""
    return FreeCAD.Vector(
        projected_center.x + u * u_axis.x + v * v_axis.x,
        projected_center.y + u * u_axis.y + v * v_axis.y,
        projected_center.z + u * u_axis.z + v * v_axis.z,
    )

def _calculate_gradient(field, point, epsilon=1e-4):
    """Estimates the gradient of the SDF at a given 3D point using finite differences."""
    grad_x = (field.evaluate(point + FreeCAD.Vector(epsilon, 0, 0)) - field.evaluate(point - FreeCAD.Vector(epsilon, 0, 0))) / (2 * epsilon)
    grad_y = (field.evaluate(point + FreeCAD.Vector(0, epsilon, 0)) - field.evaluate(point - FreeCAD.Vector(0, epsilon, 0))) / (2 * epsilon)
    grad_z = (field.evaluate(point + FreeCAD.Vector(0, 0, epsilon)) - field.evaluate(point - FreeCAD.Vector(0, 0, epsilon))) / (2 * epsilon)
    grad = FreeCAD.Vector(grad_x, grad_y, grad_z)
    if grad.Length < 1e-6: # Avoid division by zero for flat regions
        return FreeCAD.Vector(0,0,0)
    return grad.normalize()


def _solve_qef(points_on_edges, gradients_on_edges):
    """
    Solves the Quadratic Error Function (QEF) for a cell to find the optimal vertex.
    The QEF minimizes sum((p_i - x) . n_i)^2 for points p_i and normals n_i.
    This leads to a linear system Ax = b.
    """
    A = np.zeros((3, 3))
    b = np.zeros(3)

    for p, n in zip(points_on_edges, gradients_on_edges):
        # A += n * n.T (outer product)
        A[0, 0] += n.x * n.x
        A[0, 1] += n.x * n.y
        A[0, 2] += n.x * n.z
        A[1, 0] += n.y * n.x
        A[1, 1] += n.y * n.y
        A[1, 2] += n.y * n.z
        A[2, 0] += n.z * n.x
        A[2, 1] += n.z * n.y
        A[2, 2] += n.z * n.z

        # b += (p . n) * n
        dot_pn = p.dot(n)
        b[0] += dot_pn * n.x
        b[1] += dot_pn * n.y
        b[2] += dot_pn * n.z

    try:
        # Solve Ax = b for x using least-squares for stability in 2D/3D
        x, residuals, rank, s = np.linalg.lstsq(A, b, rcond=None)
        return FreeCAD.Vector(x[0], x[1], x[2])
    except Exception:
        # If A is singular, fall back to centroid of crossing points
        if points_on_edges:
            centroid = FreeCAD.Vector(0,0,0)
            for p in points_on_edges:
                centroid += p
            return centroid / len(points_on_edges)
        return FreeCAD.Vector(0,0,0) # Should not happen if crossing_points is not empty


def _simplify_rdp_recursive(points, epsilon):
    """Internal recursive helper for open-line RDP."""
    if len(points) < 3:
        return points

    start, end = points[0], points[-1]
    max_dist = 0
    max_idx = 0
    direction = end - start
    line_len = direction.Length
    if line_len > 1e-12:
        direction.normalize()

    for i in range(1, len(points) - 1):
        v = points[i] - start
        if line_len < 1e-12:
            dist = v.Length
        else:
            proj = v.dot(direction)
            dist = (v - direction * proj).Length
        if dist > max_dist:
            max_dist = dist
            max_idx = i

    if max_dist > epsilon:
        left = _simplify_rdp_recursive(points[:max_idx + 1], epsilon)
        right = _simplify_rdp_recursive(points[max_idx:], epsilon)
        return left[:-1] + right
    else:
        return [start, end]

def _simplify_contour_rdp(points, epsilon):
    """Ramer-Douglas-Peucker simplification for polylines and closed loops."""
    if len(points) < 3:
        return points

    start, end = points[0], points[-1]
    is_closed = (start - end).Length < 1e-7

    if is_closed:
        # Find the point furthest from the start to use as a 'corner' to break the loop
        max_dist = -1
        max_idx = 0
        for i in range(1, len(points) - 1):
            d = (points[i] - start).Length
            if d > max_dist:
                max_dist = d
                max_idx = i
        
        # Roll points to start at max_idx, then simplify as an open line
        rolled = points[max_idx:-1] + points[:max_idx+1]
        simplified = _simplify_rdp_recursive(rolled, epsilon)
        # Ensure it's still closed if it was originally
        if (simplified[0] - simplified[-1]).Length > 1e-7:
            simplified.append(simplified[0])
        return simplified
    else:
        return _simplify_rdp_recursive(points, epsilon)


def _chain_segments(segments, tol=1e-4):
    """Chain unordered line segments into ordered polylines.
    Works with both (u,v) tuples and FreeCAD.Vector.
    """
    if not segments:
        return []

    remaining = list(segments)
    contours = []

    def get_dist(p1, p2):
        if hasattr(p1, "x"): # FreeCAD.Vector
            return (p1 - p2).Length
        # Manhattan for tuples
        return abs(p1[0] - p2[0]) + abs(p1[1] - p2[1])

    while remaining:
        seg = remaining.pop(0)
        chain = [seg[0], seg[1]]

        changed = True
        while changed:
            changed = False
            for k in range(len(remaining) - 1, -1, -1):
                s = remaining[k]
                
                # Try all 4 connection possibilities
                if get_dist(chain[-1], s[0]) < tol:
                    chain.append(s[1])
                    remaining.pop(k)
                    changed = True
                elif get_dist(chain[-1], s[1]) < tol:
                    chain.append(s[0])
                    remaining.pop(k)
                    changed = True
                elif get_dist(chain[0], s[0]) < tol:
                    chain.insert(0, s[1])
                    remaining.pop(k)
                    changed = True
                elif get_dist(chain[0], s[1]) < tol:
                    chain.insert(0, s[0])
                    remaining.pop(k)
                    changed = True

        contours.append(chain)

    return contours


def fit_dm_curve(contour_points, closed=False, smooth_factor=0.33, sharp_threshold_deg=30.0):
    """
    Fit a DM curve through a list of 3D points.

    Args:
        contour_points:      list[FreeCAD.Vector] — ordered 3D points.
        closed:              bool — whether the contour is closed.
        smooth_factor:       float — handle length as fraction of chord.
        sharp_threshold_deg: float — angle above which a corner is "sharp" (zero-length handles).
    """
    pts = list(contour_points)

    # Remove near-duplicate last point if closed
    if closed and len(pts) > 2:
        if (pts[0] - pts[-1]).Length < 0.001:
            pts = pts[:-1]

    # Decimate: skip points that are too close together
    # Only if we have many points (RDP should have already reduced most)
    if len(pts) > 10:
        decimated = [pts[0]]
        avg_dist = (pts[0] - pts[-1]).Length / max(len(pts), 1)
        min_dist = max(0.01, avg_dist * 0.1)
        for p in pts[1:]:
            if (p - decimated[-1]).Length >= min_dist:
                decimated.append(p)
        pts = decimated

    n = len(pts)
    if n < 2:
        return {"Points": pts, "HandleIn": [p for p in pts], "HandleOut": [p for p in pts],
                "Closed": closed, "is_closed": closed}

    handles_in = []
    handles_out = []

    cos_threshold = math.cos(math.radians(sharp_threshold_deg))

    for i in range(n):
        p = pts[i]
        prev_p = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        next_p = pts[(i + 1) % n] if (closed or i < n - 1) else pts[i]

        v_in = p - prev_p
        v_out = next_p - p
        
        is_sharp = False
        if v_in.Length > 1e-6 and v_out.Length > 1e-6:
            v_in.normalize()
            v_out.normalize()
            # If dot product is small, the turn is sharp
            if v_in.dot(v_out) < cos_threshold:
                is_sharp = True
        elif not closed and (i == 0 or i == n - 1):
            is_sharp = True # Endpoints are always sharp (handles point inwards)

        if is_sharp:
            handles_in.append(p)
            handles_out.append(p)
        else:
            # Smooth handles based on tangent between prev and next
            tangent = next_p - prev_p
            chord_prev = (p - prev_p).Length
            chord_next = (next_p - p).Length

            if tangent.Length > 1e-6:
                tangent.normalize()
            else:
                tangent = FreeCAD.Vector(1, 0, 0)

            handles_in.append(p - tangent * (chord_prev * smooth_factor))
            handles_out.append(p + tangent * (chord_next * smooth_factor))

    return {
        "Points": pts,
        "HandleIn": handles_in,
        "HandleOut": handles_out,
        "Closed": closed,
        "is_closed": closed,
    }
