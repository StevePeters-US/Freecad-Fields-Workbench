
import numpy as np
import sys
import os

def log_debug(msg):
    try:
        with open("/tmp/fc_debug.log", "a") as f:
            f.write(f"[MC] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except:
        pass


def marching_cubes(volume, level=0.0):
    """
    Generate mesh from volume using Marching Tetrahedra algorithm.
    This avoids heavy lookup tables of Marching Cubes while providing
    topologically correct manifolds.
    
    volume: 3D numpy array
    level: isosurface value
    Returns: (verts, faces, normals, values)
    """
    log_debug(f"marching_cubes start. Volume shape: {volume.shape}, Level: {level}")

    
    # Dimensions
    dim_z, dim_y, dim_x = volume.shape
    
    # We will process in simple loops for clarity and to avoid complex indexing logic
    # Optimization: Use Cython or Numba or C++ extension for speed.
    # Pure Python/Numpy matching tetrahedra might be slow for large grids.
    # But for an SDF object of resolution ~32-64 it should be acceptable (seconds).
        
    # Standard decomposition of a cube into 6 tetrahedra
    # Vertices of a unit cube (x,y,z):
    # 0: 0,0,0
    # 1: 1,0,0
    # 2: 0,1,0
    # 3: 1,1,0
    # 4: 0,0,1
    # 5: 1,0,1
    # 6: 0,1,1
    # 7: 1,1,1
    
    # Tetrahedra (indices 0-7):
    # T0: 0, 1, 3, 5
    # T1: 0, 3, 2, 6 
    # T2: 0, 5, 6, 4
    # T3: 0, 3, 6, 5 (This central one... usually 5 is better?)
    # A standard 6-tet decomposition:
    # 1. 0 1 2 5 (base 012 top 5?) No.
    # We need a consistent decomposition.
    
    # Let's use a specific one:
    # T1: 0, 2, 3, 7
    # T2: 0, 6, 2, 7
    # T3: 0, 4, 6, 7
    # T4: 0, 6, 4, 5
    # T5: 0, 5, 4, 1
    # T6: 0, 1, 5, 7  -> Wait, too many 0s.
    
    # Let's use this valid 6-tet decomposition:
    # 0 1 3 5
    # 1 3 5 7
    # 0 3 5 6  <-- Central?
    # 0 2 3 6
    # 0 5 6 4
    # 5 6 7 3
    
    # Let's stick to the simplest code structure. 
    # We iterate x, y, z.
    # Map cube indices to volume indices.
    
    verts = []
    # We can output triangles directly (faces will just be 0,1,2, 3,4,5...)
    # Or we can try to merge vertices.
    # Merging is slow in Python.
    # SDF renderer in `sdf_renderer.py` can handle indexed faces or just raw triangles.
    # We'll return raw triangles for simplicity and speed (no hash map lookups).
    # `sdf_renderer.py` might need to be adjusted if it expects unique verts + faces.
    # The existing code there:
    #   verts, faces = ...
    #   self.coords.point.setValues(...)
    #   indices = np.full((n_faces, 4), -1)
    #   indices[:, :3] = faces
    # It assumes `faces` indexes into `verts`.
    # If we return raw triangles, `verts` will be (N*3, 3) and `faces` will be (N, 3) implicitly?
    # No, we must return (verts, faces) where faces are indices.
    # If we don't merge, `verts` size = 3 * num_triangles. `faces` = [[0,1,2], [3,4,5]...]
    
    # Lists to accumulate
    # We will use coordinate dictionary to merge vertices on the fly?
    # Too slow for python.
    # We will just dump all vertices and indices 0..N.
    
    # Tetrahedra definitions (local indices 0-7)
    # 0:000, 1:100, 2:010, 3:110, 4:001, 5:101, 6:011, 7:111
    # Using specific 5-tet decomposition (canonical for even/odd checkerboard to avoid diagonals aligning):
    # But 6-tet is easier for consistent edge choice.
    # Let's use 6 tets.
    # 1: 0 1 2 6
    # 2: 0 1 6 5
    # 3: 0 1 5 4
    # 4: 1 7 6 5
    # 5: 1 3 7 6
    # 6: 1 2 3 6
    # Order matters for orientation.
    # Let's use a simpler table-based look up for tets.
    
    # Helper to interpolate
    def get_pt(p1, p2, v1, v2, lvl):
        if abs(v2 - v1) < 1e-6:
             return p1
        t = (lvl - v1) / (v2 - v1)
        return p1 + t * (p2 - p1)

    # All edges of the 6 tets
    # If we implement this in pure python it will be SLOW for 32^3 = 32k cells * 6 tets = 200k tets.
    # We need to vectorize.
    
    # Vectorized approach:
    # 1. Construct arrays of cell values: v0..v7
    # 2. Divide into 6 sets of 4 values (for 6 tets).
    # 3. For each tet type (N cells * 1 tet), calculate case index (0-15).
    # 4. Lookup triangles for that tet.
    # 5. Calculate vertices.
    
    # Values at grid points
    # shape (D, H, W)
    # v0 = vol[:-1, :-1, :-1] (000)
    # v1 = vol[:-1, :-1, 1:]  (001) !! Notice: x is last index in numpy? 
    # Usually volume[z, y, x].
    # Let's assume x=last, y=middle, z=first.
    # 000: z, y, x
    # 100: z, y, x+1 -> this is v1 if we map bits to x,y,z? 
    # Let's match bit 0->x, bit 1->y, bit 2->z.
    
    # Calculate Gradients for Normals
    # Central differences: Gx[z,y,x] = (Vol[z,y,x+1] - Vol[z,y,x-1])/2
    # We need to pad or handle edges.
    # Pad volume with edge values or 0
    pad_vol = np.pad(volume, 1, mode='edge')
    
    dz = (pad_vol[2:, 1:-1, 1:-1] - pad_vol[:-2, 1:-1, 1:-1]) / 2.0
    dy = (pad_vol[1:-1, 2:, 1:-1] - pad_vol[1:-1, :-2, 1:-1]) / 2.0
    dx = (pad_vol[1:-1, 1:-1, 2:] - pad_vol[1:-1, 1:-1, :-2]) / 2.0
    
    # Gradient vector field (components)
    grad_x = dx
    grad_y = dy
    grad_z = dz
    
    # Vertices of gradient field correspond to vertices of volume
    
    # Helper to get sub-block
    def g(arr, _dx, _dy, _dz):
        return arr[_dz:dim_z-1+_dz, _dy:dim_y-1+_dy, _dx:dim_x-1+_dx]

    # Helper for volume values
    def s(dx, dy, dz):
        return volume[dz:dim_z-1+dz, dy:dim_y-1+dy, dx:dim_x-1+dx]
        
    # Vertices values for all cells
    val0 = s(0,0,0) # 000
    val1 = s(1,0,0) # 001 (x+1)
    val2 = s(0,1,0) # 010 (y+1)
    val3 = s(1,1,0) # 011
    val4 = s(0,0,1) # 100 (z+1)
    val5 = s(1,0,1) # 101
    val6 = s(0,1,1) # 110
    val7 = s(1,1,1) # 111
    
    # Gradients at cube corners
    gx0, gy0, gz0 = g(grad_x,0,0,0), g(grad_y,0,0,0), g(grad_z,0,0,0) # 000
    gx1, gy1, gz1 = g(grad_x,1,0,0), g(grad_y,1,0,0), g(grad_z,1,0,0) # 001
    gx2, gy2, gz2 = g(grad_x,0,1,0), g(grad_y,0,1,0), g(grad_z,0,1,0) # 010
    gx3, gy3, gz3 = g(grad_x,1,1,0), g(grad_y,1,1,0), g(grad_z,1,1,0) # 011
    gx4, gy4, gz4 = g(grad_x,0,0,1), g(grad_y,0,0,1), g(grad_z,0,0,1) # 100
    gx5, gy5, gz5 = g(grad_x,1,0,1), g(grad_y,1,0,1), g(grad_z,1,0,1) # 101
    gx6, gy6, gz6 = g(grad_x,0,1,1), g(grad_y,0,1,1), g(grad_z,0,1,1) # 110
    gx7, gy7, gz7 = g(grad_x,1,1,1), g(grad_y,1,1,1), g(grad_z,1,1,1) # 111

    # Stack into (..., 3) vectors
    # This creates arrays of shape (nz, ny, nx, 3)
    def stack_grad(gx, gy, gz):
        return np.stack([gx, gy, gz], axis=-1)

    n0 = stack_grad(gx0, gy0, gz0)
    n1 = stack_grad(gx1, gy1, gz1)
    n2 = stack_grad(gx2, gy2, gz2)
    n3 = stack_grad(gx3, gy3, gz3)
    n4 = stack_grad(gx4, gy4, gz4)
    n5 = stack_grad(gx5, gy5, gz5)
    n6 = stack_grad(gx6, gy6, gz6)
    n7 = stack_grad(gx7, gy7, gz7)

    # Coordinate grids
    grid_z, grid_y, grid_x = np.meshgrid(
        np.arange(dim_z-1), np.arange(dim_y-1), np.arange(dim_x-1), indexing='ij'
    )
    # Coordinates of 000 corner
    coords = np.stack([grid_x, grid_y, grid_z], axis=-1).astype(np.float32)
    
    # 6 Tetrahedra Decomposition
    # T0..T5 defined as before
    # We need to pass Normals as well
    
    tets = [
        (val0, val1, val3, val7,  n0, n1, n3, n7,  [0,0,0], [1,0,0], [1,1,0], [1,1,1]), # T0
        (val0, val1, val5, val7,  n0, n1, n5, n7,  [0,0,0], [1,0,0], [1,0,1], [1,1,1]), # T1
        (val0, val5, val4, val7,  n0, n5, n4, n7,  [0,0,0], [1,0,1], [0,0,1], [1,1,1]), # T2
        (val0, val4, val6, val7,  n0, n4, n6, n7,  [0,0,0], [0,0,1], [0,1,1], [1,1,1]), # T3
        (val0, val6, val2, val7,  n0, n6, n2, n7,  [0,0,0], [0,1,1], [0,1,0], [1,1,1]), # T4
        (val0, val2, val3, val7,  n0, n2, n3, n7,  [0,0,0], [0,1,0], [1,1,0], [1,1,1]), # T5
    ]
    
    all_verts = []
    all_norms = []
    
    for v_a, v_b, v_c, v_d, n_a, n_b, n_c, n_d, da, db, dc, dd in tets:
        # Calculate case index 0-15
        idx = ((v_a < level).astype(int) * 1) + \
              ((v_b < level).astype(int) * 2) + \
              ((v_c < level).astype(int) * 4) + \
              ((v_d < level).astype(int) * 8)
              
        # Active cells
        active = (idx > 0) & (idx < 15)
        if not np.any(active):
            continue
            
        case_indices = idx[active]
        base_coords = coords[active]
        
        vals_a = v_a[active]
        vals_b = v_b[active]
        vals_c = v_c[active]
        vals_d = v_d[active]
        
        norms_a = n_a[active]
        norms_b = n_b[active]
        norms_c = n_c[active]
        norms_d = n_d[active]
        
        off_a = np.array(da, dtype=np.float32)
        off_b = np.array(db, dtype=np.float32)
        off_c = np.array(dc, dtype=np.float32)
        off_d = np.array(dd, dtype=np.float32)
        
        p_a = base_coords + off_a
        p_b = base_coords + off_b
        p_c = base_coords + off_c
        p_d = base_coords + off_d
        
        tet_table = {
            0: [],
            1: [[0, 2, 1]], 
            2: [[0, 3, 4]], 
            3: [[1, 2, 4], [1, 4, 3]],
            4: [[1, 5, 3]], 
            5: [[0, 2, 5], [0, 5, 3]],
            6: [[0, 1, 5], [0, 5, 4]], 
            7: [[2, 5, 4]],
            8: [[2, 4, 5]], 
            9: [[0, 1, 5], [0, 5, 4]], 
            10:[[0, 2, 5], [0, 5, 3]], 
            11:[[1, 5, 3]],
            12:[[1, 2, 4], [1, 4, 3]],
            13:[[0, 3, 4]], 
            14:[[0, 2, 1]], 
            15:[]
        }
        
        # Helper: interpolate edge with normals
        def interp_n(pts1, pts2, v1, v2, n1, n2):
            denom = v2 - v1
            denom[np.abs(denom) < 1e-6] = 1.0 
            t = (level - v1) / denom
            t = t.reshape(-1, 1) # (N, 1)
            
            p = pts1 + t * (pts2 - pts1)
            n = n1 + t * (n2 - n1)
            # Normalize n? Not strictly necessary if shading, but better.
            # Fast normalization
            # norm = np.linalg.norm(n, axis=1, keepdims=True)
            # n = n / (norm + 1e-6)
            return p, n

        edges = {
            0: (vals_a, vals_b, p_a, p_b, norms_a, norms_b),
            1: (vals_a, vals_c, p_a, p_c, norms_a, norms_c),
            2: (vals_a, vals_d, p_a, p_d, norms_a, norms_d),
            3: (vals_b, vals_c, p_b, p_c, norms_b, norms_c),
            4: (vals_b, vals_d, p_b, p_d, norms_b, norms_d),
            5: (vals_c, vals_d, p_c, p_d, norms_c, norms_d),
        }

        cut_pts = {}
        cut_norms = {}
        
        for e_id in range(6):
            v1, v2, pt1, pt2, n1, n2 = edges[e_id]
            cp, cn = interp_n(pt1, pt2, v1, v2, n1, n2)
            cut_pts[e_id] = cp
            cut_norms[e_id] = cn
            
        unique_cases = np.unique(case_indices)
        for c in unique_cases:
            tris = tet_table[c]
            if not tris: continue
            mask = (case_indices == c)
            for tri in tris:
                all_verts.append(np.stack([cut_pts[tri[0]][mask], cut_pts[tri[1]][mask], cut_pts[tri[2]][mask]], axis=1).reshape(-1, 3))
                all_norms.append(np.stack([cut_norms[tri[0]][mask], cut_norms[tri[1]][mask], cut_norms[tri[2]][mask]], axis=1).reshape(-1, 3))
                
    if not all_verts:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0)
        
    check_verts = np.concatenate(all_verts, axis=0)
    check_norms = np.concatenate(all_norms, axis=0)
    
    # Normalize normals one last time
    norms_mag = np.linalg.norm(check_norms, axis=1, keepdims=True)
    check_norms = check_norms / (norms_mag + 1e-6)
    
    num_verts = check_verts.shape[0]
    faces = np.arange(num_verts).reshape(-1, 3)
    values = np.full(num_verts, level)

    log_debug(f"marching_cubes end. Generated {num_verts} verts, {faces.shape[0]} faces.")
    return check_verts, faces, check_norms, values


