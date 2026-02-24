
import numpy as np
import FreeCAD
import FreeCADGui

# Remove top-level import to avoid lazy_loader crash
HAS_SKIMAGE = False 

class SDFObject:
    def __init__(self):
        self.inverse_matrix = None
        self.matrix = None # Forward matrix

    def set_placement(self, placement):
        """
        Set the placement (transformation) of the SDF object.
        placement: FreeCAD.Placement or matrix-like object.
        """
        # Get the matrix (4x4)
        mat = placement.Matrix
        # FreeCAD Matrix -> Numpy
        self.matrix = np.array([
            [mat.A11, mat.A12, mat.A13, mat.A14],
            [mat.A21, mat.A22, mat.A23, mat.A24],
            [mat.A31, mat.A32, mat.A33, mat.A34],
            [mat.A41, mat.A42, mat.A43, mat.A44]
        ]).transpose()

        # Invert the matrix for world->local transform
        inv = mat.inverse()
        self.inverse_matrix = np.array([
            [inv.A11, inv.A12, inv.A13, inv.A14],
            [inv.A21, inv.A22, inv.A23, inv.A24],
            [inv.A31, inv.A32, inv.A33, inv.A34],
            [inv.A41, inv.A42, inv.A43, inv.A44]
        ]).transpose()

    def evaluate(self, points):
        """
        Public evaluate method. Transforms points if a placement is set, 
        then calls _evaluate_local.
        """
        if self.inverse_matrix is not None:
            # Add homogeneous coordinate w=1
            ones = np.ones((points.shape[0], 1))
            pts_h = np.hstack((points, ones))
            
            # Transform: (N, 4) dot (4, 4) -> (N, 4)
            # using transposed matrix
            local_pts_h = np.dot(pts_h, self.inverse_matrix)
            
            # Back to 3D
            local_points = local_pts_h[:, :3]
            return self._evaluate_local(local_points)
        else:
            return self._evaluate_local(points)

    def _evaluate_local(self, points):
        """
        Evaluate SDF in local coordinates. Override this.
        """
        raise NotImplementedError

    def bounds(self):
        """
        Return the axis-aligned bounding box of the object.
        If placement is set, returns the AABB of the transformed object.
        Returns: (min_point, max_point) as numpy arrays.
        """
        # Get local bounds from subclass
        local_min, local_max = self._bounds_local()
        
        if self.matrix is None:
            return local_min, local_max
            
        # Transform the 8 corners of the local AABB
        corners = [
            [local_min[0], local_min[1], local_min[2]],
            [local_min[0], local_min[1], local_max[2]],
            [local_min[0], local_max[1], local_min[2]],
            [local_min[0], local_max[1], local_max[2]],
            [local_max[0], local_min[1], local_min[2]],
            [local_max[0], local_min[1], local_max[2]],
            [local_max[0], local_max[1], local_min[2]],
            [local_max[0], local_max[1], local_max[2]]
        ]
        
        corners = np.array(corners)
        ones = np.ones((8, 1))
        corners_h = np.hstack((corners, ones))
        
        # Transform to world
        world_corners_h = np.dot(corners_h, self.matrix)
        world_corners = world_corners_h[:, :3]
        
        # Find new AABB
        min_p = np.min(world_corners, axis=0)
        max_p = np.max(world_corners, axis=0)
        
        return min_p, max_p

    def _bounds_local(self):
        raise NotImplementedError

    def get_vertices(self):
        """
        Returns an explicit list of sharp geometric vertices (points) for this object.
        Subclasses should override this if they have distinctive points (e.g. Box corners).
        Returns: (N, 3) numpy array
        """
        return np.zeros((0, 3))

    def get_edges(self):
        """
        Returns an explicit list of analytic geometric edges (curves) for this object.
        Subclasses should override this if they have distinct curves (e.g. Box edges, Cone base).
        Returns: list of (N, 3) numpy arrays
        """
        return []

    def trace_edges(self, num_seeds=200, step_size=0.1, max_steps=1000, variance_threshold=0.5):
        """
        Mathematically traces the sharp edges (ridges of maximum normal variance) 
        of the SDF object. Used for arbitrary boolean intersections.
        """
        bound_min, bound_max = self._bounds_local()
        size = bound_max - bound_min
        size[size < 1e-6] = 1.0 # Safety
        
        # 1. Generate random seed points around the object bounds
        rng = np.random.default_rng(42)
        base_points = bound_min + rng.random((num_seeds, 3)) * size
        
        # 2. Snap to mathematically perfect surface (SDF = 0)
        # Using 5 Newton-Raphson steps
        pts = base_points
        for _ in range(5):
            dists = self._evaluate_local(pts)
            grads = self.compute_normals(pts, epsilon=1e-4)
            pts = pts - grads * dists[:, np.newaxis]
            
        # 3. Slide points "uphill" to the crests of the edges (Gradient Ascent on Variance)
        # We need to compute the gradient of the 'variance' field
        
        # Determine dynamic sampling radius for variance based on object size
        # We need a stable radius so variance doesn't depend on raw geometry scale
        # Normal variance maxes out around 0.29 for a 90 degree corner
        radius = np.max(size) * 0.05
        if radius < 0.1: radius = 0.1
        
        # Step size for gradient ascent (keep it small to prevent jumping over the sharp edge)
        lr = radius * 0.2
        
        FreeCAD.Console.PrintMessage(f"SDFRenderer: Sliding {len(pts)} edge seeds to crests...\n")
        
        # Perform constrained gradient ascent
        # We want to maximize variance, so we step in direction of +grad(variance)
        # But we must project this step onto the surface tangent plane to stay on the object
        eps = radius * 0.1
        for step in range(30):
            # Compute variance at current points
            v_center = self.compute_variances(pts, radius=radius)
            
            # Compute numerical gradient of variance
            # Offset points in x, y, z
            pts_x = pts + np.array([eps, 0, 0])
            pts_y = pts + np.array([0, eps, 0])
            pts_z = pts + np.array([0, 0, eps])
            
            v_x = self.compute_variances(pts_x, radius=radius)
            v_y = self.compute_variances(pts_y, radius=radius)
            v_z = self.compute_variances(pts_z, radius=radius)
            
            # Gradient vector of variance (N, 3)
            grad_v = np.column_stack([
                (v_x - v_center) / eps,
                (v_y - v_center) / eps,
                (v_z - v_center) / eps
            ])
            
            # Surface normals (N, 3)
            normals = self.compute_normals(pts, epsilon=1e-4)
            
            # Project variance gradient onto surface tangent plane
            # v_proj = grad_v - dot(grad_v, normal) * normal
            dots = np.sum(grad_v * normals, axis=1)[:, np.newaxis]
            grad_v_proj = grad_v - dots * normals
            
            # Normalize projected gradient to safely step
            mags = np.linalg.norm(grad_v_proj, axis=1, keepdims=True)
            valid_mags = mags > 1e-8
            
            step_dir = np.zeros_like(grad_v_proj)
            step_dir[valid_mags[:, 0]] = grad_v_proj[valid_mags[:, 0]] / mags[valid_mags[:, 0]]
            
            # Take step
            # Scale learning rate by how far we are from the max variance (approx 0.3)
            # This makes them slow down as they reach the peak
            scale = np.clip(0.35 - v_center, 0.05, 0.35)[:, np.newaxis]
            pts = pts + step_dir * lr * (scale * 5.0)
            
            # Correction: Snap back to exact surface (SDF=0)
            d = self._evaluate_local(pts)
            n = self.compute_normals(pts, epsilon=1e-4)
            pts = pts - n * d[:, np.newaxis]
            
            # Bound points to object bounding box so they don't fly off to infinity
            # add a small margin
            margin_vec = size * 0.1
            pts = np.clip(pts, bound_min - margin_vec, bound_max + margin_vec)
            
            if step % 5 == 0:
                FreeCAD.Console.PrintMessage(f"SDFRenderer: Ascent step {step}, Max Var={np.max(v_center):.3f}...\n")
            
        # 4. Filter strictly to the converged edges
        final_vars = self.compute_variances(pts, radius=radius)
        
        FreeCAD.Console.PrintMessage(f"SDFRenderer: Max final variance: {np.max(final_vars):.3f}\n")
        
        # Valid 90 degree sharp edge is usually around 0.28
        edge_pts = pts[final_vars > 0.20]
        if len(edge_pts) == 0:
            return []
            
        # Temporarily return just the optimized edge seed points 
        # instead of undertaking the expensive curve crawling.
        return edge_pts

    def compute_normals(self, points, epsilon=1e-4):
        """
        Compute normalized gradients (normals) for points via central finite difference.
        """
        n_points = len(points)
        if n_points == 0: return np.zeros((0, 3))
        
        sx = np.array([epsilon, 0, 0])
        sy = np.array([0, epsilon, 0])
        sz = np.array([0, 0, epsilon])
        
        # 6 samples per point
        # Efficient batching
        
        # Shapes:
        # P: (N, 3)
        # Q: (N, 6, 3) -> flattened (N*6, 3)
        
        P = points[:, np.newaxis, :] # (N, 1, 3)
        offsets = np.vstack([sx, -sx, sy, -sy, sz, -sz]) # (6, 3)
        
        Q = P + offsets # Broadcasting -> (N, 6, 3)
        Q_flat = Q.reshape(-1, 3)
        
        vals = self._evaluate_local(Q_flat) # (N*6)
        vals = vals.reshape(n_points, 6)
        
        # Gradients
        gx = vals[:, 0] - vals[:, 1]
        gy = vals[:, 2] - vals[:, 3]
        gz = vals[:, 4] - vals[:, 5]
        
        grads = np.stack([gx, gy, gz], axis=1) # (N, 3)
        
        # Normalize
        mags = np.linalg.norm(grads, axis=1, keepdims=True)
        mags[mags < 1e-12] = 1.0 # Avoid div zero
        
        normals = grads / mags
        return normals

    def compute_variances(self, points, radius=None):
        """
        Compute surface normal variance for given points.
        Returns: scores (0.0 to ~1.0)
        """
        n_points = len(points)
        if n_points == 0: return np.array([])
        
        r = radius if radius is not None else 0.2
        
        # Use 6-axis sampling (deterministic)
        offsets = np.array([
            [r, 0, 0], [-r, 0, 0],
            [0, r, 0], [0, -r, 0],
            [0, 0, r], [0, 0, -r]
        ])
        n_samples = len(offsets)
        
        samples = points[:, np.newaxis, :] + offsets[np.newaxis, :, :]
        samples_flat = samples.reshape(-1, 3)
        
        # Compute normals for all samples
        # Reuse separate compute_normals? Yes.
        # But compute_normals uses epsilon for fd. radius here is for variance "search" radius.
        
        norms_flat = self.compute_normals(samples_flat, epsilon=1e-4)
        norms_grouped = norms_flat.reshape(n_points, n_samples, 3)
        
        # Variance = 1 - length(mean_normal)
        mean_norm = np.mean(norms_grouped, axis=1)
        len_mean = np.linalg.norm(mean_norm, axis=1)
        scores = 1.0 - len_mean
        
        return scores

    def segment_point_cloud(self, points, normals, scores, edge_threshold=0.2, 
                            normal_similarity=0.9, neighbor_radius=None):
        from scipy.spatial import KDTree
        
        n_points = len(points)
        
        # 1. Separate edge points from surface points
        is_edge = scores > edge_threshold
        edge_indices = np.where(is_edge)[0]
        surface_indices = np.where(~is_edge)[0]
        
        if len(surface_indices) == 0:
            return [], edge_indices, {}
        
        # 2. Auto-compute neighbor radius if not given
        if neighbor_radius is None:
            bound_min, bound_max = self._bounds_local()
            size = bound_max - bound_min
            # Use geometric size / 5.0 for robustness against sparse voxel spawning
            neighbor_radius = np.max(size) / 5.0
            
        # 3. Build KD-tree for surface points only
        surface_points = points[surface_indices]
        surface_normals = normals[surface_indices]
        tree = KDTree(surface_points)
        
        # 4. Region growing
        visited = np.zeros(len(surface_indices), dtype=bool)
        patches = []
        for i in range(len(surface_indices)):
            if visited[i]:
                continue
            
            patch = []
            stack = [i]
            visited[i] = True
            
            while stack:
                curr = stack.pop()
                patch.append(surface_indices[curr])  # Store original index
                
                # Find spatial neighbors
                neighbors = tree.query_ball_point(surface_points[curr], neighbor_radius * 1.5)
                
                for nb in neighbors:
                    if visited[nb]:
                        continue
                    
                    # Check normal similarity
                    dot = np.dot(surface_normals[curr], surface_normals[nb])
                    if dot >= normal_similarity:
                        visited[nb] = True
                        stack.append(nb)
            
            if len(patch) >= 3:
                patches.append(patch)
        
        # 5. Build patch adjacency
        adjacency = {}
        if len(patches) >= 2:
            patch_trees = []
            for patch in patches:
                patch_trees.append(KDTree(points[patch]))
                
            # Proximity-based adjacency between patches
            # Check every pair of patches
            for a in range(len(patches)):
                for b in range(a + 1, len(patches)):
                    # Check if patch A and patch B are close to each other
                    # We can use KDTree.count_neighbors or query_ball_tree
                    # A faster way: query points of patch B against tree of patch A
                    dists, _ = patch_trees[a].query(points[patches[b]], k=1)
                    # If the minimum distance is within an acceptable gap (e.g. 3 * neighbor_radius)
                    min_dist = np.min(dists) if len(dists) > 0 else float('inf')
                    
                    if min_dist < neighbor_radius * 4.0:
                        key = (a, b)
                        # Find "edge" points that might belong to the intersection
                        # Here we just store empty or a generic edge point reference
                        adjacency[key] = []
                        
                        # Optionally, if we DO have edge points, map them to this adjacency
                        if len(edge_indices) > 0:
                            edge_pts = points[edge_indices]
                            dists_a, _ = patch_trees[a].query(edge_pts)
                            dists_b, _ = patch_trees[b].query(edge_pts)
                            # points close to BOTH patch A and B
                            valid = (dists_a < neighbor_radius * 3.0) & (dists_b < neighbor_radius * 3.0)
                            adjacency[key] = edge_indices[valid].tolist()

        return patches, edge_indices, adjacency



