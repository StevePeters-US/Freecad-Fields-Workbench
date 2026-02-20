import sys
import types
sys.modules['FreeCAD'] = types.ModuleType('FreeCAD')
sys.modules['FreeCADGui'] = types.ModuleType('FreeCADGui')
sys.modules['pivy'] = types.ModuleType('pivy')
sys.modules['pivy.coin'] = types.ModuleType('coin')
sys.modules['PySide'] = types.ModuleType('PySide')
sys.modules['PySide.QtCore'] = types.ModuleType('QtCore')
sys.modules['PySide.QtGui'] = types.ModuleType('QtGui')
sys.modules['Part'] = types.ModuleType('Part')

import numpy as np
from FCDirectModeling.primitives.box import SDFBox

# Create a box
box = SDFBox(10.0)

# Generate a point cloud
pts = box.generate_point_cloud(resolution=10) # Coarse grid, gaps expected
print("Initial points:", len(pts))

# Evaluate variance at corners vs planes
v_init = box.compute_variances(pts, radius=0.2)
edge_points = pts[v_init > 0.05]
flat_points = pts[v_init <= 0.05]

print(f"init edges: {len(edge_points)}")
print(f"init flat: {len(flat_points)}")

# Snap them!
if len(edge_points) > 0:
    snapped = box.snap_to_edges(edge_points, radius=0.2, iterations=10, step_size=0.1)
    
    # See if they moved
    d = np.linalg.norm(snapped - edge_points, axis=1)
    print("Avg move distance:", np.mean(d))
    print("Max move distance:", np.max(d))
    
    # Check new variance
    v_final = box.compute_variances(snapped, radius=0.2)
    print("Avg initial variance:", np.mean(v_init[v_init > 0.05]))
    print("Avg final variance:", np.mean(v_final))
