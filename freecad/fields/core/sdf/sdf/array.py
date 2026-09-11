# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import math
import FreeCAD
import numpy as np
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf_constants import SURFACE_ID_UNSET
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf.noise import _EVAL_NS
from freecad.fields.core.input.fld_gizmo import _perp_pair

AXES = {"X": 0, "Y": 1, "Z": 2}


class SdfArrayField(SdfField):
    """Array modifier supporting grid and step-transform repetition over a source SDF field.

    Strategies:
    - fold_grid: axis-aligned 3D lattice fold (O(1) evaluations).
    - fold_step_linear: pure translation along an arbitrary direction vector (O(1)).
    - fold_radial: rotation about an axis through step_center within 1 turn (O(1)).
    - instances: per-copy transform evaluation via SSBO and bounded loop (O(n)).
    """

    def __init__(
        self,
        source: SdfField,
        mode: str = "grid",
        counts=(2, 1, 1),
        spacing=(20.0, 20.0, 20.0),
        step_count: int = 6,
        step_offset=(0.0, 0.0, 0.0),
        step_axis: str = "Z",
        step_angle: float = 60.0,
        step_center=(0.0, 0.0, 0.0),
        step_scale: float = 1.0,
        angle_formula: str = "",
        radius_formula: str = "",
        rise_formula: str = "",
        scale_formula: str = "",
        skipped=(),
        overlap_mode: str = "auto",
        overlap_safe=None,
        radial_axis=None,
        radial_count=None,
        radial_span=None,
    ):
        super().__init__()
        self.source = source
        raw_mode = str(mode).lower() if mode is not None else "grid"
        if raw_mode in ("grid", "linear"):
            self.mode = "grid"
        elif raw_mode in ("step", "radial"):
            self.mode = "step"
        else:
            self.mode = "grid"

        self.counts = (
            max(1, int(counts[0])),
            max(1, int(counts[1])),
            max(1, int(counts[2])),
        )
        self.spacing = (
            float(spacing[0]),
            float(spacing[1]),
            float(spacing[2]),
        )

        # Backward compatibility for legacy radial parameters
        if radial_count is not None:
            step_count = radial_count
        if radial_axis is not None:
            step_axis = radial_axis
        if radial_span is not None:
            cnt = int(step_count) if step_count else 6
            step_angle = float(radial_span) / max(cnt, 1)

        self.step_count = max(1, int(step_count))
        self.step_offset = (
            float(step_offset[0]),
            float(step_offset[1]),
            float(step_offset[2]),
        )
        self.step_axis = step_axis.upper() if isinstance(step_axis, str) else "Z"
        self.step_angle = float(step_angle)
        self.step_center = (
            float(step_center[0]),
            float(step_center[1]),
            float(step_center[2]),
        )
        self.step_scale = float(step_scale)

        self.angle_formula = str(angle_formula or "")
        self.radius_formula = str(radius_formula or "")
        self.rise_formula = str(rise_formula or "")
        self.scale_formula = str(scale_formula or "")

        self.skipped = set(skipped) if skipped else set()

        if overlap_safe is not None:
            self.overlap_mode = "always" if overlap_safe else "never"
        else:
            self.overlap_mode = str(overlap_mode).lower() if overlap_mode else "auto"

        self._warned_cap = False
        self._warned_formula = False

    # ── Backward compatibility properties ─────────────────────────────

    @property
    def overlap_safe(self) -> bool:
        return self.use_multi_cell()

    @overlap_safe.setter
    def overlap_safe(self, val: bool):
        self.overlap_mode = "always" if val else "never"

    @property
    def radial_axis(self) -> str:
        return self.step_axis

    @property
    def radial_count(self) -> int:
        return self.step_count

    @property
    def radial_span(self) -> float:
        return self.step_angle * self.step_count

    # ── Strategy switch ───────────────────────────────────────────────

    def strategy(self) -> str:
        """Return one of 'fold_grid', 'fold_step_linear', 'fold_radial', 'instances'."""
        # Rule 1: mode == "grid"
        if self.mode == "grid":
            return "fold_grid"

        # Rule 2: any formula non-empty, skipped non-empty, or abs(step_scale - 1.0) > 1e-9
        if (
            self.angle_formula.strip()
            or self.radius_formula.strip()
            or self.rise_formula.strip()
            or self.scale_formula.strip()
            or len(self.skipped) > 0
            or abs(self.step_scale - 1.0) > 1e-9
        ):
            return "instances"

        # Rule 3: abs(step_angle) < 1e-9 -> pure translation
        if abs(self.step_angle) < 1e-9:
            return "fold_step_linear"

        # Rule 4: step_offset length < 1e-9 and abs(step_angle) * step_count <= 360 + 1e-6
        off_len_sq = (
            self.step_offset[0] ** 2
            + self.step_offset[1] ** 2
            + self.step_offset[2] ** 2
        )
        if off_len_sq < 1e-18 and (abs(self.step_angle) * self.step_count <= 360.0 + 1e-6):
            return "fold_radial"

        # Rule 5: otherwise fall back to instances
        return "instances"

    # ── Overlap handling ──────────────────────────────────────────────

    def use_multi_cell(self) -> bool:
        if self.overlap_mode == "always":
            return True
        if self.overlap_mode == "never":
            return False
        return self.needs_multi_cell()

    def needs_multi_cell(self) -> bool:
        """Returns True if spacing along any arrayed axis/direction is smaller than source extent."""
        strat = self.strategy()
        if strat not in ("fold_grid", "fold_step_linear"):
            return False
        try:
            bb_min, bb_max = self.source.bounding_box()
        except Exception as e:
            fld_logger.warn(
                f"SdfArrayField.needs_multi_cell: {type(self.source).__name__}."
                f"bounding_box() failed ({e}); assuming multi-cell IS needed."
            )
            return True
        extent = (
            bb_max.x - bb_min.x,
            bb_max.y - bb_min.y,
            bb_max.z - bb_min.z,
        )
        if strat == "fold_grid":
            for k in range(3):
                if self.counts[k] > 1 and abs(self.spacing[k]) < extent[k]:
                    return True
            return False
        elif strat == "fold_step_linear":
            o = self.step_offset
            o_len = math.sqrt(o[0] * o[0] + o[1] * o[1] + o[2] * o[2])
            if self.step_count > 1 and o_len > 1e-9:
                d = (abs(o[0]) / o_len, abs(o[1]) / o_len, abs(o[2]) / o_len)
                proj_extent = extent[0] * d[0] + extent[1] * d[1] + extent[2] * d[2]
                if o_len < proj_extent:
                    return True
            return False
        return False

    needs_overlap_safe = needs_multi_cell

    # ── Bounds and Lipschitz ──────────────────────────────────────────

    def lipschitz(self) -> float:
        return self.source.lipschitz()

    def bounding_box(self):
        bb_min, bb_max = self.source.bounding_box()
        strat = self.strategy()
        if strat == "fold_grid":
            min_x = bb_min.x + min(0.0, (self.counts[0] - 1) * self.spacing[0])
            max_x = bb_max.x + max(0.0, (self.counts[0] - 1) * self.spacing[0])
            min_y = bb_min.y + min(0.0, (self.counts[1] - 1) * self.spacing[1])
            max_y = bb_max.y + max(0.0, (self.counts[1] - 1) * self.spacing[1])
            min_z = bb_min.z + min(0.0, (self.counts[2] - 1) * self.spacing[2])
            max_z = bb_max.z + max(0.0, (self.counts[2] - 1) * self.spacing[2])
            return FreeCAD.Vector(min_x, min_y, min_z), FreeCAD.Vector(max_x, max_y, max_z)
        elif strat == "fold_step_linear":
            min_x = bb_min.x + min(0.0, (self.step_count - 1) * self.step_offset[0])
            max_x = bb_max.x + max(0.0, (self.step_count - 1) * self.step_offset[0])
            min_y = bb_min.y + min(0.0, (self.step_count - 1) * self.step_offset[1])
            max_y = bb_max.y + max(0.0, (self.step_count - 1) * self.step_offset[1])
            min_z = bb_min.z + min(0.0, (self.step_count - 1) * self.step_offset[2])
            max_z = bb_max.z + max(0.0, (self.step_count - 1) * self.step_offset[2])
            return FreeCAD.Vector(min_x, min_y, min_z), FreeCAD.Vector(max_x, max_y, max_z)
        elif strat == "fold_radial":
            c = self.step_center
            corners = [
                [bb_min.x, bb_min.y, bb_min.z],
                [bb_min.x, bb_min.y, bb_max.z],
                [bb_min.x, bb_max.y, bb_min.z],
                [bb_min.x, bb_max.y, bb_max.z],
                [bb_max.x, bb_min.y, bb_min.z],
                [bb_max.x, bb_min.y, bb_max.z],
                [bb_max.x, bb_max.y, bb_min.z],
                [bb_max.x, bb_max.y, bb_max.z],
            ]
            r_max = 0.0
            for x, y, z in corners:
                rel_x = x - c[0]
                rel_y = y - c[1]
                rel_z = z - c[2]
                if self.step_axis == "X":
                    r = math.sqrt(rel_y * rel_y + rel_z * rel_z)
                elif self.step_axis == "Y":
                    r = math.sqrt(rel_x * rel_x + rel_z * rel_z)
                else:  # "Z"
                    r = math.sqrt(rel_x * rel_x + rel_y * rel_y)
                if r > r_max:
                    r_max = r

            if self.step_axis == "X":
                return (
                    FreeCAD.Vector(bb_min.x, c[1] - r_max, c[2] - r_max),
                    FreeCAD.Vector(bb_max.x, c[1] + r_max, c[2] + r_max),
                )
            elif self.step_axis == "Y":
                return (
                    FreeCAD.Vector(c[0] - r_max, bb_min.y, c[2] - r_max),
                    FreeCAD.Vector(c[0] + r_max, bb_max.y, c[2] + r_max),
                )
            else:  # "Z"
                return (
                    FreeCAD.Vector(c[0] - r_max, c[1] - r_max, bb_min.z),
                    FreeCAD.Vector(c[0] + r_max, c[1] + r_max, bb_max.z),
                )
        else:
            # "instances": transform the eight source-box corners by each T_i and take the union
            corners = [
                FreeCAD.Vector(x, y, z)
                for x in (bb_min.x, bb_max.x)
                for y in (bb_min.y, bb_max.y)
                for z in (bb_min.z, bb_max.z)
            ]
            mats, inv_mats, spheres, miscs = self._get_instance_transforms()
            pts = []
            for M, sphere, misc in zip(mats, spheres, miscs):
                if misc[1] < 0.5:
                    continue
                for corn in corners:
                    pts.append(M.multVec(corn))
            if not pts:
                return FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 0)
            min_x = min(p.x for p in pts)
            min_y = min(p.y for p in pts)
            min_z = min(p.z for p in pts)
            max_x = max(p.x for p in pts)
            max_y = max(p.y for p in pts)
            max_z = max(p.z for p in pts)
            return FreeCAD.Vector(min_x, min_y, min_z), FreeCAD.Vector(max_x, max_y, max_z)

    # ── Instance Transforms & SSBO Packing ────────────────────────────

    def _get_instance_transforms(self):
        """Compute (mats, inv_mats, spheres, miscs) for each instance."""
        n = self.step_count
        if n > 256:
            if not getattr(self, "_warned_cap", False):
                fld_logger.warn(
                    f"SdfArrayField: step_count={n} exceeds 256-instance cap on instance path; clamping to 256."
                )
                self._warned_cap = True
            n = 256

        if isinstance(self.step_axis, str):
            a_v = {"X": FreeCAD.Vector(1, 0, 0), "Y": FreeCAD.Vector(0, 1, 0), "Z": FreeCAD.Vector(0, 0, 1)}.get(
                self.step_axis.upper(), FreeCAD.Vector(0, 0, 1)
            )
        else:
            a_v = FreeCAD.Vector(self.step_axis)
        a_hat = a_v.normalize() if a_v.Length > 1e-9 else FreeCAD.Vector(0, 0, 1)

        c = FreeCAD.Vector(*self.step_center)

        try:
            bb_min, bb_max = self.source.bounding_box()
            p_src = (bb_min + bb_max) * 0.5
            r_src = (bb_max - bb_min).Length * 0.5
        except Exception:
            p_src = FreeCAD.Vector(0, 0, 0)
            r_src = 1.0

        v = (p_src - c) - ((p_src - c).dot(a_hat)) * a_hat
        if v.Length > 1e-6:
            u_hat = v.normalize()
        else:
            ref, _ = _perp_pair(a_hat)
            u_hat = ref.normalize()

        off = FreeCAD.Vector(*self.step_offset)
        rise_comp = off.dot(a_hat)
        planar_part = off - rise_comp * a_hat
        planar_len = planar_part.Length

        mats = []
        inv_mats = []
        spheres = []
        miscs = []

        for i in range(n):
            t_param = float(i) / max(n - 1, 1)
            scope = dict(_EVAL_NS)
            scope.update({"i": i, "n": n, "t": t_param})

            # Angle
            ang_def = i * self.step_angle
            if self.angle_formula.strip():
                try:
                    ang_i = float(eval(self.angle_formula, scope))
                except Exception as e:
                    if not getattr(self, "_warned_formula", False):
                        fld_logger.warn(f"SdfArrayField: angle_formula eval failed ({e}), using default")
                        self._warned_formula = True
                    ang_i = ang_def
            else:
                ang_i = ang_def

            # Radius (planar offset along u_hat)
            rad_def = i * planar_len
            if self.radius_formula.strip():
                try:
                    rad_i = float(eval(self.radius_formula, scope))
                except Exception as e:
                    if not getattr(self, "_warned_formula", False):
                        fld_logger.warn(f"SdfArrayField: radius_formula eval failed ({e}), using default")
                        self._warned_formula = True
                    rad_i = rad_def
            else:
                rad_i = rad_def

            # Rise (offset along a_hat)
            rise_def = i * rise_comp
            if self.rise_formula.strip():
                try:
                    rise_i = float(eval(self.rise_formula, scope))
                except Exception as e:
                    if not getattr(self, "_warned_formula", False):
                        fld_logger.warn(f"SdfArrayField: rise_formula eval failed ({e}), using default")
                        self._warned_formula = True
                    rise_i = rise_def
            else:
                rise_i = rise_def

            # Scale
            scale_def = float(self.step_scale ** i)
            if self.scale_formula.strip():
                try:
                    scale_i = float(eval(self.scale_formula, scope))
                except Exception as e:
                    if not getattr(self, "_warned_formula", False):
                        fld_logger.warn(f"SdfArrayField: scale_formula eval failed ({e}), using default")
                        self._warned_formula = True
                    scale_i = scale_def
            else:
                scale_i = scale_def

            rot = FreeCAD.Rotation(a_hat, ang_i)
            rot_mat = rot.toMatrix()

            disp = u_hat * rad_i + a_hat * rise_i - c * scale_i
            trans = c + rot_mat.multVec(disp)

            M = FreeCAD.Matrix(
                rot_mat.A11 * scale_i, rot_mat.A12 * scale_i, rot_mat.A13 * scale_i, trans.x,
                rot_mat.A21 * scale_i, rot_mat.A22 * scale_i, rot_mat.A23 * scale_i, trans.y,
                rot_mat.A31 * scale_i, rot_mat.A32 * scale_i, rot_mat.A33 * scale_i, trans.z,
                0.0, 0.0, 0.0, 1.0,
            )
            invM = M.inverse()

            # Bounding sphere
            c_i = M.multVec(p_src)
            R_i = abs(scale_i) * r_src
            sphere_i = (c_i.x, c_i.y, c_i.z, R_i)

            enabled = 0.0 if i in self.skipped else 1.0
            misc_i = (scale_i, enabled, 0.0, 0.0)

            mats.append(M)
            inv_mats.append(invM)
            spheres.append(sphere_i)
            miscs.append(misc_i)

        return mats, inv_mats, spheres, miscs

    def pack_instances(self) -> np.ndarray:
        mats, inv_mats, spheres, miscs = self._get_instance_transforms()
        n = len(mats)
        buf = np.zeros((n, 20), dtype=np.float32)
        for i in range(n):
            invM = inv_mats[i]
            sph = spheres[i]
            misc = miscs[i]
            # inv0
            buf[i, 0] = invM.A11
            buf[i, 1] = invM.A12
            buf[i, 2] = invM.A13
            buf[i, 3] = invM.A14
            # inv1
            buf[i, 4] = invM.A21
            buf[i, 5] = invM.A22
            buf[i, 6] = invM.A23
            buf[i, 7] = invM.A24
            # inv2
            buf[i, 8] = invM.A31
            buf[i, 9] = invM.A32
            buf[i, 10] = invM.A33
            buf[i, 11] = invM.A34
            # sphere
            buf[i, 12] = sph[0]
            buf[i, 13] = sph[1]
            buf[i, 14] = sph[2]
            buf[i, 15] = sph[3]
            # misc
            buf[i, 16] = misc[0]
            buf[i, 17] = misc[1]
            buf[i, 18] = misc[2]
            buf[i, 19] = misc[3]
        return buf

    def ssbo_packer(self, name: str) -> np.ndarray:
        return self.pack_instances()

    # ------------------------------------------------------------------
    # Fold Helpers (Scalar)
    # ------------------------------------------------------------------

    def _fold_linear_single(self, p: FreeCAD.Vector) -> FreeCAD.Vector:
        coords = [p.x, p.y, p.z]
        res = [0.0, 0.0, 0.0]
        for k in range(3):
            s = self.spacing[k]
            sa = 1e-6 if abs(s) < 1e-6 else s
            c = self.counts[k]
            idx = min(max(0, round(coords[k] / sa)), c - 1)
            res[k] = coords[k] - sa * idx
        return FreeCAD.Vector(res[0], res[1], res[2])

    def _fold_linear_candidates(self, p: FreeCAD.Vector):
        coords = [p.x, p.y, p.z]
        ranges = []
        for k in range(3):
            c = self.counts[k]
            if c == 1:
                ranges.append([0])
            else:
                s = self.spacing[k]
                sa = 1e-6 if abs(s) < 1e-6 else s
                idx_float = coords[k] / sa
                i0 = min(max(0, math.floor(idx_float)), c - 1)
                i1 = min(max(0, i0 + 1), c - 1)
                if i0 == i1:
                    ranges.append([i0])
                else:
                    ranges.append([i0, i1])

        candidates = []
        for ix in ranges[0]:
            sx = 1e-6 if abs(self.spacing[0]) < 1e-6 else self.spacing[0]
            px = coords[0] - sx * ix
            for iy in ranges[1]:
                sy = 1e-6 if abs(self.spacing[1]) < 1e-6 else self.spacing[1]
                py = coords[1] - sy * iy
                for iz in ranges[2]:
                    sz = 1e-6 if abs(self.spacing[2]) < 1e-6 else self.spacing[2]
                    pz = coords[2] - sz * iz
                    candidates.append(FreeCAD.Vector(px, py, pz))
        return candidates

    def _fold_step_linear_single(self, p: FreeCAD.Vector) -> FreeCAD.Vector:
        o = self.step_offset
        oo = max(o[0] * o[0] + o[1] * o[1] + o[2] * o[2], 1e-12)
        dot_po = p.x * o[0] + p.y * o[1] + p.z * o[2]
        n = self.step_count
        idx = min(max(0, round(dot_po / oo)), n - 1)
        return FreeCAD.Vector(p.x - o[0] * idx, p.y - o[1] * idx, p.z - o[2] * idx)

    def _fold_step_linear_candidates(self, p: FreeCAD.Vector):
        o = self.step_offset
        oo = max(o[0] * o[0] + o[1] * o[1] + o[2] * o[2], 1e-12)
        dot_po = p.x * o[0] + p.y * o[1] + p.z * o[2]
        n = self.step_count
        idx_f = dot_po / oo
        i0 = min(max(0, math.floor(idx_f)), n - 1)
        i1 = min(max(0, i0 + 1), n - 1)
        indices = [i0] if i0 == i1 else [i0, i1]
        return [FreeCAD.Vector(p.x - o[0] * idx, p.y - o[1] * idx, p.z - o[2] * idx) for idx in indices]

    def _fold_radial_candidates(self, p: FreeCAD.Vector):
        span_deg = abs(self.step_angle) * self.step_count
        span_rad = math.radians(span_deg)
        n = self.step_count
        sector = span_rad / max(1.0, float(n))

        c_pt = self.step_center
        x, y, z = p.x - c_pt[0], p.y - c_pt[1], p.z - c_pt[2]
        if self.step_axis == "X":
            a = math.atan2(z, y)
        elif self.step_axis == "Y":
            a = math.atan2(x, z)
        else:  # "Z"
            a = math.atan2(y, x)

        if a < 0:
            a += 2.0 * math.pi

        idx_float = a / sector if sector > 1e-9 else 0.0
        wrap = abs(span_deg - 360.0) < 1e-6
        if wrap:
            i0 = int(math.floor(idx_float)) % n
            i1 = (i0 + 1) % n
        else:
            i0 = min(max(0, math.floor(idx_float)), n - 1)
            i1 = min(max(0, i0 + 1), n - 1)

        indices = [i0] if i0 == i1 else [i0, i1]
        candidates = []
        for idx in indices:
            ang = -idx * sector
            cos_a = math.cos(ang)
            sin_a = math.sin(ang)
            if self.step_axis == "X":
                p_rot = FreeCAD.Vector(x, cos_a * y - sin_a * z, sin_a * y + cos_a * z)
            elif self.step_axis == "Y":
                p_rot = FreeCAD.Vector(sin_a * z + cos_a * x, y, cos_a * z - sin_a * x)
            else:  # "Z"
                p_rot = FreeCAD.Vector(cos_a * x - sin_a * y, sin_a * x + cos_a * y, z)
            candidates.append(p_rot + FreeCAD.Vector(*c_pt))
        return candidates

    # ── Evaluation ────────────────────────────────────────────────────

    def evaluate(self, point: FreeCAD.Vector) -> float:
        strat = self.strategy()
        if strat == "fold_grid":
            if self.use_multi_cell():
                candidates = self._fold_linear_candidates(point)
                return min(self.source.evaluate(cp) for cp in candidates)
            else:
                folded = self._fold_linear_single(point)
                return self.source.evaluate(folded)
        elif strat == "fold_step_linear":
            if self.use_multi_cell():
                candidates = self._fold_step_linear_candidates(point)
                return min(self.source.evaluate(cp) for cp in candidates)
            else:
                folded = self._fold_step_linear_single(point)
                return self.source.evaluate(folded)
        elif strat == "fold_radial":
            candidates = self._fold_radial_candidates(point)
            return min(self.source.evaluate(cp) for cp in candidates)
        else:
            # "instances"
            mats, inv_mats, spheres, miscs = self._get_instance_transforms()
            d_vals = []
            for invM, misc in zip(inv_mats, miscs):
                if misc[1] < 0.5:
                    continue
                q = invM.multVec(point)
                d_vals.append(misc[0] * self.source.evaluate(q))
            return min(d_vals) if d_vals else 1e30

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float32)
        strat = self.strategy()
        if strat == "fold_grid":
            if self.use_multi_cell():
                coords = [pts[:, 0], pts[:, 1], pts[:, 2]]
                ranges_per_axis = []
                for k in range(3):
                    c = self.counts[k]
                    if c == 1:
                        ranges_per_axis.append([np.zeros(len(pts), dtype=np.int32)])
                    else:
                        s = self.spacing[k]
                        sa = 1e-6 if abs(s) < 1e-6 else s
                        idx_f = coords[k] / sa
                        i0 = np.clip(np.floor(idx_f).astype(np.int32), 0, c - 1)
                        i1 = np.clip(i0 + 1, 0, c - 1)
                        ranges_per_axis.append([i0, i1])

                eval_results = []
                for ix in ranges_per_axis[0]:
                    sx = 1e-6 if abs(self.spacing[0]) < 1e-6 else self.spacing[0]
                    px = coords[0] - sx * ix
                    for iy in ranges_per_axis[1]:
                        sy = 1e-6 if abs(self.spacing[1]) < 1e-6 else self.spacing[1]
                        py = coords[1] - sy * iy
                        for iz in ranges_per_axis[2]:
                            sz = 1e-6 if abs(self.spacing[2]) < 1e-6 else self.spacing[2]
                            pz = coords[2] - sz * iz
                            cand_pts = np.stack([px, py, pz], axis=-1)
                            eval_results.append(self.source.evaluate_grid(cand_pts).astype(np.float32))
                return np.minimum.reduce(eval_results)
            else:
                coords = [pts[:, 0], pts[:, 1], pts[:, 2]]
                folded_coords = []
                for k in range(3):
                    s = self.spacing[k]
                    sa = 1e-6 if abs(s) < 1e-6 else s
                    c = self.counts[k]
                    idx = np.clip(np.round(coords[k] / sa).astype(np.int32), 0, c - 1)
                    folded_coords.append(coords[k] - sa * idx)
                folded = np.stack(folded_coords, axis=-1)
                return self.source.evaluate_grid(folded).astype(np.float32)
        elif strat == "fold_step_linear":
            o = np.array(self.step_offset, dtype=np.float32)
            oo = max(float(np.dot(o, o)), 1e-12)
            dot_po = pts[:, 0] * o[0] + pts[:, 1] * o[1] + pts[:, 2] * o[2]
            n = self.step_count
            if not self.use_multi_cell():
                idx = np.clip(np.round(dot_po / oo).astype(np.int32), 0, n - 1)
                folded = pts - np.outer(idx, o)
                return self.source.evaluate_grid(folded).astype(np.float32)
            else:
                i0 = np.clip(np.floor(dot_po / oo).astype(np.int32), 0, n - 1)
                i1 = np.clip(i0 + 1, 0, n - 1)
                f0 = self.source.evaluate_grid(pts - np.outer(i0, o)).astype(np.float32)
                f1 = self.source.evaluate_grid(pts - np.outer(i1, o)).astype(np.float32)
                return np.minimum(f0, f1)
        elif strat == "fold_radial":
            span_deg = abs(self.step_angle) * self.step_count
            span_rad = math.radians(span_deg)
            n = self.step_count
            sector = span_rad / max(1.0, float(n))

            c_pt = np.array(self.step_center, dtype=np.float32)
            x = pts[:, 0] - c_pt[0]
            y = pts[:, 1] - c_pt[1]
            z = pts[:, 2] - c_pt[2]
            if self.step_axis == "X":
                a = np.arctan2(z, y)
            elif self.step_axis == "Y":
                a = np.arctan2(x, z)
            else:  # "Z"
                a = np.arctan2(y, x)

            a = np.where(a < 0, a + 2.0 * np.pi, a)

            idx_f = a / sector if sector > 1e-9 else np.zeros_like(a)
            wrap = abs(span_deg - 360.0) < 1e-6
            if wrap:
                i0 = np.mod(np.floor(idx_f).astype(np.int32), n)
                i1 = np.mod(i0 + 1, n)
            else:
                i0 = np.clip(np.floor(idx_f).astype(np.int32), 0, n - 1)
                i1 = np.clip(i0 + 1, 0, n - 1)

            eval_results = []
            for i_cand in (i0, i1):
                ang = -i_cand * sector
                cos_a = np.cos(ang)
                sin_a = np.sin(ang)
                if self.step_axis == "X":
                    px, py, pz = x, cos_a * y - sin_a * z, sin_a * y + cos_a * z
                elif self.step_axis == "Y":
                    px, py, pz = sin_a * z + cos_a * x, y, cos_a * z - sin_a * x
                else:  # "Z"
                    px, py, pz = cos_a * x - sin_a * y, sin_a * x + cos_a * y, z
                cand_pts = np.stack([px + c_pt[0], py + c_pt[1], pz + c_pt[2]], axis=-1)
                eval_results.append(self.source.evaluate_grid(cand_pts).astype(np.float32))
            return np.minimum.reduce(eval_results)
        else:
            # "instances"
            mats, inv_mats, spheres, miscs = self._get_instance_transforms()
            eval_list = []
            for invM, misc in zip(inv_mats, miscs):
                if misc[1] < 0.5:
                    continue
                qx = invM.A11 * pts[:, 0] + invM.A12 * pts[:, 1] + invM.A13 * pts[:, 2] + invM.A14
                qy = invM.A21 * pts[:, 0] + invM.A22 * pts[:, 1] + invM.A23 * pts[:, 2] + invM.A24
                qz = invM.A31 * pts[:, 0] + invM.A32 * pts[:, 1] + invM.A33 * pts[:, 2] + invM.A34
                q_pts = np.stack([qx, qy, qz], axis=-1)
                d_i = misc[0] * self.source.evaluate_grid(q_pts).astype(np.float32)
                eval_list.append(d_i)
            return np.minimum.reduce(eval_list) if eval_list else np.full(len(pts), 1e30, dtype=np.float32)

    # ── GLSL Generation ───────────────────────────────────────────────

    def to_glsl(self, ctx, point_var="p"):
        strat = self.strategy()
        if strat == "fold_grid":
            sp_u = ctx.uniform("vec3", self.spacing)
            cn_u = ctx.uniform("vec3", (float(self.counts[0]), float(self.counts[1]), float(self.counts[2])))
            if not self.use_multi_cell():
                ctx.add_custom_helper("arr_linear", _GLSL_ARRAY_LINEAR)
                deformed_var = f"arr_linear({point_var}, {sp_u}, {cn_u})"
                return self.source.to_glsl(ctx, deformed_var)
            else:
                src_expr = self.source.to_glsl(ctx, "q")
                fn = ctx.get_unique_name("arr_src")
                ctx.add_custom_helper(fn, f"float {fn}(vec3 q) {{ return {src_expr}; }}\n")

                ctx.add_custom_helper("arr_linear_idx", _GLSL_ARRAY_LINEAR_IDX)
                c_x, c_y, c_z = self.counts[0], self.counts[1], self.counts[2]
                offsets = []
                for ix in range(2 if c_x > 1 else 1):
                    for iy in range(2 if c_y > 1 else 1):
                        for iz in range(2 if c_z > 1 else 1):
                            offsets.append((ix, iy, iz))

                eval_exprs = []
                for ix, iy, iz in offsets:
                    p_cand = f"arr_linear_idx({point_var}, {sp_u}, {cn_u}, vec3({ix}.0, {iy}.0, {iz}.0))"
                    eval_exprs.append(f"{fn}({p_cand})")

                result_expr = eval_exprs[0]
                for expr in eval_exprs[1:]:
                    result_expr = f"min({result_expr}, {expr})"
                return result_expr

        elif strat == "fold_step_linear":
            o_u = ctx.uniform("vec3", self.step_offset)
            n_u = ctx.uniform("float", float(self.step_count))
            if not self.use_multi_cell():
                ctx.add_custom_helper("arr_linear_dir", _GLSL_ARRAY_LINEAR_DIR)
                return self.source.to_glsl(ctx, f"arr_linear_dir({point_var}, {o_u}, {n_u})")
            else:
                src_expr = self.source.to_glsl(ctx, "q")
                fn = ctx.get_unique_name("arr_src")
                ctx.add_custom_helper(fn, f"float {fn}(vec3 q) {{ return {src_expr}; }}\n")
                ctx.add_custom_helper("arr_linear_dir_idx", _GLSL_ARRAY_LINEAR_DIR_IDX)
                p0 = f"arr_linear_dir_idx({point_var}, {o_u}, {n_u}, 0.0)"
                p1 = f"arr_linear_dir_idx({point_var}, {o_u}, {n_u}, 1.0)"
                return f"min({fn}({p0}), {fn}({p1}))"

        elif strat == "fold_radial":
            src_expr = self.source.to_glsl(ctx, "q")
            fn = ctx.get_unique_name("arr_src")
            ctx.add_custom_helper(fn, f"float {fn}(vec3 q) {{ return {src_expr}; }}\n")

            span_deg = abs(self.step_angle) * self.step_count
            span_rad = math.radians(span_deg)
            n_u = ctx.uniform("float", float(self.step_count))
            span_u = ctx.uniform("float", float(span_rad))
            wrap_u = ctx.uniform("float", 1.0 if abs(span_deg - 360.0) < 1e-6 else 0.0)
            c_u = ctx.uniform("vec3", self.step_center)

            axis = self.step_axis
            if axis == "X":
                ctx.add_custom_helper("arr_radial_x", _GLSL_ARRAY_RADIAL_X)
                p0 = f"arr_radial_x({point_var}, {n_u}, {span_u}, 0.0, {wrap_u}, {c_u})"
                p1 = f"arr_radial_x({point_var}, {n_u}, {span_u}, 1.0, {wrap_u}, {c_u})"
            elif axis == "Y":
                ctx.add_custom_helper("arr_radial_y", _GLSL_ARRAY_RADIAL_Y)
                p0 = f"arr_radial_y({point_var}, {n_u}, {span_u}, 0.0, {wrap_u}, {c_u})"
                p1 = f"arr_radial_y({point_var}, {n_u}, {span_u}, 1.0, {wrap_u}, {c_u})"
            else:  # Z
                ctx.add_custom_helper("arr_radial_z", _GLSL_ARRAY_RADIAL_Z)
                p0 = f"arr_radial_z({point_var}, {n_u}, {span_u}, 0.0, {wrap_u}, {c_u})"
                p1 = f"arr_radial_z({point_var}, {n_u}, {span_u}, 1.0, {wrap_u}, {c_u})"

            return f"min({fn}({p0}), {fn}({p1}))"

        else:
            # "instances"
            src_expr = self.source.to_glsl(ctx, "q")
            src_fn = ctx.get_unique_name("arr_src")
            ctx.add_custom_helper(src_fn, f"float {src_fn}(vec3 q) {{ return {src_expr}; }}\n")

            buf = ctx.ssbo("arr_inst", self, struct_type="ArrayInstance")
            loop_fn = ctx.get_unique_name("arr_inst")
            loop_code = f"""
float {loop_fn}(vec3 p, int n) {{
    float best = 1e30;
    for (int i = 0; i < n; ++i) {{
        ArrayInstance ins = {buf}[i];
        if (ins.misc.y < 0.5) continue;
        if (length(p - ins.sphere.xyz) - ins.sphere.w >= best) continue;
        vec3 q = vec3(dot(ins.inv0.xyz, p) + ins.inv0.w,
                      dot(ins.inv1.xyz, p) + ins.inv1.w,
                      dot(ins.inv2.xyz, p) + ins.inv2.w);
        best = min(best, ins.misc.x * {src_fn}(q));
    }}
    return best;
}}
"""
            ctx.add_custom_helper(loop_fn, loop_code)
            n_val = min(self.step_count, 256)
            n_u = ctx.uniform("int", int(n_val))
            return f"{loop_fn}({point_var}, {n_u})"

    def to_glsl_sample(self, ctx, point_var="p"):
        if getattr(self, "surface_id", SURFACE_ID_UNSET) < SURFACE_ID_UNSET and getattr(self.source, "surface_id", SURFACE_ID_UNSET) == SURFACE_ID_UNSET:
            setattr(self.source, "surface_id", self.surface_id)

        strat = self.strategy()
        if strat == "fold_grid":
            sp_u = ctx.uniform("vec3", self.spacing)
            cn_u = ctx.uniform("vec3", (float(self.counts[0]), float(self.counts[1]), float(self.counts[2])))
            if not self.use_multi_cell():
                ctx.add_custom_helper("arr_linear", _GLSL_ARRAY_LINEAR)
                deformed_var = f"arr_linear({point_var}, {sp_u}, {cn_u})"
                return self.source.to_glsl_sample(ctx, deformed_var)
            else:
                src_expr = self.source.to_glsl_sample(ctx, "q")
                fn = ctx.get_unique_name("arr_srcs")
                ctx.add_custom_helper(fn, f"FldSample {fn}(vec3 q) {{ return {src_expr}; }}\n")

                ctx.add_custom_helper("arr_linear_idx", _GLSL_ARRAY_LINEAR_IDX)
                c_x, c_y, c_z = self.counts[0], self.counts[1], self.counts[2]
                offsets = []
                for ix in range(2 if c_x > 1 else 1):
                    for iy in range(2 if c_y > 1 else 1):
                        for iz in range(2 if c_z > 1 else 1):
                            offsets.append((ix, iy, iz))

                eval_exprs = []
                for ix, iy, iz in offsets:
                    p_cand = f"arr_linear_idx({point_var}, {sp_u}, {cn_u}, vec3({ix}.0, {iy}.0, {iz}.0))"
                    eval_exprs.append(f"{fn}({p_cand})")

                result_expr = eval_exprs[0]
                for expr in eval_exprs[1:]:
                    result_expr = f"fld_min({result_expr}, {expr})"
                return result_expr

        elif strat == "fold_step_linear":
            o_u = ctx.uniform("vec3", self.step_offset)
            n_u = ctx.uniform("float", float(self.step_count))
            if not self.use_multi_cell():
                ctx.add_custom_helper("arr_linear_dir", _GLSL_ARRAY_LINEAR_DIR)
                return self.source.to_glsl_sample(ctx, f"arr_linear_dir({point_var}, {o_u}, {n_u})")
            else:
                src_expr = self.source.to_glsl_sample(ctx, "q")
                fn = ctx.get_unique_name("arr_srcs")
                ctx.add_custom_helper(fn, f"FldSample {fn}(vec3 q) {{ return {src_expr}; }}\n")
                ctx.add_custom_helper("arr_linear_dir_idx", _GLSL_ARRAY_LINEAR_DIR_IDX)
                p0 = f"arr_linear_dir_idx({point_var}, {o_u}, {n_u}, 0.0)"
                p1 = f"arr_linear_dir_idx({point_var}, {o_u}, {n_u}, 1.0)"
                return f"fld_min({fn}({p0}), {fn}({p1}))"

        elif strat == "fold_radial":
            src_expr = self.source.to_glsl_sample(ctx, "q")
            fn = ctx.get_unique_name("arr_srcs")
            ctx.add_custom_helper(fn, f"FldSample {fn}(vec3 q) {{ return {src_expr}; }}\n")

            span_deg = abs(self.step_angle) * self.step_count
            span_rad = math.radians(span_deg)
            n_u = ctx.uniform("float", float(self.step_count))
            span_u = ctx.uniform("float", float(span_rad))
            wrap_u = ctx.uniform("float", 1.0 if abs(span_deg - 360.0) < 1e-6 else 0.0)
            c_u = ctx.uniform("vec3", self.step_center)

            axis = self.step_axis
            if axis == "X":
                ctx.add_custom_helper("arr_radial_x", _GLSL_ARRAY_RADIAL_X)
                p0 = f"arr_radial_x({point_var}, {n_u}, {span_u}, 0.0, {wrap_u}, {c_u})"
                p1 = f"arr_radial_x({point_var}, {n_u}, {span_u}, 1.0, {wrap_u}, {c_u})"
            elif axis == "Y":
                ctx.add_custom_helper("arr_radial_y", _GLSL_ARRAY_RADIAL_Y)
                p0 = f"arr_radial_y({point_var}, {n_u}, {span_u}, 0.0, {wrap_u}, {c_u})"
                p1 = f"arr_radial_y({point_var}, {n_u}, {span_u}, 1.0, {wrap_u}, {c_u})"
            else:  # Z
                ctx.add_custom_helper("arr_radial_z", _GLSL_ARRAY_RADIAL_Z)
                p0 = f"arr_radial_z({point_var}, {n_u}, {span_u}, 0.0, {wrap_u}, {c_u})"
                p1 = f"arr_radial_z({point_var}, {n_u}, {span_u}, 1.0, {wrap_u}, {c_u})"

            return f"fld_min({fn}({p0}), {fn}({p1}))"

        else:
            # "instances"
            src_expr = self.source.to_glsl_sample(ctx, "q")
            src_fn = ctx.get_unique_name("arr_srcs")
            ctx.add_custom_helper(src_fn, f"FldSample {src_fn}(vec3 q) {{ return {src_expr}; }}\n")

            buf = ctx.ssbo("arr_inst", self, struct_type="ArrayInstance")
            loop_fn = ctx.get_unique_name("arr_insts")
            loop_code = f"""
FldSample {loop_fn}(vec3 p, int n) {{
    FldSample best;
    best.d  = 1e30;
    best.id = 65535u;
    for (int i = 0; i < n; ++i) {{
        ArrayInstance ins = {buf}[i];
        if (ins.misc.y < 0.5) continue;
        if (length(p - ins.sphere.xyz) - ins.sphere.w >= best.d) continue;
        vec3 q = vec3(dot(ins.inv0.xyz, p) + ins.inv0.w,
                      dot(ins.inv1.xyz, p) + ins.inv1.w,
                      dot(ins.inv2.xyz, p) + ins.inv2.w);
        FldSample c = {src_fn}(q);
        c.d *= ins.misc.x;
        best = fld_min(best, c);
    }}
    return best;
}}
"""
            ctx.add_custom_helper(loop_fn, loop_code)
            n_val = min(self.step_count, 256)
            n_u = ctx.uniform("int", int(n_val))
            return f"{loop_fn}({point_var}, {n_u})"


