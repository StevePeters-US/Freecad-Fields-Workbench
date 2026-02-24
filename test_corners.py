import numpy as np

class SDFBox:
    def __init__(self, size):
        self.half_size = np.array([size, size, size]) / 2.0
        
    def _evaluate_local(self, points):
        d = np.abs(points) - self.half_size
        return np.linalg.norm(np.maximum(d, 0.0), axis=1) + np.minimum(np.max(d, axis=1), 0.0)

    def _bounds_local(self):
        return (-self.half_size, self.half_size)

    def compute_normals(self, points, epsilon=1e-4):
        sx = np.array([epsilon, 0, 0])
        sy = np.array([0, epsilon, 0])
        sz = np.array([0, 0, epsilon])
        
        P = points[:, np.newaxis, :] 
        offsets = np.vstack([sx, -sx, sy, -sy, sz, -sz])
        
        Q = P + offsets 
        Q_flat = Q.reshape(-1, 3)
        vals = self._evaluate_local(Q_flat).reshape(len(points), 6)
        
        gx = vals[:, 0] - vals[:, 1]
        gy = vals[:, 2] - vals[:, 3]
        gz = vals[:, 4] - vals[:, 5]
        
        grads = np.stack([gx, gy, gz], axis=1) 
        mags = np.linalg.norm(grads, axis=1, keepdims=True)
        mags[mags < 1e-12] = 1.0 
        return grads / mags

    def compute_covariances(self, points, radius=0.5):
        # Sample 14 points around a sphere
        phi = (1 + np.sqrt(5)) / 2
        dirs = np.array([
            [-1,  phi, 0], [ 1,  phi, 0], [-1, -phi, 0], [ 1, -phi, 0],
            [0, -1,  phi], [0,  1,  phi], [0, -1, -phi], [0,  1, -phi],
            [ phi, 0, -1], [ phi, 0,  1], [-phi, 0, -1], [-phi, 0,  1]
        ])
        dirs = dirs / np.linalg.norm(dirs, axis=1, keepdims=True)
        offsets = dirs * radius
        n_samples = len(offsets)
        
        samples = points[:, np.newaxis, :] + offsets[np.newaxis, :, :]
        samples_flat = samples.reshape(-1, 3)
        
        norms_flat = self.compute_normals(samples_flat, epsilon=1e-4)
        norms_grouped = norms_flat.reshape(len(points), n_samples, 3)
        
        # Compute covariance matrix for each point
        covs = np.zeros((len(points), 3, 3))
        for i in range(len(points)):
            ns = norms_grouped[i]
            # ns is (12, 3), rows are normals
            cov = np.cov(ns, rowvar=False)
            covs[i] = cov
            
        return covs

    def compute_variances(self, points, radius=0.2):
        covs = self.compute_covariances(points, radius=radius)
        # Variance is the trace of the covariance of normals
        return np.trace(covs, axis1=1, axis2=2)

box = SDFBox(10.0)
bound_min, bound_max = box._bounds_local()
size = bound_max - bound_min

rng = np.random.default_rng(42)
pts = bound_min + rng.random((2000, 3)) * size

# Snap
for _ in range(5):
    dists = box._evaluate_local(pts)
    grads = box.compute_normals(pts, epsilon=1e-4)
    pts = pts - grads * dists[:, np.newaxis]

# Find points near corners using L2
# We use radius = np.max(size) * 0.1 so the corner basin is decently large
radius = np.max(size) * 0.1
covs = box.compute_covariances(pts, radius=radius)
l2_vals = np.sort(np.linalg.eigvalsh(covs), axis=1)[:, 1]

# Keep only points where L2 is significant
corner_pts = pts[l2_vals > 0.05]
print(f"Initial corner points found: {len(corner_pts)}")

if len(corner_pts) > 0:
    lr = radius * 0.5
    eps = radius * 0.05
    
    # Gradient ascent on variance to pull them exactly to the sharp corner!
    for step in range(30):
        v_center = box.compute_variances(corner_pts, radius=radius)
        
        pts_x = corner_pts + np.array([eps, 0, 0])
        pts_y = corner_pts + np.array([0, eps, 0])
        pts_z = corner_pts + np.array([0, 0, eps])
        
        v_x = box.compute_variances(pts_x, radius=radius)
        v_y = box.compute_variances(pts_y, radius=radius)
        v_z = box.compute_variances(pts_z, radius=radius)
        
        grad_v = np.column_stack([
            (v_x - v_center) / eps,
            (v_y - v_center) / eps,
            (v_z - v_center) / eps
        ])
        
        normals = box.compute_normals(corner_pts)
        dots = np.sum(grad_v * normals, axis=1)[:, np.newaxis]
        grad_v_proj = grad_v - dots * normals
        
        mags = np.linalg.norm(grad_v_proj, axis=1, keepdims=True)
        valid_mags = mags > 1e-8
        
        step_dir = np.zeros_like(grad_v_proj)
        step_dir[valid_mags[:, 0]] = grad_v_proj[valid_mags[:, 0]] / mags[valid_mags[:, 0]]
        
        corner_pts = corner_pts + step_dir * lr
        
        # Snap back
        for _ in range(2):
            d = box._evaluate_local(corner_pts)
            n = box.compute_normals(corner_pts)
            corner_pts = corner_pts - n * d[:, np.newaxis]
            
    # Cluster the perfectly snapped corner points
    visited = set()
    unique_corners = []
    for i in range(len(corner_pts)):
        if i in visited: continue
        pt = corner_pts[i]
        cluster = [pt]
        visited.add(i)
        for j in range(i+1, len(corner_pts)):
            if j not in visited and np.linalg.norm(corner_pts[j] - pt) < 0.5:
                cluster.append(corner_pts[j])
                visited.add(j)
        mean_pt = np.mean(cluster, axis=0)
        unique_corners.append(mean_pt)
        
    print(f"Unique corners found: {len(unique_corners)}")
    for uc in unique_corners:
        print(np.round(uc, 3))
