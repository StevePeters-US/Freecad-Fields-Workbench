# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import itertools
import FreeCAD
import numpy as np

from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_constants import SURFACE_ID_UNSET

_FIELD_UID = itertools.count()


def placement_matrix(placement, inverse=False, dtype=np.float64, flat=False,
                     column_major=False):
    """The (4,4) homogeneous matrix of a FreeCAD.Placement, or its inverse.

    Nine sites used to spell out A11..A44 by hand in three different shapes.
    `flat=True` returns the 16 values as a list (for GLSL uniform upload) rather
    than an ndarray; `column_major=True` transposes first, which is what GLSL
    mat4 constructors want. Returns None if placement is None.

    The inverse is taken as `placement.inverse().toMatrix()`, not
    `toMatrix()` + `Matrix.invert()`. The two agree in FreeCAD, but the suite's
    mock_freecad `Matrix.invert()` has a wrong cofactor term and is off by ~3
    on a plain rigid placement, so the four call sites that used to spell the
    second form were silently mis-transforming under test.
    """
    if placement is None:
        return None
    m = placement.inverse().toMatrix() if inverse else placement.toMatrix()
    rows = [
        [m.A11, m.A12, m.A13, m.A14],
        [m.A21, m.A22, m.A23, m.A24],
        [m.A31, m.A32, m.A33, m.A34],
        [m.A41, m.A42, m.A43, m.A44],
    ]
    arr = np.array(rows, dtype=dtype)
    if column_major:
        arr = arr.T
    if flat:
        return [float(v) for v in arr.ravel()]
    return arr


def _transform_grid(points: np.ndarray, placement, inverse: bool = False) -> np.ndarray:
    """Vectorized (N,3) point transform by a FreeCAD.Placement (or its inverse).

    `field.evaluate_grid`/`gradient_grid` always expect world-space input (they
    apply the field's own inverse-placement internally), but cage nets from
    `to_patch_cage()` are stored in the primitive's local (pre-placement) space
    -- see `_primitive_to_patches` in commands/cmd_cage.py, which bakes the same
    placement into world space explicitly. Callers that need to evaluate a
    field against local-space cage points must convert to world space first.
    """
    if placement is None or points is None or len(points) == 0:
        return points
    mat = placement_matrix(placement, inverse=inverse)
    pts_hom = np.hstack((points, np.ones((points.shape[0], 1), dtype=np.float64)))
    return (pts_hom @ mat.T)[:, :3]


def swept_rotation_bounds(bb_min, bb_max, axis_u, axis_v, centre_u, centre_v,
                          angle_lo, angle_hi):
    """AABB of a box swept by every rotation in [angle_lo, angle_hi] (radians).

    The rotation acts in the plane spanned by coordinate indices `axis_u`/`axis_v`
    about (`centre_u`, `centre_v`); the third coordinate is untouched, so only the
    two in-plane intervals are returned as (u_min, u_max, v_min, v_max).

    Twist and bend both map a query point through a rotation whose angle varies with
    position, so their deformed solid reaches outside the source's own box. Both used
    to return `source.bounding_box()` verbatim, and the renderer bakes only inside the
    box it is handed -- so the overhang was clipped away, which is the "bounding box
    might be too tight ... inside solid" warning and the sliced-off geometry with it.

    Exact for a rigid rotation: the maximum of a linear functional over a rotated box
    is attained at a rotated vertex, so tracing the four in-plane corners across the
    angular range covers every point of the swept solid. It stays exact (no padding)
    at a zero angle, which matters because the box drives bake cost.
    """
    import math
    lo, hi = (angle_lo, angle_hi) if angle_lo <= angle_hi else (angle_hi, angle_lo)
    if hi - lo >= 2.0 * math.pi:
        lo, hi = -math.pi, math.pi

    mins = (bb_min.x, bb_min.y, bb_min.z)
    maxs = (bb_max.x, bb_max.y, bb_max.z)
    us = (mins[axis_u] - centre_u, maxs[axis_u] - centre_u)
    vs = (mins[axis_v] - centre_v, maxs[axis_v] - centre_v)

    quarter = math.pi / 2.0
    u_vals, v_vals = [], []
    for cu in us:
        for cv in vs:
            r = math.hypot(cu, cv)
            if r < 1e-12:
                u_vals.append(0.0)
                v_vals.append(0.0)
                continue
            phi = math.atan2(cv, cu)
            a0, a1 = phi + lo, phi + hi
            # Endpoints, plus every axis-aligned direction the arc crosses -- those
            # are where a coordinate reaches its extreme part-way through the sweep.
            angles = [a0, a1]
            k = math.ceil(a0 / quarter)
            while k * quarter <= a1:
                angles.append(k * quarter)
                k += 1
            for a in angles:
                u_vals.append(r * math.cos(a))
                v_vals.append(r * math.sin(a))

    return (min(u_vals) + centre_u, max(u_vals) + centre_u,
            min(v_vals) + centre_v, max(v_vals) + centre_v)


