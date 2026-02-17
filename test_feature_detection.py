
import sys
import os

# FreeCAD Path (adjust if needed, but FreeCADCmd should have it)
# Usually /usr/lib/freecad/lib or similar. 
# If running with FreeCADCmd, these imports should work directly.

import FreeCAD
import FreeCADGui
import Part

# Add local path
sys.path.append("/home/steve/Documents/Github/Freecad-Direct-Modeling")

# Import numpy from FreeCAD's environment if possible, or assume it's there
import numpy as np

from FCDirectModeling import sdf_lib

def test_feature_detection():
    print("Testing Feature Detection...")
    
    # Create a Box (Sharp edges)
    # Size 10x10x10 -> Half size 5x5x5
    box = sdf_lib.SDFBox(10.0)
    
    print(f"Box Bounds: {box.bounds()}")
    
    # Test mesh generation first (since that was failing)
    print("Generating Mesh...")
    verts, faces, normals = sdf_lib.mesh_from_sdf(box, resolution=32, margin=0.1)
    
    if verts is None:
        print("Mesh generation failed!")
        # Debug why
        # Check values at center
        center_val = box.evaluate(np.array([[0,0,0]]))
        print(f"SDF at center: {center_val}")
        
        # Check values at corner
        corner_val = box.evaluate(np.array([[5,5,5]]))
        print(f"SDF at corner (5,5,5): {corner_val}")
        
        # Check values outside
        out_val = box.evaluate(np.array([[6,6,6]]))
        print(f"SDF at outside (6,6,6): {out_val}")
    else:
        print(f"Mesh generated: {len(verts)} verts.")

    # Detect features
    print("Detecting Features...")
    points, scores = sdf_lib.detect_features(box, resolution=32, threshold=5.0)
    
    if points is None:
        print("No features found (points is None).")
        return
        
    print(f"Found {len(points)} feature points.")
    if len(points) > 0:
        print(f"Sample points: {points[:5]}")
        print(f"Sample scores: {scores[:5]}")
        
    # Check if points are near edges
    # Box edges are at +/-5 on two axes.
    near_edge_count = 0
    for p in points:
        close = np.isclose(np.abs(p), 5.0, atol=0.5)
        if np.sum(close) >= 2: # Edge or Corner
            near_edge_count += 1
            
    print(f"Points near edges: {near_edge_count} / {len(points)}")

if __name__ == "__main__":
    test_feature_detection()