class SDFCapsule(SDFObject):
    def __init__(self, length, radius):
        super().__init__()
        self.length = length
        self.radius = radius
        # Defined along Z axis, centered at origin
        # Point A: (0, 0, -length/2)
        # Point B: (0, 0, length/2)
        self.a = np.array([0, 0, -length/2.0])
        self.b = np.array([0, 0, length/2.0])
        self.ba = self.b - self.a
        self.baba = np.dot(self.ba, self.ba)

    def _evaluate_local(self, points):
        # points: (N, 3)
        
        # pa = p - a
        # shape (N, 3)
        pa = points - self.a
        
        # h = clamp( dot(pa, ba) / dot(ba, ba), 0.0, 1.0 )
        # dot(pa, ba): (N,)
        dot_pa_ba = np.dot(pa, self.ba)
        
        h = np.clip(dot_pa_ba / self.baba, 0.0, 1.0)
        
        # pa - ba * h
        # ba * h: (N, 3) because h is (N,) and ba is (3,)
        # We need to reshape h for broadcasting
        h_vec = h.reshape(-1, 1)
        
        diff = pa - self.ba * h_vec
        
        return np.linalg.norm(diff, axis=1) - self.radius

    def _bounds_local(self):
        # Local bounds
        # Z: -L/2 - R to L/2 + R
        # X, Y: -R to R
        half_z = self.length / 2.0 + self.radius
        r = self.radius
        return (np.array([-r, -r, -half_z]), np.array([r, r, half_z]))





