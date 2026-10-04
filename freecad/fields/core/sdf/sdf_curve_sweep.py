# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np
import math
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.curve_sampler import compute_curve_rmf_frames

# How many Newton steps refine each of the 7 seeds. CPU and GPU must agree:
# a lower count on one side moves the projected closest point, which shows up
# as the mesh and the ray-marched surface disagreeing on tightly curved paths.
_NEWTON_ITERS = 4


def _bezier_pos_xyz(p0a, p1a, p2a, p3a, t):
    """Cubic Bezier position at parameter(s) t, as separate (bx, by, bz) arrays.
    t may be any shape broadcastable against the scalar control-point components."""
    s = 1.0 - t
    bx = s**3*p0a[0] + 3*s**2*t*p1a[0] + 3*s*t**2*p2a[0] + t**3*p3a[0]
    by = s**3*p0a[1] + 3*s**2*t*p1a[1] + 3*s*t**2*p2a[1] + t**3*p3a[1]
    bz = s**3*p0a[2] + 3*s**2*t*p1a[2] + 3*s*t**2*p2a[2] + t**3*p3a[2]
    return bx, by, bz


def _bezier_tangent_xyz(p0a, p1a, p2a, p3a, t):
    """Cubic Bezier first derivative at parameter(s) t, as separate (b1x, b1y, b1z) arrays."""
    s = 1.0 - t
    b1x = 3*(s**2*(p1a[0]-p0a[0]) + 2*s*t*(p2a[0]-p1a[0]) + t**2*(p3a[0]-p2a[0]))
    b1y = 3*(s**2*(p1a[1]-p0a[1]) + 2*s*t*(p2a[1]-p1a[1]) + t**2*(p3a[1]-p2a[1]))
    b1z = 3*(s**2*(p1a[2]-p0a[2]) + 2*s*t*(p2a[2]-p1a[2]) + t**2*(p3a[2]-p2a[2]))
    return b1x, b1y, b1z


