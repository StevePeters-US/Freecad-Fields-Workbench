import sys
import os
import numpy as np

# Ensure FCDirectModeling is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from FCDirectModeling.sdf_mesher import extract_mesh_numpy

def box_sdf(X, Y, Z, extents):
    qx = np.abs(X) - extents[0]
    qy = np.abs(Y) - extents[1]
    qz = np.abs(Z) - extents[2]
    
    out_dist = np.sqrt(np.maximum(qx, 0)**2 + np.maximum(qy, 0)**2 + np.maximum(qz, 0)**2)
    in_dist = np.minimum(np.maximum(qx, np.maximum(qy, qz)), 0)
    
    return out_dist + in_dist

extents = [5.0, 5.0, 5.0]

vertices, triangles = extract_mesh_numpy(
    lambda X,Y,Z: box_sdf(X, Y, Z, extents), 
    mn=[-6.0, -6.0, -6.0], 
    mx=[6.0, 6.0, 6.0], 
    resolution=12
)

# Surface Nets will naturally place vertices in *coplanar* sheets along flat faces,
# but those are completely flat. To get "8 vertices" we'd need to collapse coplanar tris
# or decimate the mesh. Let's verify that all 488 vertices lie exactly on one of the 6 bounding planes 
# defined by the extents (-5, 5).

pts = np.array(vertices)

# Check how many points are ON the mathematical face: (abs(x) == 5, abs(y) == 5, abs(z) == 5)
tolerance = 1e-4

on_x = np.isclose(np.abs(pts[:, 0]), 5.0, atol=tolerance)
on_y = np.isclose(np.abs(pts[:, 1]), 5.0, atol=tolerance)
on_z = np.isclose(np.abs(pts[:, 2]), 5.0, atol=tolerance)

# A point must be on AT LEAST one geometrical face to be valid
on_any_face = on_x | on_y | on_z

num_valid = np.sum(on_any_face)
print(f"Total vertices: {len(pts)}")
print(f"Vertices exactly on the true mathematical planes: {num_valid}")

if num_valid == len(pts):
    print("SUCCESS: The Naive Surface Nets perfectly reconstructed the sharp planar faces without rounding off corners!")
else:
    print(f"FAILED: {len(pts) - num_valid} vertices did not lie on a perfect plane, meaning the algorithm rounded things.")
