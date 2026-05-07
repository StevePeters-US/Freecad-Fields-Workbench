# Direct Modeling Workbench — GLSL Refactor, SVG Import, Curve Perf Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Claude Sonnet).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

This file covers three coupled work items:

1. **GLSL helper refactor.** Today `core/sdf/glsl_compiler.py` contains a global
   `GLSL_HELPERS` dict that hard-codes the GLSL bodies of `sdf_box`, `sdf_sphere`,
   `sdf_cylinder`, `sdf_torus`, `sdf_plane`, `sdf_nurbs_curve`, `sdf_nurbs_surface`,
   `sdf_box2d`, `apply_inv_mat`, `smooth_union`, `smooth_subtraction`,
   `smooth_intersection`. The compiler should not know which primitives exist —
   each primitive should register its own GLSL helper inline using the pattern
   already established in `core/sdf/sdf2d/bezier_curve.py` (module-level GLSL
   string + `ctx.add_custom_helper(...)`). After this refactor, `glsl_compiler.py`
   only knows how to assemble uniforms + helpers + expression into a final
   shader source, and adding a new primitive never requires editing the compiler.

2. **SVG path importer.** Currently the only way to author a 2D profile for
   extrusion is to click points in the curve tool. Users want to import
   pre-authored SVG paths. **Rasterization is forbidden** — accuracy must be
   exact. The importer parses SVG path commands (`M L H V C S Q T A Z`) and
   produces cubic Bezier segments compatible with `Sdf2dBezierCurve`
   (`core/sdf/sdf2d/bezier_curve.py`). Other SVG primitives (`<rect>`, `<circle>`,
   `<ellipse>`, `<line>`, `<polyline>`, `<polygon>`) are converted to cubic
   Beziers as well so a single import returns a list of `Sdf2dBezierCurve`
   instances (one per closed subpath).

3. **Curve render performance.** During curve-extrusion editing the G-buffer
   ray march pass climbs from ~28 ms to >140 ms (see session log for
   2026-05-06). Per-pixel cost is `512 steps × N_segments × ~40 GLSL ops`
   because every step calls `sd_cubic_bez_2d` for every segment of every
   visible curve. We optimize without baking to a 2D texture (no rasterization)
   by: (a) per-segment 2D AABB cull in the shader, (b) reduced inner Newton
   iterations, (c) half-resolution G-buffer FBO during interactive drag.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `GlslContext` | `core/sdf/glsl_compiler.py:11` | Per-compile uniform + helper registry |
| `GlslContext.uniform` | `core/sdf/glsl_compiler.py:33` | Register a uniform, returns its GLSL name |
| `GlslContext.need_helper` | `core/sdf/glsl_compiler.py:40` | Mark a static helper as needed (REMOVED in OPT-001) |
| `GlslContext.add_custom_helper` | `core/sdf/glsl_compiler.py:44` | Register a GLSL function body by name (deduped) |
| `GLSL_HELPERS` | `core/sdf/glsl_compiler.py:59-273` | Global helper dict (REMOVED in OPT-001) |
| `compile_field_to_glsl` | `core/sdf/glsl_compiler.py:276` | Top-level entry: field → (expr, ctx) |
| `build_multi_raymarch_fragment_shader` | `core/sdf/glsl_compiler.py:337` | Assembles full fragment shader source |
| `build_compute_shader` | `core/sdf/glsl_compiler.py:287` | Assembles compute shader (used by GPU baker) |
| `Sdf2dBezierCurve` | `core/sdf/sdf2d/bezier_curve.py:130` | Closed cubic-Bezier 2D profile |
| `Sdf2dBezierCurve.to_glsl_2d` | `core/sdf/sdf2d/bezier_curve.py:202` | Emits per-segment GLSL using `add_custom_helper` |
| `_GLSL_SOLVE_CUBIC` / `_GLSL_CUBIC_BEZ_2D` / `_GLSL_CUBIC_WINDING` | `core/sdf/sdf2d/bezier_curve.py:8/52/72` | Reference pattern: module-level GLSL strings registered via `add_custom_helper` |
| `SdfExtrusionField.to_glsl` | `core/sdf/sdf_extrusion.py:59` | Wraps a 2D profile's GLSL into 3D extrusion |
| `_FRAG_GBUF` (analytical path) | `core/sdf/glsl_compiler.py:428` | Fragment shader template generated per rebuild |
| `DMSceneRayMarchRenderer._resize_fbos` | `core/dm_scene_ray_march_renderer.py:622` | Allocates G-buffer at viewport size |
| `DMSceneRayMarchRenderer._render_gl_callback` | `core/dm_scene_ray_march_renderer.py:638` | Multi-pass render driver |

---

## Tier 1 — GLSL Helper Refactor (Do First)

Moves every primitive's GLSL helper out of `glsl_compiler.py` and into the
primitive's own file. After this tier, `glsl_compiler.py` contains only
the `GlslContext` class, `compile_field_to_glsl`, `build_compute_shader`,
and `build_multi_raymarch_fragment_shader` — no primitive-specific GLSL.

### OPT-001: Add shared `apply_inv_mat` GLSL string to `sdf_field.py`

**File:** `core/sdf/sdf_field.py` — append at module bottom (after the last class definition)

**What:** Define `apply_inv_mat` as a module-level GLSL string so any primitive
that uses an inverted placement matrix can register it via
`ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)`. This is the
only helper shared across primitives; everything else lives in its own file.

**Implementation:**

```python
# Shared GLSL helper used by primitives that apply an inverted placement matrix.
# Register via: ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
_GLSL_APPLY_INV_MAT = """
vec3 apply_inv_mat(mat4 m, vec3 p) {
    return (m * vec4(p, 1.0)).xyz;
}
"""
```

### OPT-002: Add `to_glsl` GLSL string + registration to `box.py`

**File:** `core/sdf/sdf/box.py` — replace the `to_glsl` method (line 55) and add module-level GLSL constant

**What:** Define `_GLSL_SDF_BOX` at module top and update `to_glsl` to register
it via `ctx.add_custom_helper("sdf_box", _GLSL_SDF_BOX)` instead of
`ctx.need_helper("sdf_box")`.

**Implementation:**

Add after the imports at the top of the file:

```python
from core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT

_GLSL_SDF_BOX = """
float sdf_box(vec3 p, vec3 center, vec3 half_size) {
    vec3 d = abs(p - center) - half_size;
    return length(max(d, 0.0)) + min(max(d.x, max(d.y, d.z)), 0.0);
}
"""
```

Replace the existing `to_glsl` method body (lines 55-65 approximately):

```python
def to_glsl(self, ctx, point_var="p"):
    ctx.add_custom_helper("sdf_box", _GLSL_SDF_BOX)
    c = ctx.uniform("vec3", (self.center.x, self.center.y, self.center.z))
    h = ctx.uniform("vec3", (self.half_size.x, self.half_size.y, self.half_size.z))
    if self.inv_matrix is not None:
        ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
        m = ctx.uniform("mat4", self.inv_matrix.tolist())
        return f"sdf_box(apply_inv_mat({m}, {point_var}), {c}, {h})"
    return f"sdf_box({point_var}, {c}, {h})"
```

**Depends on:** OPT-001

### OPT-003: Move `sdf_sphere` GLSL into `sphere.py`

**File:** `core/sdf/sdf/sphere.py` — replace `to_glsl` at line 41

