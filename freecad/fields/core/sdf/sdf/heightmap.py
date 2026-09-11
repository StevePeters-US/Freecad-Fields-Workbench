# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np
from freecad.fields.core.sdf.sdf_field import SdfField


def load_heightmap_grid(path):
    """Load a heightmap file as a float32 (H, W) array, or None on failure.

    `.npy`/`.npz` go through numpy and keep their stored values; every other
    extension is decoded as a grayscale image by Qt and normalised to [0, 1].

    Both callers used to be a bare `np.load(path)`, so an ordinary image -- the only
    kind the browse dialog offers -- failed with numpy's "This file contains pickled
    (object) data" message and the heightmap silently rendered flat.
    """
    from freecad.fields.core import fld_logger
    if not path:
        return None
    try:
        if str(path).lower().endswith((".npy", ".npz")):
            grid = np.load(path)
            if hasattr(grid, "files"):          # .npz archive: take the first array
                grid = grid[grid.files[0]]
            return np.ascontiguousarray(grid, dtype=np.float32)

        from PySide.QtGui import QImage
        img = QImage(path).convertToFormat(QImage.Format_Grayscale8)
        if img.isNull():
            fld_logger.error(f"load_heightmap_grid: Qt could not decode '{path}'")
            return None
        w, h = img.width(), img.height()
        # Qt pads each row out to a 4-byte boundary, so the buffer is bytesPerLine
        # wide rather than width wide; reshaping straight to (h, w) skews any image
        # whose width is not a multiple of 4.
        stride = img.bytesPerLine()
        ptr = img.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(stride * h)
        raw = np.frombuffer(ptr, dtype=np.uint8, count=stride * h).reshape((h, stride))
        return np.ascontiguousarray(raw[:, :w], dtype=np.float32) / 255.0
    except Exception as e:
        fld_logger.error(f"load_heightmap_grid: failed to load '{path}': {e}")
        return None


