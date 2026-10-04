# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT, placement_matrix
import math
import re
import numpy as np
from freecad.fields.core.sdf.sdf.noise_nodes import NOISE_NODES
from freecad.fields.core.sdf.sdf.formula_eval import (
    parse_custom_params, update_formula_param_comment, clean_formula_code,
    GlslVec2, GlslVec3,
    EVAL_NS, EVAL_NS_NP,
    MAX_SCALAR_FALLBACK_POINTS,
    _eval_formula_grid_np,
)


# ── SdfNoiseField ─────────────────────────────────────────────────────────────


class SdfNoiseField(SdfField):
    """
    Applies procedural noise to a base SDF field as a displacement, evaluated
    in the object's own Placement -- object-local Cartesian xyz, not a
    hand-rolled projection frame.

    Formula variables: x, y, z (object-local Cartesian), r (= sqrt(x*x+y*y)),
    front, amp, freq. A scalar formula displaces along local Z only; a
    vec3-valued graph may also slide the surface in x/y. `front` is 1 in front
    of the base field's surface along `direction` (the local +Z axis, in world
    space), fading to 0 behind it -- multiply it into a displacement to mask
    out the back face. There is no dedicated "ignore back face" checkbox:
    a formula/graph that wants the mask names `front` itself (a bool Custom
    Parameter wired through `mix(1.0, front, that_param)` is the normal way to
    make it optional), the same as any other named value.
    """

    PRESETS = {
        # A plain sine along local X -- no in-formula rotation. Orienting the
        # wave is the object's Placement (or an Orient node in the graph)
        # job now, not a bespoke `angle` param duplicating it.
        "Waves": (
            "// @param bool ignore_back_face 0\n"
            "sin(x * freq) * amp * mix(1.0, front, ignore_back_face)"
        ),
        # `sharp` band-limits what used to be sign(sin(...)). A sign() step is a
        # jump discontinuity, so no finite Lipschitz constant exists for it and
        # _lip() below could only ever lie — which pits the octree (it culls with
        # |d| <= half_diag * L) and lets the marcher overstep the wall.
        # tanh(k*sin(f*x)) is the same shape with a slope of exactly k*f.
        "Square": (
            "// @param bool ignore_back_face 0\n"
            "// @param float angle 0.0 0.0 360.0 5.0\n"
            "// @param float sharp 4.0 1.0 20.0 0.5\n"
            "// @param float amp2 0.0 0.0 1.0 0.05\n"
            "// @param float freq2 1.0 0.1 10.0 0.1\n"
            "// @param float angle2 90.0 0.0 360.0 5.0\n"
            "(tanh(sharp * sin((x * cos(radians(angle)) - y * sin(radians(angle))) * freq)) * amp"
            " + tanh(sharp * sin((x * cos(radians(angle2)) - y * sin(radians(angle2))) * freq2)) * amp2)"
            " * mix(1.0, front, ignore_back_face)"
        ),
        "Sawtooth": (
            "// @param bool ignore_back_face 0\n"
            "// @param float angle 0.0 0.0 360.0 5.0\n"
            "// @param float amp2 0.0 0.0 1.0 0.05\n"
            "// @param float freq2 1.0 0.1 10.0 0.1\n"
            "// @param float angle2 90.0 0.0 360.0 5.0\n"
            "((4.0 * abs(fract((x * cos(radians(angle)) - y * sin(radians(angle))) * freq - 0.25) - 0.5) - 1.0) * amp"
            " + (4.0 * abs(fract((x * cos(radians(angle2)) - y * sin(radians(angle2))) * freq2 - 0.25) - 0.5) - 1.0) * amp2)"
            " * mix(1.0, front, ignore_back_face)"
        ),
    }
    COMPLEX_PRESETS = NOISE_NODES
    PRESET_NAMES = list(PRESETS.keys()) + list(COMPLEX_PRESETS.keys()) + ["Custom"]
    DEFAULT_FORMULA = PRESETS["Waves"]

    def __init__(self, base_field: SdfField, amplitude: float = 1.0, frequency: float = 1.0,
                 placement: FreeCAD.Placement = None, formula: str = None,
                 normalization: float = 0.0, preset_name: str = None,
                 custom_params: dict = None, radial: bool = False, disp_axis=None):
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
        self.radial = radial
        # (x, y, z) unit tuple when the node graph's terminal Displace node has
        # a literal (unwired) Direction -- a rank-1 displacement, so _lip()
        # below can skip the sqrt(3) three-component bound. None (the common
        # case for a hand-typed formula, or a wired/varying Direction) falls
        # back to the honest three-component bound via `vector_output`.
        self._disp_axis = disp_axis
        self._axis_extent_cache = None

        self._preset_name = preset_name or "Waves"
        if self._preset_name in self.COMPLEX_PRESETS:
            self.formula = None
            self._clean_formula = None
        else:
            self.formula = formula if formula is not None else self.PRESETS.get(self._preset_name, self.DEFAULT_FORMULA)
            if self.formula:
                for p in parse_custom_params(self.formula):
                    if p['name'] not in self.custom_params and p['default'] is not None:
                        self.custom_params[p['name']] = p['default']
            self._clean_formula = clean_formula_code(self.formula)
        # A formula/graph reaches the front-face mask by referencing `front`
        # directly in its text, same as `radial` rides EVAL_NS without being a
        # custom param. Detected once here (text doesn't change after
        # construction) rather than re-scanned per sample.
        self._formula_uses_front = bool(
            self._clean_formula and re.search(r'\bfront\b', self._clean_formula)
        )
        self.vector_output = self._detect_vector_output()

    def _detect_vector_output(self):
        """Does this formula produce a vec3 displacement, or a scalar height?

        GLSL is statically typed, so the answer is a property of the formula text
        and never changes with the sample point -- one probe settles it. Deriving
        it is what makes NU-026 (a flag that was always true) and NU-030 (a flag
        that disagreed with the body, which killed the whole scene shader)
        unrepresentable rather than merely fixed.
        """
        if self._preset_name in self.COMPLEX_PRESETS:
            return False        # the node kernels are all scalar-valued
        if not self._clean_formula:
            return False
        try:
            return isinstance(self._eval_formula(0.37, -0.21, 0.11), GlslVec3)
        except Exception:
            return False        # a formula that cannot be evaluated cannot be vec3

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
        ROTATION ONLY -- matches apply_rot_mat's GLSL meaning (`vec4(v, 0.0)`
        drops the translation). The formula text adds `to_world_base` itself
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
        wires the same two names to the real mat4 uniforms there)."""
        b = self.placement.Base
        return {
            "to_world_rot": self._to_world_vec,
            "to_world_base": GlslVec3(b.x, b.y, b.z),
            "to_local_inv": self._to_local_vec,
        }

    def _lip(self):
        if self._normalization > 0.0:
            return self._normalization
        if self._preset_name in self.COMPLEX_PRESETS:
            factor = self.COMPLEX_PRESETS[self._preset_name].lip_factor
            slope = abs(self.amplitude) * self.frequency * factor
            if self._disp_axis is None and self.vector_output:
                slope *= 1.7320508   # sqrt(3): three components, each with this slope
            return max(1.0 + slope + self._front_weight_slope(), 1.0)
        factor = 1.414
        amp2 = abs(self.custom_params.get("amp2", 0.0))
        freq2 = abs(self.custom_params.get("freq2", 0.0))
        slope = (abs(self.amplitude) * self.frequency + amp2 * freq2) * factor
        # `Square` is a tanh-band-limited square wave: d/dx tanh(k*sin(f*x))
        # peaks at k*f (sech^2 == 1 at the zero crossing), so its slope is k
        # times a plain sinusoid's. Band-limiting is what makes this number
        # meaningful at all — a raw sign() step has no finite bound.
        if self._preset_name == "Square":
            slope *= max(abs(self.custom_params.get("sharp", 4.0)), 1.0)
        if self._disp_axis is None and self.vector_output:
            slope *= 1.7320508   # sqrt(3): three components, each with this slope
        return max(1.0 + slope + self._front_weight_slope(), 1.0)

    def lipschitz(self) -> float:
        """Upper bound on |grad| of the value this field returns.

        The value itself is NOT divided by `_lip()`. Dividing it keeps the ray
        march's step safe and wrecks everything the march compares against a
        constant in millimetres: it declares a hit at `d < 0.5*vmax`, so on a
        field scaled down by L that fires `0.5*vmax*L` mm from the surface — 28 mm
        at amp 100 on a 3.3 mm voxel, in every direction at once, which is the
        "2D noise deforms in x and y as well as z" report. The bound belongs here,
        where the octree, the bake's block scan and the march's STEP divisor read
        it. Same resolution UX-009 took for `SdfCageDeformField`.
        """
        return self.base_field.lipschitz() * self._lip()

    def _wave_peak(self):
        """Upper bound on |noise| — how far the wave can push the surface either way.

        Bias shifts the whole wave by exactly this, so it has to be an
        OVER-estimate: too small and "Top" would still let the stock grow. The
        presets stay bounded by amp + amp2 (`Waves` also multiplies by
        exp(-r*decay) <= 1, `Square` by tanh() < 1, `Sawtooth` by a triangle in
        [-1, 1]); a Custom formula can of course exceed it, which costs accuracy
        of the anchor, not validity of the field.
        """
        if self._preset_name in self.COMPLEX_PRESETS:
            return abs(self.amplitude) * self.COMPLEX_PRESETS[self._preset_name].peak_factor
        return abs(self.amplitude) + abs(self.custom_params.get("amp2", 0.0))

    def _eval_formula(self, x, y, z=0.0, front=None):
        if self._preset_name in self.COMPLEX_PRESETS:
            return self.COMPLEX_PRESETS[self._preset_name].python_fn(
                x, y, z, self.amplitude, self.frequency)
        ns = dict(EVAL_NS)
        ns["x"] = x; ns["y"] = y; ns["z"] = z
        ns["p"] = GlslVec3(x, y, z)
        ns["r"] = math.sqrt(x * x + y * y)
        is_radial = bool(self.radial or self.custom_params.get("radial", False))
        ns["d"] = ns["r"] if is_radial else x
        ns["radial"] = 1.0 if is_radial else 0.0
        ns["front"] = front if front is not None else 1.0
        ns["amp"] = self.amplitude; ns["freq"] = self.frequency
        ns.update(self._placement_ns_extra())
        for k, v in self.custom_params.items():
            if k == "radial" or k == "front":
                continue
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
            res = eval(self._clean_formula, {}, ns)
            if isinstance(res, (GlslVec3, GlslVec2)):
                return res
            return float(res)
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug_throttled(
                "noise_formula_eval_fail",
                f"SdfNoiseField: formula eval failed ({e}); contributing 0.0",
            )
            return 0.0

    def _eval_noise_grid(self, x_vals, y_vals, z_vals=None, front_vals=None):
        """Vectorized counterpart of _eval_formula for a whole grid at once."""
        if z_vals is None:
            z_vals = np.zeros_like(x_vals)
        if self._preset_name in self.COMPLEX_PRESETS:
            node = self.COMPLEX_PRESETS[self._preset_name]
            return node.numpy_fn(x_vals, y_vals, z_vals,
                                 self.amplitude, self.frequency).astype(np.float32)
        try:
            is_radial = bool(self.radial or self.custom_params.get("radial", False))
            r_vals = np.sqrt(x_vals**2 + y_vals**2)
            d_vals = r_vals if is_radial else x_vals
            radial_val = np.float32(1.0 if is_radial else 0.0)
            front_ns = front_vals if front_vals is not None else np.float32(1.0)
            return _eval_formula_grid_np(
                self._clean_formula, self.custom_params,
                {"x": x_vals, "y": y_vals, "z": z_vals,
                 "p": GlslVec3(x_vals, y_vals, z_vals),
                 "r": r_vals, "d": d_vals, "radial": radial_val, "front": front_ns,
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
            fld_logger.debug_throttled(
                "noise_vectorize_fallback",
                f"SdfNoiseField: formula not vectorizable ({e}), falling back to per-point eval",
            )
            return np.fromiter(
                (self._eval_formula(float(x_vals[i]), float(y_vals[i]), float(z_vals[i]),
                                     front=(float(front_vals[i]) if front_vals is not None else None))
                 for i in range(len(x_vals))),
                dtype=np.float32, count=len(x_vals),
            )

    def _eval_displacement(self, x, y, z, front=None):
        """(du, dw, dn) in frame coords. Scalar noise fills only `dn`."""
        n = self._eval_formula(x, y, z, front=front)
        if isinstance(n, GlslVec3):
            return (n.x, n.y, n.z)
        if isinstance(n, GlslVec2):
            return (n.x, n.y, 0.0)
        return (0.0, 0.0, float(n))

    def _eval_displacement_grid(self, x_vals, y_vals, z_vals=None, front_vals=None):
        """(du, dw, dn) float32 arrays in frame coords."""
        if z_vals is None:
            z_vals = np.zeros_like(x_vals)
        if self._preset_name in self.COMPLEX_PRESETS:
            zeros = np.zeros_like(x_vals, dtype=np.float32)
            noise = self._eval_noise_grid(x_vals, y_vals, z_vals)
            return zeros, zeros, noise

        try:
            is_radial = bool(self.radial or self.custom_params.get("radial", False))
            r_vals = np.sqrt(x_vals**2 + y_vals**2)
            d_vals = r_vals if is_radial else x_vals
            radial_val = np.float32(1.0 if is_radial else 0.0)
            front_ns = front_vals if front_vals is not None else np.float32(1.0)
            res = _eval_formula_grid_np(
                self._clean_formula, self.custom_params,
                {"x": x_vals, "y": y_vals, "z": z_vals,
                 "p": GlslVec3(x_vals, y_vals, z_vals),
                 "r": r_vals, "d": d_vals, "radial": radial_val, "front": front_ns,
                 "amp": self.amplitude, "freq": self.frequency,
                 **self._placement_ns_extra()},
                x_vals.shape,
            )
            if isinstance(res, GlslVec3):
                return res.x, res.y, res.z
            if isinstance(res, GlslVec2):
                zeros = np.zeros_like(x_vals, dtype=np.float32)
                return res.x, res.y, zeros
            zeros = np.zeros_like(x_vals, dtype=np.float32)
            return zeros, zeros, res.astype(np.float32)
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
                return zeros, zeros, zeros
            fld_logger.debug_throttled(
                "noise_vectorize_fallback",
                f"SdfNoiseField: formula not vectorizable ({e}), falling back to per-point eval"
            )
            dus = np.empty(n, dtype=np.float32)
            dws = np.empty(n, dtype=np.float32)
            dns = np.empty(n, dtype=np.float32)
            for i in range(n):
                front_i = float(front_vals[i]) if front_vals is not None else None
                du, dw, dn = self._eval_displacement(
                    float(x_vals[i]), float(y_vals[i]), float(z_vals[i]), front=front_i)
                dus[i] = du
                dws[i] = dw
                dns[i] = dn
            return dus, dws, dns

    def _reach(self):
        """The furthest the surface can travel, in mm."""
        return self._wave_peak()

    def _axis_extent(self):
        """How thick the stock is along `direction`, from its own bounding box.

        Cached: the base field is fixed at construction, but bounding_box()
        recurses the whole tree and _front_face_band runs once per sample.
        """
        if self._axis_extent_cache is None:
            bb_min, bb_max = self.base_field.bounding_box()
            size = bb_max - bb_min
            d = self.direction
            self._axis_extent_cache = (abs(size.x * d.x) + abs(size.y * d.y)
                                       + abs(size.z * d.z))
        return self._axis_extent_cache

    def _front_face_band(self):
        """(lift, ramp) for the front-face weight, `clamp(base(p - dir*lift) / ramp)`.

        `lift` is how far a sample is carried back along the direction to ask "is
        there still material above me?", and it is squeezed from both sides.

        Too long and it clears the whole part: the back face reads as
        front-facing and travels with the wave -- 5.6 mm of growth on a 20 mm
        plate, the exact thing "Top never grows the stock" rules out. The stock's
        own extent is what caps it, halved, since a bounding box over-states the
        local thickness of anything that is not a slab.

        Too short and it clears the part the other way. A point sitting `s` below
        the back face lifts to `s - lift`, and once that is out the far side the
        mask switches back ON down there; the wave then drags a phantom skin off
        the bottom of the solid (measured: a second surface 20 mm adrift). Only
        points within `_reach()` of the solid can be pulled into it at all, so a
        lift of `_reach()` covers every one that matters -- and that floor wins,
        because a back face creeping is a smaller lie than a detached one.

        `ramp` is the width of the 0..1 fade. On a planar front face the weight
        works out to `(lift - depth)/ramp`, so it holds at a flat 1 down to
        `lift - ramp` and only then fades: that plateau has to cover the whole
        displacement or the wave fights its own mask, losing weight with every mm
        it sinks and settling short (at ramp = lift there is no plateau at all
        and a full trough lands 20% shallow). Half the lift is the plateau, which
        covers the displacement whenever the stock is thick enough to hold it.
        """
        reach = max(self._reach(), 1e-6)
        lift = min(max(self._axis_extent() * 0.5, reach), reach * 2.0)
        ramp = max(lift * 0.5, reach)
        lift = max(lift, ramp)
        return lift, ramp

    def _uses_front_weight(self):
        return self._formula_uses_front

    def _front_weight_slope(self):
        """The mask's own contribution to the Lipschitz bound.

        The weight is a clamped distance ramp, so it tilts by up to 1/ramp, and
        it multiplies a displacement of up to `_reach()` mm. Ignoring that term
        under-reports the bound by ~1 for a full mask, which is the number the
        octree culls with and the marcher steps by.
        """
        if not self._uses_front_weight():
            return 0.0
        return self._reach() / self._front_face_band()[1]

    def _front_weight_at_point(self, point):
        """Scalar twin of the weight `to_glsl` computes for `weight_expr`: how far
        `point` sits in front of the base field's surface, clamped to [0, 1]."""
        lift, ramp = self._front_face_band()
        behind = FreeCAD.Vector(
            point.x - self.direction.x * lift,
            point.y - self.direction.y * lift,
            point.z - self.direction.z * lift,
        )
        d_behind = self.base_field.evaluate(behind)
        return max(0.0, min(1.0, d_behind / ramp))

    def _front_weight_grid(self, points, da):
        """Grid twin of `_front_weight_at_point`. `da` is `direction` as a
        float32 array, already available to every caller in `evaluate_grid`."""
        lift, ramp = self._front_face_band()
        behind_points = points - da * lift
        d_behind = self.base_field.evaluate_grid(behind_points)
        return np.clip(d_behind / ramp, 0.0, 1.0).astype(np.float32)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        q = self._to_local_point(point)
        weight = self._front_weight_at_point(point) if self._uses_front_weight() else None
        dx, dy, dz = self._eval_displacement(q.x, q.y, q.z, front=weight)
        disp = self.placement.Rotation.multVec(FreeCAD.Vector(dx, dy, dz))
        return self.base_field.evaluate(point - disp)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        local = self._to_local_grid(points)
        weight = None
        if self._uses_front_weight():
            d = self.direction
            da = np.array([d.x, d.y, d.z], dtype=np.float32)
            weight = self._front_weight_grid(points, da)
        dx, dy, dz = self._eval_displacement_grid(
            local[:, 0], local[:, 1], local[:, 2], front_vals=weight)
        disp_local = np.stack((dx, dy, dz), axis=1).astype(np.float32)
        displaced = points - (disp_local @ self._rot_np.T)
        return self.base_field.evaluate_grid(displaced).astype(np.float32)

    def bounding_box(self):
        bb_min, bb_max = self.base_field.bounding_box()
        # A biased wave reaches two peaks to one side instead of one peak to
        # each, so the box has to grow with the bias or "Bottom" would push
        # geometry straight through the wall of its own bounds.
        reach = self._reach()
        offset = FreeCAD.Vector(reach, reach, reach)
        return (bb_min - offset, bb_max + offset)

    def to_patch_cage(self):
        # Same rationale as SdfNoiseField.to_patch_cage: 2D noise perturbs the
        # SDF value along the projection direction, not the coordinate space,
        # so the base field's cage net is still valid to forward as-is.
        return self.base_field.to_patch_cage()

    def to_glsl(self, ctx, point_var="p"):
        amp_u = ctx.uniform("float", self.amplitude)
        freq_u = ctx.uniform("float", self.frequency)
        ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
        inv_u = ctx.uniform("mat4", self.inv_matrix.tolist())
        # mat4, not mat3: GLProgram.set_uniform (gl_program.py) has no mat3
        # branch yet and would log "no setter for GLSL type" and bind nothing
        # -- a silently black field. CN-024 adds mat3 as an optimisation later.
        rot_mat = placement_matrix(
            FreeCAD.Placement(FreeCAD.Vector(), self.placement.Rotation), dtype=np.float32)
        rot_u = ctx.uniform("mat4", rot_mat.tolist())
        ctx.add_custom_helper("apply_rot_mat", (
            "vec3 apply_rot_mat(mat4 m, vec3 v) {\n"
            "    return (m * vec4(v, 0.0)).xyz;\n"
            "}"
        ))
        q_expr = f"apply_inv_mat({inv_u}, {point_var})"
        ctx.add_custom_helper("fld_pi_const", "const float pi = 3.14159265358979323846;")

        weight_expr = None
        if self._uses_front_weight():
            lift, ramp = self._front_face_band()
            lift_u = ctx.uniform("float", lift)
            ramp_u = ctx.uniform("float", ramp)
            d = self.direction
            front_dir_u = ctx.uniform("vec3", (d.x, d.y, d.z))
            offset_pt = f"({point_var} - {front_dir_u} * {lift_u})"
            behind_glsl = self.base_field.to_glsl(ctx, offset_pt)
            weight_expr = f"clamp({behind_glsl} / {ramp_u}, 0.0, 1.0)"

        if self._preset_name in self.COMPLEX_PRESETS:
            preset = self.COMPLEX_PRESETS[self._preset_name]
            for dep_name, dep_code in preset.dep_helpers:
                ctx.add_custom_helper(dep_name, dep_code)
            ctx.add_custom_helper(preset.main_fn_name, preset.main_fn_code)
            d = self.direction
            dir_u = ctx.uniform("vec3", (d.x, d.y, d.z))
            h_scaled = f"({preset.main_fn_name}({q_expr}, {amp_u}, {freq_u}))"
            displaced_pt = f"({point_var} - {h_scaled} * {dir_u})"
            base_at_displaced = self.base_field.to_glsl(ctx, displaced_pt)
            return f"({base_at_displaced})"

        # Register custom parameters as uniforms
        param_args_decl = []
        param_args_call = []
        is_radial = bool(self.radial or self.custom_params.get("radial", False))
        # `front` is never a real custom param -- it rides its own dedicated
        # parameter (added below) fed by `weight_expr`, not the generic uniform
        # loop, so a stray `@param ... front ...` can't double-declare it.
        builtin_names = {"front"}
        for name, val in self.custom_params.items():
            if name in builtin_names:
                continue
            if name == "radial":
                ptype = "float"
                u_val = ctx.uniform(ptype, 1.0 if is_radial else 0.0)
            elif isinstance(val, bool):
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

        if weight_expr is not None:
            # A formula naming `front` reads this directly, exactly like any
            # other parameter.
            param_args_decl.append("float front")
            param_args_call.append(weight_expr)

        # ToWorldLocalNode (transform.py) compiles to `apply_rot_mat(to_world_rot,
        # v) + to_world_base` / `apply_inv_mat(to_local_inv, v)` -- well-known
        # identifiers, not uniform names, so wire them to THIS field's own
        # placement matrices only if the formula actually names them. A fixed
        # global uniform name would let a second noise field with a different
        # placement silently reuse the first field's transform (GlProgram
        # dedups custom helpers by name); passing them as extra formula-helper
        # parameters, like `front` above, keeps each field's own values.
        formula_text = self._clean_formula or ""
        if "to_world_rot" in formula_text:
            param_args_decl.append("mat4 to_world_rot")
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
        call_str = ", ".join([q_expr, amp_u, freq_u] + param_args_call)

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

        fn = f"noise_{abs(hash((self._clean_formula, is_radial, self.vector_output))) & 0xFFFFFF:06x}"
        d_line = "r" if is_radial else "x"
        radial_line = "1.0" if is_radial else "0.0"

        body_lines = [
            "    float x = p.x;",
            "    float y = p.y;",
            "    float z = p.z;",
        ]
        if "r" not in self.custom_params:
            body_lines.append("    float r = sqrt(x * x + y * y);")
        if "d" not in self.custom_params:
            body_lines.append(f"    float d = {d_line};")
        if "radial" not in self.custom_params:
            body_lines.append(f"    float radial = {radial_line};")
        body_lines.append(f"    return {self._clean_formula};")

        body_str = "\n".join(body_lines)
        return_type = "vec3" if self.vector_output else "float"
        ctx.add_custom_helper(fn,
            f"{return_type} {fn}({decl_str}) {{\n"
            f"{body_str}\n"
            f"}}"
        )
        if self.vector_output:
            # `front`, when the formula names it, already reached `fn` above as
            # one of its own parameters (`call_str`), so the vec3 it returns is
            # already weighted -- this wrapper only rotates the local-frame
            # result (x, y, z components along the object's own axes) into world
            # space, replacing the old u_ax/w_ax/dir basis reconstruction.
            disp = f"apply_rot_mat({rot_u}, {fn}({call_str}))"
        else:
            d = self.direction
            dir_u = ctx.uniform("vec3", (d.x, d.y, d.z))
            h_scaled = f"({fn}({call_str}))"
            disp = f"({h_scaled} * {dir_u})"

        displaced_pt = f"({point_var} - {disp})"
        base_at_displaced = self.base_field.to_glsl(ctx, displaced_pt)
        return f"({base_at_displaced})"