**What:** Define `_GLSL_SDF_SPHERE` at module top, update `to_glsl` to use
`add_custom_helper`. Same pattern as OPT-002.

**Implementation:**

Add after imports:

```python
from core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT

_GLSL_SDF_SPHERE = """
float sdf_sphere(vec3 p, vec3 center, float radius) {
    return length(p - center) - radius;
}
"""
```

Replace `to_glsl`:

```python
def to_glsl(self, ctx, point_var="p"):
    ctx.add_custom_helper("sdf_sphere", _GLSL_SDF_SPHERE)
    c = ctx.uniform("vec3", (self.center.x, self.center.y, self.center.z))
    r = ctx.uniform("float", self.radius)
    if self.inv_matrix is not None:
        ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
        m = ctx.uniform("mat4", self.inv_matrix.tolist())
        return f"sdf_sphere(apply_inv_mat({m}, {point_var}), {c}, {r})"
    return f"sdf_sphere({point_var}, {c}, {r})"
```

**Depends on:** OPT-001

### OPT-004: Move `sdf_cylinder` GLSL into `cylinder.py`

**File:** `core/sdf/sdf/cylinder.py` — replace `to_glsl` at line 68

**What:** Define `_GLSL_SDF_CYLINDER` at module top, update `to_glsl`.

**Implementation:**

Add after imports:

```python
from core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT

_GLSL_SDF_CYLINDER = """
float sdf_cylinder(vec3 p, vec3 base_center, vec3 axis, float radius, float height) {
    vec3 pa = p - base_center;
    float h = dot(pa, axis);
    vec3 radial = pa - axis * h;
    float d_radial = length(radial) - radius;
    float h_center = h - height * 0.5;
    float d_axial = abs(h_center) - abs(height) * 0.5;
    float d_r_pos = max(d_radial, 0.0);
    float d_a_pos = max(d_axial, 0.0);
    return sqrt(d_r_pos * d_r_pos + d_a_pos * d_a_pos) + min(max(d_radial, d_axial), 0.0);
}
"""
```

Replace `to_glsl` (preserve all existing uniform registration; only swap
`need_helper` calls for `add_custom_helper`):

```python
def to_glsl(self, ctx, point_var="p"):
    ctx.add_custom_helper("sdf_cylinder", _GLSL_SDF_CYLINDER)
    bc = ctx.uniform("vec3", (self.base_center.x, self.base_center.y, self.base_center.z))
    ax = ctx.uniform("vec3", (self.axis.x, self.axis.y, self.axis.z))
    r  = ctx.uniform("float", self.radius)
    h  = ctx.uniform("float", self.height)
    if self.inv_matrix is not None:
        ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
        m = ctx.uniform("mat4", self.inv_matrix.tolist())
        return f"sdf_cylinder(apply_inv_mat({m}, {point_var}), {bc}, {ax}, {r}, {h})"
    return f"sdf_cylinder({point_var}, {bc}, {ax}, {r}, {h})"
```

**Depends on:** OPT-001

### OPT-005: Move `sdf_torus` GLSL into `torus.py`

**File:** `core/sdf/sdf/torus.py` — replace `to_glsl` at line 59

**What:** Define `_GLSL_SDF_TORUS` at module top.

**Implementation:**

Add after imports:

```python
from core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT

_GLSL_SDF_TORUS = """
float sdf_torus(vec3 p, vec3 center, float major_r, float tube_r) {
    vec3 lp = p - center;
    vec2 q = vec2(length(lp.xy) - major_r, lp.z);
    return length(q) - tube_r;
}
"""
```

Replace the body of `to_glsl` so that every `ctx.need_helper("sdf_torus")`
becomes `ctx.add_custom_helper("sdf_torus", _GLSL_SDF_TORUS)` and every
`ctx.need_helper("apply_inv_mat")` becomes
`ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)`. Leave all other
logic and uniform registration unchanged.

**Depends on:** OPT-001

### OPT-006: Move `sdf_plane` GLSL into `plane.py`

**File:** `core/sdf/sdf/plane.py` — replace `to_glsl` at line 14

**Implementation:**

Add after imports:

```python
_GLSL_SDF_PLANE = """
float sdf_plane(vec3 p, vec3 origin, vec3 normal) {
    return dot(p - origin, normal);
}
"""
```

Replace `to_glsl` so its first line becomes
`ctx.add_custom_helper("sdf_plane", _GLSL_SDF_PLANE)` and otherwise leave
the body unchanged.

**Depends on:** OPT-001

### OPT-007: Move `sdf_nurbs_curve` GLSL into `nurbs_curve.py`

**File:** `core/sdf/sdf/nurbs_curve.py` — replace `to_glsl` at line 86

**What:** Move the entire `evaluate_bspline` + `bspline_deriv` + `sdf_nurbs_curve`
GLSL body (currently `glsl_compiler.py:119-186`) into `core/sdf/sdf/nurbs_curve.py`
as a single module-level string `_GLSL_SDF_NURBS_CURVE`.

**Implementation:**

Add after imports in `nurbs_curve.py`:

```python
from core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT

_GLSL_SDF_NURBS_CURVE = """
vec3 evaluate_bspline(float t, vec3 poles[32], float knots[32], int degree, int n) {
    int k = degree;
    for (int i = degree; i < n; i++) {
        if (t >= knots[i]) k = i;
    }
    vec3 d[4];
    for (int i = 0; i <= 3; i++) {
        if (i <= degree) d[i] = poles[clamp(k - degree + i, 0, n-1)];
    }
    for (int r = 1; r <= 3; r++) {
        if (r > degree) break;
        for (int i = 3; i >= 1; i--) {
            if (i < r || i > degree) continue;
            float den = knots[k + 1 + i - r] - knots[k - degree + i];
            float alpha = (den > 1e-8) ? (t - knots[k - degree + i]) / den : 0.0;
            d[i] = mix(d[i-1], d[i], alpha);
        }
    }
    return d[clamp(degree, 0, 3)];
}

vec3 bspline_deriv(float t, vec3 poles[32], float knots[32], int degree, int n) {
    if (degree < 1) return vec3(0.0);
    int k = degree;
    for (int i = degree; i < n; i++) {
        if (t >= knots[i]) k = i;
    }
    vec3 d[4];
    for (int i = 0; i < degree; i++) {
        float den = knots[k - degree + i + degree + 1] - knots[k - degree + i + 1];
        float alpha = (den > 1e-8) ? float(degree) / den : 0.0;
        d[i] = (poles[k - degree + i + 1] - poles[k - degree + i]) * alpha;
    }
    int deg1 = degree - 1;
    for (int r = 1; r <= 2; r++) {
        if (r > deg1) break;
        for (int i = 2; i >= 1; i--) {
            if (i < r || i > deg1) continue;
            float den = knots[k + 1 + i - r] - knots[k - deg1 + i];
            float alpha = (den > 1e-8) ? (t - knots[k - deg1 + i]) / den : 0.0;
            d[i] = mix(d[i-1], d[i], alpha);
        }
    }
    return d[clamp(deg1, 0, 2)];
}

float sdf_nurbs_curve(vec3 p, vec3 poles[32], float knots[32], int degree, int n, float u0, float u1, float r) {
    float min_d2 = 1e18;
    float best_t = u0;
    for (int i = 0; i <= 16; i++) {
        float ut = u0 + (u1 - u0) * float(i) / 16.0;
        vec3 q = evaluate_bspline(ut, poles, knots, degree, n);
        float d2 = dot(p - q, p - q);
        if (d2 < min_d2) { min_d2 = d2; best_t = ut; }
    }
    float t = best_t;
    for (int i = 0; i < 4; i++) {
        vec3 q = evaluate_bspline(t, poles, knots, degree, n);
        vec3 dq = bspline_deriv(t, poles, knots, degree, n);
        float d2 = dot(dq, dq);
        if (d2 > 1e-8) t = clamp(t - dot(q - p, dq) / d2, u0, u1);
    }
    vec3 final_q = evaluate_bspline(t, poles, knots, degree, n);
    return length(p - final_q) - r;
}
"""
```