class SdfCurveSweepField(SdfField):
    """SDF for an analytic cross-section swept along a 3D cubic Bezier path.

    Parameters:
        profile: Sdf2dField (e.g. Sdf2dBox, Sdf2dCircle). To sweep the section
            of an existing 3D solid, wrap it in Sdf2dSectionOfField first.
        segments_3d: list of (p0, p1, p2, p3) cubic Bezier tuples in world space.
        start_scale: float scale multiplier at s=0.
        end_scale: float scale multiplier at s=L.
        twist_degrees: total twist in degrees along path length.
        cap_type: 'Flat' (plane at each path end) or 'Round' (spherical ends,
            matching SdfPipeField's convention where the cap bulges one profile
            radius past the end point). Anything else is read as 'Flat'.
        is_closed: bool indicating closed loop. A closed sweep needs no caps.
    """

    CAP_TYPES = ("Flat", "Round")

    def __init__(self, profile, segments_3d: list, start_scale: float = 1.0,
                 end_scale: float = 1.0, twist_degrees: float = 0.0,
                 cap_type: str = "Flat", is_closed: bool = False):
        super().__init__()
        if not hasattr(profile, 'evaluate_2d_grid'):
            raise TypeError(
                f"SdfCurveSweepField needs a 2D profile (Sdf2dField), got "
                f"{type(profile).__name__}. Wrap a 3D solid in Sdf2dSectionOfField "
                f"rather than passing it here: sampling a world-space 3D field at "
                f"(u, v, 0) slices through the world origin, not through the solid."
            )
        self.profile = profile
        self.segments = [
            tuple(tuple(float(c) for c in pt) for pt in seg)
            for seg in segments_3d
        ]
        self.start_scale = float(start_scale)
        self.end_scale = float(end_scale)
        self.twist_degrees = float(twist_degrees)
        # An uncapped solid is not a solid: with t clamped to [0,1] the profile
        # extends along the end tangents forever, so the field never turns
        # positive past the ends and bounding_box() would be a lie. Every open
        # sweep is capped; the only choice is the shape of the cap.
        self.cap_type = cap_type if cap_type in self.CAP_TYPES else "Flat"
        self.is_closed = bool(is_closed)
        self.frames = compute_curve_rmf_frames(self.segments, is_closed=self.is_closed)

        self.seg_lengths = []
        for p0, p1, p2, p3 in self.segments:
            p0a, p1a, p2a, p3a = np.array(p0), np.array(p1), np.array(p2), np.array(p3)
            chord = np.linalg.norm(p3a - p0a)
            poly = np.linalg.norm(p1a - p0a) + np.linalg.norm(p2a - p1a) + np.linalg.norm(p3a - p2a)
            self.seg_lengths.append(float(0.5 * (chord + poly)))
        self.total_length = max(1e-6, sum(self.seg_lengths))

        # Rounding radius of a 'Round' cap, at unit scale. Half the profile's
        # smaller extent, so a circular profile of radius R gets a true
        # hemisphere and the sweep reduces exactly to SdfPipeField.
        self.cap_radius = self._profile_inradius()

    def _profile_inradius(self) -> float:
        """Half the smaller extent of the profile's 2D bounding box."""
        bbox = self.profile.bbox_2d() if hasattr(self.profile, 'bbox_2d') else None
        if not bbox:
            return 0.0
        x0, y0, x1, y1 = bbox
        return float(max(0.0, 0.5 * min(abs(x1 - x0), abs(y1 - y0))))

    def evaluate(self, point: FreeCAD.Vector) -> float:
        pts = np.array([[point.x, point.y, point.z]], dtype=np.float64)
        res = self.evaluate_grid(pts)
        return float(res[0])

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        N = points.shape[0]
        if N == 0 or not self.segments:
            return np.full(N, np.inf, dtype=np.float32)

        px, py, pz = points[:, 0], points[:, 1], points[:, 2]
        min_d2 = np.full(N, np.inf, dtype=np.float64)
        best_u = np.zeros(N, dtype=np.float64)
        best_v = np.zeros(N, dtype=np.float64)
        best_s = np.zeros(N, dtype=np.float64)
        best_t = np.zeros(N, dtype=np.float64)
        best_seg = np.zeros(N, dtype=np.int32)

        accum_len = 0.0
        for seg_idx, (p0, p1, p2, p3) in enumerate(self.segments):
            p0a, p1a, p2a, p3a = np.array(p0), np.array(p1), np.array(p2), np.array(p3)
            seg_len = self.seg_lengths[seg_idx]
            frame = self.frames[seg_idx] if seg_idx < len(self.frames) else None

            for k in range(7):
                t_cand = np.full(N, k / 6.0, dtype=np.float64)
                for _ in range(_NEWTON_ITERS):
                    s = 1.0 - t_cand
                    bx, by, bz = _bezier_pos_xyz(p0a, p1a, p2a, p3a, t_cand)
                    dx, dy, dz = bx - px, by - py, bz - pz

                    b1x, b1y, b1z = _bezier_tangent_xyz(p0a, p1a, p2a, p3a, t_cand)

                    b2x = 6*(s*(p2a[0]-2*p1a[0]+p0a[0]) + t_cand*(p3a[0]-2*p2a[0]+p1a[0]))
                    b2y = 6*(s*(p2a[1]-2*p1a[1]+p0a[1]) + t_cand*(p3a[1]-2*p2a[1]+p1a[1]))
                    b2z = 6*(s*(p2a[2]-2*p1a[2]+p0a[2]) + t_cand*(p3a[2]-2*p2a[2]+p1a[2]))

                    f = dx*b1x + dy*b1y + dz*b1z
                    df = b1x**2 + b1y**2 + b1z**2 + dx*b2x + dy*b2y + dz*b2z
                    mask = np.abs(df) > 1e-12
                    t_cand = np.where(mask, np.clip(t_cand - f / np.where(mask, df, 1.0), 0.0, 1.0), t_cand)

                bx, by, bz = _bezier_pos_xyz(p0a, p1a, p2a, p3a, t_cand)
                d2_cand = (bx - px)**2 + (by - py)**2 + (bz - pz)**2

                closer = d2_cand < min_d2
                if np.any(closer):
                    min_d2[closer] = d2_cand[closer]
                    best_t[closer] = t_cand[closer]
                    best_seg[closer] = seg_idx
                    best_s[closer] = accum_len + t_cand[closer] * seg_len

                    if frame:
                        tc = t_cand[closer, None]
                        n_vec = (1.0 - tc) * frame['n0'] + tc * frame['n1']

                        # Re-seat the frame on the tangent at THIS parameter.
                        # The RMF is only solved at the segment ends, and a
                        # tangent can swing far between them, so the plain
                        # interpolation mix(n0, n1, t) is not perpendicular to
                        # the path mid-segment -- measured up to 35 degrees out,
                        # which shears the cross-section and makes the swept
                        # solid bulge and pinch along its length.
                        t_closer = t_cand[closer]
                        b1x, b1y, b1z = _bezier_tangent_xyz(p0a, p1a, p2a, p3a, t_closer)
                        tan = np.column_stack([b1x, b1y, b1z])
                        tan_norm = np.linalg.norm(tan, axis=1, keepdims=True)
                        tan = tan / np.where(tan_norm > 1e-9, tan_norm, 1.0)

                        n_vec = n_vec - np.sum(n_vec * tan, axis=1, keepdims=True) * tan
                        n_norm = np.linalg.norm(n_vec, axis=1, keepdims=True)
                        # Degenerate only if the normal fell onto the tangent;
                        # any perpendicular direction will do there.
                        fallback = np.tile(np.array([0.0, 0.0, 1.0]), (n_vec.shape[0], 1))
                        fallback = fallback - np.sum(fallback * tan, axis=1, keepdims=True) * tan
                        fb_norm = np.linalg.norm(fallback, axis=1, keepdims=True)
                        use_fb = (n_norm <= 1e-9) & (fb_norm > 1e-9)
                        n_vec = np.where(use_fb, fallback / np.where(fb_norm > 1e-9, fb_norm, 1.0),
                                         n_vec / np.where(n_norm > 1e-9, n_norm, 1.0))
                        b_vec = np.cross(tan, n_vec)

                        diff = np.column_stack([px[closer] - bx[closer], py[closer] - by[closer], pz[closer] - bz[closer]])
                        best_u[closer] = np.sum(diff * n_vec, axis=1)
                        best_v[closer] = np.sum(diff * b_vec, axis=1)

            accum_len += seg_len

        s_norm = np.clip(best_s / self.total_length, 0.0, 1.0)
        scale = self.start_scale + (self.end_scale - self.start_scale) * s_norm
        scale = np.maximum(1e-4, scale)

        if abs(self.twist_degrees) > 1e-4:
            theta = np.radians(self.twist_degrees * s_norm)
            cos_t, sin_t = np.cos(theta), np.sin(theta)
            u_rot = cos_t * best_u - sin_t * best_v
            v_rot = sin_t * best_u + cos_t * best_v
        else:
            u_rot, v_rot = best_u, best_v

        u_scaled = u_rot / scale
        v_scaled = v_rot / scale

        uv = np.column_stack([u_scaled, v_scaled]).astype(np.float32)
        d_profile = self.profile.evaluate_2d_grid(uv) * scale.astype(np.float32)

        if self.is_closed:
            return d_profile.astype(np.float32)
        # How far past an end the point lies. In the interior of the path the
        # closest-point condition makes p - B perpendicular to the tangent, so
        # u and v account for the whole offset and this is zero.
        axial_excess = np.sqrt(np.maximum(0.0, min_d2 - best_u**2 - best_v**2))
        return self._apply_caps(points, d_profile, best_s, axial_excess).astype(np.float32)

    def _apply_caps(self, points, d_profile, best_s, axial_excess):
        """Bound an open sweep at both path ends.

        The cap term is measured ALONG THE PATH, not against a pair of global
        half-spaces. Half-spaces only work while the tube stays out of the
        region behind each end plane; on a path that curls back -- a hook, a
        spiral, a helix closing on itself -- the end plane saturates and slices
        through an earlier part of the tube, cutting geometry nowhere near the
        end. On a straight path the two formulations are identical, because
        there dot(p_start - p, t_start) is exactly -s and dot(p - p_end, t_end)
        is exactly s - L.

        `axial_excess` is how far past a path end the point lies: zero wherever
        the closest-point projection landed in the interior of the path, where
        p - B is perpendicular to the tangent by construction.

        Flat is then the exact intersection of the swept profile with the two
        ends. Round is the same expression on a profile inflated by the cap
        radius and deflated again, which turns each end into a sphere of that
        radius on the end point -- so a circular profile reproduces
        SdfPipeField exactly, ends included.
        """
        inside_run = np.minimum(best_s, self.total_length - best_s)
        d_caps = axial_excess - inside_run

        if self.cap_type == "Round" and self.cap_radius > 0.0:
            r_start = self.cap_radius * max(abs(self.start_scale), 1e-4)
            r_end = self.cap_radius * max(abs(self.end_scale), 1e-4)
            # r applies over the whole field, not just past the end: switching
            # it off at d_caps == 0 would put the flat cap's own surface back at
            # the end point and the field would step from -cap_radius to 0
            # across it. Away from the ends the +r and -r cancel and this is
            # the plain profile distance, which is why picking r by the nearer
            # end is unambiguous where it matters.
            r = np.where(best_s * 2.0 <= self.total_length, r_start, r_end)
        else:
            r = 0.0

        dp = d_profile + r
        return (np.hypot(np.maximum(dp, 0.0), np.maximum(d_caps, 0.0))
                + np.minimum(np.maximum(dp, d_caps), 0.0) - r)

    def bounding_box(self):
        if not self.segments:
            return (FreeCAD.Vector(-10, -10, -10), FreeCAD.Vector(10, 10, 10))
        all_pts = [pt for seg in self.segments for pt in seg]
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        zs = [p[2] for p in all_pts]

        max_scale = max(abs(self.start_scale), abs(self.end_scale))
        r = 10.0 * max_scale
        if hasattr(self.profile, 'bbox_2d'):
            x0, y0, x1, y1 = self.profile.bbox_2d()
            r = max(abs(x0), abs(x1), abs(y0), abs(y1)) * max_scale
        elif hasattr(self.profile, 'radius'):
            r = abs(self.profile.radius) * max_scale

        # A Bezier stays inside its control polygon's hull, so padding the
        # control points by the profile's own reach covers the swept solid.
        # A Round cap bulges cap_radius past the end point, and cap_radius is
        # half the profile's smaller extent -- never more than r.
        return (
            FreeCAD.Vector(min(xs) - r, min(ys) - r, min(zs) - r),
            FreeCAD.Vector(max(xs) + r, max(ys) + r, max(zs) + r),
        )

    def lipschitz(self) -> float:
        min_scale = max(1e-3, min(abs(self.start_scale), abs(self.end_scale)))
        return float(1.0 / min_scale)

    @staticmethod
    def _vec3_literal(v) -> str:
        return f"vec3({float(v[0]):.6g},{float(v[1]):.6g},{float(v[2]):.6g})"

    def to_glsl(self, ctx, point_var: str = "p") -> str:
        func_name = ctx.get_unique_name("sdf_curve_sweep")
        helper_code = """
vec4 sd_curve_sweep_eval(vec3 p, vec3 p0, vec3 p1, vec3 p2, vec3 p3, vec3 n0, vec3 n1) {
    float md = 1e18;
    float best_t = 0.5;
    for (int i = 0; i <= 6; i++) {
        float t = float(i) / 6.0;
        for (int j = 0; j < %d; j++) {
            float s = 1.0 - t;
            vec3 B = s*s*s*p0 + 3.0*s*s*t*p1 + 3.0*s*t*t*p2 + t*t*t*p3;
            vec3 dB = 3.0*(s*s*(p1-p0) + 2.0*s*t*(p2-p1) + t*t*(p3-p2));
            float dBdB = dot(dB, dB);
            if (dBdB > 1e-10) t = clamp(t - dot(B-p, dB)/dBdB, 0.0, 1.0);
        }
        float s = 1.0 - t;
        vec3 B = s*s*s*p0 + 3.0*s*s*t*p1 + 3.0*s*t*t*p2 + t*t*t*p3;
        float d2 = dot(B-p, B-p);
        if (d2 < md) { md = d2; best_t = t; }
    }
    float s = 1.0 - best_t;
    float t2 = best_t * best_t;
    float s2 = s * s;
    vec3 B = s2*s*p0 + 3.0*s2*best_t*p1 + 3.0*s*t2*p2 + t2*best_t*p3;
    // Re-seat the frame on the tangent here: the RMF is solved only at the
    // segment ends, so mix(n0, n1, t) is not perpendicular to the path in
    // between. Must match evaluate_grid exactly.
    vec3 T = 3.0*(s2*(p1-p0) + 2.0*s*best_t*(p2-p1) + t2*(p3-p2));
    float tl = length(T);
    T = tl > 1e-9 ? T / tl : vec3(0.0, 0.0, 1.0);
    vec3 n = mix(n0, n1, best_t);
    n = n - dot(n, T) * T;
    float nl = length(n);
    if (nl <= 1e-9) {
        n = vec3(0.0, 0.0, 1.0) - dot(vec3(0.0, 0.0, 1.0), T) * T;
        nl = length(n);
        if (nl <= 1e-9) { n = vec3(1.0, 0.0, 0.0) - dot(vec3(1.0, 0.0, 0.0), T) * T; nl = length(n); }
    }
    n /= nl;
    vec3 b = cross(T, n);
    vec3 diff = p - B;
    return vec4(dot(diff, n), dot(diff, b), best_t, sqrt(md));
}
""" % _NEWTON_ITERS
        ctx.add_custom_helper("sd_curve_sweep_eval", helper_code)

        prof_glsl = self.profile.to_glsl_2d(ctx, "uv_in")

        u_s_scale = ctx.uniform("float", self.start_scale)
        u_e_scale = ctx.uniform("float", self.end_scale)
        u_twist = ctx.uniform("float", math.radians(self.twist_degrees))
        u_len = ctx.uniform("float", self.total_length)

        # Path geometry goes in as literals inside a const array, the way
        # SdfPipeField inlines its control points. Nine uniforms per Bezier
        # segment would put a 40-segment path over 370 uniforms on its own,
        # which is the per-field uniform wall SX-001..011 tore down for the
        # rest of the scene. Editing the curve recompiles; moving a panel
        # slider does not, because the taper and twist stay uniforms.
        # Six vec3 per segment: the four control points and the two end
        # normals. The binormal is cross(T, n) at the evaluated parameter, so
        # storing b0/b1 would only duplicate what the helper recomputes.
        n_seg = len(self.segments)
        stride = 6
        seg_data = []
        for i, (p0, p1, p2, p3) in enumerate(self.segments):
            frame = self.frames[i] if i < len(self.frames) else None
            n0 = frame['n0'] if frame else (0, 1, 0)
            n1 = frame['n1'] if frame else (0, 1, 0)
            seg_data.extend(self._vec3_literal(v) for v in (p0, p1, p2, p3, n0, n1))

        if n_seg == 0:
            ctx.add_custom_helper(func_name, f"float {func_name}(vec3 p) {{ return 1e18; }}")
            return f"{func_name}({point_var})"

        # Declared at file scope, not inside the function: a const array
        # initialised in the body is re-materialised per invocation on some
        # drivers, and this one is 8 vec3 per segment.
        lines = [
            f"const vec3 {func_name}_SEG[{stride * n_seg}] = vec3[{stride * n_seg}]({', '.join(seg_data)});",
            f"const float {func_name}_LEN[{n_seg}] = float[{n_seg}]("
            + ", ".join(f"{L:.6g}" for L in self.seg_lengths) + ");",
            f"float {func_name}(vec3 p) {{",
        ]
        lines.append("    float min_d = 1e18;")
        lines.append("    vec2 best_uv = vec2(0.0);")
        lines.append("    float best_s = 0.0;")
        lines.append("    float accum = 0.0;")
        lines.append(f"    for (int i = 0; i < {n_seg}; i++) {{")
        lines.append(f"        int o = i * {stride};")
        lines.append(f"        vec4 res = sd_curve_sweep_eval(p, {func_name}_SEG[o], {func_name}_SEG[o+1],"
                     f" {func_name}_SEG[o+2], {func_name}_SEG[o+3], {func_name}_SEG[o+4],"
                     f" {func_name}_SEG[o+5]);")
        lines.append("        if (res.w < min_d) { min_d = res.w; best_uv = res.xy;"
                     f" best_s = accum + res.z * {func_name}_LEN[i]; }}")
        lines.append(f"        accum += {func_name}_LEN[i];")
        lines.append("    }")

        lines.append(f"    float s_norm = clamp(best_s / {u_len}, 0.0, 1.0);")
        lines.append(f"    float sc = max(mix({u_s_scale}, {u_e_scale}, s_norm), 1e-4);")
        lines.append(f"    float th = {u_twist} * s_norm;")
        lines.append("    vec2 uv_in = vec2(cos(th)*best_uv.x - sin(th)*best_uv.y,"
                     " sin(th)*best_uv.x + cos(th)*best_uv.y) / sc;")
        lines.append(f"    float d_prof = ({prof_glsl}) * sc;")

        if self.is_closed:
            lines.append("    return d_prof;")
        else:
            # Same arc-length cap as evaluate_grid -- see _apply_caps.
            lines.append("    float axial = sqrt(max(0.0, min_d*min_d - dot(best_uv, best_uv)));")
            lines.append(f"    float d_caps = axial - min(best_s, {u_len} - best_s);")
            if self.cap_type == "Round" and self.cap_radius > 0.0:
                lines.append(f"    float r = {self.cap_radius:.6g} * "
                             f"max(abs(s_norm <= 0.5 ? {u_s_scale} : {u_e_scale}), 1e-4);")
            else:
                lines.append("    float r = 0.0;")
            lines.append("    float dp = d_prof + r;")
            lines.append("    return length(max(vec2(dp, d_caps), 0.0))"
                         " + min(max(dp, d_caps), 0.0) - r;")
        lines.append("}")

        ctx.add_custom_helper(func_name, "\n".join(lines))
        return f"{func_name}({point_var})"