def _project_to_isosurface(field, points: np.ndarray, iters: int = 8, tol: float = 1e-6,
                            plane_normal: np.ndarray = None, grad_eps: float = 1e-12) -> np.ndarray:
    """Project an (N,3) array of 3D points onto field's zero-isosurface via Newton-Raphson.

    Uses vectorized evaluate_grid and gradient_grid so the full array updates
    in a single pass. Zero/near-zero gradient regions are guarded to prevent div-by-zero.

    This is the one Newton-projection engine shared by `sdf_contour._snap_to_surface`
    (single point, plane-constrained) and `sdf_slicer._surface_distance_grid` (batch,
    plane-constrained, distance-only) -- both call this directly rather than
    reimplementing the step. The three call sites differ only in iteration count,
    whether a plane constrains the step, and whether early-exit-on-convergence is
    worth the extra `max(abs(vals))` check (skip it -- pass `tol=None` -- when every
    point is going to be walked the same fixed number of times regardless, as
    `_surface_distance_grid` does for its distance measurement).

    Args:
        field:  SdfField to evaluate.
        points: (N, 3) numpy array of points.
        iters:  maximum Newton refinement steps.
        tol:    convergence tolerance on absolute SDF value (mm). None (default) runs
                all `iters` steps unconditionally -- no per-point early exit.
        plane_normal: optional (3,) vector. When given, the gradient's component along
                it is removed before stepping, so every point stays in that plane.
        grad_eps: near-zero-(in-plane-)gradient guard; points below this squared
                magnitude are left in place rather than divided by ~0.

    Returns:
        (N, 3) float64 numpy array of projected points.
    """
    if points is None or len(points) == 0:
        return points
    pts = np.array(points, dtype=np.float64, copy=True)

    nrm = None
    if plane_normal is not None:
        nrm = np.asarray(plane_normal, dtype=np.float64)
        nl = float(np.sqrt(nrm.dot(nrm)))
        nrm = nrm / nl if nl > grad_eps else None

    for _ in range(iters):
        vals = field.evaluate_grid(pts)
        if tol is not None and np.max(np.abs(vals)) < tol:
            break
        grads = field.gradient_grid(pts)
        if nrm is not None:
            grads = grads - np.outer(grads.dot(nrm), nrm)
        grad_sq = np.sum(grads ** 2, axis=1)
        if tol is not None:
            active = (np.abs(vals) > tol) & (grad_sq > grad_eps)
            if not np.any(active):
                break
            step = grads[active] * (vals[active] / grad_sq[active])[:, None]
            pts[active] -= step
        else:
            scale = np.where(grad_sq > grad_eps, vals / np.maximum(grad_sq, grad_eps), 0.0)
            pts = pts - grads * scale[:, None]
    return pts


