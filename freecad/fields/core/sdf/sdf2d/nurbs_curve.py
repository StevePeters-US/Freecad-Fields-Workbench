# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf2d/nurbs_curve.py

2D SDF field for a closed NURBS profile. Used for extrusion profiles.
"""
import FreeCAD
from freecad.fields.core.sdf.sdf2d.sdf2d_field import Sdf2dField
import numpy as np


class Sdf2dNurbsCurve(Sdf2dField):
    """
    2D signed distance to a closed NURBS (BSpline) curve.

    Negative inside, positive outside.
    Distance = closest_point_distance, signed by winding number.
    """

    def __init__(self, bspline_curve_2d, sample_count: int = 64):
        """
        bspline_curve_2d: Part.BSplineCurve in 2D (z=0 plane)
        sample_count:     number of polyline samples for winding number
        """
        self.curve = bspline_curve_2d
        self.sample_count = sample_count
        self._samples = None  # lazy-initialized (N,2) polyline

    def _get_samples(self):
        if self._samples is not None:
            return self._samples
        try:
            u0, u1 = self.curve.FirstParameter, self.curve.LastParameter
            params = np.linspace(u0, u1, self.sample_count + 1)[:-1]
            pts = []
            for u in params:
                v = self.curve.value(u)
                pts.append([v.x, v.y])
            self._samples = np.array(pts, dtype=np.float64)
        except Exception:
            from freecad.fields.core import fld_logger
            fld_logger.debug("Sdf2dNurbsCurve._get_samples failed")
            self._samples = np.zeros((4, 2), dtype=np.float64)
        return self._samples

    def evaluate_2d(self, x: float, y: float) -> float:
        return float(self.evaluate_2d_grid(np.array([[x, y]], dtype=np.float64))[0])

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        samples = self._get_samples()
        pts = np.asarray(pts, dtype=np.float64)
        # Closest point distance, vectorized over (N points x M samples)
        deltas = pts[:, None, :] - samples[None, :, :]
        dists = np.linalg.norm(deltas, axis=2)
        min_dist = np.min(dists, axis=1)
        # Winding number for sign
        winding = _winding_number_2d_grid(pts, samples)
        sign = np.where(winding != 0, -1.0, 1.0)
        return (sign * min_dist).astype(np.float32)

    def to_glsl_2d(self, ctx, point_var="p2"):
        from .bezier_curve import Sdf2dBezierCurve
        try:
            # Convert BSpline to piecewise Beziers. 
            # Note: toBezier() returns a list of Part.BezierCurve objects.
            bez_curves = self.curve.toBezier()
            segments = []
            for b in bez_curves:
                poles = b.getPoles()
                # Ensure we have exactly 4 poles (cubic). 
                # If degree < 3, we might need to elevate, but Sdf2dBezierCurve
                # handles it if we pass them. Wait, sd_cubic_bez_2d expects 4 points.
                # If degree is 1 (line), we can still use it by duplicating points
                # or adding a separate line helper. 
                # For now, assume cubic-compatible or handles by Sdf2dBezierCurve.
                if len(poles) == 4:
                    segments.append([(p.x, p.y) for p in poles])
                elif len(poles) == 2:
                    # Linear segment: p0, p0, p1, p1 makes it a cubic bez line
                    p0, p1 = poles
                    segments.append([(p0.x, p0.y), (p0.x, p0.y), (p1.x, p1.y), (p1.x, p1.y)])
                elif len(poles) == 3:
                    # Quadratic to cubic conversion
                    p0, p1, p2 = poles
                    q0 = p0
                    q1 = (p0 + p1 * 2.0) / 3.0
                    q2 = (p2 + p1 * 2.0) / 3.0
                    q3 = p2
                    segments.append([(q0.x, q0.y), (q1.x, q1.y), (q2.x, q2.y), (q3.x, q3.y)])
            
            proxy = Sdf2dBezierCurve(segments)
            return proxy.to_glsl_2d(ctx, point_var)
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug(f"Sdf2dNurbsCurve.to_glsl_2d conversion failed: {e}")
            return "1e18"


def _winding_number_2d_grid(points: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    """Vectorized winding number test. points: (N,2), polygon: (M,2) -> (N,) int array."""
    x1 = polygon[None, :, 0]
    y1 = polygon[None, :, 1]
    x2 = np.roll(polygon[:, 0], -1)[None, :]
    y2 = np.roll(polygon[:, 1], -1)[None, :]
    px = points[:, 0][:, None]
    py = points[:, 1][:, None]
    cross = (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1)
    up = (y1 <= py) & (y2 > py) & (cross > 0)
    down = (y1 > py) & (y2 <= py) & (cross < 0)
    return up.sum(axis=1) - down.sum(axis=1)
