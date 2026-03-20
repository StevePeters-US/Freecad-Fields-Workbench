"""Test SDF boolean composition — runs without FreeCAD."""
import sys, os
from types import ModuleType
import numpy as np

# Minimal FreeCAD stubs
fc = ModuleType("FreeCAD")
class _Vec:
    def __init__(self, x=0, y=0, z=0): self.x=x; self.y=y; self.z=z
    def __add__(self, o): return _Vec(self.x+o.x, self.y+o.y, self.z+o.z)
    def __sub__(self, o): return _Vec(self.x-o.x, self.y-o.y, self.z-o.z)
    def __mul__(self, s): return _Vec(self.x*s, self.y*s, self.z*s)
    def __neg__(self): return _Vec(-self.x, -self.y, -self.z)
    @property
    def Length(self): return (self.x**2+self.y**2+self.z**2)**0.5
fc.Vector = _Vec
class _Placement:
    def __init__(self, *args, **kwargs): pass
fc.Placement = _Placement
sys.modules["FreeCAD"] = fc

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.sdf.sdf.sphere import SdfSphereField
from core.sdf.sdf.box import SdfBoxField
from core.sdf.sdf_composer import UnionField, SubtractionField, IntersectionField


def test_union():
    """Union of two non-overlapping spheres returns min() of both."""
    s1 = SdfSphereField(center=fc.Vector(-5, 0, 0), radius=3.0)
    s2 = SdfSphereField(center=fc.Vector( 5, 0, 0), radius=3.0)
    u = UnionField(s1, s2)

    # Point at origin: equidistant from both spheres, inside neither
    val = u.evaluate(fc.Vector(0, 0, 0))
    assert val > 0, f"Origin should be outside union, got {val}"

    # Point inside s1
    val = u.evaluate(fc.Vector(-5, 0, 0))
    assert val < 0, f"Center of s1 should be inside union, got {val}"

    # Point inside s2
    val = u.evaluate(fc.Vector(5, 0, 0))
    assert val < 0, f"Center of s2 should be inside union, got {val}"

    # Bounding box should enclose both spheres
    mn, mx = u.bounding_box()
    assert mn.x <= -8.0, f"bbox min.x should be <= -8, got {mn.x}"
    assert mx.x >=  8.0, f"bbox max.x should be >= 8, got {mx.x}"

    print("PASS: test_union", flush=True)


def test_subtraction():
    """Subtraction: big sphere minus small sphere creates a hollow."""
    big = SdfSphereField(center=fc.Vector(0, 0, 0), radius=10.0)
    small = SdfSphereField(center=fc.Vector(0, 0, 0), radius=5.0)
    sub = SubtractionField(big, small)

    # Point at origin: inside small sphere, so outside the subtraction
    val = sub.evaluate(fc.Vector(0, 0, 0))
    assert val > 0, f"Origin should be outside subtraction, got {val}"

    # Point at radius 7.5: inside big, outside small → inside result
    val = sub.evaluate(fc.Vector(7.5, 0, 0))
    assert val < 0, f"Shell point should be inside subtraction, got {val}"

    # Point at radius 15: outside both → outside result
    val = sub.evaluate(fc.Vector(15, 0, 0))
    assert val > 0, f"Far point should be outside subtraction, got {val}"

    print("PASS: test_subtraction", flush=True)


def test_intersection():
    """Intersection of two overlapping spheres."""
    s1 = SdfSphereField(center=fc.Vector(-2, 0, 0), radius=5.0)
    s2 = SdfSphereField(center=fc.Vector( 2, 0, 0), radius=5.0)
    inter = IntersectionField(s1, s2)

    # Origin: inside both → inside intersection
    val = inter.evaluate(fc.Vector(0, 0, 0))
    assert val < 0, f"Origin should be inside intersection, got {val}"

    # Point far left: inside s1 only → outside intersection
    val = inter.evaluate(fc.Vector(-6, 0, 0))
    assert val > 0, f"Far left should be outside intersection, got {val}"

    print("PASS: test_intersection", flush=True)


def test_evaluate_grid_matches():
    """Batch evaluate_grid() must match pointwise evaluate()."""
    s1 = SdfSphereField(center=fc.Vector(0, 0, 0), radius=5.0)
    s2 = SdfSphereField(center=fc.Vector(3, 0, 0), radius=4.0)
    u = UnionField(s1, s2)

    pts = np.array([
        [0, 0, 0], [3, 0, 0], [10, 0, 0], [-5, 0, 0], [1.5, 2, 1]
    ], dtype=np.float32)

    batch = u.evaluate_grid(pts)
    for i in range(len(pts)):
        single = u.evaluate(fc.Vector(*pts[i]))
        assert abs(batch[i] - single) < 1e-5, (
            f"Mismatch at point {i}: batch={batch[i]}, single={single}"
        )

    print("PASS: test_evaluate_grid_matches", flush=True)


def test_chained_booleans():
    """Verify chained booleans: (A ∪ B) - C."""
    a = SdfBoxField(center=fc.Vector(0, 0, 0), size=fc.Vector(10, 10, 10))
    b = SdfSphereField(center=fc.Vector(8, 0, 0), radius=3.0)
    c = SdfSphereField(center=fc.Vector(0, 0, 0), radius=2.0)

    union_ab = UnionField(a, b)
    result = SubtractionField(union_ab, c)

    # Origin: inside A but also inside C → should be outside result
    val = result.evaluate(fc.Vector(0, 0, 0))
    assert val > 0, f"Origin should be outside (A∪B)-C, got {val}"

    # (4,0,0): inside A, outside C → inside result
    val = result.evaluate(fc.Vector(4, 0, 0))
    assert val < 0, f"(4,0,0) should be inside (A∪B)-C, got {val}"

    # (8,0,0): inside B, outside C → inside result
    val = result.evaluate(fc.Vector(8, 0, 0))
    assert val < 0, f"(8,0,0) should be inside (A∪B)-C, got {val}"

    print("PASS: test_chained_booleans", flush=True)


if __name__ == "__main__":
    try:
        test_union()
        test_subtraction()
        test_intersection()
        test_evaluate_grid_matches()
        test_chained_booleans()
        print("\nAll SDF boolean tests passed.", flush=True)
    except Exception as e:
        print(f"Test FAILED: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
