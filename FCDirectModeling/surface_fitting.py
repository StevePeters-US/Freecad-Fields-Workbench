
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
    N = len(points)
    if N < 5:
        return None, None, None, float('inf')
    
    center = np.mean(points, axis=0)
    
    # Estimate axis from normals! Normals of a cone form a distinct circular band
    n_centered = normals - np.mean(normals, axis=0)
    try:
        _, _, vh = np.linalg.svd(n_centered, full_matrices=False)
        # The direction with the LEAST variance in the normals is the axis of the cone
        axis_dir = vh[2, :]
    except np.linalg.LinAlgError:
         return None, None, None, float('inf')
         
    axis_dir = axis_dir / np.linalg.norm(axis_dir)
    
    p_centered = points - center
    
    # Project points along axis (Z) and orthogonal to it (R)
    Z = np.dot(p_centered, axis_dir)
    proj_along = np.outer(Z, axis_dir)
    radial_vecs = p_centered - proj_along
    R = np.linalg.norm(radial_vecs, axis=1)
    
    # A cone makes a linear relationship: R(Z) = a * Z + b
    # where apex is at R=0 => Z_apex = -b/a
    if np.std(Z) < 1e-6:
        return None, None, None, float('inf')
        
    A = np.column_stack([Z, np.ones(N)])
    try:
         result, _, _, _ = np.linalg.lstsq(A, R, rcond=None)
         a, b = result
    except:
         return None, None, None, float('inf')
         
    if abs(a) < 1e-6: # essentially a cylinder
        return None, None, None, float('inf')
        
    t_apex = -b / a
    apex = center + t_apex * axis_dir
    
    # Ensure axis points from base to tip or vice versa by convention
    # Let's normalize orientation so half_angle > 0 and axis is well-defined
    # actually a is tan(half_angle) roughly if signs are right
    # but more precisely:
    half_angle = np.arctan(abs(a))
    
    expected_R = np.abs(a * Z + b)
    error = np.mean((R - expected_R)**2)
    
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
