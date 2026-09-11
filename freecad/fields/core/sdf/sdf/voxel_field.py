# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf/voxel_field.py

Discrete 3D voxel-backed signed distance field primitive.
Supports trilinear and nearest-neighbor interpolation, GPU GLSL sampling,
and direct in-place sculpting/carving operations.
"""
import FreeCAD
import numpy as np
from scipy.ndimage import map_coordinates
from freecad.fields.core.sdf.sdf_field import SdfField, _transform_grid, _GLSL_APPLY_INV_MAT


# Union identity for an unsculpted SCULPT LAYER grid -- NOT the standalone
# primitive's seed, which is default_voxel_grid()'s solid block (VOX-017).
# Large enough that min(base, VOXEL_FAR) never clips a real distance, small
# enough to stay exact in float32.
VOXEL_FAR = 1.0e4

# Fraction of each grid extent the default block fills. A fresh field used to be
# uniform +10.0 -- positive everywhere, so no zero crossing, so no isosurface and
# nothing on screen. Worse, the task panel's brush defaults to Carve, which is
# max(data, -sphere): carving empty space leaves it empty, so no sequence of default
# clicks ever produced geometry. The default is now a solid rectangular block with
# clearance on all six sides, so Carve has something to bite and Deposit has room to
# grow into without immediately hitting the outside-blend at the grid boundary.
DEFAULT_FILL_FRACTION = 0.7


def default_voxel_grid(size, resolution) -> np.ndarray:
    """The seed grid for a voxel field with no data: an exact box SDF.

    The single definition of "a default field". `SdfVoxelField.__init__` uses it when
    `data is None`, which is every path that builds an unsculpted standalone grid --
    `create_voxel_field_object` and `FldVoxelFieldProxy._build_field` both reach it that
    way rather than keeping their own copy.
    """
    size = np.asarray(size, dtype=np.float64)
    nx, ny, nz = (int(r) for r in resolution)
    xs = np.linspace(-size[0] * 0.5, size[0] * 0.5, nx, dtype=np.float64)
    ys = np.linspace(-size[1] * 0.5, size[1] * 0.5, ny, dtype=np.float64)
    zs = np.linspace(-size[2] * 0.5, size[2] * 0.5, nz, dtype=np.float64)
    gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")

    h = size * 0.5 * DEFAULT_FILL_FRACTION
    qx = np.abs(gx) - h[0]
    qy = np.abs(gy) - h[1]
    qz = np.abs(gz) - h[2]
    outside = np.sqrt(np.maximum(qx, 0.0) ** 2
                      + np.maximum(qy, 0.0) ** 2
                      + np.maximum(qz, 0.0) ** 2)
    inside = np.minimum(np.maximum(qx, np.maximum(qy, qz)), 0.0)
    return (outside + inside).astype(np.float32)


class SdfVoxelField(SdfField):
    """A discrete 3D voxel grid representation of a signed distance field."""

    def __init__(self, size=(100.0, 100.0, 100.0), resolution=(32, 32, 32),
                 data: np.ndarray = None, interpolation: str = "trilinear",
                 placement: FreeCAD.Placement = None, band: float = None):
        super().__init__()
        self.size = np.array(size, dtype=np.float64)
        self.resolution = tuple(int(r) for r in resolution)
        self.interpolation = str(interpolation).lower()
        self.placement = placement
        self.inv_matrix = self._compute_inv_matrix(self.placement)
        self.geometry_version = 0

        self.band = float(band) if band is not None else None
        if data is not None:
            raw = np.ascontiguousarray(data, dtype=np.float32)
        else:
            raw = default_voxel_grid(self.size, self.resolution)

        if self.band is not None:
            self.data = np.clip(raw, -self.band, self.band)
        else:
            self.data = raw

    @classmethod
    def from_field(cls, source: SdfField, size=(100.0, 100.0, 100.0),
                   resolution=(32, 32, 32), interpolation: str = "trilinear",
                   placement: FreeCAD.Placement = None):
        """Discretizes an analytical SDF field into a 3D voxel field."""
        nx, ny, nz = resolution
        lx, ly, lz = size
        xs = np.linspace(-lx * 0.5, lx * 0.5, nx, dtype=np.float64)
        ys = np.linspace(-ly * 0.5, ly * 0.5, ny, dtype=np.float64)
        zs = np.linspace(-lz * 0.5, lz * 0.5, nz, dtype=np.float64)

        gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")
        local_pts = np.column_stack((gx.ravel(), gy.ravel(), gz.ravel()))

        if placement is not None:
            pts = _transform_grid(local_pts, placement, inverse=False)
        else:
            pts = local_pts

        sampled = source.evaluate_grid(pts).reshape((nx, ny, nz)).astype(np.float32)
        return cls(size=size, resolution=resolution, data=sampled,
                   interpolation=interpolation, placement=placement)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        # Pass WORLD coordinates through: evaluate_grid owns the placement
        # transform (`_to_local_grid` is its first line). Mapping the point with
        # `_to_local_point` here first applied the inverse twice, which on a
        # sphere at (30,0,0) with a matching placement read +10.02 at the centre
        # instead of -20.0 -- the wrong sign. Identity placements hid it, since
        # inv_matrix is None makes both transforms no-ops.
        pts = np.array([[point.x, point.y, point.z]], dtype=np.float64)
        return float(self.evaluate_grid(pts)[0])

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        local_pts = self._to_local_grid(points)
        nx, ny, nz = self.resolution
        lx, ly, lz = self.size

        # Map local coordinates [-size/2, size/2] to UVW [0, 1]
        uvw = (local_pts + self.size * 0.5) / np.maximum(self.size, 1e-6)

        # Outside bounds fallback: distance to AABB plus border distance
        d_box = np.maximum(np.abs(local_pts) - self.size * 0.5, 0.0)
        box_dist = np.linalg.norm(d_box, axis=-1)

        if self.interpolation == "nearest":
            ix = np.clip(np.round(uvw[:, 0] * (nx - 1)).astype(int), 0, nx - 1)
            iy = np.clip(np.round(uvw[:, 1] * (ny - 1)).astype(int), 0, ny - 1)
            iz = np.clip(np.round(uvw[:, 2] * (nz - 1)).astype(int), 0, nz - 1)
            val = self.data[ix, iy, iz]
        else:
            # Trilinear interpolation
            coords = np.column_stack((
                uvw[:, 0] * (nx - 1),
                uvw[:, 1] * (ny - 1),
                uvw[:, 2] * (nz - 1)
            )).T
            val = map_coordinates(self.data, coords, order=1, mode="nearest")

        # Blend with outside distance if point is outside bounding box
        outside = np.any((uvw < 0.0) | (uvw > 1.0), axis=-1)
        res = val.copy()
        res[outside] = np.maximum(val[outside], 0.0) + box_dist[outside]
        return res.astype(np.float32)

    def bounding_box(self):
        h = self.size * 0.5
        c_min = FreeCAD.Vector(-h[0], -h[1], -h[2])
        c_max = FreeCAD.Vector(h[0], h[1], h[2])
        if self.placement is not None:
            corners_local = [
                FreeCAD.Vector(x, y, z)
                for x in (-h[0], h[0])
                for y in (-h[1], h[1])
                for z in (-h[2], h[2])
            ]
            corners = [self.placement.multVec(pt) for pt in corners_local]
            return (
                FreeCAD.Vector(min(p.x for p in corners), min(p.y for p in corners), min(p.z for p in corners)),
                FreeCAD.Vector(max(p.x for p in corners), max(p.y for p in corners), max(p.z for p in corners))
            )
        return (c_min, c_max)

    def lipschitz(self) -> float:
        """Trilinear interpolation of an exact SDF is 1-Lipschitz, but in-place
        min/max stamping is not: max(a, -b) is only metric near the surface, and
        each stroke compounds the error. Tier 7 redistancing resets this to 1.0;
        until a grid has been redistanced since its last stamp, report the measured
        bound so the march shortens its steps instead of overshooting."""
        return float(getattr(self, "_lipschitz", 1.0))

    def measure_lipschitz(self) -> float:
        """Max |finite-difference gradient| over the grid. Sets and returns
        `_lipschitz`. Call after a stamp batch, not per dab."""
        if min(self.resolution) < 2:
            self._lipschitz = 1.0
            return 1.0
        spacing = self.size / np.maximum(np.array(self.resolution) - 1, 1)
        data = self.data.astype(np.float64)
        mask = np.abs(data) < (VOXEL_FAR * 0.5)
        if not np.any(mask):
            self._lipschitz = 1.0
            return 1.0
        g = np.gradient(data, *spacing)
        mag = np.sqrt(sum(np.square(c) for c in g))
        mag[~mask] = 1.0
        self._lipschitz = float(max(1.0, np.nanmax(mag)))
        return self._lipschitz

    def eroded(self, distance: float):
        """A discrete grid CAN erode by offsetting its level set, unlike an analytic
        field.

        `SdfField.eroded` warns that `evaluate() + d` is not erosion, and for a formula
        that is correct -- the expression still measures to the original faces, so
        dilating back cancels term for term and corners stay sharp. A sampled grid has
        no faces to measure to: shifting every stored value shifts the zero level set
        itself, and re-dilating cannot recover a corner the shift rounded off. The
        result is exact to within the grid spacing, which is the field's own accuracy.
        """
        out = SdfVoxelField(size=tuple(self.size), resolution=self.resolution,
                            data=self.data + float(distance),
                            interpolation=self.interpolation, placement=self.placement)
        out.geometry_version = self.geometry_version
        return out

    def max_erosion(self) -> float:
        """Half the shortest box dimension -- beyond that the grid holds nothing."""
        return float(np.min(self.size) * 0.5)

    def texture3d_key(self):
        """Cheap: no allocation, no copy. Every mutator bumps `geometry_version`."""
        return f"vox_{self.field_uid}_{self.geometry_version}"

    def texture3d_data(self):
        """The grid itself, as an R16F volume."""
        nx, ny, nz = self.resolution
        # GL expects x fastest; self.data is indexed [ix, iy, iz] (C order = iz
        # fastest), so transpose before flattening.
        vol = np.ascontiguousarray(self.data.transpose(2, 1, 0), dtype=np.float16)
        return {
            "nx": nx, "ny": ny, "nz": nz,
            "fmt": "r16f",
            "bytes": vol.tobytes(),
            "uniforms": {},
        }

    def to_glsl(self, ctx, point_var="p"):
        tex_name = ctx.sampler3d("vox_field", provider=self)
        u_size = ctx.uniform("vec3", tuple(float(s) for s in self.size), name="vsize")
        u_mode = ctx.uniform("int", 1 if self.interpolation == "nearest" else 0, name="vmode")
        u_res  = ctx.uniform("vec3", tuple(float(r) for r in self.resolution), name="vres")

        func_name = ctx.get_unique_name("sdf_voxel_field")
        helper_body = f"""