In `to_glsl`, replace `ctx.need_helper("apply_inv_mat")` with
`ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)` and
`ctx.need_helper("sdf_nurbs_curve")` with
`ctx.add_custom_helper("sdf_nurbs_curve", _GLSL_SDF_NURBS_CURVE)`. Leave all
other logic unchanged.

**Depends on:** OPT-001

### OPT-008: Move `sdf_nurbs_surface` GLSL into `nurbs_surface.py`

**File:** `core/sdf/sdf/nurbs_surface.py` — replace `to_glsl` at line 87

**What:** Same as OPT-007 but for the surface. Copy the
`evaluate_bspline_surf` + `sdf_nurbs_surface` GLSL bodies (currently
`glsl_compiler.py:188-265`) into a `_GLSL_SDF_NURBS_SURFACE` constant in
`core/sdf/sdf/nurbs_surface.py` and switch to `add_custom_helper`.

**Implementation:**

Define `_GLSL_SDF_NURBS_SURFACE` containing both `evaluate_bspline_surf` and
`sdf_nurbs_surface` exactly as currently in `glsl_compiler.py:188-265`. In
`to_glsl`, replace both `need_helper` calls with `add_custom_helper` calls
referencing `_GLSL_APPLY_INV_MAT` and `_GLSL_SDF_NURBS_SURFACE`.

**Depends on:** OPT-001

### OPT-009: Move `sdf_box2d` GLSL into `sdf2d/box.py`

**File:** `core/sdf/sdf2d/box.py` — replace `to_glsl_2d` at line 24

**Implementation:**

Add after imports:

```python
_GLSL_SDF_BOX2D = """
float sdf_box2d(vec2 p, vec2 h) {
    vec2 d = abs(p) - h;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}
"""
```

Replace `to_glsl_2d` body so its first line is
`ctx.add_custom_helper("sdf_box2d", _GLSL_SDF_BOX2D)`. Leave the rest
unchanged.

**Depends on:** OPT-001

### OPT-010: Move smooth-boolean GLSL into `sdf_composer.py`

**File:** `core/sdf/sdf_composer.py` — add module-level constants near the top, update three `to_glsl` methods

**What:** Define the three smooth-blend GLSL strings inline and switch
the three `to_glsl` methods (`SmoothUnionField:110`, `SmoothSubtractionField:136`,
`SmoothIntersectionField:167`) from `need_helper` to `add_custom_helper`.

**Implementation:**

Add after the existing imports near the top of the file:

```python
_GLSL_SMOOTH_UNION = """
float smooth_union(float a, float b, float k) {
    float h = clamp(0.5 + 0.5*(b-a)/k, 0.0, 1.0);
    return mix(b, a, h) - k*h*(1.0-h);
}
"""

_GLSL_SMOOTH_SUBTRACTION = """
float smooth_subtraction(float a, float b, float k) {
    float h = clamp(0.5 - 0.5*(a+b)/k, 0.0, 1.0);
    return mix(a, -b, h) + k*h*(1.0-h);
}
"""

_GLSL_SMOOTH_INTERSECTION = """
float smooth_intersection(float a, float b, float k) {
    float h = clamp(0.5 - 0.5*(b-a)/k, 0.0, 1.0);
    return mix(b, a, h) + k*h*(1.0-h);
}
"""
```

In each of the three classes' `to_glsl` methods, replace
`ctx.need_helper("smooth_union")` with
`ctx.add_custom_helper("smooth_union", _GLSL_SMOOTH_UNION)` (and matching
substitution/intersection variants). Leave all other body logic intact.

### OPT-011: Strip `glsl_compiler.py` to skeleton + remove `need_helper`

**File:** `core/sdf/glsl_compiler.py` — replace the entire file with the assembly-only version

**What:** Remove `GLSL_HELPERS` (lines 59-273), remove `GlslContext.need_helper`
(line 40-42), remove `GlslContext._helpers` set and the `helpers` property,
and simplify the helper-merging logic in `build_compute_shader` /
`build_multi_raymarch_fragment_shader` to iterate `ctx._custom_helpers`
directly. After this task the file is roughly 100 lines.

**Implementation:**