class SDFOperation(SDFObject):
    def __init__(self, sdf_a, sdf_b):
        super().__init__()
        self.sdf_a = sdf_a
        self.sdf_b = sdf_b
        
    def _combine(self, d1, d2):
        raise NotImplementedError
        
    def _evaluate_local(self, points):
        # points are in Local coordinates of this Boolean object.
        # But sdf_a and sdf_b are independent objects with their own transforms,
        # so they expect World coordinates.
        # We must transform points: Local -> World
        
        world_points = points
        if self.matrix is not None:
             # Add homogeneous w=1
             ones = np.ones((points.shape[0], 1))
             pts_h = np.hstack((points, ones))
             # Local -> World: dot(pts, matrix)
             w_pts_h = np.dot(pts_h, self.matrix)
             world_points = w_pts_h[:, :3]

        d1 = self.sdf_a.evaluate(world_points)
        d2 = self.sdf_b.evaluate(world_points)
        return self._combine(d1, d2)

    def _get_child_bounds_in_local(self, child):
        """
        Helper to get child's world bounds and transform them into 
        this operation's local coordinate system.
        """
        c_min, c_max = child.bounds() # World Bounds
        
        if self.inverse_matrix is None:
            return c_min, c_max
            
        # Transform World Bounds -> Local
        # Note: Bounds are AABB. Rotating AABB is complex.
        # We transform the 8 corners of the World AABB and find new Local AABB.
        
        corners = [
            [c_min[0], c_min[1], c_min[2]],
            [c_min[0], c_min[1], c_max[2]],
            [c_min[0], c_max[1], c_min[2]],
            [c_min[0], c_max[1], c_max[2]],
            [c_max[0], c_min[1], c_min[2]],
            [c_max[0], c_min[1], c_max[2]],
            [c_max[0], c_max[1], c_min[2]],
            [c_max[0], c_max[1], c_max[2]]
        ]
        
        corners = np.array(corners)
        ones = np.ones((8, 1))
        corners_h = np.hstack((corners, ones))
        
        # World -> Local
        l_corners_h = np.dot(corners_h, self.inverse_matrix)
        l_corners = l_corners_h[:, :3]
        
        l_min = np.min(l_corners, axis=0)
        l_max = np.max(l_corners, axis=0)
        
        return l_min, l_max

class SDFUnion(SDFOperation):
    def _combine(self, d1, d2):
        return np.minimum(d1, d2)
        
    def _bounds_local(self):
        min_a, max_a = self._get_child_bounds_in_local(self.sdf_a)
        min_b, max_b = self._get_child_bounds_in_local(self.sdf_b)
        return np.minimum(min_a, min_b), np.maximum(max_a, max_b)

class SDFDifference(SDFOperation):
    def _combine(self, d1, d2):
        return np.maximum(d1, -d2)
        
    def _bounds_local(self):
        # Difference bounds is at most A
        return self._get_child_bounds_in_local(self.sdf_a)

class SDFIntersection(SDFOperation):
    def _combine(self, d1, d2):
        return np.maximum(d1, d2)
        
    def _bounds_local(self):
        min_a, max_a = self._get_child_bounds_in_local(self.sdf_a)
        min_b, max_b = self._get_child_bounds_in_local(self.sdf_b)
        return np.maximum(min_a, min_b), np.minimum(max_a, max_b)