_GLSL_ARRAY_LINEAR = """
vec3 arr_linear(vec3 p, vec3 s, vec3 n) {
    vec3 sa = vec3(abs(s.x) < 1e-6 ? 1e-6 : s.x,
                   abs(s.y) < 1e-6 ? 1e-6 : s.y,
                   abs(s.z) < 1e-6 ? 1e-6 : s.z);
    vec3 id = clamp(round(p / sa), vec3(0.0), max(n - 1.0, vec3(0.0)));
    return p - sa * id;
}
"""

_GLSL_ARRAY_LINEAR_IDX = """
vec3 arr_linear_idx(vec3 p, vec3 s, vec3 n, vec3 offset_idx) {
    vec3 sa = vec3(abs(s.x) < 1e-6 ? 1e-6 : s.x,
                   abs(s.y) < 1e-6 ? 1e-6 : s.y,
                   abs(s.z) < 1e-6 ? 1e-6 : s.z);
    vec3 i0 = clamp(floor(p / sa), vec3(0.0), max(n - 1.0, vec3(0.0)));
    vec3 id = clamp(i0 + offset_idx, vec3(0.0), max(n - 1.0, vec3(0.0)));
    return p - sa * id;
}
"""

_GLSL_ARRAY_LINEAR_DIR = """
vec3 arr_linear_dir(vec3 p, vec3 o, float n) {
    float oo = max(dot(o, o), 1e-12);
    float i = clamp(round(dot(p, o) / oo), 0.0, max(n - 1.0, 0.0));
    return p - o * i;
}
"""

