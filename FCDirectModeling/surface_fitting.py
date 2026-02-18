
import numpy as np

def fit_plane(points):
    """
    Fit a plane to points using SVD.
    points: (N, 3)
    Returns: (center, normal, error)
    """
    center = np.mean(points, axis=0)
    centered = points - center
    u, s, vh = np.linalg.svd(centered, full_matrices=False)
    
    # Normal is the last row of vh (corresponding to smallest singular value)
    normal = vh[2, :]
    
    # Error: sum of squared distances
    dists = np.dot(centered, normal)
    error = np.mean(dists**2) # MSE
    
    return center, normal, error

def fit_sphere(points):
    """
    Fit a sphere to points using linear least squares.
    Returns: (center, radius, error)
    """
    N = len(points)
    x = points[:,0]
    y = points[:,1]
    z = points[:,2]
    
    rhs = x**2 + y**2 + z**2
    M = np.column_stack((x, y, z, np.ones(N)))
    
    params, residuals, rank, s = np.linalg.lstsq(M, rhs, rcond=None)
    
    A, B, C, D = params
    
    xc = A / 2.0
    yc = B / 2.0
    zc = C / 2.0
    
    center = np.array([xc, yc, zc])
    r_sq = D + xc**2 + yc**2 + zc**2
    radius = np.sqrt(max(r_sq, 0.0))
    
    dists = np.linalg.norm(points - center, axis=1) - radius
    error = np.mean(dists**2)
    
    return center, radius, error

def fit_cylinder(points, normals=None):
    """
    Fit a cylinder to points with optional normals.
    
    Heuristic:
    1. Estimate axis direction via SVD on normals (smallest eigenvector)
       If no normals, use SVD on points (largest eigenvector = axis direction).
    2. Project points onto plane perpendicular to axis.
    3. Fit circle in 2D.
    
    Returns: (axis_point, axis_dir, radius, error) or (None, None, None, inf)
    """
    N = len(points)
    if N < 5:
        return None, None, None, float('inf')
    
    center = np.mean(points, axis=0)
    
    # Estimate axis
    if normals is not None and len(normals) == N:
        # For a cylinder, normals are perpendicular to axis.
        # SVD of normals: smallest singular vector = axis direction
        n_centered = normals - np.mean(normals, axis=0)
        _, s_vals, vh = np.linalg.svd(n_centered, full_matrices=False)
        axis_dir = vh[2, :]  # Smallest singular value direction
    else:
        # Without normals, use point distribution
        # For a cylinder, points spread along axis -> largest singular vector
        p_centered = points - center
        _, s_vals, vh = np.linalg.svd(p_centered, full_matrices=False)
        axis_dir = vh[0, :]  # Largest spread = axis
    
    axis_dir = axis_dir / np.linalg.norm(axis_dir)
    
    # Project points onto plane perpendicular to axis
    p_centered = points - center
    # Remove axis component
    proj_along_axis = np.outer(np.dot(p_centered, axis_dir), axis_dir)
    p_2d = p_centered - proj_along_axis
    
    # Fit circle in 2D (using distance from projected center)
    dists_2d = np.linalg.norm(p_2d, axis=1)
    radius = np.mean(dists_2d)
    
    # Error
    error = np.mean((dists_2d - radius)**2)
    
    return center, axis_dir, radius, error

def fit_cone(points, normals=None):
    """
    Fit a cone to points.
    
    Heuristic:
    1. Estimate apex by finding where normals converge (intersection of normal lines).
    2. Compute half-angle from apex to points.
    
    Returns: (apex, axis_dir, half_angle, error) or (None, None, None, inf)
    """
    N = len(points)
    if N < 5:
        return None, None, None, float('inf')
    
    if normals is None:
        return None, None, None, float('inf')
    
    center = np.mean(points, axis=0)
    
    # Estimate axis from point distribution (axis = direction of most spread)
    p_centered = points - center
    _, _, vh = np.linalg.svd(p_centered, full_matrices=False)
    axis_dir = vh[0, :]  # Largest spread direction
    axis_dir = axis_dir / np.linalg.norm(axis_dir)
    
    # Project points along axis to estimate apex
    # On a cone, projecting point positions along axis:
    # The radius decreases linearly toward the apex.
    t = np.dot(p_centered, axis_dir)  # distance along axis from center
    
    # Radial distance from axis at each point
    proj_along = np.outer(t, axis_dir)
    radial_vecs = p_centered - proj_along
    r = np.linalg.norm(radial_vecs, axis=1)
    
    # Linear fit: r = a * t + b
    # Apex is where r = 0: t_apex = -b/a
    if np.std(t) < 1e-10:
        return None, None, None, float('inf')
    
    A_mat = np.column_stack([t, np.ones(N)])
    result = np.linalg.lstsq(A_mat, r, rcond=None)
    a, b = result[0]
    
    if abs(a) < 1e-10:
        return None, None, None, float('inf')
    
    t_apex = -b / a
    apex = center + t_apex * axis_dir
    
    # Half angle
    half_angle = np.arctan(abs(a))
    
    # Error: distance from each point to the cone surface
    # For point p, the cone surface at axis-distance t has radius |a*t + b|
    expected_r = np.abs(a * t + b)
    error = np.mean((r - expected_r)**2)
    
    return apex, axis_dir, half_angle, error

def best_fit(points, normals=None):
    """
    Try primitive fits and return the best one.
    Returns: (type_name, (error, fit_params))
    
    fit_params by type:
        Plane: (center, normal)
        Sphere: (center, radius) 
        Cylinder: (axis_point, axis_dir, radius)
        Cone: (apex, axis_dir, half_angle)
    """
    res = {}
    
    # Plane
    c_p, n_p, err_p = fit_plane(points)
    res['Plane'] = (err_p, (c_p, n_p))
    
    # Sphere
    if len(points) > 4:
        c_s, r_s, err_s = fit_sphere(points)
        res['Sphere'] = (err_s, (c_s, r_s))
    
    # Cylinder
    if len(points) > 5:
        c_cy, d_cy, r_cy, err_cy = fit_cylinder(points, normals)
        if c_cy is not None:
            res['Cylinder'] = (err_cy, (c_cy, d_cy, r_cy))
    
    # Cone
    if len(points) > 5 and normals is not None:
        apex, d_co, ha_co, err_co = fit_cone(points, normals)
        if apex is not None:
            res['Cone'] = (err_co, (apex, d_co, ha_co))
    
    # Select best
    best_type = min(res, key=lambda k: res[k][0])
    return best_type, res[best_type]
