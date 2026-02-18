"""
Curve Extraction from SDF Objects

Pipeline: point cloud → segmentation → surface fitting → intersection curves
→ SDF-based trimming

Produces analytical curves (lines, circles) as numpy arrays of 3D points
suitable for rendering as Coin3D line sets or converting to Part.Edge objects.
"""

import numpy as np
from FCDirectModeling import surface_fitting


def extract_curves(sdf_object, resolution=32, edge_threshold=0.2,
                   normal_similarity=0.9, samples_per_curve=64):
    """
    Top-level entry point: extract feature curves from an SDF object.
    
    Returns:
        curves: list of (N, 3) numpy arrays, each a polyline of 3D points
        patch_fits: list of (type, params) for each fitted patch
    """
    import FreeCAD
    
    # 1. Generate point cloud
    FreeCAD.Console.PrintMessage("CurveExtraction: Generating point cloud...\n")
    points = sdf_object.generate_point_cloud(resolution=resolution, samples=8, iterations=5)
    
    if points is None or len(points) < 10:
        FreeCAD.Console.PrintWarning("CurveExtraction: Not enough points generated.\n")
        return [], []
    
    # 2. Compute normals and variances
    normals = sdf_object.compute_normals(points)
    
    bound_min, bound_max = sdf_object._bounds_local()
    size = bound_max - bound_min
    step = np.max(size) / max(resolution - 1, 1)
    radius = step * 1.0
    
    scores = sdf_object.compute_variances(points, radius=radius)
    
    # 3. Segment into patches
    patches, edge_indices, adjacency = sdf_object.segment_point_cloud(
        points, normals, scores,
        edge_threshold=edge_threshold,
        normal_similarity=normal_similarity
    )
    
    FreeCAD.Console.PrintMessage(
        f"CurveExtraction: {len(patches)} patches, "
        f"{len(edge_indices)} edge pts, "
        f"{len(adjacency)} adj pairs.\n"
    )
    
    if len(patches) < 1:
        return [], []
    
    # 4. Fit primitives to each patch
    patch_fits = []
    for i, patch in enumerate(patches):
        patch_pts = points[patch]
        patch_norms = normals[patch]
        fit_type, (error, params) = surface_fitting.best_fit(patch_pts, patch_norms)
        patch_fits.append((fit_type, params, error))
        FreeCAD.Console.PrintMessage(
            f"  Patch {i}: {fit_type} (N={len(patch)}, err={error:.6f})\n"
        )
    
    # 5. Merge coplanar patches to avoid spurious edges between same-face fragments
    merged_fits, merge_map = _merge_coplanar_patches(patch_fits, patches, points)
    
    # Rebuild adjacency with merged patch IDs
    merged_adjacency = {}
    for (pi, pj), shared_edge_pts in adjacency.items():
        mi = merge_map[pi]
        mj = merge_map[pj]
        if mi == mj:
            continue  # Same merged patch — no edge
        key = (min(mi, mj), max(mi, mj))
        if key not in merged_adjacency:
            merged_adjacency[key] = []
        merged_adjacency[key].extend(shared_edge_pts)
    
    FreeCAD.Console.PrintMessage(
        f"CurveExtraction: After merge: {len(merged_fits)} patches, "
        f"{len(merged_adjacency)} adj pairs.\n"
    )
    
    # 6. Compute intersection curves between adjacent patches
    curves = []
    
    for (pi, pj), shared_edge_pts in merged_adjacency.items():
        if pi >= len(merged_fits) or pj >= len(merged_fits):
            continue
            
        fit_a = merged_fits[pi]
        fit_b = merged_fits[pj]
        
        edge_pts = points[shared_edge_pts] if len(shared_edge_pts) > 0 else None
        
        curve = intersect_surfaces(
            fit_a, fit_b, 
            bound_min, bound_max,
            edge_hint_points=edge_pts,
            n_samples=samples_per_curve
        )
        
        if curve is not None and len(curve) >= 2:
            # CRITICAL: Trim curve to SDF surface
            trimmed = _trim_curve_to_sdf(curve, sdf_object, tolerance=step * 0.5)
            if trimmed is not None and len(trimmed) >= 2:
                curves.append(trimmed)
    
    FreeCAD.Console.PrintMessage(f"CurveExtraction: Extracted {len(curves)} curves.\n")
    return curves, merged_fits


