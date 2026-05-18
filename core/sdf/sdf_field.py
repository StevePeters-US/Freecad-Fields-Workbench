import FreeCAD
import numpy as np
class SdfField:
    _octree_cache = None
    _is_subtractive = False

    def __init__(self):
        self._octree_cache = None
        self._is_subtractive = False
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

    @property
    def octree_cache(self):
        """On-demand SdfOctreeCache for this field. Automatically builds at 2.0mm res."""
        if self._octree_cache is None:
            from core.sdf.sdf_octree import SdfOctreeCache
            # Default leaf size 2.0mm for snapping is enough
            self._octree_cache = SdfOctreeCache(self, leaf_size=2.0)
            self._octree_cache.build()
        return self._octree_cache

    def invalidate_cache(self):
        """Must be called when field parameters change."""
        self._octree_cache = None

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

    def ray_march(self, ray_origin, ray_direction, max_steps=256, surface_eps=0.001, octree_cache=None):
        """
        Sphere-trace a ray against this SDF field.
        Returns (hit_point, hit_normal) as FreeCAD.Vector pair, or None if no hit.

        ray_origin:    world-space ray origin (FreeCAD.Vector)
        ray_direction: world-space direction (will be normalized internally)
        max_steps:     maximum sphere-trace iterations (default 256)
        surface_eps:   surface hit threshold in mm (default 0.001)
        octree_cache:  optional SdfOctreeCache — if provided, use to skip empty space.
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

        # Use AABB diagonal as march budget
        aabb_diag = (bb_max - bb_min).Length
        max_dist = t_near + max(aabb_diag, 1.0)

        # Sphere trace from AABB entry point
        for _i in range(max_steps):
            if t > max_dist:
                break
            pos = ray_origin + d * t
            
            # ── Spatial Acceleration (Octree) ──
            if octree_cache is None:
                octree_cache = self._octree_cache # Use internal cache if available
            
            if octree_cache is not None:
                dist = octree_cache.query(pos)
                if dist == float('inf'):
                    # Skip empty space by advancing by the leaf size
                    t += octree_cache.leaf_size
                    continue
            else:
                dist = self.evaluate(pos)
                
            if dist < surface_eps:
                # Hit — final refinement to snap exactly to theoretical surface
                if dist > 0:
                    # Final analytical check if we were using cache
                    dist = self.evaluate(pos)
                    pos = pos + d * dist
                
                # Compute outward normal via gradient
                normal = self.gradient(pos)
                nl = normal.Length
                if nl > 1e-10:
                    normal = normal * (1.0 / nl)
                else:
                    normal = FreeCAD.Vector(0, 0, 1)
                return pos, normal
                
            # Advance by the SDF value
            t += max(dist, surface_eps * 0.1)

        return None


# Shared GLSL helper: apply an inverted placement matrix.
# Register via: ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
_GLSL_APPLY_INV_MAT = """
vec3 apply_inv_mat(mat4 m, vec3 p) {
    return (m * vec4(p, 1.0)).xyz;
}
"""
