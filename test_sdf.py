
import sys
import os
import FreeCAD

# Add local path to sys.path if running as macro from this dir
# Or assume installed in Mod
if FreeCAD.GuiUp:
    print("Running in FreeCAD GUI")

# Try to find the module locally if not installed
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

import numpy as np
from FCDirectModeling import sdf_lib

def test_box_sdf():
    try:
        import skimage
        print(f"Skimage version: {skimage.__version__}")
    except ImportError:
        FreeCAD.Console.PrintError("Skimage not found!\n")
        return

    box = sdf_lib.SDFBox(size=(10, 20, 30))
    print(f"Box bounds: {box.bounds()}")
    
    # Test point inside
    p_in = np.array([[0, 0, 0]])
    d_in = box.evaluate(p_in)
    print(f"Distance at center (should be negative): {d_in}")
    
    # Test point outside
    p_out = np.array([[10, 0, 0]])
    d_out = box.evaluate(p_out)
    print(f"Distance at (10,0,0) (should be positive): {d_out}")
    
    # Mesh
    verts, faces = sdf_lib.mesh_from_sdf(box, resolution=32)
    
    if verts is not None:
        print(f"Generated mesh with {len(verts)} vertices and {len(faces)} faces.")
    else:
        print("Failed to generate mesh.")

if __name__ == "__main__":
    test_box_sdf()
