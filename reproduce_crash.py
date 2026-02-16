import numpy as np
import sys
import os

# Ensure path
current_dir = os.getcwd()
if current_dir not in sys.path:
    sys.path.append(current_dir)

from FCDirectModeling import sdf_lib

def test_sphere_crash(resolution=32):
    print(f"Testing Sphere SDF with resolution {resolution}...")
    
    # 1. Create Sphere
    sphere = sdf_lib.SDFSphere(radius=5.0)
    
    # 2. Evaluate and Mesh
    print("Generating mesh...")
    try:
        verts, faces, normals = sdf_lib.mesh_from_sdf(sphere, resolution=resolution, margin=0.2)
        print(f"Success! Verts: {len(verts) if verts is not None else 'None'}")
        if verts is not None and len(verts) > 0:
            # Check for NaNs or Infs
            if np.any(np.isnan(verts)):
                print("WARNING: NaNs in vertices!")
            if np.any(np.isinf(verts)):
                print("WARNING: Infs in vertices!")
                
            print(f"Faces: {faces.shape}")
            print(f"Normals: {normals.shape}")
            
    except Exception as e:
        print(f"CRASHED during mesh generation: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_sphere_crash(32)
