"""
core/sdf/sdf2d/nurbs_curve.py

2D SDF field for a closed NURBS profile. Used for extrusion profiles.
"""
import numpy as np
import FreeCAD
from core.sdf.sdf2d.sdf2d_field import Sdf2dField


class Sdf2dNurbsCurveField(Sdf2dField):
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
            from core import dm_logger
            dm_logger.debug("Sdf2dNurbsCurveField._get_samples failed")
            self._samples = np.zeros((4, 2), dtype=np.float64)
        return self._samples

    def evaluate_2d(self, x: float, y: float) -> float:
        samples = self._get_samples()
        p = np.array([x, y])
        # Closest point distance
        deltas = samples - p
        dists = np.linalg.norm(deltas, axis=1)
        min_dist = float(np.min(dists))
        # Winding number for sign
        winding = _winding_number_2d(p, samples)
        sign = -1.0 if winding != 0 else 1.0
        return sign * min_dist

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
            from core import dm_logger
            dm_logger.debug(f"Sdf2dNurbsCurveField.to_glsl_2d conversion failed: {e}")
            return "1e18"


def _winding_number_2d(point, polygon: np.ndarray) -> int:
    """Winding number test. Returns non-zero if point is inside polygon."""
    wn = 0
    n = len(polygon)
    px, py = point[0], point[1]
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if y1 <= py:
            if y2 > py:
                if (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1) > 0:
                    wn += 1
        else:
            if y2 <= py:
                if (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1) < 0:
                    wn -= 1
    return wn