def _merge_coplanar_patches(patch_fits, patches, points):
    """
    Merge patches that are the same type and coplanar/coincident.
    
    For planes: merge if normals are parallel and centers lie on same plane.
    For others: merge if same type with similar parameters.
    
    Returns:
        merged_fits: list of (type, params, error)
        merge_map: dict {original_idx: merged_idx}
    """
    n = len(patch_fits)
    merge_target = list(range(n))  # Each patch maps to itself initially
    
    for i in range(n):
        if merge_target[i] != i:
            continue  # Already merged
        type_i, params_i, err_i = patch_fits[i]
        
        for j in range(i + 1, n):
            if merge_target[j] != j:
                continue
            type_j, params_j, err_j = patch_fits[j]
            
            if type_i != type_j:
                continue
            
            if type_i == 'Plane':
                c_i, n_i = params_i
                c_j, n_j = params_j
                
                # Check normals are parallel (dot product ~1 or ~-1)
                dot = abs(np.dot(n_i, n_j))
                if dot < 0.95:
                    continue
                
                # Check coplanarity: distance from c_j to plane_i
                dist = abs(np.dot(c_j - c_i, n_i))
                
                # Use a relative threshold based on object size
                size_ref = np.linalg.norm(np.ptp(points, axis=0))
                if dist < size_ref * 0.05:
                    merge_target[j] = i
            
            elif type_i == 'Sphere':
                c_i, r_i = params_i
                c_j, r_j = params_j
                if (np.linalg.norm(c_i - c_j) < max(r_i, r_j) * 0.1 and
                    abs(r_i - r_j) < max(r_i, r_j) * 0.1):
                    merge_target[j] = i
    
    # Build merged list
    # Flatten merge chains
    for i in range(n):
        root = i
        while merge_target[root] != root:
            root = merge_target[root]
        merge_target[i] = root
    
    # Collect unique roots
    unique_roots = sorted(set(merge_target))
    root_to_new_idx = {r: idx for idx, r in enumerate(unique_roots)}
    
    merge_map = {i: root_to_new_idx[merge_target[i]] for i in range(n)}
    merged_fits = [patch_fits[r] for r in unique_roots]
    
    return merged_fits, merge_map


def _trim_curve_to_sdf(curve, sdf_object, tolerance=0.5):
    """
    Trim a curve by evaluating each point through the SDF.
    Only keep points where |SDF(p)| < tolerance (i.e. on the surface).
    
    Returns the longest contiguous run of valid points, or None.
    """
    dists = np.abs(sdf_object._evaluate_local(curve))
    valid = dists < tolerance
    
    if not np.any(valid):
        return None
    
    # Find the longest contiguous run of valid points
    runs = []
    start = None
    for i in range(len(valid)):
        if valid[i]:
            if start is None:
                start = i
        else:
            if start is not None:
                runs.append((start, i))
                start = None
    if start is not None:
        runs.append((start, len(valid)))
    
    if not runs:
        return None
    
    # Return the longest run
    best_run = max(runs, key=lambda r: r[1] - r[0])
    result = curve[best_run[0]:best_run[1]]
    
    return result if len(result) >= 2 else None


def intersect_surfaces(fit_a, fit_b, bound_min, bound_max, 
                       edge_hint_points=None, n_samples=64):
    """
    Compute the intersection curve between two fitted surfaces.
    Always clips lines to the bounding box.
    """
    type_a, params_a, _ = fit_a
    type_b, params_b, _ = fit_b
    
    pair = tuple(sorted([type_a, type_b]))
    
    if pair == ('Plane', 'Plane'):
        return _intersect_plane_plane(
            params_a if type_a == 'Plane' else params_b,
            params_b if type_a == 'Plane' else params_a,
            bound_min, bound_max, n_samples
        )
    elif pair == ('Plane', 'Sphere'):
        p_params = params_a if type_a == 'Plane' else params_b
        s_params = params_b if type_a == 'Plane' else params_a
        return _intersect_plane_sphere(p_params, s_params, n_samples)
    elif pair == ('Cone', 'Plane'):
        p_params = params_a if type_a == 'Plane' else params_b
        c_params = params_b if type_a == 'Plane' else params_a
        return _intersect_plane_cone(p_params, c_params, n_samples)
    elif pair == ('Cylinder', 'Plane'):
        p_params = params_a if type_a == 'Plane' else params_b
        cy_params = params_b if type_a == 'Plane' else params_a
        return _intersect_plane_cylinder(p_params, cy_params, n_samples)
    else:
        if edge_hint_points is not None and len(edge_hint_points) >= 2:
            return _order_points_as_polyline(edge_hint_points)
        return None


