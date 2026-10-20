# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT, placement_matrix
import math
import re
import numpy as np
from freecad.fields.core.sdf.sdf.formula_eval import (
    parse_custom_params, update_formula_param_comment, clean_formula_code,
    GlslVec2, GlslVec3, GlslVec4,
    EVAL_NS, EVAL_NS_NP, eval_bound,
    MAX_SCALAR_FALLBACK_POINTS,
    _eval_formula_grid_np,
    safe_compile,
)


# ── SdfNoiseField ─────────────────────────────────────────────────────────────


class SdfNoiseField(SdfField):
    """
    Applies procedural noise to a base SDF field as a displacement, evaluated
    in the object's own Placement -- object-local Cartesian xyz, not a
    hand-rolled projection frame. The field always evaluates formula.
    Lipschitz and peak bounds are derived from what the formula contains
    (which kernels it calls, whether it has periodic terms, and declared params).

    Formula variables: x, y, z (world space point), p (GlslVec3(x, y, z)),
    surf_n (surface normal of base field), amp, freq.
    """

    #: An unboundable graph -- an Expression node, a discontinuous op (floor, sign,
    #: fract), or a formula with no `// @bound` lines -- is charged this per unit of
    #: |amp| * freq, unless Normalization overrides it. A stated guess (4x a sine's
    #: 2*pi); the warning tells the user to set Normalization.
    _UNBOUNDED_SLOPE_FACTOR = 4.0 * 6.2831853
    #: Its peak, per unit of |amp|.
    _UNBOUNDED_PEAK_FACTOR = 4.0

    def __init__(self, base_field: SdfField, amplitude: float, frequency: float,
                 placement: FreeCAD.Placement = None, formula: str = None,
                 normalization: float = 0.0,
                 custom_params: dict = None,
                 gates: dict = None,
                 label: str = ""):
        """Procedural noise SDF field.

        Args:
            label: Human-readable object label (e.g. from Part object Label) used to name
                the object in logs and warnings.
        """
        self.label = label
        self.base_field = base_field
        self.amplitude = amplitude
        self.frequency = frequency
        self.placement = placement if placement is not None else FreeCAD.Placement()
        # world -> local, for the sample point.
        self.inv_matrix = self._compute_inv_matrix(self.placement)
        # local -> world, rotation only, for the displacement the graph returns.
        # Rows are the world-space images of the local basis vectors, so that a
        # row-vector array of local displacements transforms via `disp @ _rot_np.T`.
        rot = self.placement.Rotation
        ex = rot.multVec(FreeCAD.Vector(1, 0, 0))
        ey = rot.multVec(FreeCAD.Vector(0, 1, 0))
        ez = rot.multVec(FreeCAD.Vector(0, 0, 1))
        self._rot_np = np.array(
            [[ex.x, ex.y, ex.z],
             [ey.x, ey.y, ey.z],
             [ez.x, ez.y, ez.z]],
            dtype=np.float32).T
        self._normalization = normalization
        self.custom_params = custom_params or {}
        self.gates = dict(gates) if gates else {}
        self._axis_extent_cache = None

        if not formula:
            raise ValueError("SdfNoiseField needs a formula")
        self.formula = formula
        for p in parse_custom_params(self.formula):
            if p['name'] not in self.custom_params and p['default'] is not None:
                self.custom_params[p['name']] = p['default']
        self._formula_valid = True
        self._clean_formula = clean_formula_code(self.formula)
        if self._clean_formula:
            try:
                safe_compile(self._clean_formula)
            except Exception as e:
                self._formula_valid = False
                from freecad.fields.core import fld_logger
                fld_logger.warn(f"SdfNoiseField: formula validation failed ({e})")

        self._formula_uses_surf_n = bool(
            self._clean_formula and re.search(r'\bsurf_n\b', self._clean_formula)
        )
        self._formula_uses_front = bool(
            self._clean_formula and re.search(r'\bfront\b', self._clean_formula)
        )
        self._h_step = None
        self._returns_vec4 = self._check_return_shape()
        self._warned_fallback = False
        self._peak_cache = None
        self._slope_cache = None
        self._warned_unbounded = False

    def _check_return_shape(self):
        """Enforce that the formula returns a vec3 displacement or a vec4
        displacement + offset (NV-029/NV-038), and return True for vec4.

        GLSL is statically typed, so the shape is a property of the formula text:
        decided once here, and read by to_glsl, which must declare the helper's
        return type to match its body (NV-039)."""
        if not self._formula_valid:
            return False
        res = self._eval_formula(0.37, -0.21, 0.11) if self._clean_formula else None
        if not isinstance(res, (GlslVec3, GlslVec4)):
            name = self.label or "SdfNoiseField"
            raise ValueError(f"{name}: noise formula must return a vec3 or vec4")
        return isinstance(res, GlslVec4)

    @staticmethod
    def _make_basis(d, roll: float = 0.0):
        """Orthonormal (u, w) spanning the plane the pattern is drawn in.

        `roll` (degrees) spins that basis about `d` itself. Direction alone cannot
        express it — rotating d about d is the identity — so without a stored roll
        the in-plane orientation of the pattern is not editable at all.
        """
        ref = FreeCAD.Vector(0, 1, 0) if abs(d.x) > 0.9 else FreeCAD.Vector(1, 0, 0)
        u = ref - d * ref.dot(d)
        u = u / u.Length if u.Length > 1e-8 else FreeCAD.Vector(1, 0, 0)
        # u x d, NOT d x u. (u, w, d) is deliberately LEFT-handed: the default
        # direction is (0, 0, -1), and the frame exists so that a vec3 displacement
        # reads the way the top view draws it -- u on +X, w on +Y. `d.cross(u)`
        # is the right-handed choice and lands w on -Y, which leaves the pattern
        # mirrored in y from the top. That is half of the swizzle NN-006 fixed,
        # and it looks like a sign typo to anyone tidying this up. It is not.
        w = u.cross(d)
        w = w / w.Length if w.Length > 1e-8 else FreeCAD.Vector(0, 1, 0)
        if abs(roll) > 1e-9:
            rot = FreeCAD.Rotation(d, roll)
            u = rot.multVec(u)
            w = rot.multVec(w)
        return u, w

    @staticmethod
    def roll_from_basis(d, u_vec):
        """Inverse of _make_basis' roll: the spin of `u_vec` about `d`, in degrees.

        Lets a caller rotate the whole frame (direction + u axis) with one rotation
        and hand the leftover spin back as a Roll value.
        """
        u0, w0 = SdfNoiseField._make_basis(d)
        return math.degrees(math.atan2(u_vec.dot(d.cross(u0)), u_vec.dot(u0)))

    @property
    def direction(self) -> FreeCAD.Vector:
        """The local +Z axis in world space -- the historical Direction arrow.

        Local q.z (what `evaluate`/`to_glsl` hand the formula as `z`) is the
        component of a world point along this same axis: for a rotation matrix
        R, `q = R^-1(p - base)` has `q.z = (p - base) . R(+Z)`. CN-001's default
        placement rotates local +Z onto world (0, 0, -1), so this reproduces the
        old default Direction with no stored vector.
        """
        return self.placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))

    def _to_world_vec(self, v):
        """ToWorldLocalNode's 'To World' (transform.py): local -> world,
        ROTATION ONLY -- matches apply_rot_mat's GLSL meaning (a mat3 carries
        no translation). The formula text adds `to_world_base` itself
        afterward, on both the GLSL and this CPU side, so translation is not
        applied twice. Vectorized when v's components are numpy arrays, using
        `_rot_np` -- the same matrix evaluate_grid uses for the opposite
        direction."""
        if isinstance(v.x, np.ndarray):
            arr = np.stack([v.x, v.y, v.z], axis=-1).astype(np.float32)
            rotated = arr @ self._rot_np.T
            return GlslVec3(rotated[..., 0], rotated[..., 1], rotated[..., 2])
        world = self.placement.Rotation.multVec(FreeCAD.Vector(v.x, v.y, v.z))
        return GlslVec3(world.x, world.y, world.z)

    def _to_local_vec(self, v):
        """ToWorldLocalNode's 'To Local': world point -> local point
        (translation included -- matches apply_inv_mat's GLSL meaning).
        Reuses _to_local_point/_to_local_grid, the same placement transform
        every other placed field goes through."""
        if isinstance(v.x, np.ndarray):
            pts = np.stack([v.x, v.y, v.z], axis=-1).astype(np.float32)
            local = self._to_local_grid(pts)
            return GlslVec3(local[:, 0], local[:, 1], local[:, 2])
        local = self._to_local_point(FreeCAD.Vector(v.x, v.y, v.z))
        return GlslVec3(local.x, local.y, local.z)

    def _placement_ns_extra(self):
        """Namespace entries ToWorldLocalNode's compiled text calls by name --
        `apply_rot_mat`/`apply_inv_mat` (formula_eval.py) just invoke whichever
        of these two closures is passed as `m`, so the identical formula text
        means the identical thing on the CPU and the GPU (noise.py:to_glsl
        wires the same two names to the real matrix uniforms there)."""
        b = self.placement.Base
        return {
            "to_world_rot": self._to_world_vec,
            "to_world_base": GlslVec3(b.x, b.y, b.z),
            "to_local_inv": self._to_local_vec,
        }

    def _builtin_active(self, name: str) -> bool:
        """Returns True if the builtin (surf_n or front) should be computed and charged.

        If gated by a custom parameter, checks that parameter value (>0 or True).
        If not gated, active if referenced by the formula.
        """
        if name == "surf_n" and not self._formula_uses_surf_n:
            return False
        if name == "front" and not self._formula_uses_front:
            return False
        gate_param = self.gates.get(name)
        if not gate_param:
            return True
        val = self.custom_params.get(gate_param, 0.0)
        if isinstance(val, bool):
            return val
        try:
            return float(val) > 0.0
        except Exception:
            return bool(val)

    def _front_face_band(self):
        """(lift, ramp) for the front-face weight, `clamp(base(p - dir*lift) / ramp)`."""
        reach = max(self._reach(), 1e-6)
        lift = min(max(self._axis_extent() * 0.5, reach), reach * 2.0)
        ramp = max(lift * 0.5, reach)
        lift = max(lift, ramp)
        return lift, ramp

    def _bound_values(self, front_slope, surf_n_slope):
        return {"amp": self.amplitude, "freq": self.frequency, **self.custom_params,
                "front_slope": front_slope, "surf_n_slope": surf_n_slope}

    def _warn_unbounded(self, what):
        if self._normalization > 0.0 or self._warned_unbounded:
            return
        self._warned_unbounded = True
        from freecad.fields.core import fld_logger
        fld_logger.warn(f"{self.label or 'Noise'}: the noise graph's {what} has no "
                        f"bound (an Expression node or a discontinuous op); the march "
                        f"uses a guessed one. Set Normalization to override it.")

    def _wave_peak(self):
        """Upper bound on |noise| in mm: the graph's peak (NV-053). Evaluated with
        both builtin slopes at 0 -- no peak reads a slope -- because those slopes
        need _reach(), which is this."""
        if self._peak_cache is None:
            got = eval_bound(self.formula, self._bound_values(0.0, 0.0))
            if got is None or not math.isfinite(got[0]):
                self._warn_unbounded("peak")
                self._peak_cache = abs(self.amplitude) * self._UNBOUNDED_PEAK_FACTOR
            else:
                self._peak_cache = abs(got[0])
        return self._peak_cache

    def _graph_slope(self):
        """Upper bound on |d displacement / d position| (NV-053)."""
        if self._slope_cache is None:
            front = (1.0 / self._front_face_band()[1]) if self._builtin_active("front") else 0.0
            surf = self._normal_slope() if self._builtin_active("surf_n") else 0.0
            got = eval_bound(self.formula, self._bound_values(front, surf))
            if got is None or not math.isfinite(got[1]):
                self._warn_unbounded("slope")
                peak = self._wave_peak()
                self._slope_cache = (abs(self.amplitude) * self.frequency
                                     * self._UNBOUNDED_SLOPE_FACTOR
                                     + front * peak + 2.0 * surf * peak)
            else:
                self._slope_cache = abs(got[1])
        return self._slope_cache

    def _lip(self):
        if self._normalization > 0.0:
            return self._normalization
        return 1.0 + self._graph_slope()

    def _step_h(self):
        """The finite-difference step h for surface normal computation."""
        if self._h_step is None:
            self._h_step = max(2.0 * self._reach(), 0.1)
        return self._h_step

    def _normal_slope(self):
        """|grad surf_n| <= 4 * sqrt(3) * L_b / h."""
        h = self._step_h()
        l_b = self.base_field.lipschitz()
        return 4.0 * math.sqrt(3.0) * l_b / h

    def _eval_surface_normal(self, p: FreeCAD.Vector):
        """Compute outward surface normal at world point p over tetrahedral step h."""
        h = self._step_h()
        # Tetrahedral offsets k:
        # k1 = (1, -1, -1), k2 = (-1, -1, 1), k3 = (-1, 1, -1), k4 = (1, 1, 1)
        k1 = FreeCAD.Vector(1.0, -1.0, -1.0)
        k2 = FreeCAD.Vector(-1.0, -1.0, 1.0)
        k3 = FreeCAD.Vector(-1.0, 1.0, -1.0)
        k4 = FreeCAD.Vector(1.0, 1.0, 1.0)
        d1 = self.base_field.evaluate(p + k1 * h)
        d2 = self.base_field.evaluate(p + k2 * h)
        d3 = self.base_field.evaluate(p + k3 * h)
        d4 = self.base_field.evaluate(p + k4 * h)
        g = k1 * d1 + k2 * d2 + k3 * d3 + k4 * d4
        denom = max(g.Length, 2.0 * h)
        n = g * (1.0 / denom)
        return GlslVec3(n.x, n.y, n.z)

    def _eval_surface_normal_grid(self, points: np.ndarray):
        """Compute outward surface normal for points (N, 3) over tetrahedral step h."""
        h = np.float32(self._step_h())
        k1 = np.array([1.0, -1.0, -1.0], dtype=np.float32)
        k2 = np.array([-1.0, -1.0, 1.0], dtype=np.float32)
        k3 = np.array([-1.0, 1.0, -1.0], dtype=np.float32)
        k4 = np.array([1.0, 1.0, 1.0], dtype=np.float32)

        d1 = self.base_field.evaluate_grid(points + k1 * h)
        d2 = self.base_field.evaluate_grid(points + k2 * h)
        d3 = self.base_field.evaluate_grid(points + k3 * h)
        d4 = self.base_field.evaluate_grid(points + k4 * h)

        gx = k1[0] * d1 + k2[0] * d2 + k3[0] * d3 + k4[0] * d4
        gy = k1[1] * d1 + k2[1] * d2 + k3[1] * d3 + k4[1] * d4
        gz = k1[2] * d1 + k2[2] * d2 + k3[2] * d3 + k4[2] * d4

        g_len = np.sqrt(gx**2 + gy**2 + gz**2)
        denom = np.maximum(g_len, 2.0 * h)
        nx = gx / denom
        ny = gy / denom
        nz = gz / denom
        return GlslVec3(nx, ny, nz)

    def lipschitz(self) -> float:
        """Upper bound on |grad| of the value this field returns."""
        return self.base_field.lipschitz() * self._lip()

    def _front_weight_at_point(self, point: FreeCAD.Vector):
        """Scalar twin of the weight `to_glsl` computes for `weight_expr`: how far
        `point` sits in front of the base field's surface, clamped to [0, 1]."""
        lift, ramp = self._front_face_band()
        d = self.direction
        behind = FreeCAD.Vector(
            point.x - d.x * lift,
            point.y - d.y * lift,
            point.z - d.z * lift,
        )
        d_behind = self.base_field.evaluate(behind)
        return max(0.0, min(1.0, d_behind / ramp))

    def _front_weight_grid(self, points: np.ndarray, da: np.ndarray):
        """Grid twin of `_front_weight_at_point`. `da` is `direction` as a
        float32 array, already available to every caller in `evaluate_grid`."""
        lift, ramp = self._front_face_band()
        behind_points = points - da * lift
        d_behind = self.base_field.evaluate_grid(behind_points)
        return np.clip(d_behind / ramp, 0.0, 1.0).astype(np.float32)

    def _eval_formula(self, x, y, z=0.0, surf_n=None, front=None):
        ns = dict(EVAL_NS)
        ns["x"] = x; ns["y"] = y; ns["z"] = z
        ns["p"] = GlslVec3(x, y, z)
        if surf_n is not None:
            ns["surf_n"] = surf_n
        elif self._formula_uses_surf_n:
            ns["surf_n"] = GlslVec3(0.0, 0.0, 0.0)
        if front is not None:
            ns["front"] = front
        elif self._formula_uses_front:
            ns["front"] = 1.0
        ns["amp"] = self.amplitude; ns["freq"] = self.frequency
        ns.update(self._placement_ns_extra())
        for k, v in self.custom_params.items():
            if isinstance(v, (tuple, list)):
                if len(v) == 2:
                    ns[k] = GlslVec2(float(v[0]), float(v[1]))
                else:
                    ns[k] = GlslVec3(float(v[0]), float(v[1]), float(v[2]))
            elif hasattr(v, 'x') and hasattr(v, 'y'):
                if hasattr(v, 'z'):
                    ns[k] = GlslVec3(float(v.x), float(v.y), float(v.z))
                else:
                    ns[k] = GlslVec2(float(v.x), float(v.y))
            else:
                ns[k] = v
        try:
            res = eval(safe_compile(self._clean_formula), {"__builtins__": {}}, ns)
            if isinstance(res, (GlslVec4, GlslVec3, GlslVec2)):
                return res
            return float(res)
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug_throttled(
                "noise_formula_eval_fail",
                f"SdfNoiseField: formula eval failed ({e}); contributing 0.0",
            )
            return 0.0

    def _eval_noise_grid(self, x_vals, y_vals, z_vals=None):
        """Vectorized counterpart of _eval_formula for a whole grid at once."""
        if z_vals is None:
            z_vals = np.zeros_like(x_vals)
        try:
            return _eval_formula_grid_np(
                self._clean_formula, self.custom_params,
                {"x": x_vals, "y": y_vals, "z": z_vals,
                 "p": GlslVec3(x_vals, y_vals, z_vals),
                 "amp": self.amplitude, "freq": self.frequency,
                 **self._placement_ns_extra()},
                x_vals.shape,
            )
        except Exception as e:
            from freecad.fields.core import fld_logger
            n = len(x_vals)
            if n > MAX_SCALAR_FALLBACK_POINTS:
                fld_logger.error(
                    f"SdfNoiseField: formula not vectorizable ({e}) and grid is "
                    f"{n} points — refusing the per-point fallback (would freeze). "
                    "Noise contributes 0; rewrite the formula using numpy-safe ops."
                )
                return np.zeros(x_vals.shape, dtype=np.float32)
            if not self._warned_fallback:
                self._warned_fallback = True
                fld_snippet = (self._clean_formula[:80] if self._clean_formula else "")
                name = self.label or "SdfNoiseField"
                fld_logger.warn(
                    f"{name}: formula not vectorizable ({e}), falling back to per-point eval; "
                    f"formula[:80]={fld_snippet!r}"
                )
            fld_logger.debug_throttled(
                "noise_vectorize_fallback",
                f"SdfNoiseField: formula not vectorizable ({e}), falling back to per-point eval",
            )
            return np.fromiter(
                (self._eval_formula(float(x_vals[i]), float(y_vals[i]), float(z_vals[i]))
                 for i in range(len(x_vals))),
                dtype=np.float32, count=len(x_vals),
            )

    def _eval_displacement(self, x, y, z, surf_n=None, front=None):
        """(dx, dy, dz, offset) world displacement and additive offset."""
        if not self._formula_valid:
            return (0.0, 0.0, 0.0, 0.0)
        n = self._eval_formula(x, y, z, surf_n=surf_n, front=front)
        if isinstance(n, GlslVec4):
            return (n.x, n.y, n.z, n.w)
        if isinstance(n, GlslVec3):
            return (n.x, n.y, n.z, 0.0)
        raise ValueError("noise formula must return a vec3 or vec4")

    def _eval_displacement_grid(self, x_vals, y_vals, z_vals=None, surf_n_vals=None, front_vals=None):
        """(dx, dy, dz, offset) float32 arrays of world displacement and additive offset."""
        if z_vals is None:
            z_vals = np.zeros_like(x_vals)

        try:
            grid_env = {
                "x": x_vals, "y": y_vals, "z": z_vals,
                "p": GlslVec3(x_vals, y_vals, z_vals),
                "amp": self.amplitude, "freq": self.frequency,
                **self._placement_ns_extra()
            }
            if surf_n_vals is not None:
                grid_env["surf_n"] = surf_n_vals
            elif self._formula_uses_surf_n:
                zeros = np.zeros_like(x_vals, dtype=np.float32)
                grid_env["surf_n"] = GlslVec3(zeros, zeros, zeros)
            if front_vals is not None:
                grid_env["front"] = front_vals
            elif self._formula_uses_front:
                grid_env["front"] = np.ones_like(x_vals, dtype=np.float32)
            res = _eval_formula_grid_np(
                self._clean_formula, self.custom_params,
                grid_env,
                x_vals.shape,
            )
            if isinstance(res, GlslVec4):
                return res.x, res.y, res.z, res.w
            if isinstance(res, GlslVec3):
                zeros = np.zeros_like(x_vals, dtype=np.float32)
                return res.x, res.y, res.z, zeros
            raise ValueError("noise formula must return a vec3 or vec4")
        except Exception as e:
            from freecad.fields.core import fld_logger
            n = len(x_vals)
            if n > MAX_SCALAR_FALLBACK_POINTS:
                fld_logger.error(
                    f"SdfNoiseField: formula not vectorizable ({e}) and grid is "
                    f"{n} points — refusing the per-point fallback (would freeze). "
                    "Noise contributes 0; rewrite the formula using numpy-safe ops."
                )
                zeros = np.zeros(x_vals.shape, dtype=np.float32)
                return zeros, zeros, zeros, zeros
            if not self._warned_fallback:
                self._warned_fallback = True
                fld_snippet = (self._clean_formula[:80] if self._clean_formula else "")
                name = self.label or "SdfNoiseField"
                fld_logger.warn(
                    f"{name}: formula not vectorizable ({e}), falling back to per-point eval; "
                    f"formula[:80]={fld_snippet!r}"
                )
            fld_logger.debug_throttled(
                "noise_vectorize_fallback",
                f"SdfNoiseField: formula not vectorizable ({e}), falling back to per-point eval"
            )
            dus = np.empty(n, dtype=np.float32)
            dws = np.empty(n, dtype=np.float32)
            dns = np.empty(n, dtype=np.float32)
            offs = np.empty(n, dtype=np.float32)
            for i in range(n):
                sn_i = GlslVec3(float(surf_n_vals.x[i]), float(surf_n_vals.y[i]), float(surf_n_vals.z[i])) if surf_n_vals is not None else None
                front_i = float(front_vals[i]) if front_vals is not None else None
                du, dw, dn, off = self._eval_displacement(
                    float(x_vals[i]), float(y_vals[i]), float(z_vals[i]), surf_n=sn_i, front=front_i)
                dus[i] = du
                dws[i] = dw
                dns[i] = dn
                offs[i] = off
            return dus, dws, dns, offs

    def _reach(self):
        """The furthest the surface can travel, in mm."""
        return self._wave_peak()

    def _axis_extent(self):
        """How thick the stock is along `direction`, from its own bounding box."""
        if self._axis_extent_cache is None:
            bb_min, bb_max = self.base_field.bounding_box()
            size = bb_max - bb_min
            d = self.direction
            self._axis_extent_cache = (abs(size.x * d.x) + abs(size.y * d.y)
                                       + abs(size.z * d.z))
        return self._axis_extent_cache

    def evaluate(self, point: FreeCAD.Vector) -> float:
        if self._builtin_active("surf_n"):
            surf_n = self._eval_surface_normal(point)
        elif self._formula_uses_surf_n:
            surf_n = GlslVec3(0.0, 0.0, 0.0)
        else:
            surf_n = None

        if self._builtin_active("front"):
            front = self._front_weight_at_point(point)
        elif self._formula_uses_front:
            front = 1.0
        else:
            front = None

        dx, dy, dz, offset = self._eval_displacement(point.x, point.y, point.z, surf_n=surf_n, front=front)
        disp = FreeCAD.Vector(dx, dy, dz)
        return self.base_field.evaluate(point - disp) + float(offset)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        if self._builtin_active("surf_n"):
            surf_n = self._eval_surface_normal_grid(points)
        elif self._formula_uses_surf_n:
            zeros = np.zeros(len(points), dtype=np.float32)
            surf_n = GlslVec3(zeros, zeros, zeros)
        else:
            surf_n = None

        if self._builtin_active("front"):
            d = self.direction
            da = np.array([d.x, d.y, d.z], dtype=np.float32)
            front = self._front_weight_grid(points, da)
        elif self._formula_uses_front:
            front = np.ones(len(points), dtype=np.float32)
        else:
            front = None

        dx, dy, dz, offsets = self._eval_displacement_grid(
            points[:, 0], points[:, 1], points[:, 2], surf_n_vals=surf_n, front_vals=front)
        disp = np.stack((dx, dy, dz), axis=1).astype(np.float32)
        displaced = points - disp
        base_vals = self.base_field.evaluate_grid(displaced).astype(np.float32)
        return (base_vals + offsets).astype(np.float32)

    def bounding_box(self):
        bb_min, bb_max = self.base_field.bounding_box()
        reach = self._reach()
        offset = FreeCAD.Vector(reach, reach, reach)
        return (bb_min - offset, bb_max + offset)

    def to_patch_cage(self):
        return self.base_field.to_patch_cage()

    def to_glsl(self, ctx, point_var="p"):
        amp_u = ctx.uniform("float", self.amplitude)
        freq_u = ctx.uniform("float", self.frequency)
        ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
        inv_u = ctx.uniform("mat4", self.inv_matrix.tolist())
        rot_mat = placement_matrix(
            FreeCAD.Placement(FreeCAD.Vector(), self.placement.Rotation), dtype=np.float32)
        rot_u = ctx.uniform("mat3", rot_mat[:3, :3].tolist())
        ctx.add_custom_helper("apply_rot_mat", (
            "vec3 apply_rot_mat(mat3 m, vec3 v) {\n"
            "    return m * v;\n"
            "}"
        ))
        q_expr = f"apply_inv_mat({inv_u}, {point_var})"
        ctx.add_custom_helper("fld_pi_const", "const float pi = 3.14159265358979323846;")

        surf_n_expr = None
        if self._formula_uses_surf_n:
            if not self._builtin_active("surf_n"):
                surf_n_expr = "vec3(0.0)"
            else:
                h = self._step_h()
                h_u = ctx.uniform("float", h)
                # Tetrahedral offsets: k = (1,-1,-1), (-1,-1,1), (-1,1,-1), (1,1,1)
                # p here is world space point_var
                d1 = self.base_field.to_glsl(ctx, f"({point_var} + {h_u} * vec3(1.0, -1.0, -1.0))")
                d2 = self.base_field.to_glsl(ctx, f"({point_var} + {h_u} * vec3(-1.0, -1.0, 1.0))")
                d3 = self.base_field.to_glsl(ctx, f"({point_var} + {h_u} * vec3(-1.0, 1.0, -1.0))")
                d4 = self.base_field.to_glsl(ctx, f"({point_var} + {h_u} * vec3(1.0, 1.0, 1.0))")
                ctx.add_custom_helper("fld_calc_surf_n", (
                    "vec3 fld_calc_surf_n(float d1, float d2, float d3, float d4, float h) {\n"
                    "    vec3 g = vec3(1.0, -1.0, -1.0) * d1 +\n"
                    "             vec3(-1.0, -1.0, 1.0) * d2 +\n"
                    "             vec3(-1.0, 1.0, -1.0) * d3 +\n"
                    "             vec3(1.0, 1.0, 1.0) * d4;\n"
                    "    float lg = length(g);\n"
                    "    return g / max(lg, 2.0 * h);\n"
                    "}"
                ))
                surf_n_expr = f"fld_calc_surf_n({d1}, {d2}, {d3}, {d4}, {h_u})"

        front_expr = None
        if self._formula_uses_front:
            if not self._builtin_active("front"):
                front_expr = "1.0"
            else:
                lift, ramp = self._front_face_band()
                lift_u = ctx.uniform("float", lift)
                ramp_u = ctx.uniform("float", ramp)
                d = self.direction
                front_dir_u = ctx.uniform("vec3", (d.x, d.y, d.z))
                offset_pt = f"({point_var} - {front_dir_u} * {lift_u})"
                behind_glsl = self.base_field.to_glsl(ctx, offset_pt)
                front_expr = f"clamp({behind_glsl} / {ramp_u}, 0.0, 1.0)"

        # Register custom parameters as uniforms
        param_args_decl = []
        param_args_call = []
        # `surf_n` and `front` are built-ins fed by their respective expressions.
        builtin_names = {"surf_n", "front"}
        for name, val in self.custom_params.items():
            if name in builtin_names:
                continue
            if isinstance(val, bool):
                ptype = "float"
                u_val = ctx.uniform(ptype, 1.0 if val else 0.0)
            elif isinstance(val, int):
                ptype = "int"
                u_val = ctx.uniform(ptype, val)
            elif isinstance(val, (tuple, list)):
                if len(val) == 2:
                    ptype = "vec2"
                    u_val = ctx.uniform(ptype, (float(val[0]), float(val[1])))
                else:
                    ptype = "vec3"
                    u_val = ctx.uniform(ptype, (float(val[0]), float(val[1]), float(val[2])))
            elif hasattr(val, 'x') and hasattr(val, 'y'):
                if hasattr(val, 'z'):
                    ptype = "vec3"
                    u_val = ctx.uniform(ptype, (float(val.x), float(val.y), float(val.z)))
                else:
                    ptype = "vec2"
                    u_val = ctx.uniform(ptype, (float(val.x), float(val.y)))
            else:
                ptype = "float"
                u_val = ctx.uniform(ptype, float(val))
            param_args_decl.append(f"{ptype} {name}")
            param_args_call.append(u_val)

        if surf_n_expr is not None:
            # A formula naming `surf_n` reads this directly.
            param_args_decl.append("vec3 surf_n")
            param_args_call.append(surf_n_expr)

        if front_expr is not None:
            # A formula naming `front` reads this directly.
            param_args_decl.append("float front")
            param_args_call.append(front_expr)

        # ToWorldLocalNode (transform.py) compiles to `apply_rot_mat(to_world_rot,
        # v) + to_world_base` / `apply_inv_mat(to_local_inv, v)` -- well-known
        # identifiers, not uniform names, so wire them to THIS field's own
        # placement matrices only if the formula actually names them. A fixed
        # global uniform name would let a second noise field with a different
        # placement silently reuse the first field's transform (GlProgram
        # dedups custom helpers by name); passing them as extra formula-helper
        # parameters keeps each field's own values.
        formula_text = self._clean_formula or ""
        if "to_world_rot" in formula_text:
            param_args_decl.append("mat3 to_world_rot")
            param_args_call.append(rot_u)
        if "to_world_base" in formula_text:
            b = self.placement.Base
            base_u = ctx.uniform("vec3", (b.x, b.y, b.z))
            param_args_decl.append("vec3 to_world_base")
            param_args_call.append(base_u)
        if "to_local_inv" in formula_text:
            param_args_decl.append("mat4 to_local_inv")
            param_args_call.append(inv_u)

        decl_str = ", ".join(["vec3 p", "float amp", "float freq"] + param_args_decl)
        call_str = ", ".join([point_var, amp_u, freq_u] + param_args_call)

        # Register the GLSL for every pattern node (CN-013/CN-014) the formula
        # text actually calls. Substring matching is deliberate: the field
        # holds compiled text, not the graph, and every kernel entry point is
        # prefixed `fld_`, which glsl_param_identifier (base.py) cannot
        # produce from a user-typed custom-param name -- a collision is not
        # reachable.
        from freecad.fields.core.gui.node_editor.nodes.patterns import PATTERN_NODES
        for cls in PATTERN_NODES:
            if cls.kernel.main_fn_name in (self._clean_formula or ""):
                for dep_name, dep_code in cls.glsl_helpers():
                    ctx.add_custom_helper(dep_name, dep_code)

        is_vec4 = self._returns_vec4
        fn = f"noise_{abs(hash((self._clean_formula, is_vec4))) & 0xFFFFFF:06x}"

        body_lines = [
            "    float x = p.x;",
            "    float y = p.y;",
            "    float z = p.z;",
        ]
        body_lines.append(f"    return {self._clean_formula};")

        ret_type = "vec4" if is_vec4 else "vec3"
        body_str = "\n".join(body_lines)
        ctx.add_custom_helper(fn,
            f"{ret_type} {fn}({decl_str}) {{\n"
            f"{body_str}\n"
            f"}}"
        )
        call_expr = f"{fn}({call_str})"

        if is_vec4:
            # The vec4 reaches a wrapper as one argument, so the noise -- and the
            # base samples its front/surf_n take -- runs once per sample. Inlining
            # `call.xyz` and `call.w` would run the whole formula twice (NV-039).
            base_at_displaced = self.base_field.to_glsl(ctx, "(nz_q - nz_r.xyz)")
            wrap = f"nzapply_{abs(hash(base_at_displaced)) & 0xFFFFFF:06x}"
            ctx.add_custom_helper(wrap, (
                f"float {wrap}(vec4 nz_r, vec3 nz_q) {{\n"
                f"    return ({base_at_displaced}) + nz_r.w;\n"
                f"}}"
            ))
            return f"{wrap}({call_expr}, {point_var})"
        else:
            displaced_pt = f"({point_var} - {call_expr})"
            base_at_displaced = self.base_field.to_glsl(ctx, displaced_pt)
            return f"({base_at_displaced})"

