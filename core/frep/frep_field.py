import FreeCAD
import numpy as np

class FRepField:
    """
    Abstract base class for all F-Rep (Functional Representation) fields.
    A field evaluates to a negative number inside the solid, positive outside, and 0 on the surface.
    """
    def evaluate(self, point: FreeCAD.Vector) -> float:
        """Returns the signed distance at the given point."""
        raise NotImplementedError("evaluate() must be implemented by subclass.")

    def gradient(self, point: FreeCAD.Vector, h: float = 1e-4) -> FreeCAD.Vector:
        """Computes the numerical gradient via central differences. Can be overridden analytically."""
        dx = self.evaluate(point + FreeCAD.Vector(h, 0, 0)) - self.evaluate(point - FreeCAD.Vector(h, 0, 0))
        dy = self.evaluate(point + FreeCAD.Vector(0, h, 0)) - self.evaluate(point - FreeCAD.Vector(0, h, 0))
        dz = self.evaluate(point + FreeCAD.Vector(0, 0, h)) - self.evaluate(point - FreeCAD.Vector(0, 0, h))
        return FreeCAD.Vector(dx/(2*h), dy/(2*h), dz/(2*h))

    def bounding_box(self):
        """Returns (min_corner: Vector, max_corner: Vector)."""
        raise NotImplementedError("Subclasses must implement bounding_box()")

    def sign_at(self, point: FreeCAD.Vector, tol: float = 1e-5) -> int:
        """Returns -1 (inside), 0 (surface), or +1 (outside)."""
        v = self.evaluate(point)
        if abs(v) <= tol: return 0
        return -1 if v < 0 else 1

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """
        Evaluates the field over an (N, 3) numpy array of points.
        Default implementation is a Python loop. Override for performance if possible.
        """
        results = np.zeros(points.shape[0], dtype=np.float32)
        for i in range(points.shape[0]):
            pt = FreeCAD.Vector(points[i, 0], points[i, 1], points[i, 2])
            results[i] = self.evaluate(pt)
        return results

    def gradient_grid(self, points: np.ndarray, h: float = 1e-4) -> np.ndarray:
        """Batch central-difference gradient over (N,3) points → (N,3) float64."""
        # Shift in each axis to perform central difference (f(x+h) - f(x-h))/(2h)
        pts_xp = points.copy(); pts_xp[:, 0] += h
        pts_xm = points.copy(); pts_xm[:, 0] -= h
        pts_yp = points.copy(); pts_yp[:, 1] += h
        pts_ym = points.copy(); pts_ym[:, 1] -= h
        pts_zp = points.copy(); pts_zp[:, 2] += h
        pts_zm = points.copy(); pts_zm[:, 2] -= h
        
        # Batch evaluate all shifted points
        gx = (self.evaluate_grid(pts_xp) - self.evaluate_grid(pts_xm)) / (2 * h)
        gy = (self.evaluate_grid(pts_yp) - self.evaluate_grid(pts_ym)) / (2 * h)
        gz = (self.evaluate_grid(pts_zp) - self.evaluate_grid(pts_zm)) / (2 * h)
        
        # Stack into (N,3) array and ensure float64 output
        return np.column_stack([gx, gy, gz]).astype(np.float64)