_GLSL_ARRAY_LINEAR_DIR_IDX = """
vec3 arr_linear_dir_idx(vec3 p, vec3 o, float n, float offset_step) {
    float oo = max(dot(o, o), 1e-12);
    float i0 = clamp(floor(dot(p, o) / oo), 0.0, max(n - 1.0, 0.0));
    float id = clamp(i0 + offset_step, 0.0, max(n - 1.0, 0.0));
    return p - o * id;
}
"""

_GLSL_ARRAY_RADIAL_X = """
vec3 arr_radial_x(vec3 p, float n, float span_rad, float offset_step, float wrap, vec3 c_in) {
    float sector = span_rad / max(n, 1.0);
    vec3 p_rel = p - c_in;
    float a = atan(p_rel.z, p_rel.y);
    if (a < 0.0) a += 6.283185307179586;
    float i0 = wrap > 0.5 ? mod(floor(a / sector), n) : clamp(floor(a / sector), 0.0, max(n - 1.0, 0.0));
    float idx = wrap > 0.5 ? mod(i0 + offset_step, n) : clamp(i0 + offset_step, 0.0, max(n - 1.0, 0.0));
    float ang = -idx * sector;
    float c = cos(ang), s = sin(ang);
    return vec3(p_rel.x, c * p_rel.y - s * p_rel.z, s * p_rel.y + c * p_rel.z) + c_in;
}
"""

