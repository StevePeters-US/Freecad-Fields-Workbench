import os
import sys
import numpy as np
import FreeCAD
import FreeCADGui

from FCDirectModeling.primitives.box import SDFBox
from FCDirectModeling.primitives.sphere import SDFSphere
from FCDirectModeling.primitives.cone import SDFCone
from FCDirectModeling import curve_extraction

# Create bodies
box = SDFBox(np.array([10.0, 10.0, 10.0]))

# Test trace_edges
print("--- TRACING BOX EDGES ---")
edge_pts = box.trace_edges(num_seeds=500, variance_threshold=0.5)
print(f"Traced {len(edge_pts)} edge points")

if len(edge_pts) > 0:
    print("Edge point var: ", np.max(box.compute_variances(edge_pts)))