```python
"""
core/sdf/glsl_compiler.py

Assembles a compiled SDF field tree into a complete GLSL shader source.
The compiler does NOT contain primitive-specific GLSL — every primitive
registers its own helper bodies via `ctx.add_custom_helper(name, body)`.
"""

import uuid


class GlslContext:
    """Collects uniforms and helper functions during SDF→GLSL compilation."""

    def __init__(self, prefix=None):
        self._uniforms = []          # [(name, glsl_type, value)]
        self._counter = 0
        self._custom_helpers = {}    # name → GLSL function body string (insertion-ordered)
        self._helper_counter = 0
        if prefix is None:
            self._prefix = f"u_{uuid.uuid4().hex[:8]}_"
        else:
            clean_prefix = prefix.replace(".", "_").replace(" ", "_").replace("-", "_")
            self._prefix = f"u_{clean_prefix}_"

    def get_unique_name(self, base_name: str) -> str:
        name = f"{self._prefix}{base_name}_{self._helper_counter}"
        self._helper_counter += 1
        return name

    def uniform(self, glsl_type, value):
        name = f"{self._prefix}{self._counter}"
        self._counter += 1
        self._uniforms.append((name, glsl_type, value))
        return name

    def add_custom_helper(self, name: str, body: str):
        """Register a GLSL helper by name. Subsequent calls with the same
        name are no-ops (deduplication)."""
        if name not in self._custom_helpers:
            self._custom_helpers[name] = body

    @property
    def uniforms(self):
        return list(self._uniforms)


def compile_field_to_glsl(field, prefix=None):
    """Compile an SDF field tree to a GLSL expression.
    Returns: (glsl_expression: str, ctx: GlslContext)
    """
    ctx = GlslContext(prefix=prefix)
    expr = field.to_glsl(ctx, "p")
    return expr, ctx


def build_compute_shader(expression, ctx):
    """Assemble a complete compute shader source from a compiled SDF expression."""
    uniform_decls = "\n".join(
        f"uniform {glsl_type} {name};"
        for name, glsl_type, _ in ctx.uniforms
    )
    helper_defs = "\n".join(ctx._custom_helpers.values())
    return f"""#version 430
layout(local_size_x = 8, local_size_y = 8, local_size_z = 8) in;
layout(r32f, binding = 0) uniform image3D u_volume;

uniform vec3  u_grid_min;
uniform vec3  u_grid_step;
uniform ivec3 u_grid_count;
uniform int   u_z_offset;

{uniform_decls}

{helper_defs}

float sdf_eval(vec3 p) {{
    return {expression};
}}

void main() {{
    ivec3 gid = ivec3(gl_GlobalInvocationID);
    if (gid.x >= u_grid_count.x || gid.y >= u_grid_count.y || gid.z >= u_grid_count.z)
        return;

    vec3 p = u_grid_min + vec3(gid) * u_grid_step;
    float d = sdf_eval(p);

    imageStore(u_volume, ivec3(gid.x, gid.y, u_z_offset + gid.z), vec4(d, 0.0, 0.0, 0.0));
}}
"""


def build_multi_raymarch_fragment_shader(fields_data):
    """Build a complete fragment shader that evaluates multiple analytical SDF fields."""
    all_uniforms = []
    merged_helpers = {}
    for fd in fields_data:
        all_uniforms.extend(fd["ctx"].uniforms)
        for name, body in fd["ctx"]._custom_helpers.items():
            if name not in merged_helpers:
                merged_helpers[name] = body

    seen_uniforms = set()
    uniform_decls_list = []
    for name, glsl_type, _ in all_uniforms:
        if name not in seen_uniforms:
            uniform_decls_list.append(f"uniform {glsl_type} {name};")
            seen_uniforms.add(name)
    uniform_decls = "\n".join(uniform_decls_list)
    helper_defs = "\n".join(merged_helpers.values())

    eval_funcs = ""
    for i, fd in enumerate(fields_data):
        eval_funcs += f"""
float sdf_eval_{i}(vec3 p) {{
    return {fd['expr']};
}}

vec3 sdf_normal_{i}(vec3 p) {{
    float h = 1.0;
    vec2 k = vec2(1.0, -1.0);
    vec3 n = k.xyy * sdf_eval_{i}(p + k.xyy*h)
           + k.yyx * sdf_eval_{i}(p + k.yyx*h)
           + k.yxy * sdf_eval_{i}(p + k.yxy*h)
           + k.xxx * sdf_eval_{i}(p + k.xxx*h);
    float len2 = dot(n, n);
    return (len2 > 1e-10) ? n * inversesqrt(len2) : vec3(0.0, 0.0, 1.0);
}}
"""

    num_fields = len(fields_data)

    field_bboxes = ""
    for i, fd in enumerate(fields_data):
        bmin = fd["bbox_min"]
        bmax = fd["bbox_max"]
        field_bboxes += f"    vec3 bmin_{i} = vec3({bmin.x}, {bmin.y}, {bmin.z});\n"
        field_bboxes += f"    vec3 bmax_{i} = vec3({bmax.x}, {bmax.y}, {bmax.z});\n"

    intersect_bboxes = ""
    for i in range(num_fields):
        intersect_bboxes += f"    vec2 fi_int_{i} = intersect_aabb(ro, rd, bmin_{i}, bmax_{i});\n"
        intersect_bboxes += f"    ftn[{i}] = max(fi_int_{i}.x, 0.0);\n"
        intersect_bboxes += f"    ftf[{i}] = min(fi_int_{i}.y, ray_tmax);\n"

    sample_loop = ""
    for i, fd in enumerate(fields_data):
        sample_loop += f"""
        if (t >= ftn[{i}] && t <= ftf[{i}]) {{
            float d_{i} = sdf_eval_{i}(p);
            if (abs(d_{i}) < 0.005) {{ hit = true; hit_field = {i}; break; }}
            min_d = min(min_d, abs(d_{i}));
        }} else if (t < ftn[{i}]) {{
            min_d = min(min_d, ftn[{i}] - t);
        }}
"""

    normal_switch = "    vec3 n = vec3(0, 0, 1);\n"
    for i in range(num_fields):
        normal_switch += f"    if (hit_field == {i}) n = sdf_normal_{i}(hp);\n"

    color_switch = "    vec3 base_color = vec3(1.0, 0.5, 0.0);\n"
    for i, fd in enumerate(fields_data):
        color = "vec3(0.3, 0.5, 1.0)" if fd["is_subtractive"] else "vec3(1.0, 0.5, 0.0)"
        color_switch += f"    if (hit_field == {i}) base_color = {color};\n"

    scene_bbox_lines = "\n".join(
        f"    scene_min = min(scene_min, bmin_{i});\n    scene_max = max(scene_max, bmax_{i});"
        for i in range(1, num_fields)
    )

    return f"""#version 330 compatibility
in vec2 v_uv;

uniform vec3 u_light_dir;

{uniform_decls}

layout(location = 0) out vec4 out_color;
layout(location = 1) out vec4 out_vspos;
layout(location = 2) out vec4 out_vsnorm;

{helper_defs}

{eval_funcs}

vec2 intersect_aabb(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {{
    vec3 t1 = (bmin - ro) / rd;
    vec3 t2 = (bmax - ro) / rd;
    vec3 tmin_ = min(t1, t2);
    vec3 tmax_ = max(t1, t2);
    return vec2(max(max(tmin_.x, tmin_.y), tmin_.z),
                min(min(tmax_.x, tmax_.y), tmax_.z));
}}

void main() {{
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;
    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;

    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    float ray_tmax = length(world_far.xyz - world_near.xyz);

{field_bboxes}

    vec3 scene_min = bmin_0;
    vec3 scene_max = bmax_0;
{scene_bbox_lines}
    vec2 tBox = intersect_aabb(ro, rd, scene_min, scene_max);
    float tNear = max(tBox.x, 0.0);
    float tFar  = min(tBox.y, ray_tmax);

    if (tNear > tFar) {{
        out_color = vec4(0.0); out_vspos = vec4(0.0); out_vsnorm = vec4(0.0);
        return;
    }}

    float ftn[{num_fields}]; float ftf[{num_fields}];
{intersect_bboxes}

    float t = tNear;
    bool hit = false;
    int hit_field = 0;

    for (int i = 0; i < 512; i++) {{
        vec3 p = ro + t * rd;
        float min_d = 1.0e10;

{sample_loop}

        if (hit) break;
        t += max(min_d * 0.9, 0.005);
        if (t > tFar) break;
    }}

    if (!hit) {{
        out_color = vec4(0.0); out_vspos = vec4(0.0); out_vsnorm = vec4(0.0);
        return;
    }}

    vec3 hp = ro + t * rd;
{normal_switch}
    vec3 vd = normalize(-rd);

    float diff = max(dot(n, u_light_dir), 0.0);
    float spec = pow(max(dot(reflect(-u_light_dir, n), vd), 0.0), 32.0);

{color_switch}
    vec3 color = base_color * (0.25 + 0.70 * diff) + vec3(0.3) * spec;

    vec4 vs   = gl_ModelViewMatrix * vec4(hp, 1.0);
    vec3 vs_n = normalize(mat3(gl_ModelViewMatrix) * n);

    out_color  = vec4(color, 1.0);
    out_vspos  = vec4(vs.xyz, 1.0);
    out_vsnorm = vec4(vs_n,   1.0);

    vec4 clip   = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z = clip.z / clip.w;
    gl_FragDepth = gl_DepthRange.near + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
}}
"""
```

**Depends on:** OPT-002, OPT-003, OPT-004, OPT-005, OPT-006, OPT-007, OPT-008, OPT-009, OPT-010

### OPT-012: Smoke-test the refactor

**File:** none (manual verification step)