class SdfField:
    _octree_cache = None
    _is_subtractive = False
    surface_id = SURFACE_ID_UNSET

    def __init__(self):
        self._octree_cache = None
        self._is_subtractive = False
        self.surface_id = SURFACE_ID_UNSET
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

    def compute_bounding_box_margin(self, extent: float, floor: float = 5.0, fraction: float = 0.05) -> float:
        """Computes a dynamic margin for bounding boxes based on shape extent.
        
        Args:
            extent: A measure of the shape size (e.g. diagonal length or maximum dimension).
            floor: The minimum absolute margin in mm (default 5.0).
            fraction: The relative fraction of the extent (default 0.05).
        """
        return max(floor, extent * fraction)

    @staticmethod
    def _compute_inv_matrix(placement):
        """Builds the (4,4) float32 inverse-placement matrix used to map world-space
        points into local space. Returns None if placement is None."""
        return placement_matrix(placement, inverse=True, dtype=np.float32)

    def _to_local_point(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        """Maps a single world-space point into local (placement-inverse) space."""
        if self.placement is None:
            return point
        return self.placement.inverse().multVec(point)

    def _to_local_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized batch transform of (N,3) world-space points into local space."""
        if self.inv_matrix is None:
            return points
        N = points.shape[0]
        pts_hom = np.hstack((points, np.ones((N, 1), dtype=np.float32)))
        return (pts_hom @ self.inv_matrix.T)[:, :3]


    def lipschitz(self) -> float:
        """Upper bound on |gradient|. 1.0 for true distance fields."""
        return 1.0

    def max_erosion(self) -> float:
        """Largest inward offset this field can express in closed form, in mm.

        0.0 means the field has no exact erosion and therefore cannot be rounded.
        """
        return 0.0

    def eroded(self, distance: float):
        """Return this field shrunk inward by `distance` mm, or None if it has no
        exact closed form.

        Erosion is NOT `evaluate() + distance`. That expression still measures to
        the original faces, so dilating it back by the same distance cancels term
        for term and leaves every corner as sharp as it started. The shrink has to
        be pushed into the field's own parameters, which is why each subclass has
        to answer for itself.
        """
        return None

    @property
    def field_uid(self):
        """A process-unique id for this object, assigned on first use.

        `id()` is NOT a substitute: CPython reuses an address the moment the previous
        field is freed, and fields are rebuilt constantly, so an `id()`-keyed cache
        serves a stale entry to a new field. Assigned lazily so no subclass has to
        touch its `__init__`.
        """
        uid = getattr(self, "_field_uid", None)
        if uid is None:
            uid = next(_FIELD_UID)
            self._field_uid = uid
        return uid

    def to_glsl(self, ctx, point_var="p"):
        """Return GLSL expression evaluating this SDF at point_var.
        Register uniforms in ctx (GlslContext). Override in subclasses."""
        raise NotImplementedError("to_glsl() not implemented for GPU evaluation.")

    def preferred_scene_resolution(self, scene_extent):
        """Returns the scene-volume resolution along the longest axis preferred by this field,
        or None if no floor is requested."""
        return None

    def texture3d_key(self):
        """Cheap identity of what `texture3d_data()` would return, or None.

        The renderer calls this on every bake and `texture3d_data()` only when it
        changes, so this MUST NOT bake, allocate or copy the volume -- see SB-011.
        """
        return None

    def texture3d_data(self):
        """Data for a 3D texture this field's GLSL samples, or None. May be expensive.

        Returns a dict:
            {"nx","ny","nz": int,
             "fmt":      "r32f" | "rgba32f",
             "bytes":    bytes,            # nx*ny*nz*<texels> little-endian float32
             "uniforms": {suffix: (setter, values)}}   # extra uniforms, resolved to
                                                       # real names by SB-036

        Only fields that call `ctx.sampler3d(..., provider=self)` need to override it,
        and one that does must override `texture3d_key()` too.
        """
        return None

    def surface_count(self) -> int:
        """Number of distinct surfaces owned by this field. Defaults to 1."""
        return 1

    def collect_surface_ids(self) -> set:
        """Return the set of all valid surface IDs (< SURFACE_ID_UNSET) present in this field tree."""
        sids = set()
        sid = getattr(self, "surface_id", SURFACE_ID_UNSET)
        if sid is not None and sid < SURFACE_ID_UNSET:
            count = self.surface_count() if hasattr(self, "surface_count") else 1
            sids.update(range(sid, sid + count))

        bid = getattr(self, "blend_surface_id", SURFACE_ID_UNSET)
        if bid is not None and bid < SURFACE_ID_UNSET:
            sids.add(int(bid))

        if hasattr(self, "__dict__"):
            for val in self.__dict__.values():
                if isinstance(val, SdfField):
                    sids.update(val.collect_surface_ids())
                elif isinstance(val, (list, tuple, set)):
                    for item in val:
                        if isinstance(item, SdfField):
                            sids.update(item.collect_surface_ids())

        return sids

    def to_glsl_sample(self, ctx, point_var="p"):
        """Return a GLSL expression of type FldSample for this field at point_var.

        Leaves do not override this. A leaf is one surface with one bound, and both
        are supplied here, so a new SdfField subclass cannot forget them -- which is
        exactly how SlabCompositeField shipped with no lipschitz() and turned a sphere into 27
        open contour fragments.

        Composers and modifiers DO override it, because they have to decide which of
        their children's ids survives and what the composed bound is.
        """
        sid = getattr(self, "surface_id", SURFACE_ID_UNSET)
        return f"FldSample({self.to_glsl(ctx, point_var)}, {int(sid)}u)"

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """
        Evaluates the field over an (N, 3) numpy array of points.
        The underlying evaluate() method must support duck-typed np.ndarray inputs.
        """
        # Pass the raw Nx3 array directly to evaluate.
        # Subclasses must implement evaluate() using ops that work on both FreeCAD.Vectors and ndarrays.
        return np.asarray(self.evaluate(points), dtype=np.float32)

    def gradient_grid(self, points: np.ndarray, h: float = 1e-4) -> np.ndarray:
        """Batch central-difference gradient over (N,3) points → (N,3) float64."""
        N = points.shape[0]
        # Shift in each axis to perform central difference (f(x+h) - f(x-h))/(2h)
        pts_xp = points.copy(); pts_xp[:, 0] += h
        pts_xm = points.copy(); pts_xm[:, 0] -= h
        pts_yp = points.copy(); pts_yp[:, 1] += h
        pts_ym = points.copy(); pts_ym[:, 1] -= h
        pts_zp = points.copy(); pts_zp[:, 2] += h
        pts_zm = points.copy(); pts_zm[:, 2] -= h
        
        # Batch evaluate all shifted points in a single call to avoid composed tree traversal overhead
        all_pts = np.vstack([pts_xp, pts_xm, pts_yp, pts_ym, pts_zp, pts_zm])
        all_vals = self.evaluate_grid(all_pts)
        
        gx = (all_vals[0:N] - all_vals[N:2*N]) / (2 * h)
        gy = (all_vals[2*N:3*N] - all_vals[3*N:4*N]) / (2 * h)
        gz = (all_vals[4*N:5*N] - all_vals[5*N:6*N]) / (2 * h)
        
        # Stack into (N,3) array and ensure float64 output
        return np.column_stack([gx, gy, gz]).astype(np.float64)

    @property
    def octree_cache(self):
        """On-demand SdfOctreeCache for this field. Automatically builds at 2.0mm res."""
        if self._octree_cache is None:
            import time
            from freecad.fields.core.sdf.sdf_octree import SdfOctreeCache
            # Default leaf size 2.0mm for snapping is enough
            t0 = time.perf_counter()
            self._octree_cache = SdfOctreeCache(self, leaf_size=2.0)
            self._octree_cache.build()
            # This build is lazy and invisible: it fires from ray_march, which
            # runs on a mouse move or a click, with nothing in the log tying the
            # stall to it. Any field edit drops the cache, so a picking ray after
            # an edit pays the whole build again.
            fld_logger.debug(
                f"{type(self).__name__}: picking octree rebuilt in "
                f"{(time.perf_counter() - t0)*1000:.1f}ms "
                f"(lazy, triggered by a ray_march)", category="sdf")
        return self._octree_cache

    def to_patch_cage(self):
        """Convert this field to an SdfCageField. Returns None if not supported."""
        return None

    def to_deform_cage(self):
        """Build a deform-cage CageNet for this primitive.

        Derived generically from ``to_patch_cage()`` so that every primitive
        which knows how to build its own cage net automatically gets a deform
        cage — adding a new primitive only requires implementing
        ``to_patch_cage()`` (see memory ``design_primitive_cage_descriptor``).
        Returns None if the primitive has no cage.
        """
        cage = self.to_patch_cage()
        if cage is None:
            return None
        from freecad.fields.core.sdf.sdf.cage_net import CageNet
        net = CageNet.from_field(cage)
        net.source = self

        if net.vertices is not None and len(net.vertices) > 0:
            old_verts = net.vertices.copy()
            world_verts = _transform_grid(old_verts, net.placement)
            proj_world = _project_to_isosurface(self, world_verts)
            proj_verts = _transform_grid(proj_world, net.placement, inverse=True)
            net.vertices = proj_verts
            net.rest_vertices = proj_verts.copy()
            delta = proj_verts - old_verts

            if net.handles is not None and len(net.handles) > 0 and net.edges:
                net.handles = net.handles.copy()
                for i, (vi, vj) in enumerate(net.edges):
                    if 2 * i < len(net.handles):
                        net.handles[2 * i] += delta[vi]
                    if 2 * i + 1 < len(net.handles):
                        net.handles[2 * i + 1] += delta[vj]
                net.rest_handles = net.handles.copy()

        return net

    def invalidate_cache(self):
        """Must be called when field parameters change."""
        self._octree_cache = None

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
        L = self.lipschitz()
        for _i in range(max_steps):
            if t > max_dist:
                break
            pos = ray_origin + d * t
            
            # ── Spatial Acceleration (Octree) ──
            if octree_cache is None:
                octree_cache = self.octree_cache # Use internal cache if available
            
            if octree_cache is not None:
                dist = octree_cache.query(pos)
                if dist == float('inf'):
                    # Skip empty space by advancing by the leaf size
                    t += octree_cache.leaf_size
                    continue
            else:
                # CP-008: Batch step through evaluate_grid
                pos_np = np.array([[pos.x, pos.y, pos.z]], dtype=np.float32)
                dist = self.evaluate_grid(pos_np)[0]
                
            if dist < surface_eps:
                # Hit — final refinement to snap exactly to theoretical surface
                if dist > 0:
                    # Final analytical check if we were using cache
                    pos_np = np.array([[pos.x, pos.y, pos.z]], dtype=np.float32)
                    dist = self.evaluate_grid(pos_np)[0]
                    pos = pos + d * (dist / L)
                
                # Compute outward normal via gradient
                normal = self.gradient(pos)
                nl = normal.Length
                if nl > 1e-10:
                    normal = normal * (1.0 / nl)
                else:
                    normal = FreeCAD.Vector(0, 0, 1)
                return pos, normal
                
            # Advance by the SDF value scaled by Lipschitz bound
            t += max(dist / L, surface_eps * 0.1)

        return None


# Shared GLSL helper: apply an inverted placement matrix.
# Register via: ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
_GLSL_APPLY_INV_MAT = """
vec3 apply_inv_mat(mat4 m, vec3 p) {
    return (m * vec4(p, 1.0)).xyz;
}
"""