def _intersect_plane_plane(params_a, params_b, bound_min, bound_max, n_samples=64):
    """
    Plane-Plane intersection → line segment, always clipped to bounding box.
    """
    c1, n1 = params_a
    c2, n2 = params_b
    
    # Direction of intersection line
    direction = np.cross(n1, n2)
    d_len = np.linalg.norm(direction)
    
    if d_len < 1e-10:
        return None  # Parallel planes
    
    direction = direction / d_len
    
    # Find a point on the intersection line via least squares
    A = np.array([n1, n2])
    b = np.array([np.dot(n1, c1), np.dot(n2, c2)])
    p0, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    
    # ALWAYS clip to bounding box — this prevents lines from extending past the object
    t_min, t_max = _clip_line_to_box(p0, direction, bound_min, bound_max)
    if t_min is None:
        return None
    
    # Generate straight line segment (just 2 points for a straight line)
    curve = np.array([
        p0 + t_min * direction,
        p0 + t_max * direction
    ])
    
    return curve


def _intersect_plane_sphere(plane_params, sphere_params, n_samples=32):
    """Plane-Sphere → circle."""
    p_center, p_normal = plane_params
    s_center, s_radius = sphere_params
    
    d = np.dot(s_center - p_center, p_normal)
    
    if abs(d) >= s_radius:
        return None
    
    circle_center = s_center - d * p_normal
    circle_radius = np.sqrt(s_radius**2 - d**2)
    
    return _circle_in_plane(circle_center, p_normal, circle_radius, n_samples)


def _intersect_plane_cone(plane_params, cone_params, n_samples=32):
    """Plane-Cone → circle (perpendicular cut only)."""
    p_center, p_normal = plane_params
    apex, axis_dir, half_angle = cone_params
    
    dot = abs(np.dot(p_normal, axis_dir))
    
    if dot > 0.9:
        d = np.dot(p_center - apex, axis_dir) / max(dot, 1e-10)
        if d < 0:
            return None
        
        circle_center = apex + d * axis_dir
        circle_radius = d * np.tan(half_angle)
        
        return _circle_in_plane(circle_center, axis_dir, circle_radius, n_samples)
    
    return None


def _intersect_plane_cylinder(plane_params, cyl_params, n_samples=32):
    """Plane-Cylinder → circle (perpendicular cut only)."""
    p_center, p_normal = plane_params
    c_point, c_dir, c_radius = cyl_params
    
    dot = abs(np.dot(p_normal, c_dir))
    
    if dot > 0.9:
        d = np.dot(p_center - c_point, p_normal) / max(dot, 1e-10)
        circle_center = c_point + d * c_dir
        return _circle_in_plane(circle_center, c_dir, c_radius, n_samples)
    
    return None


# ---- Helpers ----

def _circle_in_plane(center, normal, radius, n_samples=32):
    """Generate closed polyline for a circle in a plane."""
    normal = normal / np.linalg.norm(normal)
    
    if abs(normal[0]) < 0.9:
        arbitrary = np.array([1.0, 0.0, 0.0])
    else:
        arbitrary = np.array([0.0, 1.0, 0.0])
    
    u = np.cross(normal, arbitrary)
    u = u / np.linalg.norm(u)
    v = np.cross(normal, u)
    v = v / np.linalg.norm(v)
    
    theta = np.linspace(0, 2 * np.pi, n_samples + 1)
    return center + radius * (np.outer(np.cos(theta), u) + np.outer(np.sin(theta), v))


def _clip_line_to_box(p0, direction, box_min, box_max):
    """Find t range where p0 + t*direction is inside bounding box."""
    t_min = -1e10
    t_max = 1e10
    
    for i in range(3):
        if abs(direction[i]) < 1e-12:
            if p0[i] < box_min[i] or p0[i] > box_max[i]:
                return None, None
        else:
            t1 = (box_min[i] - p0[i]) / direction[i]
            t2 = (box_max[i] - p0[i]) / direction[i]
            if t1 > t2:
                t1, t2 = t2, t1
            t_min = max(t_min, t1)
            t_max = min(t_max, t2)
    
    if t_min > t_max:
        return None, None
    
    return t_min, t_max


def _order_points_as_polyline(points):
    """Order scattered points into a polyline via nearest-neighbor traversal."""
    from scipy.spatial import KDTree
    
    if len(points) < 2:
        return points
    
    tree = KDTree(points)
    visited = np.zeros(len(points), dtype=bool)
    order = [0]
    visited[0] = True
    
    for _ in range(len(points) - 1):
        curr = order[-1]
        dists, indices = tree.query(points[curr], k=min(len(points), 10))
        
        if isinstance(dists, float):
            dists = [dists]
            indices = [indices]
        
        found = False
        for d, idx in zip(dists, indices):
            if not visited[idx]:
                order.append(idx)
                visited[idx] = True
                found = True
                break
        
        if not found:
            break
    
    return points[order]
