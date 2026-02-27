import unittest
import FreeCAD as App
import Part
import math
print("DEBUG: Importing NURBS primitives...")
from FCDirectModeling import nurbs_primitives
print("DEBUG: NURBS primitives imported.")

class TestNURBSPrimitives(unittest.TestCase):
    
    def test_build_box_positive(self):
        """Test building a box with positive dimensions."""
        shape = nurbs_primitives.build_box(10, 20, 30)
        self.assertFalse(shape.isNull(), "Box shape should not be null")
        self.assertEqual(len(shape.Faces), 6, "Box should have 6 faces")
        # Check volume (roughly 10*20*30 = 6000)
        self.assertAlmostEqual(shape.Volume, 6000.0, places=1)

    def test_build_box_negative(self):
        """Test building a box with negative dimensions (extrude should handle this)."""
        shape = nurbs_primitives.build_box(-10, 20, -30)
        self.assertFalse(shape.isNull(), "Box shape should not be null")
        self.assertEqual(len(shape.Faces), 6, "Box should have 6 faces")
        self.assertAlmostEqual(abs(shape.Volume), 6000.0, places=1)

    def test_build_box_zero(self):
        """Test building a box with zero dimensions."""
        shape = nurbs_primitives.build_box(0, 10, 10)
        self.assertTrue(shape.isNull(), "Box with zero dimension should be null or empty")

    def test_build_sphere(self):
        """Test building a sphere."""
        shape = nurbs_primitives.build_sphere(10)
        self.assertFalse(shape.isNull(), "Sphere shape should not be null")
        # Increase tolerance for NURBS approximation
        self.assertAlmostEqual(shape.Volume, (4/3.0) * math.pi * 10**3, delta=5.0)

    def test_build_cone(self):
        """Test building a cone."""
        shape = nurbs_primitives.build_cone(10, 20)
        self.assertFalse(shape.isNull(), "Cone shape should not be null")
        # Volume of cone = 1/3 * pi * r^2 * h
        expected_vol = (1/3.0) * math.pi * 10**2 * 20
        self.assertAlmostEqual(shape.Volume, expected_vol, delta=20.0)

    def test_build_torus(self):
        """Test building a torus."""
        shape = nurbs_primitives.build_torus(20, 5) # Major R=20, minor r=5
        self.assertFalse(shape.isNull(), "Torus shape should not be null")
        # Volume of torus = 2 * pi^2 * R * r^2
        expected_vol = 2 * (math.pi**2) * 20 * (5**2)
        self.assertAlmostEqual(shape.Volume, expected_vol, delta=30.0)

    def test_build_torus_inverted_radii(self):
        """Test building a torus where major < minor (should swap)."""
        shape = nurbs_primitives.build_torus(5, 20)
        self.assertFalse(shape.isNull(), "Torus shape should not be null")
        expected_vol = 2 * (math.pi**2) * 20 * (5**2)
        self.assertAlmostEqual(shape.Volume, expected_vol, delta=30.0)

    def test_build_curve_basic(self):
        """Test building a NURBS curve from points."""
        pts = [
            App.Vector(0,0,0),
            App.Vector(10,10,0),
            App.Vector(20,0,0),
            App.Vector(30,10,0)
        ]
        shape = nurbs_primitives.build_curve(pts)
        self.assertFalse(shape.isNull(), "Curve shape should not be null")
        self.assertEqual(shape.ShapeType, "Edge", "Curve should be an Edge")
        # Length of straight segments is ~42.4, curve should be close
        self.assertGreater(shape.Length, 30.0)

    def test_build_curve_insufficient_points(self):
        """Test building a curve with too few points."""
        pts = [App.Vector(0,0,0)]
        shape = nurbs_primitives.build_curve(pts)
        self.assertTrue(shape.isNull(), "Curve with 1 point should be null")

    def test_build_curve_closed(self):
        """Test that a closed curve produces a Face."""
        pts = [
            App.Vector(0,0,0),
            App.Vector(10,0,0),
            App.Vector(10,10,0),
            App.Vector(0,10,0),
            App.Vector(0,0,0) # Closed
        ]
        shape = nurbs_primitives.build_curve(pts)
        self.assertFalse(shape.isNull(), "Closed curve should not be null")
        self.assertEqual(shape.ShapeType, "Face", "Closed curve should be a Face")

def run_tests():
    print("DEBUG: Running tests...")
    import sys
    suite = unittest.TestLoader().loadTestsFromTestCase(TestNURBSPrimitives)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

if __name__ == "__main__":
    run_tests()
