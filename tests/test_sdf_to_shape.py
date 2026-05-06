"""Test mesh export helper — requires FreeCAD runtime."""
import sys, os

# Setup FreeCAD environment (see freecad_env skill)
FREECAD_LIB = os.environ.get("FREECAD_LIB", "/usr/lib/freecad-python3/lib")
if os.path.isdir(FREECAD_LIB):
    sys.path.insert(0, FREECAD_LIB)

try:
    import FreeCAD
    import Part
    import Mesh
except ImportError:
    print("SKIP: FreeCAD not available in this environment", flush=True)
    sys.exit(0)

print(f"Adding to path: {os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))}", flush=True)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import numpy as np
from commands.cmd_sdf_export import _triangles_to_shape


def test_tetrahedron():
    """A 4-triangle tetrahedron should produce a valid solid."""
    verts = np.array([
        [0, 0, 0], [10, 0, 0], [5, 10, 0],  # tri 0
        [0, 0, 0], [5, 10, 0], [5, 5, 10],   # tri 1
        [10, 0, 0], [5, 5, 10], [5, 10, 0],  # tri 2
        [0, 0, 0], [5, 5, 10], [10, 0, 0],   # tri 3
    ], dtype=np.float32)
    idx = np.array([
        0, 1, 2, -1,
        3, 4, 5, -1,
        6, 7, 8, -1,
        9, 10, 11, -1,
    ], dtype=np.int32)

    print("Calling _triangles_to_shape...", flush=True)
    shape = _triangles_to_shape(verts, idx)
    assert shape is not None, "Shape should not be None"
    assert not shape.isNull(), "Shape should not be null"
    print(f"Shape type: {shape.ShapeType}", flush=True)
    assert shape.ShapeType in ["Solid", "Shell"], f"Expected Solid or Shell, got {shape.ShapeType}"
    print("PASS: test_tetrahedron", flush=True)

try:
    test_tetrahedron()
    print("\nAll SDF to Shape tests passed.", flush=True)
except Exception as e:
    print(f"Test FAILED: {e}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)