**What:** Open FreeCAD, create one of each: Box, Sphere, Cylinder, Torus,
Plane, NURBS curve extrusion, Bezier curve extrusion, smooth union of two
spheres. Verify each one renders identically to before (same shape, same
shading). Check the FreeCAD console for any "GLSL compile failed" errors.

**Implementation:**
1. Launch FreeCAD with the workbench loaded.
2. From the DM toolbar create one Box, Sphere, Cylinder, Torus.
3. Create a closed Bezier curve via the curve tool, extrude it.
4. Create a smooth union (two overlapping spheres + smooth union command if available).
5. Confirm no shader compile errors in the FreeCAD report view.
6. Confirm objects render with the same color/shading as before the refactor.

**Depends on:** OPT-011

---

## Tier 2 — SVG Path Importer (No Rasterization)

Adds an SVG → `Sdf2dBezierCurve` importer. Every SVG path becomes one or
more closed cubic-Bezier subpaths, with all primitive shapes (`<rect>`,
`<circle>`, `<ellipse>`, `<line>`, `<polyline>`, `<polygon>`) converted to
cubic Beziers analytically. No rasterization. No texture bake.

### OPT-020: Create `core/sdf/sdf2d/svg_importer.py` skeleton

**File:** `core/sdf/sdf2d/svg_importer.py` — new file

**What:** Create the importer module with the public API surface and the
SVG path tokenizer. Path-command handlers are stubbed; later tasks fill
them in.

**Implementation:**

```python
"""
core/sdf/sdf2d/svg_importer.py

Parses an SVG file (or string) into a list of Sdf2dBezierCurve instances.
No rasterization — all primitives are converted to cubic Bezier segments
analytically. Supported elements: <path>, <rect>, <circle>, <ellipse>,
<line>, <polyline>, <polygon>. CSS, gradients, transforms, and groups
are NOT yet supported (only top-level shapes with no transform).

Public API:
    parse_svg(source)   -> list[Sdf2dBezierCurve]
    parse_path_d(d_str) -> list[list[(p0, p1, p2, p3)]]    # one list per subpath
"""

import re
import math
import xml.etree.ElementTree as ET
from .bezier_curve import Sdf2dBezierCurve

_CMD_RE  = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])")
_NUM_RE  = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")


def _tokenize_path(d: str):
    """Split an SVG path 'd' attribute into [(cmd_letter, [floats]), ...]."""
    tokens = []
    parts = _CMD_RE.split(d)
    i = 1
    while i < len(parts):
        cmd = parts[i]
        args = parts[i + 1] if i + 1 < len(parts) else ""
        nums = [float(m.group(0)) for m in _NUM_RE.finditer(args)]
        tokens.append((cmd, nums))
        i += 2
    return tokens


def parse_path_d(d: str):
    """Convert one SVG 'd' attribute into subpaths of cubic Beziers.
    Returns: list[list[(p0, p1, p2, p3)]] — outer list = subpaths, inner = segments.
    Each point is a (x, y) tuple of floats. Open subpaths are closed by appending
    a straight-line cubic Bezier from end → start.
    """
    raise NotImplementedError("filled in by OPT-021..OPT-025")


def parse_svg(source):
    """Parse an SVG file path or XML string into a list of Sdf2dBezierCurve.
    Each closed subpath becomes one Sdf2dBezierCurve.
    """
    raise NotImplementedError("filled in by OPT-026")
```

### OPT-021: Implement straight-line path commands (M, L, H, V, Z)

**File:** `core/sdf/sdf2d/svg_importer.py` — replace the body of `parse_path_d`

**What:** Implement subpath state-machine for `M m L l H h V v Z z`. A line
segment becomes a degenerate cubic Bezier:
`(p0, p0+(p3-p0)/3, p0+2*(p3-p0)/3, p3)`. Cubic, smooth-cubic, quadratic,
smooth-quadratic, and arc commands are still NotImplementedError; later
tasks add them.

**Implementation:**

The SVG path-data state has: current point `cp`, subpath start `sp`,
last control point `prev_ctrl` (for `S`/`T`), and a list of finished
subpaths plus the current subpath being built. Lower-case commands are
relative to `cp`; upper-case are absolute.

```python
def _line_as_cubic(p0, p1):
    return (
        p0,
        (p0[0] + (p1[0] - p0[0]) / 3.0, p0[1] + (p1[1] - p0[1]) / 3.0),
        (p0[0] + 2 * (p1[0] - p0[0]) / 3.0, p0[1] + 2 * (p1[1] - p0[1]) / 3.0),
        p1,
    )


def parse_path_d(d: str):
    tokens = _tokenize_path(d)
    subpaths = []
    current = []
    cp = (0.0, 0.0)
    sp = (0.0, 0.0)

    def _close_subpath():
        nonlocal current, cp
        if current:
            # Close with a line from current end → subpath start, if not coincident
            end = current[-1][3]
            if abs(end[0] - sp[0]) > 1e-9 or abs(end[1] - sp[1]) > 1e-9:
                current.append(_line_as_cubic(end, sp))
            subpaths.append(current)
        current = []
        cp = sp

    i = 0
    while i < len(tokens):
        cmd, nums = tokens[i]
        i += 1
        rel = cmd.islower()

        if cmd in ("M", "m"):
            # First pair = moveto, subsequent pairs = implicit lineto
            j = 0
            first = True
            while j < len(nums):
                x = nums[j]; y = nums[j + 1]; j += 2
                if rel and not (first and not current and not subpaths):
                    x += cp[0]; y += cp[1]
                if first:
                    if current:
                        subpaths.append(current); current = []
                    cp = (x, y); sp = cp
                    first = False
                else:
                    new = (x, y)
                    current.append(_line_as_cubic(cp, new))
                    cp = new
        elif cmd in ("L", "l"):
            j = 0
            while j < len(nums):
                x = nums[j]; y = nums[j + 1]; j += 2
                if rel: x += cp[0]; y += cp[1]
                new = (x, y)
                current.append(_line_as_cubic(cp, new))
                cp = new
        elif cmd in ("H", "h"):
            for x in nums:
                xa = x + cp[0] if rel else x
                new = (xa, cp[1])
                current.append(_line_as_cubic(cp, new))
                cp = new
        elif cmd in ("V", "v"):
            for y in nums:
                ya = y + cp[1] if rel else y
                new = (cp[0], ya)
                current.append(_line_as_cubic(cp, new))
                cp = new
        elif cmd in ("Z", "z"):
            _close_subpath()
        elif cmd in ("C", "c", "S", "s", "Q", "q", "T", "t", "A", "a"):
            # Filled in by OPT-022..OPT-025
            raise NotImplementedError(f"path command '{cmd}' not yet supported")
        else:
            raise ValueError(f"unknown SVG path command: {cmd}")

    if current:
        subpaths.append(current)
    return subpaths
```

**Depends on:** OPT-020

### OPT-022: Implement cubic Bezier commands (C, c, S, s)

**File:** `core/sdf/sdf2d/svg_importer.py` — extend `parse_path_d`