float {func_name}(vec3 p, vec3 sz, int mode, vec3 res) {{
    vec3 h = sz * 0.5;
    vec3 d_box = max(abs(p) - h, vec3(0.0));
    float outside_dist = length(d_box);

    vec3 uvw = (p + h) / max(sz, vec3(1e-6));
    vec3 uvw_tex;
    if (mode == 1) {{
        // Nearest-neighbor sampling for blocky / voxel look
        uvw_tex = (floor(uvw * (res - vec3(1.0)) + vec3(0.5)) + vec3(0.5)) / res;
    }} else {{
        // Trilinear: map node [0, 1] to texel center [(0.5)/res, (res-0.5)/res]
        uvw_tex = (uvw * (res - vec3(1.0)) + vec3(0.5)) / res;
    }}
    vec3 clamped_uvw = clamp(uvw_tex, vec3(0.5) / res, (res - vec3(0.5)) / res);
    float val = texture({tex_name}, clamped_uvw).r;

    if (any(lessThan(uvw, vec3(0.0))) || any(greaterThan(uvw, vec3(1.0)))) {{
        return max(val, 0.0) + outside_dist;
    }}
    return val;
}}
"""
        ctx.add_custom_helper(func_name, helper_body)

        if self.inv_matrix is not None:
            ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
            m = ctx.uniform("mat4", self.inv_matrix.tolist())
            return f"{func_name}(apply_inv_mat({m}, {point_var}), {u_size}, {u_mode}, {u_res})"
        return f"{func_name}({point_var}, {u_size}, {u_mode}, {u_res})"

    def _sub_index_box(self, world_min, world_max):
        """Grid index ranges covering a world-space AABB, clipped. None if disjoint."""
        lo = self._to_local_point(world_min)
        hi = self._to_local_point(world_max)
        lo_a = np.minimum([lo.x, lo.y, lo.z], [hi.x, hi.y, hi.z])
        hi_a = np.maximum([lo.x, lo.y, lo.z], [hi.x, hi.y, hi.z])
        res = np.array(self.resolution)
        step = self.size / np.maximum(res - 1, 1)
        i0 = np.maximum(np.floor((lo_a + self.size * 0.5) / step).astype(int), 0)
        i1 = np.minimum(np.ceil((hi_a + self.size * 0.5) / step).astype(int) + 1, res)
        if np.any(i1 <= i0):
            return None
        return i0, i1

    def stamp_local(self, field, operation="add", smooth_k=0.0):
        """Boolean-stamp `field` into this grid, touching only its bounding box.

        Returns the (i0, i1) index box written, or None if the stamp missed the grid.
        The caller bumps `geometry_version` -- a stroke stamps many dabs and should
        version once.
        """
        box = self._sub_index_box(*field.bounding_box())
        if box is None:
            return None
        i0, i1 = box

        # Sample the stamp on the sub-box only.
        axes = [np.linspace(-self.size[a] * 0.5, self.size[a] * 0.5, self.resolution[a])[i0[a]:i1[a]]
                for a in range(3)]
        gx, gy, gz = np.meshgrid(*axes, indexing="ij")
        local_pts = np.column_stack((gx.ravel(), gy.ravel(), gz.ravel()))
        world_pts = (_transform_grid(local_pts, self.placement, inverse=False)
                     if self.placement is not None else local_pts)
        shape = gx.shape
        other = field.evaluate_grid(world_pts).reshape(shape).astype(np.float32)

        sub = self.data[i0[0]:i1[0], i0[1]:i1[1], i0[2]:i1[2]]
        if operation == "add":
            if smooth_k > 0.0:
                h = np.clip(0.5 + 0.5 * (other - sub) / smooth_k, 0.0, 1.0)
                out = (1.0 - h) * other + h * sub - smooth_k * h * (1.0 - h)
            else:
                out = np.minimum(sub, other)
        elif operation == "cut":
            if smooth_k > 0.0:
                # Matches _GLSL_SMOOTH_SUBTRACTION (sdf_composer.py:14) exactly:
                # h = clamp(0.5 - 0.5*(a+b)/k), mix(a, -b, h) + k*h*(1-h), with
                # a = the existing solid and b = the cutter. The (a - b) spelling
                # this replaces inverted the blend band -- 8.0 mm out at k = 2.0,
                # against the 0.5 mm a k/4 blend is allowed.
                h = np.clip(0.5 - 0.5 * (sub + other) / smooth_k, 0.0, 1.0)
                out = (1.0 - h) * sub + h * (-other) + smooth_k * h * (1.0 - h)
            else:
                out = np.maximum(sub, -other)
        else:
            raise ValueError(f"unknown stamp operation {operation!r}")

        if getattr(self, "band", None) is not None:
            out = np.clip(out, -self.band, self.band)
        self.data[i0[0]:i1[0], i0[1]:i1[1], i0[2]:i1[2]] = out.astype(np.float32)
        return i0, i1

    def redistance(self, box=None):
        """Restore Eikonal property (|grad d| = 1) over a sub-box using Zhao's Fast Sweeping Method."""
        if box is None:
            i0 = np.zeros(3, dtype=int)
            i1 = np.array(self.resolution, dtype=int)
        else:
            i0, i1 = box
            pad = 4
            i0 = np.maximum(i0 - pad, 0)
            i1 = np.minimum(i1 + pad, np.array(self.resolution, dtype=int))
            if np.any(i1 <= i0):
                return

        nx, ny, nz = self.resolution
        h = self.size / np.maximum(np.array(self.resolution) - 1, 1)
        hx, hy, hz = float(h[0]), float(h[1]), float(h[2])
        inv_h2 = np.array([1.0 / (hx * hx), 1.0 / (hy * hy), 1.0 / (hz * hz)], dtype=np.float64)

        sub_slice = (slice(i0[0], i1[0]), slice(i0[1], i1[1]), slice(i0[2], i1[2]))
        sub = self.data[sub_slice]
        sub_shape = sub.shape
        sx, sy, sz = sub_shape

        # Step 1: Identify interface voxels (boundary condition)
        # An interface voxel has at least one 6-neighbor with opposite sign.
        padded_sub = np.pad(sub, 1, mode='edge')
        if i0[0] > 0:
            padded_sub[0, 1:sy + 1, 1:sz + 1] = self.data[i0[0] - 1, i0[1]:i1[1], i0[2]:i1[2]]
        if i1[0] < nx:
            padded_sub[sx + 1, 1:sy + 1, 1:sz + 1] = self.data[i1[0], i0[1]:i1[1], i0[2]:i1[2]]
        if i0[1] > 0:
            padded_sub[1:sx + 1, 0, 1:sz + 1] = self.data[i0[0]:i1[0], i0[1] - 1, i0[2]:i1[2]]
        if i1[1] < ny:
            padded_sub[1:sx + 1, sy + 1, 1:sz + 1] = self.data[i0[0]:i1[0], i1[1], i0[2]:i1[2]]
        if i0[2] > 0:
            padded_sub[1:sx + 1, 1:sy + 1, 0] = self.data[i0[0]:i1[0], i0[1]:i1[1], i0[2] - 1]
        if i1[2] < nz:
            padded_sub[1:sx + 1, 1:sy + 1, sz + 1] = self.data[i0[0]:i1[0], i0[1]:i1[1], i1[2]]

        frozen = (
            ((sub * padded_sub[0:sx, 1:sy + 1, 1:sz + 1]) <= 0.0) |
            ((sub * padded_sub[2:sx + 2, 1:sy + 1, 1:sz + 1]) <= 0.0) |
            ((sub * padded_sub[1:sx + 1, 0:sy, 1:sz + 1]) <= 0.0) |
            ((sub * padded_sub[1:sx + 1, 2:sy + 2, 1:sz + 1]) <= 0.0) |
            ((sub * padded_sub[1:sx + 1, 1:sy + 1, 0:sz]) <= 0.0) |
            ((sub * padded_sub[1:sx + 1, 1:sy + 1, 2:sz + 2]) <= 0.0)
        )

        # Step 2: Initialize non-frozen voxels with large value keeping original signs
        orig_sign = np.where(sub < 0.0, -1.0, 1.0).astype(np.float32)
        d_abs = np.abs(sub).astype(np.float32)
        d_abs[~frozen] = 1.0e6

        # Build padded distance grid (sx+2, sy+2, sz+2) with boundary conditions from outside sub-box
        padded_d = np.full((sx + 2, sy + 2, sz + 2), 1.0e6, dtype=np.float32)
        padded_d[1:sx + 1, 1:sy + 1, 1:sz + 1] = d_abs

        if i0[0] > 0:
            padded_d[0, 1:sy + 1, 1:sz + 1] = np.abs(padded_sub[0, 1:sy + 1, 1:sz + 1])
        if i1[0] < nx:
            padded_d[sx + 1, 1:sy + 1, 1:sz + 1] = np.abs(padded_sub[sx + 1, 1:sy + 1, 1:sz + 1])

        if i0[1] > 0:
            padded_d[1:sx + 1, 0, 1:sz + 1] = np.abs(padded_sub[1:sx + 1, 0, 1:sz + 1])
        if i1[1] < ny:
            padded_d[1:sx + 1, sy + 1, 1:sz + 1] = np.abs(padded_sub[1:sx + 1, sy + 1, 1:sz + 1])

        if i0[2] > 0:
            padded_d[1:sx + 1, 1:sy + 1, 0] = np.abs(padded_sub[1:sx + 1, 1:sy + 1, 0])
        if i1[2] < nz:
            padded_d[1:sx + 1, 1:sy + 1, sz + 1] = np.abs(padded_sub[1:sx + 1, 1:sy + 1, sz + 1])

        # Step 3: Fast sweeping 8 directions across anti-diagonal planes
        gx, gy, gz = np.indices((sx, sy, sz))
        plane_sum = (gx + gy + gz).ravel()
        max_c = sx + sy + sz - 2
        order_diag = np.argsort(plane_sum)
        split_pts = np.searchsorted(plane_sum[order_diag], np.arange(max_c + 1))

        gx_flat = gx.ravel()
        gy_flat = gy.ravel()
        gz_flat = gz.ravel()

        stride_x = (sy + 2) * (sz + 2)
        stride_y = sz + 2

        # Extract coordinate arrays per plane once
        plane_coords_list = []
        for c in range(max_c):
            p0 = split_pts[c]
            p1 = split_pts[c + 1]
            if p0 == p1:
                plane_coords_list.append(None)
            else:
                c_idx = order_diag[p0:p1]
                plane_coords_list.append((gx_flat[c_idx], gy_flat[c_idx], gz_flat[c_idx]))

        dirs = [
            (1, 1, 1), (-1, 1, 1), (1, -1, 1), (-1, -1, 1),
            (1, 1, -1), (-1, 1, -1), (1, -1, -1), (-1, -1, -1)
        ]

        precomputed_sweeps = []
        for dx, dy, dz in dirs:
            planes_for_dir = []
            for c in range(max_c):
                coords = plane_coords_list[c]
                if coords is None:
                    continue
                x_c, y_c, z_c = coords
                ix = x_c if dx == 1 else (sx - 1 - x_c)
                iy = y_c if dy == 1 else (sy - 1 - y_c)
                iz = z_c if dz == 1 else (sz - 1 - z_c)
                act = ~frozen[ix, iy, iz]
                if np.any(act):
                    px = ix[act] + 1
                    py = iy[act] + 1
                    pz = iz[act] + 1
                    idx_1d = px * stride_x + py * stride_y + pz
                    planes_for_dir.append(idx_1d)
            precomputed_sweeps.append(planes_for_dir)

        flat_d = padded_d.ravel()

        ihx, ihy, ihz = np.float32(inv_h2[0]), np.float32(inv_h2[1]), np.float32(inv_h2[2])
        H_vals = np.array([hx, hy, hz], dtype=np.float32)
        IH_vals = np.array([ihx, ihy, ihz], dtype=np.float32)
        is_isotropic = (abs(hx - hy) < 1e-7 and abs(hy - hz) < 1e-7)
        hx32 = np.float32(hx)
        hx2 = np.float32(hx * hx)
        inv_3 = np.float32(1.0 / 3.0)

        for sweep_planes in precomputed_sweeps:
            for idx_curr in sweep_planes:
                ax = np.minimum(flat_d[idx_curr - stride_x], flat_d[idx_curr + stride_x])
                ay = np.minimum(flat_d[idx_curr - stride_y], flat_d[idx_curr + stride_y])
                az = np.minimum(flat_d[idx_curr - 1], flat_d[idx_curr + 1])

                if is_isotropic:
                    a12 = np.minimum(ax, ay)
                    a1 = np.minimum(a12, az)
                    a34 = np.maximum(ax, ay)
                    a3 = np.maximum(a34, az)
                    a2 = (ax + ay + az) - (a1 + a3)

                    cand1 = a1 + hx32
                    d12 = a1 - a2
                    d2_term = 2.0 * hx2 - d12 * d12
                    cand2 = 0.5 * (a1 + a2 + np.sqrt(np.maximum(d2_term, 0.0)))

                    d23 = a2 - a3
                    d31 = a3 - a1
                    d3_term = 3.0 * hx2 - (d12 * d12 + d23 * d23 + d31 * d31)
                    cand3 = (a1 + a2 + a3 + np.sqrt(np.maximum(d3_term, 0.0))) * inv_3

                    d_sol = np.where(cand1 <= a2, cand1, np.where((d2_term >= 0) & (cand2 <= a3), cand2, cand3))
                else:
                    A_stack = np.vstack([ax, ay, az])
                    order = np.argsort(A_stack, axis=0)
                    cols = np.arange(len(ax))

                    a1 = A_stack[order[0], cols]
                    a2 = A_stack[order[1], cols]
                    a3 = A_stack[order[2], cols]

                    h1 = H_vals[order[0]]
                    ih1 = IH_vals[order[0]]
                    ih2 = IH_vals[order[1]]
                    ih3 = IH_vals[order[2]]

                    cand1 = a1 + h1

                    A2 = ih1 + ih2
                    B2 = -2.0 * (a1 * ih1 + a2 * ih2)
                    C2 = (a1 * a1 * ih1) + (a2 * a2 * ih2) - 1.0
                    disc2 = B2 * B2 - 4.0 * A2 * C2
                    cand2 = np.where(disc2 >= 0, (-B2 + np.sqrt(np.maximum(disc2, 0.0))) / (2.0 * A2), 1.0e6)

                    A3 = ih1 + ih2 + ih3
                    B3 = -2.0 * (a1 * ih1 + a2 * ih2 + a3 * ih3)
                    C3 = (a1 * a1 * ih1) + (a2 * a2 * ih2) + (a3 * a3 * ih3) - 1.0
                    disc3 = B3 * B3 - 4.0 * A3 * C3
                    cand3 = np.where(disc3 >= 0, (-B3 + np.sqrt(np.maximum(disc3, 0.0))) / (2.0 * A3), 1.0e6)

                    d_sol = np.where(cand1 <= a2, cand1, np.where(cand2 <= a3, cand2, cand3))

                old_d = flat_d[idx_curr]
                flat_d[idx_curr] = np.minimum(old_d, d_sol)

        d_abs = padded_d[1:sx + 1, 1:sy + 1, 1:sz + 1]

        # Step 4: Restore original signs and narrow-band clamp
        out = (orig_sign * d_abs).astype(np.float32)
        if getattr(self, "band", None) is not None:
            out = np.clip(out, -self.band, self.band)
        self.data[sub_slice] = out

        # Step 5: Lipschitz check (diagnostic only in debug mode, scoped to sub-slice)
        from freecad.fields.core import fld_logger
        if fld_logger.get_enable_debug_log():
            if min(sub_shape) >= 2:
                g = np.gradient(out.astype(np.float64), *h)
                mag = np.sqrt(sum(np.square(c) for c in g))
                mask = np.abs(out) < (VOXEL_FAR * 0.5)
                mag[~mask] = 1.0
                lip = float(max(1.0, np.nanmax(mag)))
                if lip > 1.05:
                    fld_logger.debug(f"voxel_field: measured Lipschitz {lip:.3f} after redistance")

    def preferred_scene_resolution(self, scene_extent) -> int:
        """SB-033: Returns the scene-volume resolution along the longest axis at which one scene
        voxel is no larger than one grid voxel."""
        spacing = self.size / np.maximum(np.array(self.resolution) - 1, 1)
        grid_vox = float(np.min(spacing))
        if grid_vox <= 0.0:
            return 128
        longest_scene = float(max(scene_extent))
        if longest_scene <= 0.0:
            return 128
        return int(np.ceil(longest_scene / grid_vox))
