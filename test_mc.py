import numpy as np
import sys
import os

# Ensure path
current_dir = os.getcwd()
if current_dir not in sys.path:
    sys.path.append(current_dir)

from FCDirectModeling import marching_cubes

def test_mc_direct():
    print("Testing marching_cubes directly...", flush=True)
    # Create simple volume (sphere-like)
    size = 16
    x, y, z = np.meshgrid(np.linspace(-1,1,size), np.linspace(-1,1,size), np.linspace(-1,1,size))
    vol = np.sqrt(x**2 + y**2 + z**2)
    
    print(f"Volume shape: {vol.shape}", flush=True)
    
    try:
        verts, faces, normals, values = marching_cubes.marching_cubes(vol, level=0.5)
        print(f"Success! Verts: {len(verts)}", flush=True)
    except Exception as e:
        print(f"Crash: {e}", flush=True)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_mc_direct()
