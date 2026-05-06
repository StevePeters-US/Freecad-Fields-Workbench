import FreeCAD
import numpy as np
class SdfField:
    """
    Abstract base class for all SDF (Signed Distance Field) fields.
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

    def to_glsl(self, ctx, point_var="p"):
        """Return GLSL expression evaluating this SDF at point_var.
        Register uniforms in ctx (GlslContext). Override in subclasses."""
        raise NotImplementedError("to_glsl() not implemented for GPU evaluation.")

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """
        Evaluates the field over an (N, 3) numpy array of points.
        The underlying evaluate() method must support duck-typed np.ndarray inputs.
        """
        # Pass the raw Nx3 array directly to evaluate.
        # Subclasses must implement evaluate() using ops that work on both FreeCAD.Vectors and ndarrays.
        return self.evaluate(points)

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

    def curvature_grid(self, points: np.ndarray, h: float = 1e-3) -> np.ndarray:
        """Approximate mean curvature via Laplacian of SDF. Returns (N,) float64."""
        f0 = self.evaluate_grid(points)
        lap = np.zeros(len(points), dtype=np.float64)
        for axis in range(3):
            p_plus = points.copy(); p_plus[:, axis] += h
            p_minus = points.copy(); p_minus[:, axis] -= h
            lap += (self.evaluate_grid(p_plus) + self.evaluate_grid(p_minus) - 2 * f0) / (h * h)
        
        grad = self.gradient_grid(points, h)
        grad_mag = np.linalg.norm(grad, axis=1)
        grad_mag = np.maximum(grad_mag, 1e-12)
        
        return np.abs(lap) / grad_mag

    def ray_march(self, ray_origin, ray_direction, max_steps=256, surface_eps=0.001):
        """
        Sphere-trace a ray against this SDF field.
        Returns (hit_point, hit_normal) as FreeCAD.Vector pair, or None if no hit.

        ray_origin:    world-space ray origin (FreeCAD.Vector)
        ray_direction: world-space direction (will be normalized internally)
        max_steps:     maximum sphere-trace iterations (default 256)
        surface_eps:   surface hit threshold in mm (default 0.001)
        """
        # Normalize direction
        d = FreeCAD.Vector(ray_direction)
        dlen = d.Length
        if dlen < 1e-10:
            return None
        d = d * (1.0 / dlen)

        # AABB slab test — find ray entry/exit t values along the bounding box
        try:
            bb_min, bb_max = self.bounding_box()
        except NotImplementedError:
            bb_min = ray_origin - FreeCAD.Vector(50000, 50000, 50000)
            bb_max = ray_origin + FreeCAD.Vector(50000, 50000, 50000)

        t_near = -1e18
        t_far  =  1e18
        axes = [
            (d.x, ray_origin.x, bb_min.x, bb_max.x),
            (d.y, ray_origin.y, bb_min.y, bb_max.y),
            (d.z, ray_origin.z, bb_min.z, bb_max.z),
        ]
        for d_comp, o_comp, mn, mx in axes:
            if abs(d_comp) < 1e-10:
                if o_comp < mn or o_comp > mx:
                    return None  # parallel to slab and outside — miss
            else:
                t1 = (mn - o_comp) / d_comp
                t2 = (mx - o_comp) / d_comp
                if t1 > t2:
                    t1, t2 = t2, t1
                t_near = max(t_near, t1)
                t_far  = min(t_far,  t2)
                if t_near > t_far:
                    return None  # missed AABB

        # Don't reject negative t_far: in orthographic mode the focal-plane origin
        # is often past the object, so valid intersections have negative t values.
        t = t_near  # start at AABB entry (may be negative)

        # Use AABB diagonal as march budget, not t_far.
        # t_far can be deceptively close to t_near when the ray clips an AABB corner
        # (a rotated/transformed box has a larger AABB than the shape itself), causing
        # one SDF step to overshoot the interval.  Marching for a full diagonal from
        # the entry point guarantees we cover the whole object.
        aabb_diag = (bb_max - bb_min).Length
        max_dist = t_near + max(aabb_diag, 1.0)

        # Sphere trace from AABB entry point
        for _i in range(max_steps):
            if t > max_dist:
                break
            pos = ray_origin + d * t
            dist = self.evaluate(pos)
            if dist < surface_eps:
                # Hit — final refinement to snap exactly to theoretical surface
                # (only if dist is positive; if we are already inside, stay at pos)
                if dist > 0:
                    pos = pos + d * dist
                
                # Compute outward normal via gradient
                normal = self.gradient(pos)
                nl = normal.Length
                if nl > 1e-10:
                    normal = normal * (1.0 / nl)
                else:
                    normal = FreeCAD.Vector(0, 0, 1)
                return pos, normal
            # Advance by the SDF value; min step prevents stalling at a near-zero surface
            t += max(dist, surface_eps * 0.1)

        return None

