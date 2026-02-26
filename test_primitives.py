import numpy as np
import sys
sys.path.append('/home/steve/Documents/Github/Freecad-Direct-Modeling')
from FCDirectModeling.sdf_mesher import extract_mesh_numpy

r = 5.0
h = 10.0

def cone_sdf(X, Y, Z):
    Z_clamped = np.clip(Z, 0, h)
    current_radius = r * (1.0 - Z_clamped / h) if h > 1e-4 else r
    dist_xy = np.sqrt(X**2 + Y**2)
    dist_surface = dist_xy - current_radius
    dist_bottom = -Z
    dist_top = Z - h
    return np.maximum(dist_surface, np.maximum(dist_bottom, dist_top))

margin = r * 0.2 + 0.5
eval_min = np.array([-r - margin, -r - margin, -margin])
eval_max = np.array([r + margin, r + margin, h + margin])

verts, tris = extract_mesh_numpy(cone_sdf, eval_min, eval_max, 15, True)
print(f"Cone: {len(verts)} verts, {len(tris)} tris")

def torus_sdf(X, Y, Z):
    R = 10.0
    r_minor = 1.0
    q_xy = np.sqrt(X**2 + Y**2) - R
    return np.sqrt(q_xy**2 + Z**2) - r_minor

margin = 1.0 * 0.2 + 0.5
total_r = 10.0 + 1.0
eval_min = np.array([-total_r - margin, -total_r - margin, -1.0 - margin])
eval_max = np.array([total_r + margin, total_r + margin, 1.0 + margin])

verts, tris = extract_mesh_numpy(torus_sdf, eval_min, eval_max, 15, True)
print(f"Torus res=15: {len(verts)} verts, {len(tris)} tris")

verts, tris = extract_mesh_numpy(torus_sdf, eval_min, eval_max, 40, True)
print(f"Torus res=40: {len(verts)} verts, {len(tris)} tris")