# Marching Squares Table & Logic (reused from above)
def find_contours(image, level=0.0):
    # Same as implemented before
    rows, cols = image.shape
    contours = []
    
    binary = image > level
    
    # Convention: 0:TL, 1:TR, 2:BR, 3:BL
    # image indices: r,c
    # 0:(r,c), 1:(r,c+1), 2:(r+1,c+1), 3:(r+1,c)
    
    c0 = binary[:-1, :-1]
    c1 = binary[:-1, 1:]
    c2 = binary[1:, 1:]
    c3 = binary[1:, :-1]
    
    indices = (c0.astype(int) * 8) + (c1.astype(int) * 4) + (c2.astype(int) * 2) + (c3.astype(int) * 1)
    
    v0 = image[:-1, :-1]
    v1 = image[:-1, 1:]
    v2 = image[1:, 1:]
    v3 = image[1:, :-1]
    
    # Base coords
    grid_r, grid_c = np.meshgrid(np.arange(rows-1), np.arange(cols-1), indexing='ij')
    
    # Coordinates for vertices (using image coordinates r, c)
    # We will output segments in (r, c)
    
    segments = []
    
    # Collect all segments first
    collected_segments = []
    
    # Lookup table for 0-15
    # Edges: 0:T, 1:R, 2:B, 3:L
    table = {
        0: [], 1: [(3, 2)], 2: [(1, 2)], 3: [(3, 1)],
        4: [(0, 1)], 5: [(0, 3), (1, 2)], 6: [(0, 2)], 7: [(0, 3)],
        8: [(3, 0)], 9: [(2, 0)], 10: [(3, 2), (1, 0)], 11: [(2, 1)],
        12: [(3, 1)], 13: [(1, 2)], 14: [(3, 2)], 15: []
    }
    
    active = (indices > 0) & (indices < 15)
    r_idx, c_idx = np.where(active)
    
    for r, c in zip(r_idx, c_idx):
        idx = indices[r, c]
        lines = table.get(idx, [])
        for e1, e2 in lines:
            # Interpolate
            # Edge 0 (T): (r,c) to (r,c+1) using v0, v1
            # Edge 1 (R): (r,c+1) to (r+1,c+1) using v1, v2
            # Edge 2 (B): (r+1,c) to (r+1,c+1) using v3, v2 -> Wait, v3 is BL, v2 is BR.
            # Edge 3 (L): (r,c) to (r+1,c) using v0, v3
            
            def get_p(edge, r, c):
                if edge == 0:
                    a, b = v0[r,c], v1[r,c]
                    pa, pb = (r,c), (r,c+1)
                elif edge == 1:
                    a, b = v1[r,c], v2[r,c]
                    pa, pb = (r,c+1), (r+1,c+1)
                elif edge == 2:
                    a, b = v3[r,c], v2[r,c]
                    pa, pb = (r+1,c), (r+1,c+1)
                elif edge == 3:
                    a, b = v0[r,c], v3[r,c]
                    pa, pb = (r,c), (r+1,c)
                
                denom = b - a
                if abs(denom) < 1e-9: t = 0.5
                else: t = (level - a) / denom
                
                pr = pa[0] + t * (pb[0] - pa[0])
                pc = pa[1] + t * (pb[1] - pa[1])
                return (pr, pc)
            
            p1 = get_p(e1, r, c)
            p2 = get_p(e2, r, c)
            collected_segments.append((p1, p2))

    # Chain segments into contours
    # Use a dictionary mapping endpoints to segments
    # Key: point tuple, Value: list of other point tuples
    adj = {}
    
    for p1, p2 in collected_segments:
        if p1 not in adj: adj[p1] = []
        if p2 not in adj: adj[p2] = []
        adj[p1].append(p2)
        adj[p2].append(p1)
        
    contours = []
    visited = set()
    
    # Iterate over all points in adj to find loops
    for start_node in list(adj.keys()):
        if start_node in visited:
            continue
            
        # Start a new contour
        # It might be a loop or an open line
        # Marching squares usually produces loops for closed internal shapes, 
        # but could be open if hitting boundary? 
        # We'll traverse.
        
        poly = [start_node]
        visited.add(start_node)
        
        curr = start_node
        # Greedy walk
        while True:
            neighbors = adj.get(curr, [])
            # Find an unvisited neighbor, OR the start node if valid loop
            next_node = None
            for n in neighbors:
                if n == start_node and len(poly) > 2:
                    # Closing the loop
                    # We don't add start_node again to poly usually, or do we?
                    # Let's just stop.
                    next_node = "CLOSED"
                    break
                if n not in visited:
                    next_node = n
                    break
            
            if next_node == "CLOSED":
                break
            elif next_node is None:
                # Dead end
                break
            else:
                curr = next_node
                poly.append(curr)
                visited.add(curr)
                
        if len(poly) >= 3:
             contours.append(np.array(poly))
             
    return contours