_GLSL_ARRAY_RADIAL_Y = """
vec3 arr_radial_y(vec3 p, float n, float span_rad, float offset_step, float wrap, vec3 c_in) {
    float sector = span_rad / max(n, 1.0);
    vec3 p_rel = p - c_in;
    float a = atan(p_rel.x, p_rel.z);
    if (a < 0.0) a += 6.283185307179586;
    float i0 = wrap > 0.5 ? mod(floor(a / sector), n) : clamp(floor(a / sector), 0.0, max(n - 1.0, 0.0));
    float idx = wrap > 0.5 ? mod(i0 + offset_step, n) : clamp(i0 + offset_step, 0.0, max(n - 1.0, 0.0));
    float ang = -idx * sector;
    float c = cos(ang), s = sin(ang);
    return vec3(s * p_rel.z + c * p_rel.x, p_rel.y, c * p_rel.z - s * p_rel.x) + c_in;
}
"""

_GLSL_ARRAY_RADIAL_Z = """
vec3 arr_radial_z(vec3 p, float n, float span_rad, float offset_step, float wrap, vec3 c_in) {
    float sector = span_rad / max(n, 1.0);
    vec3 p_rel = p - c_in;
    float a = atan(p_rel.y, p_rel.x);
    if (a < 0.0) a += 6.283185307179586;
    float i0 = wrap > 0.5 ? mod(floor(a / sector), n) : clamp(floor(a / sector), 0.0, max(n - 1.0, 0.0));
    float idx = wrap > 0.5 ? mod(i0 + offset_step, n) : clamp(i0 + offset_step, 0.0, max(n - 1.0, 0.0));
    float ang = -idx * sector;
    float c = cos(ang), s = sin(ang);
    return vec3(c * p_rel.x - s * p_rel.y, s * p_rel.x + c * p_rel.y, p_rel.z) + c_in;
}
"""
