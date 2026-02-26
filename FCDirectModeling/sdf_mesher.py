import numpy as np

def compute_normals_from_sdf(sdf_func, X, Y, Z, epsilon=1e-5):
    """Compute gradients of SDF using central differences"""
    dx = (sdf_func(X + epsilon, Y, Z) - sdf_func(X - epsilon, Y, Z)) / (2 * epsilon)
    dy = (sdf_func(X, Y + epsilon, Z) - sdf_func(X, Y - epsilon, Z)) / (2 * epsilon)
    dz = (sdf_func(X, Y, Z + epsilon) - sdf_func(X, Y, Z - epsilon)) / (2 * epsilon)
    
    # Normalize
    norms = np.sqrt(dx**2 + dy**2 + dz**2)
    norms[norms == 0] = 1.0 # Prevent div by zero
    
    return dx/norms, dy/norms, dz/norms


def extract_mesh_numpy(sdf_func, mn, mx, resolution, sharp=True):
    """
    Enhanced Surface Nets / Dual Contouring approximation in Numpy.
    If sharp=True, it uses the edge crossing points and their normals
    to project the voxel vertex to the sharp feature intersection (QEF approximation).
    """
    mn = np.array(mn)
    mx = np.array(mx)
    bounds = mx - mn
    
    # Target isotropic step
    step_target = np.max(bounds) / resolution
    if step_target < 1e-5: step_target = 0.1
    
    # Calculate resolutions per axis, enforcing at least 5 voxels for thin features
    # (if the bound is non-zero). If it is zero, make it small but > 2.
    res_x = max(int(bounds[0] / step_target), 5) if bounds[0] > 1e-3 else 2
    res_y = max(int(bounds[1] / step_target), 5) if bounds[1] > 1e-3 else 2
    res_z = max(int(bounds[2] / step_target), 5) if bounds[2] > 1e-3 else 2
    
    # Cap maximum resolution to prevent memory explosions
    MAX_RES = 100
    res_x, res_y, res_z = min(res_x, MAX_RES), min(res_y, MAX_RES), min(res_z, MAX_RES)
    
    rx = np.linspace(mn[0], mx[0], res_x)
    ry = np.linspace(mn[1], mx[1], res_y)
    rz = np.linspace(mn[2], mx[2], res_z)
    
    step_x = (mx[0] - mn[0]) / max(res_x - 1, 1) if res_x > 1 else 1.0
    step_y = (mx[1] - mn[1]) / max(res_y - 1, 1) if res_y > 1 else 1.0
    step_z = (mx[2] - mn[2]) / max(res_z - 1, 1) if res_z > 1 else 1.0
    
    X, Y, Z = np.meshgrid(rx, ry, rz, indexing='ij')
    
    # 1. Evaluate SDF
    V = sdf_func(X, Y, Z)
    signs = V < 0.0
    
    if sharp:
        # Use an isotropic epsilon for gradients to avoid skewed normals
        epsilon = step_target / 10.0
        Nx, Ny, Nz = compute_normals_from_sdf(sdf_func, X, Y, Z, epsilon=epsilon)
    
    # 2. Find Active Voxels (crossing boundary)
    c000 = signs[:-1, :-1, :-1]
    c100 = signs[1:, :-1, :-1]
    c010 = signs[:-1, 1:, :-1]
    c110 = signs[1:, 1:, :-1]
    c001 = signs[:-1, :-1, 1:]
    c101 = signs[1:, :-1, 1:]
    c011 = signs[:-1, 1:, 1:]
    c111 = signs[1:, 1:, 1:]
    
    crossings = (
        (c000 != c100) | (c000 != c010) | (c000 != c110) |
        (c000 != c001) | (c000 != c101) | (c000 != c011) | (c000 != c111)
    )
    
    ax, ay, az = np.nonzero(crossings)
    if len(ax) == 0: return np.array([]), np.array([])
        
    num_active = len(ax)
    vertices = np.zeros((num_active, 3))
    voxel_idx = np.zeros((res_x-1, res_y-1, res_z-1), dtype=int) - 1
    voxel_idx[ax, ay, az] = np.arange(num_active)
    
    for i, (ix, iy, iz) in enumerate(zip(ax, ay, az)):
        # Calculate exactly the 12 edges for this voxel
        v = np.array([
            V[ix, iy, iz],       V[ix+1, iy, iz],
            V[ix, iy+1, iz],     V[ix+1, iy+1, iz],
            V[ix, iy, iz+1],     V[ix+1, iy, iz+1],
            V[ix, iy+1, iz+1],   V[ix+1, iy+1, iz+1],
            V[ix, iy, iz],       V[ix, iy+1, iz],
            V[ix+1, iy, iz],     V[ix+1, iy+1, iz],
            V[ix, iy, iz+1],     V[ix, iy+1, iz+1],
            V[ix+1, iy, iz+1],   V[ix+1, iy+1, iz+1],
            V[ix, iy, iz],       V[ix, iy, iz+1],
            V[ix+1, iy, iz],     V[ix+1, iy, iz+1],
            V[ix, iy+1, iz],     V[ix, iy+1, iz+1],
            V[ix+1, iy+1, iz],   V[ix+1, iy+1, iz+1]
        ]).reshape(12, 2)
        
        bx, by, bz = X[ix,iy,iz], Y[ix,iy,iz], Z[ix,iy,iz]
        crosses = (v[:, 0] < 0) != (v[:, 1] < 0)
        
        if not np.any(crosses):
            vertices[i] = [bx + step_x/2, by + step_y/2, bz + step_z/2]
            continue
            
        edge_pts = []
        edge_normals = []
        
        for e_idx in range(12):
            if crosses[e_idx]:
                v0, v1 = v[e_idx]
                t = 0.5 if v0 == v1 else max(0.0, min(1.0, v0 / (v0 - v1)))
                
                # Corner indices for normals
                if e_idx == 0:   c0=(ix,iy,iz); c1=(ix+1,iy,iz)
                elif e_idx == 1: c0=(ix,iy+1,iz); c1=(ix+1,iy+1,iz)
                elif e_idx == 2: c0=(ix,iy,iz+1); c1=(ix+1,iy,iz+1)
                elif e_idx == 3: c0=(ix,iy+1,iz+1); c1=(ix+1,iy+1,iz+1)
                elif e_idx == 4: c0=(ix,iy,iz); c1=(ix,iy+1,iz)
                elif e_idx == 5: c0=(ix+1,iy,iz); c1=(ix+1,iy+1,iz)
                elif e_idx == 6: c0=(ix,iy,iz+1); c1=(ix,iy+1,iz+1)
                elif e_idx == 7: c0=(ix+1,iy,iz+1); c1=(ix+1,iy+1,iz+1)
                elif e_idx == 8: c0=(ix,iy,iz); c1=(ix,iy,iz+1)
                elif e_idx == 9: c0=(ix+1,iy,iz); c1=(ix+1,iy,iz+1)
                elif e_idx == 10:c0=(ix,iy+1,iz); c1=(ix,iy+1,iz+1)
                elif e_idx == 11:c0=(ix+1,iy+1,iz); c1=(ix+1,iy+1,iz+1)
                
                # Edge geometries (offset from base corner)
                if e_idx < 4: 
                    p0 = [0, c0[1]-iy, c0[2]-iz]; p1 = [1, c1[1]-iy, c1[2]-iz]
                elif e_idx < 8:
                    p0 = [c0[0]-ix, 0, c0[2]-iz]; p1 = [c1[0]-ix, 1, c1[2]-iz]
                else:
                    p0 = [c0[0]-ix, c0[1]-iy, 0]; p1 = [c1[0]-ix, c1[1]-iy, 1]
                    
                p0 = np.array(p0) * np.array([step_x, step_y, step_z])
                p1 = np.array(p1) * np.array([step_x, step_y, step_z])
                
                pt = p0 + t * (p1 - p0)
                edge_pts.append([bx + pt[0], by + pt[1], bz + pt[2]])
                
                if sharp:
                    # Evaluate true normal at intersection
                    pt_x = bx + pt[0]
                    pt_y = by + pt[1]
                    pt_z = bz + pt[2]
                    eps = 1e-4
                    dx = (sdf_func(pt_x + eps, pt_y, pt_z) - sdf_func(pt_x - eps, pt_y, pt_z))
                    dy = (sdf_func(pt_x, pt_y + eps, pt_z) - sdf_func(pt_x, pt_y - eps, pt_z))
                    dz = (sdf_func(pt_x, pt_y, pt_z + eps) - sdf_func(pt_x, pt_y, pt_z - eps))
                    n = np.array([dx, dy, dz])
                    n_norm = np.linalg.norm(n)
                    if n_norm > 0: n /= n_norm
                    edge_normals.append(n)
                
        # Calculate optimal vertex placement (Pseudo Dual-Contouring)
        avg_pt = np.mean(edge_pts, axis=0) # Naive surface nets
        
        if sharp and len(edge_pts) >= 3:
            # Quadratic Error Function (QEF) minimization
            # We shift points relative to avg_pt so that the underdetermined lstsq
            # naturally minimizes distance to the mass center (pseudo-inverse property).
            A = np.array(edge_normals) # shape (K, 3)
            shifted_pts = np.array(edge_pts) - avg_pt
            b = np.sum(A * shifted_pts, axis=1) # shape (K,)
            
            try:
                # Least squares solution
                p_shifted, residuals, rank, s = np.linalg.lstsq(A, b, rcond=1e-3)
                p = avg_pt + p_shifted
                
                voxel_min = np.array([bx, by, bz])
                voxel_max = voxel_min + np.array([step_x, step_y, step_z])
                
                # Constrain p to be within the voxel bounds! (crucial to avoid spikes)
                p = np.clip(p, voxel_min, voxel_max)
                vertices[i] = p
            except np.linalg.LinAlgError:
                vertices[i] = avg_pt
        else:
            vertices[i] = avg_pt

    # 3. Generate Triangles (identical to before)
    triangles = []
    
    # X edges
    cross_X = (signs[:-1, :, :] != signs[1:, :, :])
    cx, cy, cz = np.nonzero(cross_X)
    for x, y, z in zip(cx, cy, cz):
        if y > 0 and y < res_y-1 and z > 0 and z < res_z-1 and x < res_x-1:
            v1, v2, v3, v4 = voxel_idx[x,y,z], voxel_idx[x,y-1,z], voxel_idx[x,y-1,z-1], voxel_idx[x,y,z-1]
            if v1 != -1 and v2 != -1 and v3 != -1 and v4 != -1:
                if signs[x, y, z]:
                    triangles.extend([[v1, v2, v3], [v1, v3, v4]])
                else:
                    triangles.extend([[v1, v3, v2], [v1, v4, v3]])
                    
    # Y edges
    cross_Y = (signs[:, :-1, :] != signs[:, 1:, :])
    cx, cy, cz = np.nonzero(cross_Y)
    for x, y, z in zip(cx, cy, cz):
        if x > 0 and x < res_x-1 and z > 0 and z < res_z-1 and y < res_y-1:
            v1, v2, v3, v4 = voxel_idx[x,y,z], voxel_idx[x,y,z-1], voxel_idx[x-1,y,z-1], voxel_idx[x-1,y,z]
            if v1 != -1 and v2 != -1 and v3 != -1 and v4 != -1:
                if signs[x, y, z]:
                    triangles.extend([[v1, v2, v3], [v1, v3, v4]])
                else:
                    triangles.extend([[v1, v3, v2], [v1, v4, v3]])
                    
    # Z edges
    cross_Z = (signs[:, :, :-1] != signs[:, :, 1:])
    cx, cy, cz = np.nonzero(cross_Z)
    for x, y, z in zip(cx, cy, cz):
        if x > 0 and x < res_x-1 and y > 0 and y < res_y-1 and z < res_z-1:
            v1, v2, v3, v4 = voxel_idx[x,y,z], voxel_idx[x-1,y,z], voxel_idx[x-1,y-1,z], voxel_idx[x,y-1,z]
            if v1 != -1 and v2 != -1 and v3 != -1 and v4 != -1:
                if signs[x, y, z]:
                    triangles.extend([[v1, v2, v3], [v1, v3, v4]])
                else:
                    triangles.extend([[v1, v3, v2], [v1, v4, v3]])

    return vertices, np.array(triangles)