**What:** Replace the `cmd in ("C", "c", "S", "s", ...)` block with handlers
for `C`/`c` (full cubic) and `S`/`s` (smooth-cubic, where the first control
point is the reflection of the previous segment's last control point).

**Implementation:**

State must now track `prev_ctrl` — the last cubic control point of the most
recently emitted cubic segment, used by `S`/`s`. Initialize to `None` and
reset to `None` on any non-cubic command.

```python
        elif cmd in ("C", "c"):
            j = 0
            while j + 5 < len(nums):
                x1, y1, x2, y2, x, y = nums[j:j+6]; j += 6
                if rel:
                    x1 += cp[0]; y1 += cp[1]
                    x2 += cp[0]; y2 += cp[1]
                    x  += cp[0]; y  += cp[1]
                seg = (cp, (x1, y1), (x2, y2), (x, y))
                current.append(seg)
                prev_ctrl = (x2, y2)
                cp = (x, y)
        elif cmd in ("S", "s"):
            j = 0
            while j + 3 < len(nums):
                x2, y2, x, y = nums[j:j+4]; j += 4
                if rel:
                    x2 += cp[0]; y2 += cp[1]
                    x  += cp[0]; y  += cp[1]
                if prev_ctrl is not None:
                    x1 = 2 * cp[0] - prev_ctrl[0]
                    y1 = 2 * cp[1] - prev_ctrl[1]
                else:
                    x1, y1 = cp
                seg = (cp, (x1, y1), (x2, y2), (x, y))
                current.append(seg)
                prev_ctrl = (x2, y2)
                cp = (x, y)
```

In every other branch (M, L, H, V, Q, T, A, Z), set `prev_ctrl = None`
before exiting the branch. Add `prev_ctrl = None` next to the `cp = (0.0, 0.0)`
initializer.

**Depends on:** OPT-021

### OPT-023: Implement quadratic Bezier commands (Q, q, T, t)

**File:** `core/sdf/sdf2d/svg_importer.py` — extend `parse_path_d`

**What:** Quadratic Bezier `(P0, P1, P2)` lifts to cubic exactly as
`(P0, P0 + 2/3*(P1-P0), P2 + 2/3*(P1-P2), P2)`. Implement `Q`/`q` (full
quad) and `T`/`t` (smooth quad: implicit P1 is the reflection of the
previous segment's quad control point about the current point).

**Implementation:**

State must track `prev_q_ctrl` (the quadratic control point of the most
recent Q/q/T/t segment) for use by `T`/`t`. Reset to `None` on any
non-quadratic command.

```python
def _quad_to_cubic(p0, p1, p2):
    return (
        p0,
        (p0[0] + 2.0 / 3.0 * (p1[0] - p0[0]), p0[1] + 2.0 / 3.0 * (p1[1] - p0[1])),
        (p2[0] + 2.0 / 3.0 * (p1[0] - p2[0]), p2[1] + 2.0 / 3.0 * (p1[1] - p2[1])),
        p2,
    )

# Inside parse_path_d:
        elif cmd in ("Q", "q"):
            j = 0
            while j + 3 < len(nums):
                x1, y1, x, y = nums[j:j+4]; j += 4
                if rel:
                    x1 += cp[0]; y1 += cp[1]
                    x  += cp[0]; y  += cp[1]
                qctrl = (x1, y1)
                current.append(_quad_to_cubic(cp, qctrl, (x, y)))
                prev_q_ctrl = qctrl
                cp = (x, y)
        elif cmd in ("T", "t"):
            j = 0
            while j + 1 < len(nums):
                x, y = nums[j:j+2]; j += 2
                if rel: x += cp[0]; y += cp[1]
                if prev_q_ctrl is not None:
                    qctrl = (2 * cp[0] - prev_q_ctrl[0], 2 * cp[1] - prev_q_ctrl[1])
                else:
                    qctrl = cp
                current.append(_quad_to_cubic(cp, qctrl, (x, y)))
                prev_q_ctrl = qctrl
                cp = (x, y)
```

Reset `prev_q_ctrl = None` in every non-Q/T branch. Initialize alongside
`prev_ctrl`.

**Depends on:** OPT-022

### OPT-024: Implement arc commands (A, a) — endpoint-to-center conversion + cubic approx

**File:** `core/sdf/sdf2d/svg_importer.py` — extend `parse_path_d`, add helper functions

**What:** SVG arcs are given by (rx, ry, x_axis_rot_deg, large_arc_flag,
sweep_flag, x, y). Convert to center parameterization (per SVG 1.1
implementation notes B.2.4), then split the swept angle into cubic
Bezier segments where each segment covers ≤ π/2 of arc. Use the standard
approximation: for an arc on a unit circle from angle θ₁ to θ₂ (with
Δθ = θ₂-θ₁ ≤ π/2), the cubic control points are
`P0 = (cos θ₁, sin θ₁)`, `P1 = P0 + α·(-sin θ₁, cos θ₁)`,
`P2 = P3 - α·(-sin θ₂, cos θ₂)`, `P3 = (cos θ₂, sin θ₂)`,
with `α = (4/3) · tan(Δθ/4)`. Then apply the ellipse scaling (rx, ry),
the x-axis rotation, and translate by the ellipse center.

**Implementation:**

See `.agents/skills/dm_svg_path_to_bezier/SKILL.md` for the full algorithm
including endpoint→center conversion and degenerate cases (rx=0, ry=0,
endpoint coincident with start). The skill provides a complete reference
implementation; copy it into `svg_importer.py` and wire it into
`parse_path_d`:

```python
        elif cmd in ("A", "a"):
            j = 0
            while j + 6 < len(nums):
                rx, ry, x_rot_deg, large_arc, sweep, x, y = nums[j:j+7]; j += 7
                if rel: x += cp[0]; y += cp[1]
                segs = _arc_to_cubics(cp, (x, y), rx, ry, x_rot_deg,
                                      bool(int(large_arc)), bool(int(sweep)))
                current.extend(segs)
                cp = (x, y)
```

The `_arc_to_cubics` helper is defined in the skill.

**Depends on:** OPT-023

### OPT-025: Wire `parse_svg()` to traverse XML and dispatch by tag

**File:** `core/sdf/sdf2d/svg_importer.py` — replace `parse_svg`

**What:** Walk the SVG tree, collect `<path>`, `<rect>`, `<circle>`,
`<ellipse>`, `<line>`, `<polyline>`, `<polygon>` elements (ignoring
`<defs>`, `<g>`, attributes other than `d`/the geometry attributes for
each shape). For each element produce subpaths, then convert each closed
subpath into one `Sdf2dBezierCurve`. Open subpaths are closed by a final
straight-line cubic from end→start (already handled in `parse_path_d`).

**Implementation:**

```python
SVG_NS = "{http://www.w3.org/2000/svg}"


def _shape_to_subpaths(elem):
    """Convert one SVG shape element to a list of subpaths
    (each subpath = list of cubic Bezier tuples)."""
    tag = elem.tag.replace(SVG_NS, "")
    if tag == "path":
        d = elem.get("d", "")
        return parse_path_d(d) if d else []
    if tag == "rect":
        x = float(elem.get("x", 0)); y = float(elem.get("y", 0))
        w = float(elem.get("width", 0)); h = float(elem.get("height", 0))
        if w <= 0 or h <= 0: return []
        d = f"M {x} {y} h {w} v {h} h {-w} z"
        return parse_path_d(d)
    if tag == "circle":
        cx = float(elem.get("cx", 0)); cy = float(elem.get("cy", 0))
        r  = float(elem.get("r", 0))
        if r <= 0: return []
        d = f"M {cx-r} {cy} a {r} {r} 0 1 0 {2*r} 0 a {r} {r} 0 1 0 {-2*r} 0 z"
        return parse_path_d(d)
    if tag == "ellipse":
        cx = float(elem.get("cx", 0)); cy = float(elem.get("cy", 0))
        rx = float(elem.get("rx", 0)); ry = float(elem.get("ry", 0))
        if rx <= 0 or ry <= 0: return []
        d = f"M {cx-rx} {cy} a {rx} {ry} 0 1 0 {2*rx} 0 a {rx} {ry} 0 1 0 {-2*rx} 0 z"
        return parse_path_d(d)
    if tag == "line":
        x1 = float(elem.get("x1", 0)); y1 = float(elem.get("y1", 0))
        x2 = float(elem.get("x2", 0)); y2 = float(elem.get("y2", 0))
        d = f"M {x1} {y1} L {x2} {y2}"
        return parse_path_d(d)
    if tag in ("polyline", "polygon"):
        pts = re.findall(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?",
                         elem.get("points", ""))
        if len(pts) < 4: return []
        d = "M " + " ".join(pts[:2]) + " L " + " ".join(pts[2:])
        if tag == "polygon": d += " Z"
        return parse_path_d(d)
    return []


def parse_svg(source):
    """Parse an SVG file path, file-like, or XML string into a list of Sdf2dBezierCurve.
    Each closed subpath becomes one Sdf2dBezierCurve."""
    if isinstance(source, str) and source.lstrip().startswith("<"):
        root = ET.fromstring(source)
    else:
        root = ET.parse(source).getroot()

    curves = []
    for elem in root.iter():
        subpaths = _shape_to_subpaths(elem)
        for sp in subpaths:
            if len(sp) >= 1:
                curves.append(Sdf2dBezierCurve(sp))
    return curves
```

**Note on Y axis:** SVG Y points down; FreeCAD profiles use Y up. The
caller (OPT-026) decides whether to flip Y. By convention in this importer,
we leave Y as-is — coordinate flipping happens at the import command level.

**Depends on:** OPT-024

### OPT-026: Add `Import SVG as Curve` command

**File:** `commands/cmd_import_svg.py` — new file

**What:** A FreeCAD command that opens a file dialog, parses the SVG with
`parse_svg`, flips the Y axis (SVG → FreeCAD convention), and creates one
DM curve object per imported `Sdf2dBezierCurve`. The created object stores
the curve segments so the existing curve-tool edit flow works on it.

**Implementation:**

```python
import FreeCAD
import FreeCADGui
from PySide import QtGui


class CommandDMImportSVG:
    def GetResources(self):
        return {
            'Pixmap': 'CreateCurve',  # reuse curve icon for now
            'MenuText': 'Import SVG as Curve',
            'ToolTip': 'Import an SVG file as one or more 2D bezier curves (no rasterization).'
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        path, _ = QtGui.QFileDialog.getOpenFileName(
            None, "Import SVG", "", "SVG Files (*.svg)"
        )
        if not path:
            return

        from core.sdf.sdf2d.svg_importer import parse_svg
        from core.dm_object import create_dm_object

        try:
            curves = parse_svg(path)
        except Exception as e:
            QtGui.QMessageBox.critical(None, "Import SVG", f"Parse failed: {e}")
            return

        if not curves:
            QtGui.QMessageBox.warning(None, "Import SVG", "No shapes found in SVG.")
            return

        # SVG Y points down; FreeCAD profiles assume Y up.
        flipped = []
        for c in curves:
            new_segs = [
                tuple((p[0], -p[1]) for p in seg)
                for seg in c.segments
            ]
            from core.sdf.sdf2d.bezier_curve import Sdf2dBezierCurve
            flipped.append(Sdf2dBezierCurve(new_segs))

        for i, curve in enumerate(flipped):
            obj = create_dm_object(name=f'SvgCurve_{i+1}', shape_type='curve')
            # Convert curve.segments → (Points, HandleIn, HandleOut) form expected by DM curve.
            # NOTE: The DM curve object stores N control points + N handle-in + N handle-out.
            # Each Bezier segment k uses (Points[k], HandleOut[k], HandleIn[k+1], Points[k+1]).
            n_segs = len(curve.segments)
            pts, hi, ho = [], [], []
            for k, seg in enumerate(curve.segments):
                p0, p1, p2, p3 = seg
                if k == 0:
                    pts.append(FreeCAD.Vector(p0[0], p0[1], 0.0))
                    hi.append(FreeCAD.Vector(p0[0], p0[1], 0.0))
                ho.append(FreeCAD.Vector(p1[0], p1[1], 0.0))
                pts.append(FreeCAD.Vector(p3[0], p3[1], 0.0))
                hi.append(FreeCAD.Vector(p2[0], p2[1], 0.0))
            # Last point's handle-out is itself (curve is closed by repeating the start).
            ho.append(FreeCAD.Vector(pts[-1].x, pts[-1].y, 0.0))

            obj.Points = pts
            obj.HandleIn = hi
            obj.HandleOut = ho
            obj.Closed = True
            obj.touch()

        FreeCAD.activeDocument().recompute()


FreeCADGui.addCommand('DM_ImportSVG', CommandDMImportSVG())
```

**Note:** The handle indexing here mirrors the convention used in
`tools/curve_tool.py:_get_auto_handles` — verify against that file when
implementing.

**Depends on:** OPT-025

### OPT-027: Register `DM_ImportSVG` command in `InitGui.py`

**File:** `InitGui.py` — find the workbench's `Initialize` method (where other commands are imported and added to the menu/toolbar)

**What:** Import `commands.cmd_import_svg` and add `'DM_ImportSVG'` to the
appropriate command list (likely the curve toolbar or a new "Import" group).

**Implementation:**

```python
# In Initialize(), in the same block where other curve commands are imported:
import commands.cmd_import_svg  # noqa: F401  (registers DM_ImportSVG)

# In the toolbar/menu definition, append "DM_ImportSVG" to the curve commands list.
```

**Depends on:** OPT-026

---

## Tier 3 — Curve Render Performance (No Rasterization)

Reduces per-pixel cost of the analytical Bezier-curve extrusion path
without baking to a texture. All optimizations preserve exact analytical
SDF accuracy.

### OPT-030: Per-segment 2D AABB cull in `Sdf2dBezierCurve.to_glsl_2d`

**File:** `core/sdf/sdf2d/bezier_curve.py` — modify `to_glsl_2d` at line 202

**What:** Today every ray-march step calls `sd_cubic_bez_2d` for every
segment. Pre-compute each segment's 2D AABB (control-polygon bounds, which
are conservative for cubic Beziers) at GLSL emission time, and skip the
distance-and-winding evaluation when the query point's distance to the
expanded AABB is larger than the running minimum. The winding contribution
must still be evaluated for any segment whose Y-range straddles the query
point (otherwise the winding number is wrong); only the expensive
`sd_cubic_bez_2d` distance call is skipped.

**Implementation:**

```python
def to_glsl_2d(self, ctx, pvar: str = "q") -> str:
    ctx.add_custom_helper("sd_solve_cubic", _GLSL_SOLVE_CUBIC)
    ctx.add_custom_helper("sd_cubic_bez_2d", _GLSL_CUBIC_BEZ_2D)
    ctx.add_custom_helper("sd_cubic_winding", _GLSL_CUBIC_WINDING)

    func_name = ctx.get_unique_name("sdf_bezier")

    lines = [f"float {func_name}(vec2 p) {{"]
    lines.append("    float d = 1e18;")
    lines.append("    int w = 0;")
    lines.append("    float d_aabb;")

    for k, (p0, p1, p2, p3) in enumerate(self.segments):
        # Control-polygon AABB (a strict superset of the curve AABB)
        xs = [p0[0], p1[0], p2[0], p3[0]]
        ys = [p0[1], p1[1], p2[1], p3[1]]
        bx0, bx1 = min(xs), max(xs)
        by0, by1 = min(ys), max(ys)
        lines.append(
            f"    {{ vec2 a{k}=vec2({p0[0]},{p0[1]}), b{k}=vec2({p1[0]},{p1[1]}),"
            f" c{k}=vec2({p2[0]},{p2[1]}), d{k}=vec2({p3[0]},{p3[1]});"
        )
        # Distance from p to AABB; if > current min d, skip the expensive call.
        lines.append(
            f"      vec2 cl{k} = clamp(p, vec2({bx0},{by0}), vec2({bx1},{by1}));"
        )
        lines.append(f"      d_aabb = length(p - cl{k});")
        lines.append(f"      if (d_aabb < d) d = min(d, sd_cubic_bez_2d(p, a{k}, b{k}, c{k}, d{k}));")
        # Winding must still be checked when the segment's Y-range crosses p.y
        lines.append(f"      if (p.y >= {by0} - 1e-6 && p.y <= {by1} + 1e-6)")
        lines.append(f"          w += sd_cubic_winding(p, a{k}, b{k}, c{k}, d{k}); }}")

    lines.append("    return (w == 0 ? 1.0 : -1.0) * d;")
    lines.append("}")

    ctx.add_custom_helper(func_name, "\n".join(lines))
    return f"{func_name}({pvar})"
```

**Depends on:** OPT-001 (uses `add_custom_helper` already in place)

### OPT-031: Reduce Newton iterations in `_GLSL_CUBIC_BEZ_2D`

**File:** `core/sdf/sdf2d/bezier_curve.py` — modify `_GLSL_CUBIC_BEZ_2D` at line 52

**What:** The current loop does 9 outer + 4 Newton inner iterations per
segment. Profiling indicates the inner Newton converges in 2 iterations
for any seed within the outer 1/8 spacing; the outer step count is the
expensive driver. Change `i <= 8` (9 outer) to `i <= 6` (7 outer) and
`j < 4` (4 Newton) to `j < 2` (2 Newton). The hit-threshold in the
ray-march loop (0.005 units) is loose enough that the residual error
from this is invisible at any normal viewing scale.

**Implementation:**

Replace the body of `_GLSL_CUBIC_BEZ_2D`:

```python
_GLSL_CUBIC_BEZ_2D = """
float sd_cubic_bez_2d(vec2 p, vec2 a, vec2 b, vec2 c, vec2 d) {
    float md = 1e18;
    for (int i = 0; i <= 6; i++) {
        float t = float(i) / 6.0;
        for (int j = 0; j < 2; j++) {
            float s = 1.0 - t;
            vec2 B  = s*s*s*a + 3.0*s*s*t*b + 3.0*s*t*t*c + t*t*t*d;
            vec2 dB = 3.0*(s*s*(b-a) + 2.0*s*t*(c-b) + t*t*(d-c));
            float dBdB = dot(dB, dB);
            if (dBdB > 1e-10) t = clamp(t - dot(B-p, dB)/dBdB, 0.0, 1.0);
        }
        float s = 1.0 - t;
        vec2 B = s*s*s*a + 3.0*s*s*t*b + 3.0*s*t*t*c + t*t*t*d;
        md = min(md, dot(B-p, B-p));
    }
    return sqrt(md);
}
"""
```

### OPT-032: Half-resolution G-buffer during interactive drag

**File:** `core/dm_scene_ray_march_renderer.py` — modify `_render_gl_callback` at line 638 and `_resize_fbos` at line 622

**What:** During an active tool drag (any mouse button held + tool
active), render the G-buffer FBO at half resolution (W/2 × H/2). Pass 4
(composite) already up-samples via the bilinear sampler — no shader
changes needed. When the mouse is released, snap back to full resolution
on the next frame. Quality is acceptable because (a) SSAO + blur already
soften high-frequency detail, (b) the user is moving so detail is masked,
(c) full-res returns immediately on idle.

**Implementation:**

Add an `_interactive` flag to the renderer (defaults to `False`). When
true, all FBOs are sized at half. The flag is updated by checking
`QApplication.mouseButtons() != Qt.NoButton` at the top of
`_render_gl_callback`.

```python
# Top of _render_gl_callback, after the existing "if not action.isOfType(...)" guard:
from PySide.QtWidgets import QApplication
from PySide.QtCore import Qt
interactive = bool(QApplication.mouseButtons() & Qt.LeftButton)

# Replace the existing FBO size block:
target_w = max(w // 2, 1) if interactive else w
target_h = max(h // 2, 1) if interactive else h
size_changed = (target_w, target_h) != self._vp_size
if size_changed:
    try:
        self._resize_fbos(target_w, target_h)
        self._vp_size = (target_w, target_h)
        _force_full = True
    except Exception as e:
        dm_logger.debug(f"SceneRayMarch: _resize_fbos failed: {e}")
        return

# In Pass 1, 2, 3: glViewport(0, 0, target_w, target_h) instead of (0, 0, w, h).
# In Pass 4 (composite): glViewport(0, 0, w, h) — composite samples FBO at fullscreen.
```

**Caveat:** The existing FBO resize already triggers `_force_full` on
size change, so toggling between full and half resolution is automatic.
But each toggle pays a one-frame cost; the code is correct as long as
the toggle is sticky (i.e. only flips when the mouse button state
changes).

### OPT-033: Smoke-test perf on curve-extrusion drag

**File:** none (manual verification step)

**What:** Reproduce the original session: open the workbench, draw a
closed Bezier curve with ≥6 control points, run the Curve Extrude tool,
drag the height handle. Compare Pass1 timings in the FreeCAD console
with the values from the 2026-05-06 session log (28-145 ms). After
OPT-030 + OPT-031 + OPT-032 we expect:
- Idle Pass1: ≤ 50% of the previous idle cost (AABB cull + Newton drop).
- Drag Pass1: ≤ 25% of the previous drag cost (the half-res FBO is
  4× cheaper for the ray-march pixel count).

**Implementation:**
1. Run the same scenario as the session log: closed curve + extrude tool + drag height.
2. Note Pass1 timings in console output (`Render frame ... Pass1=Xms`).
3. Confirm visual quality: the extruded shape should look identical at idle and only mildly softened during drag.

**Depends on:** OPT-030, OPT-031, OPT-032

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_glsl_helper_pattern` | New convention for primitive GLSL helpers (required for OPT-002..OPT-011) |
| `dm_svg_path_to_bezier` | Full SVG path → cubic Bezier conversion incl. arc decomposition (required for OPT-021..OPT-025) |
| `dm_sdf_primitive_pattern` | Existing — primitive file template (still applies, with the new GLSL pattern) |
| `dm_todo_format` | Existing — todo file format reference |
