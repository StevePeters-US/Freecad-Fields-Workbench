
import numpy as np

def fit_plane(points):
    """
    Fit a plane to points using SVD.
    points: (N, 3)
    Returns: (center, normal, error)
    """
    center = np.mean(points, axis=0)
    centered = points - center
    u, s, vh = np.linalg.svd(centered)
    
    # Normal is the last row of vh (corresponding to smallest singular value)
    normal = vh[2, :]
    
    # Error: sum of squared distances
    # Distance = dot(point - center, normal)
    # Normals are normalized by SVD
    dists = np.dot(centered, normal)
    error = np.mean(dists**2) # MSE
    
    return center, normal, error

def fit_sphere(points):
    """
    Fit a sphere to points using linear least squares.
    Equation: (x-xc)^2 + (y-yc)^2 + (z-zc)^2 = R^2
    x^2 + y^2 + z^2 - 2*x*xc - 2*y*yc - 2*z*zc + xc^2+yc^2+zc^2 - R^2 = 0
    Let A = 2*xc, B = 2*yc, C = 2*zc, D = R^2 - xc^2 - yc^2 - zc^2
    2*x*xc + 2*y*yc + 2*z*zc + D = x^2 + y^2 + z^2
    Ax + By + Cz + D = x^2 + y^2 + z^2
    Linear system M * [A,B,C,D]^T = RHS
    """
    N = len(points)
    x = points[:,0]
    y = points[:,1]
    z = points[:,2]
    
    rhs = x**2 + y**2 + z**2
    
    # M matrix: [x, y, z, 1]
    M = np.column_stack((x, y, z, np.ones(N)))
    
    # Solve M * params = rhs
    params, residuals, rank, s = np.linalg.lstsq(M, rhs, rcond=None)
    
    A, B, C, D = params
    
    xc = A / 2.0
    yc = B / 2.0
    zc = C / 2.0
    
    center = np.array([xc, yc, zc])
    radius = np.sqrt(D + xc**2 + yc**2 + zc**2)
    
    # Calculate MSE
    dists = np.linalg.norm(points - center, axis=1) - radius
    error = np.mean(dists**2)
    
    return center, radius, error

def fit_cylinder(points):
    """
    Fit a cylinder. This is geometric non-linear least squares.
    Hard to do robustly without good initial guess.
    
    Simple heuristic:
    1. Estimate axis direction using normals (Points on cylinder have normals perpendicular to axis).
       SVD of normals -> smallest eigenvector is axis direction.
    2. Project points onto plane perpendicular to axis.
    3. Fit circle in 2D.
    """
    # Requires normals? If not, we have to estimate them or use different method.
    # We can rely on `mesh_features` to provide normals if we fit to a mesh patch.
    # Here assume we just have points.
    
    # Placeholder: Return None to indicate not implemented fully
    return None, None, float('inf')

def best_fit(points, normals=None):
    """
    Try primitive fits and return the best one.
    """
    res = {}
    
    # Plane
    c_p, n_p, err_p = fit_plane(points)
    res['Plane'] = (err_p, (c_p, n_p))
    
    # Sphere
    if len(points) > 4:
        c_s, r_s, err_s = fit_sphere(points)
        res['Sphere'] = (err_s, (c_s, r_s))
    
    # Cylinder (Skip for now or implement)
    
    # Select best
    best_type = min(res, key=lambda k: res[k][0])
    return best_type, res[best_type]
