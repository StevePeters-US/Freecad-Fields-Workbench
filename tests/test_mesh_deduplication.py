import numpy as np
import sys
import os
from types import ModuleType

# Minimal FreeCAD stubs
fc = ModuleType("FreeCAD")
class _Vec:
    def __init__(self, x=0, y=0, z=0): self.x=x; self.y=y; self.z=z
    def __repr__(self): return f"Vector({self.x}, {self.y}, {self.z})"
fc.Vector = _Vec
fc.ParamGet = lambda path: ModuleType("Param") # Simplified stub
sys.modules["FreeCAD"] = fc

part = ModuleType("Part")
sys.modules["Part"] = part

# Add the project root to sys.path to allow importing from core
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock dm_object preferences
import core.dm_object
core.dm_object.get_deduplicate_enabled = lambda: True

from core.dm_mesher import deduplicate_verts

def test_deduplicate_quad():
    print("Testing deduplicate_verts with a simple quad (2 triangles)...")
    
    # Simple quad: two triangles sharing two vertices
    # T1: (0,0,0), (1,0,0), (1,1,0)
    # T2: (0,0,0), (1,1,0), (0,1,0)
    
    flat_verts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0],  # T1
        [0, 0, 0], [1, 1, 0], [0, 1, 0]   # T2
    ], dtype=np.float32)
    
    # Coin3D format: [0, 1, 2, -1, 3, 4, 5, -1]
    flat_idx = np.array([0, 1, 2, -1, 3, 4, 5, -1], dtype=np.int32)
    
    unique_verts, new_idx = deduplicate_verts(flat_verts, flat_idx)
    
    print(f"Original vertices: {len(flat_verts)}")
    print(f"Unique vertices: {len(unique_verts)}")
    print(f"New index buffer: {new_idx.tolist()}")
    
    assert len(unique_verts) == 4, f"Expected 4 unique vertices, got {len(unique_verts)}"
    assert len(new_idx) == 8, f"Expected 8 indices (including -1s), got {len(new_idx)}"
    
    # Check if triangles are preserved in terms of topology
    # T1 unique indices should be some permutation of {A, B, C}
    # T2 unique indices should be some permutation of {A, C, D}
    
    t1_indices = set(new_idx[0:3])
    t2_indices = set(new_idx[4:7])
    
    assert len(t1_indices) == 3
    assert len(t2_indices) == 3
    
    # They should share exactly 2 vertices
    intersection = t1_indices.intersection(t2_indices)
    assert len(intersection) == 2, f"Expected 2 shared vertices, got {len(intersection)}"
    
    print("Test passed: Simple quad deduplicated correctly.")

def test_empty_mesh():
    print("\nTesting deduplicate_verts with an empty mesh...")
    flat_verts = np.zeros((0, 3), dtype=np.float32)
    flat_idx = np.zeros(0, dtype=np.int32)
    
    v, i = deduplicate_verts(flat_verts, flat_idx)
    assert len(v) == 0
    assert len(i) == 0
    print("Test passed: Empty mesh handled correctly.")

def test_tolerance():
    print("\nTesting deduplicate_verts with tolerance...")
    # Vertices very close to each other
    flat_verts = np.array([
        [0, 0, 0],
        [0.000001, 0, 0],
        [1, 0, 0]
    ], dtype=np.float32)
    flat_idx = np.array([0, 1, 2, -1], dtype=np.int32)
    
    # With default tol=1e-5, [0,0,0] and [1e-6, 0, 0] should merge
    v, i = deduplicate_verts(flat_verts, flat_idx, tol=1e-5)
    assert len(v) == 2, f"Expected 2 unique vertices with tolerance, got {len(v)}"
    print("Test passed: Tolerance merging worked.")

if __name__ == "__main__":
    try:
        test_deduplicate_quad()
        test_empty_mesh()
        test_tolerance()
        print("\nAll deduplication tests passed!")
    except Exception as e:
        print(f"\nTest FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