class SdfHeightmapField(SdfField):
    """
    Heightmap image displacement modifier.

    Projects 3D evaluation coordinates onto a plane perpendicular to
    `direction`, samples a grayscale image for intensity h(u,v) in [0,1],
    and displaces the surface by shifting the evaluation point along the
    direction vector:

        d(p) = d_base(p - h(u, v) * amplitude * direction) / lip

    This ensures displacement is strictly along the direction axis.
    Side faces (whose normals are perpendicular to direction) are unaffected.

    UV coordinates are computed as:
        u = dot(p - origin, u_axis) / size_x + 0.5
        v = dot(p - origin, v_axis) / size_y + 0.5

    When tile=True (default), UV wraps with fract() for seamless tiling.
    When tile=False, UV clamps to the nearest edge pixel.
    """

    def __init__(self, base_field: SdfField, image_path: str = "",
                 amplitude: float = 5.0, size_x: float = 100.0, size_y: float = 100.0,
                 direction: FreeCAD.Vector = None, origin: FreeCAD.Vector = None,
                 facing_cutoff: float = 0.0, tile: bool = True):
        self.base_field = base_field
        self.image_path = image_path
        self.amplitude = amplitude
        self.size_x = max(size_x, 1e-6)
        self.size_y = max(size_y, 1e-6)
        d = FreeCAD.Vector(direction) if direction is not None else FreeCAD.Vector(0, 0, 1)
        length = d.Length
        self.direction = d * (1.0 / length) if length > 1e-8 else FreeCAD.Vector(0, 0, 1)
        self.origin = FreeCAD.Vector(origin) if origin is not None else FreeCAD.Vector(0, 0, 0)
        self._u_axis, self._v_axis = self._make_basis(self.direction)
        # facing_cutoff: -1.0 = no cutoff (all surfaces), 0.0 = front hemisphere, 1.0 = exact front
        self.facing_cutoff = float(facing_cutoff)
        self.tile = bool(tile)

        self._image_data = None  # numpy float32 (H, W), values in [0, 1]
        if image_path:
            self._load_image(image_path)

    @staticmethod
    def _make_basis(d):
        ref = FreeCAD.Vector(1, 0, 0) if abs(d.x) < 0.9 else FreeCAD.Vector(0, 1, 0)
        u = d.cross(ref)
        u = u * (1.0 / u.Length) if u.Length > 1e-8 else FreeCAD.Vector(0, 1, 0)
        v = d.cross(u)
        v = v * (1.0 / v.Length) if v.Length > 1e-8 else FreeCAD.Vector(1, 0, 0)
        return u, v

    def _load_image(self, path):
        self._image_data = load_heightmap_grid(path)

    def _sample_cpu(self, uv_x: float, uv_y: float) -> float:
        if self._image_data is None:
            return 0.0
        H, W = self._image_data.shape
        px = np.clip(uv_x * W - 0.5, 0.0, W - 1.0)
        py = np.clip(uv_y * H - 0.5, 0.0, H - 1.0)
        x0 = int(px)
        y0 = int(py)
        x1 = min(x0 + 1, W - 1)
        y1 = min(y0 + 1, H - 1)
        tx = px - x0
        ty = py - y0
        return float(
            self._image_data[y0, x0] * (1 - tx) * (1 - ty)
            + self._image_data[y0, x1] * tx * (1 - ty)
            + self._image_data[y1, x0] * (1 - tx) * ty
            + self._image_data[y1, x1] * tx * ty
        )

    def _lip(self):
        return max(1.0 + abs(self.amplitude) * 2.0 / min(self.size_x, self.size_y), 1.0)

    def lipschitz(self) -> float:
        """Upper bound on |grad| of the value this field returns.

        The value is returned unscaled — see `SdfNoise2DField.lipschitz` for what
        dividing it costs. The bound rides here, where the octree and the ray
        march's step divisor read it.
        """
        return self.base_field.lipschitz() * self._lip()

    def _facing_eps(self):
        """Finite-difference step for normal estimation (directional derivative of base SDF)."""
        return max(min(self.size_x, self.size_y) * 0.02, 0.5)

    def _facing_weight_scalar(self, point: FreeCAD.Vector) -> float:
        """Compute facing weight in [0, 1] using directional derivative of base SDF."""
        eps = self._facing_eps()
        d = self.direction
        p_fwd = FreeCAD.Vector(point.x + d.x * eps, point.y + d.y * eps, point.z + d.z * eps)
        p_bwd = FreeCAD.Vector(point.x - d.x * eps, point.y - d.y * eps, point.z - d.z * eps)
        facing = (self.base_field.evaluate(p_fwd) - self.base_field.evaluate(p_bwd)) / (2.0 * eps)
        margin = 0.1
        t = max(0.0, min(1.0, (facing - (self.facing_cutoff - margin)) / (2.0 * margin)))
        return t * t * (3.0 - 2.0 * t)  # smoothstep

    def _uv_wrap(self, uv: float) -> float:
        return uv % 1.0 if self.tile else max(0.0, min(1.0, uv))

    def evaluate(self, point: FreeCAD.Vector) -> float:
        rel = point - self.origin
        uv_x = self._uv_wrap(rel.dot(self._u_axis) / self.size_x + 0.5)
        uv_y = self._uv_wrap(rel.dot(self._v_axis) / self.size_y + 0.5)
        h = self._sample_cpu(uv_x, uv_y)
        if self.facing_cutoff > -0.99:
            h *= self._facing_weight_scalar(point)
        d = self.direction
        disp = h * self.amplitude
        displaced = FreeCAD.Vector(
            point.x - disp * d.x,
            point.y - disp * d.y,
            point.z - disp * d.z,
        )
        return self.base_field.evaluate(displaced)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        if self._image_data is None:
            return self.base_field.evaluate_grid(points).astype(np.float32)

        H, W = self._image_data.shape
        orig = np.array([self.origin.x, self.origin.y, self.origin.z], dtype=np.float32)
        ua = np.array([self._u_axis.x, self._u_axis.y, self._u_axis.z], dtype=np.float32)
        va = np.array([self._v_axis.x, self._v_axis.y, self._v_axis.z], dtype=np.float32)
        rel = points - orig
        uv_x = rel @ ua / self.size_x + 0.5
        uv_y = rel @ va / self.size_y + 0.5
        if self.tile:
            uv_x = uv_x % 1.0
            uv_y = uv_y % 1.0
        else:
            uv_x = np.clip(uv_x, 0.0, 1.0)
            uv_y = np.clip(uv_y, 0.0, 1.0)

        px = np.clip(uv_x * W - 0.5, 0.0, W - 1.0)
        py = np.clip(uv_y * H - 0.5, 0.0, H - 1.0)
        x0 = px.astype(np.int32)
        y0 = py.astype(np.int32)
        x1 = np.minimum(x0 + 1, W - 1)
        y1 = np.minimum(y0 + 1, H - 1)
        tx = (px - x0).astype(np.float32)
        ty = (py - y0).astype(np.float32)
        heights = (
            self._image_data[y0, x0] * (1 - tx) * (1 - ty)
            + self._image_data[y0, x1] * tx * (1 - ty)
            + self._image_data[y1, x0] * (1 - tx) * ty
            + self._image_data[y1, x1] * tx * ty
        )

        if self.facing_cutoff > -0.99:
            eps = self._facing_eps()
            dir_arr = np.array([self.direction.x, self.direction.y, self.direction.z], dtype=np.float32)
            d_fwd = self.base_field.evaluate_grid(points + dir_arr * eps)
            d_bwd = self.base_field.evaluate_grid(points - dir_arr * eps)
            facing = (d_fwd - d_bwd) / (2.0 * eps)
            margin = 0.1
            t = np.clip((facing - (self.facing_cutoff - margin)) / (2.0 * margin), 0.0, 1.0)
            heights = heights * (t * t * (3.0 - 2.0 * t)).astype(np.float32)

        dir_arr = np.array([self.direction.x, self.direction.y, self.direction.z], dtype=np.float32)
        displaced = points - (heights * self.amplitude)[:, np.newaxis] * dir_arr
        return self.base_field.evaluate_grid(displaced).astype(np.float32)

    def bounding_box(self):
        bb_min, bb_max = self.base_field.bounding_box()
        offset = FreeCAD.Vector(abs(self.amplitude), abs(self.amplitude), abs(self.amplitude))
        return (bb_min - offset, bb_max + offset)

    def to_glsl(self, ctx, point_var="p"):
        if not self.image_path:
            return self.base_field.to_glsl(ctx, point_var)

        u_sx     = ctx.uniform("float", self.size_x)
        u_sy     = ctx.uniform("float", self.size_y)
        u_orig   = ctx.uniform("vec3",  (self.origin.x,    self.origin.y,    self.origin.z))
        u_u_axis = ctx.uniform("vec3",  (self._u_axis.x,   self._u_axis.y,   self._u_axis.z))
        u_v_axis = ctx.uniform("vec3",  (self._v_axis.x,   self._v_axis.y,   self._v_axis.z))
        u_amp    = ctx.uniform("float", self.amplitude)
        u_dir    = ctx.uniform("vec3",  (self.direction.x,  self.direction.y,  self.direction.z))

        tex_name = ctx.sampler2d("heightmap", self.image_path)
        self._sampler_name = tex_name

        # tile vs clamp determines both the function name and the UV expression
        tile_char = "t" if self.tile else "c"
        fn = f"hmap_{tile_char}_{abs(hash(self.image_path)) & 0xFFFFFF:06x}"
        uv_expr = "fract(uv)" if self.tile else "clamp(uv, 0.0, 1.0)"
        ctx.add_custom_helper(fn,
            f"float {fn}(vec3 p, sampler2D tex, float sx, float sy,"
            f" vec3 orig, vec3 u_ax, vec3 v_ax) {{\n"
            f"    vec3 rel = p - orig;\n"
            f"    vec2 uv = vec2(dot(rel, u_ax) / sx + 0.5, dot(rel, v_ax) / sy + 0.5);\n"
            f"    return texture(tex, {uv_expr}).r;\n"
            f"}}"
        )
        h_expr = f"{fn}({point_var}, {tex_name}, {u_sx}, {u_sy}, {u_orig}, {u_u_axis}, {u_v_axis})"

        # Facing cutoff: weight = smoothstep of dot(surface_normal, direction)
        if self.facing_cutoff > -0.99:
            u_feps   = ctx.uniform("float", self._facing_eps())
            u_cutoff = ctx.uniform("float", self.facing_cutoff)
            fwd_pt   = f"({point_var} + {u_dir} * {u_feps})"
            bwd_pt   = f"({point_var} - {u_dir} * {u_feps})"
            fwd_glsl = self.base_field.to_glsl(ctx, fwd_pt)
            bwd_glsl = self.base_field.to_glsl(ctx, bwd_pt)
            face_expr   = f"(({fwd_glsl}) - ({bwd_glsl})) / (2.0 * {u_feps})"
            weight_expr = f"smoothstep({u_cutoff} - 0.1, {u_cutoff} + 0.1, {face_expr})"
            h_scaled = f"({h_expr} * {weight_expr} * {u_amp})"
        else:
            h_scaled = f"({h_expr} * {u_amp})"

        # Directional displacement: shift evaluation point along direction axis
        displaced_pt = f"({point_var} - {h_scaled} * {u_dir})"
        base_at_displaced = self.base_field.to_glsl(ctx, displaced_pt)
        return f"({base_at_displaced})"
