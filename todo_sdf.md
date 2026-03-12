# Direct Modeling Workbench — Formulaic SDF Rendering Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The Direct Modeling workbench represents 3D shapes as F-Rep Signed Distance Fields (SDFs).
Currently, SDF rendering uses a voxel-based approach: `sdf_baker.py` samples the SDF on a
uniform grid, quantizes to uint8, tiles z-slices into a 2D atlas, and uploads as a GPU
texture. A GLSL fragment shader trilinear-interpolates the atlas and sphere-traces to
ray march the surface.

**Problem:** The uint8 quantization limits accuracy to ~0.031mm per step (at cell_size=1mm).
The grid resolution loses detail between samples. This is insufficient for CNC toolpath
generation which requires µm-level fidelity.

**Solution:** Compile the FRep expression tree (box, sphere, union, etc.) directly into
GLSL code. The fragment shader evaluates the *exact analytical* SDF formula at every pixel.
Zero quantization, zero resolution loss, zero bake time. The same mathematical formula
used for rendering becomes the source of truth for CNC toolpaths.

### Architecture

```
FRepField tree              GLSL Assembler              Fragment Shader
┌──────────┐                ┌──────────────┐           ┌──────────────────┐
│ Union     │   to_glsl()   │ walk tree,   │  source   │ float sdf(vec3 p)│
│ ├─ Box    │──────────────→│ emit funcs + │──────────→│   return min(    │
│ └─ Sphere │               │ uniforms     │           │     box(p),      │
└──────────┘                └──────────────┘           │     sphere(p));  │
                                                       └──────────────────┘
```

Each FRepField subclass implements `to_glsl(node_id) → dict` returning:
- `functions`: GLSL function definition(s) for this node
- `uniforms`: GLSL uniform declarations
- `call`: the GLSL expression to invoke this node's SDF (e.g. `sdf_box_0(p)`)
- `params`: dict of `{uniform_name: value}` for CPU→GPU data push

The `glsl_assembler.py` walks the tree, collects all fragments, and assembles a complete
fragment shader. Shader is compiled once per tree topology change. Parameter changes
(move, resize) only update uniform values — no recompile.

### `to_glsl()` Return Format

```python
def to_glsl(self, node_id: str) -> dict:
    """Emit GLSL fragments for this SDF node.
    
    Args:
        node_id: Unique ID string for this node (used to generate unique
                 function/uniform names). May contain hyphens — replace
                 with underscores for GLSL identifiers.
    
    Returns dict:
        functions : str   — GLSL function definition(s)
        uniforms  : str   — GLSL uniform declarations
        call      : str   — GLSL expression evaluating this SDF at point p
        params    : dict  — {uniform_name: value} for Coin3D uniform setup
                            Values are: float, (x,y,z) tuple, or 4x4 list
    """
```

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `FRepField.evaluate_grid(pts)` | `core/frep/frep_field.py:30` | Batch SDF eval `(N,3) → (N,)` |
| `FRepField.bounding_box()` | `core/frep/frep_field.py:20` | AABB `→ (Vector, Vector)` |
| `DMRayMarchRenderer.__init__()` | `core/dm_ray_march_renderer.py:15` | Renderer constructor |
| `DMRayMarchRenderer.update()` | `core/dm_ray_march_renderer.py:216` | Called when SDF changes |
| `SoShaderProgram` / `SoFragmentShader` | pivy.coin | Shader setup (see `coin3d_shader_api` skill) |
| `DMViewProvider.attach()` | `core/dm_object.py:400` | Hook for renderer creation |
| `DMViewProvider.updateData()` | `core/dm_object.py:435` | Hook for update calls |

---

## Tier 1 — `to_glsl()` Interface (Do First)

Add the `to_glsl()` method to every FRepField subclass. No rendering yet — just
the GLSL code generation. Each task is independent after S-001.

### S-001: Add abstract `to_glsl()` to `FRepField` base class

**File:** `core/frep/frep_field.py` — append after `curvature_grid()` (line 72)

**What:** Add a base `to_glsl()` method that raises `NotImplementedError` with a
helpful message. This defines the contract all subclasses must satisfy.

```python
def to_glsl(self, node_id: str) -> dict:
    """Emit GLSL fragments for this SDF node.

    Args:
        node_id: Unique identifier for name-mangling (hyphens will be
                 replaced with underscores by the caller).

    Returns dict with keys:
        functions : str   — GLSL function definition(s)
        uniforms  : str   — GLSL uniform declarations
        call      : str   — GLSL expression that evaluates this SDF at vec3 p
        params    : dict  — {uniform_name: value} for Coin3D uniform nodes
    """
    raise NotImplementedError(
        f"{type(self).__name__} does not implement to_glsl(). "
        "Formulaic rendering requires all FRepField subclasses to provide "
        "GLSL code generation."
    )
```

---

### S-002: Implement `SdfSphereField.to_glsl()`

**File:** `core/frep/sdf/sphere.py` — append after `bounding_box()` (line 28)

**What:** Return GLSL for `length(p - center) - radius`. Two uniforms: center (vec3) and radius (float).

```python
def to_glsl(self, node_id: str) -> dict:
    uid = node_id.replace('-', '_')
    fn_name = f"sdf_sphere_{uid}"
    u_center = f"u_sphere_{uid}_center"
    u_radius = f"u_sphere_{uid}_radius"

    functions = (
        f"float {fn_name}(vec3 p) {{\n"
        f"    return length(p - {u_center}) - {u_radius};\n"
        f"}}\n"
    )
    uniforms = (
        f"uniform vec3  {u_center};\n"
        f"uniform float {u_radius};\n"
    )
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"{fn_name}(p)",
        "params": {
            u_center: (self.center.x, self.center.y, self.center.z),
            u_radius: float(self.radius),
        },
    }
```

**Depends on:** S-001

---

### S-003: Implement `SdfBoxField.to_glsl()`

**File:** `core/frep/sdf/box.py` — append after `bounding_box()` (line 82)

**What:** Return GLSL for the axis-aligned box SDF with optional placement.
When `self.placement` is not None, transform `p` by the inverse matrix first.

Without placement:
```glsl
float sdf_box_{uid}(vec3 p) {
    vec3 d = abs(p - u_box_{uid}_center) - u_box_{uid}_half;
    return length(max(d, 0.0)) + min(max(d.x, max(d.y, d.z)), 0.0);
}
```

With placement (inverse matrix as a mat4 uniform):
```glsl
float sdf_box_{uid}(vec3 p) {
    vec3 lp = (u_box_{uid}_inv_mat * vec4(p, 1.0)).xyz;
    vec3 d = abs(lp - u_box_{uid}_center) - u_box_{uid}_half;
    return length(max(d, 0.0)) + min(max(d.x, max(d.y, d.z)), 0.0);
}
```

```python
def to_glsl(self, node_id: str) -> dict:
    uid = node_id.replace('-', '_')
    fn_name = f"sdf_box_{uid}"
    u_center = f"u_box_{uid}_center"
    u_half = f"u_box_{uid}_half"
    u_inv = f"u_box_{uid}_inv_mat"

    params = {
        u_center: (self.center.x, self.center.y, self.center.z),
        u_half: (self.half_size.x, self.half_size.y, self.half_size.z),
    }

    if self.placement is not None and self.inv_matrix is not None:
        uniforms = (
            f"uniform vec3 {u_center};\n"
            f"uniform vec3 {u_half};\n"
            f"uniform mat4 {u_inv};\n"
        )
        functions = (
            f"float {fn_name}(vec3 p) {{\n"
            f"    vec3 lp = ({u_inv} * vec4(p, 1.0)).xyz;\n"
            f"    vec3 d = abs(lp - {u_center}) - {u_half};\n"
            f"    return length(max(d, 0.0)) + min(max(d.x, max(d.y, d.z)), 0.0);\n"
            f"}}\n"
        )
        # Flatten 4x4 matrix column-major for GLSL
        m = self.inv_matrix  # numpy 4x4
        params[u_inv] = m.T.flatten().tolist()  # column-major
    else:
        uniforms = (
            f"uniform vec3 {u_center};\n"
            f"uniform vec3 {u_half};\n"
        )
        functions = (
            f"float {fn_name}(vec3 p) {{\n"
            f"    vec3 d = abs(p - {u_center}) - {u_half};\n"
            f"    return length(max(d, 0.0)) + min(max(d.x, max(d.y, d.z)), 0.0);\n"
            f"}}\n"
        )

    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"{fn_name}(p)",
        "params":    params,
    }
```

**Depends on:** S-001

---

### S-004: Implement `SdfCylinderField.to_glsl()`

**File:** `core/frep/sdf/cylinder.py` — append after `bounding_box()` (line 49)

**What:** Return GLSL for the finite cylinder SDF. Uniforms: base_center (vec3), axis (vec3), radius (float), height (float).

```python
def to_glsl(self, node_id: str) -> dict:
    uid = node_id.replace('-', '_')
    fn_name = f"sdf_cyl_{uid}"
    u_base = f"u_cyl_{uid}_base"
    u_axis = f"u_cyl_{uid}_axis"
    u_radius = f"u_cyl_{uid}_radius"
    u_height = f"u_cyl_{uid}_height"

    functions = (
        f"float {fn_name}(vec3 p) {{\n"
        f"    vec3 pa = p - {u_base};\n"
        f"    float h = dot(pa, {u_axis});\n"
        f"    vec3 radial = pa - {u_axis} * h;\n"
        f"    float d_r = length(radial) - {u_radius};\n"
        f"    float d_a = abs(h - {u_height} * 0.5) - {u_height} * 0.5;\n"
        f"    return length(max(vec2(d_r, d_a), 0.0)) + min(max(d_r, d_a), 0.0);\n"
        f"}}\n"
    )
    uniforms = (
        f"uniform vec3  {u_base};\n"
        f"uniform vec3  {u_axis};\n"
        f"uniform float {u_radius};\n"
        f"uniform float {u_height};\n"
    )
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"{fn_name}(p)",
        "params": {
            u_base: (self.base_center.x, self.base_center.y, self.base_center.z),
            u_axis: (self.axis.x, self.axis.y, self.axis.z),
            u_radius: float(self.radius),
            u_height: float(self.height),
        },
    }
```

**Depends on:** S-001

---

### S-005: Implement `SdfPlaneField.to_glsl()`

**File:** `core/frep/sdf/plane.py` — append after current methods (end of file)

**What:** Return GLSL for a plane SDF: `dot(p - point, normal)`. Read the existing
`plane.py` to determine the field attributes (expected: `self.point`, `self.normal`).

```python
def to_glsl(self, node_id: str) -> dict:
    uid = node_id.replace('-', '_')
    fn_name = f"sdf_plane_{uid}"
    u_point = f"u_plane_{uid}_point"
    u_normal = f"u_plane_{uid}_normal"

    functions = (
        f"float {fn_name}(vec3 p) {{\n"
        f"    return dot(p - {u_point}, {u_normal});\n"
        f"}}\n"
    )
    uniforms = (
        f"uniform vec3 {u_point};\n"
        f"uniform vec3 {u_normal};\n"
    )
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"{fn_name}(p)",
        "params": {
            u_point: (self.point.x, self.point.y, self.point.z),
            u_normal: (self.normal.x, self.normal.y, self.normal.z),
        },
    }
```

**Depends on:** S-001

---

### S-006: Implement `to_glsl()` on CSG composer fields

**File:** `core/frep/frep_composer.py` — append to each of `UnionField`, `IntersectionField`, `SubtractionField`

**What:** Each CSG operation calls `to_glsl()` on its children and wraps the results.

```python
# UnionField — append after evaluate_grid/bounding_box
def to_glsl(self, node_id: str) -> dict:
    uid = node_id.replace('-', '_')
    a = self.a.to_glsl(f"{node_id}_a")
    b = self.b.to_glsl(f"{node_id}_b")
    fn_name = f"sdf_union_{uid}"
    functions = (
        a["functions"] + b["functions"] +
        f"float {fn_name}(vec3 p) {{\n"
        f"    return min({a['call']}, {b['call']});\n"
        f"}}\n"
    )
    uniforms = a["uniforms"] + b["uniforms"]
    params = {**a["params"], **b["params"]}
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"{fn_name}(p)",
        "params":    params,
    }

# IntersectionField — same pattern with max()
def to_glsl(self, node_id: str) -> dict:
    uid = node_id.replace('-', '_')
    a = self.a.to_glsl(f"{node_id}_a")
    b = self.b.to_glsl(f"{node_id}_b")
    fn_name = f"sdf_intersect_{uid}"
    functions = (
        a["functions"] + b["functions"] +
        f"float {fn_name}(vec3 p) {{\n"
        f"    return max({a['call']}, {b['call']});\n"
        f"}}\n"
    )
    uniforms = a["uniforms"] + b["uniforms"]
    params = {**a["params"], **b["params"]}
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"{fn_name}(p)",
        "params":    params,
    }

# SubtractionField — max(a, -b)
def to_glsl(self, node_id: str) -> dict:
    uid = node_id.replace('-', '_')
    a = self.a.to_glsl(f"{node_id}_a")
    b = self.b.to_glsl(f"{node_id}_b")
    fn_name = f"sdf_subtract_{uid}"
    functions = (
        a["functions"] + b["functions"] +
        f"float {fn_name}(vec3 p) {{\n"
        f"    return max({a['call']}, -{b['call']});\n"
        f"}}\n"
    )
    uniforms = a["uniforms"] + b["uniforms"]
    params = {**a["params"], **b["params"]}
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"{fn_name}(p)",
        "params":    params,
    }
```

**Depends on:** S-001

---

## Tier 2 — GLSL Assembler

### S-007: Create `core/frep/glsl_assembler.py`

**File:** `core/frep/glsl_assembler.py` — new file

**What:** Walk the FRep tree, call `to_glsl()` on the root, and wrap the result in a
complete fragment shader with ray marching loop, normals, Phong shading, and depth output.

The function `assemble_sdf_shader(field, bbox_min, bbox_max)` returns
`(frag_source: str, params: dict)`.

```python
"""
core/frep/glsl_assembler.py

Assembles a complete GLSL fragment shader from an FRepField tree.
Only uses field.to_glsl() and field.bounding_box().
"""


def assemble_sdf_shader(field, node_id: str = "root") -> tuple:
    """Walk the FRep tree and produce a self-contained fragment shader.

    Args:
        field:   Any FRepField with to_glsl() implemented.
        node_id: Root node ID for name generation.

    Returns:
        (frag_source: str, params: dict)
        - frag_source: complete GLSL fragment shader source
        - params: {uniform_name: value} for all uniforms in the shader
    """
    glsl = field.to_glsl(node_id)

    frag_source = f"""\
varying vec2 v_uv;
uniform vec3  u_bbox_min;
uniform vec3  u_bbox_max;

{glsl['uniforms']}

{glsl['functions']}

float sdf(vec3 p) {{
    return {glsl['call']};
}}

vec3 sdf_normal(vec3 p) {{
    float h = 0.001;
    vec2 k = vec2(1.0, -1.0);
    return normalize(
        k.xyy * sdf(p + k.xyy*h) +
        k.yyx * sdf(p + k.yyx*h) +
        k.yxy * sdf(p + k.yxy*h) +
        k.xxx * sdf(p + k.xxx*h));
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
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;

    float hit_thresh = 0.0005;

    float t = 0.0;
    bool hit = false;
    for (int i = 0; i < 256; i++) {{
        vec3 p = ro + t * rd;
        float d = sdf(p);
        if (d < hit_thresh) {{ hit = true; break; }}
        if (t > 10000.0) break;
        t += d;
    }}
    if (!hit) discard;

    vec3 hp = ro + t * rd;
    vec3 n  = sdf_normal(hp);
    vec3 ld = normalize(gl_LightSource[0].position.xyz - hp);
    float diff = max(dot(n, ld), 0.0);
    vec3 vd = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;

    gl_FragColor = vec4(color, 1.0);
    vec4 clip = gl_ProjectionMatrix * gl_ModelViewMatrix * vec4(hp, 1.0);
    gl_FragDepth = (clip.z / clip.w + 1.0) * 0.5;
}}
"""

    params = dict(glsl["params"])  # copy
    return frag_source, params
```

**Depends on:** S-001 (and at least one primitive, e.g. S-002, for testing)

---

### S-008: Unit test for `glsl_assembler`

**File:** `tests/test_glsl_assembler.py` — new file

**What:** Python-only test (no FreeCAD runtime needed) that:
1. Constructs a `UnionField(SdfBoxField, SdfSphereField)` tree
2. Calls `assemble_sdf_shader(field)` 
3. Asserts the GLSL source contains expected function names
4. Asserts uniform params dict has correct values and types
5. Verifies no duplicate uniform names

```python
"""Test GLSL assembly from FRep tree — runs without FreeCAD."""
import sys, os
from types import ModuleType
import numpy as np

# Minimal FreeCAD stubs
fc = ModuleType("FreeCAD")
class _Vec:
    def __init__(self, x=0, y=0, z=0): self.x=x; self.y=y; self.z=z
    def __add__(self, o): return _Vec(self.x+o.x, self.y+o.y, self.z+o.z)
    def __sub__(self, o): return _Vec(self.x-o.x, self.y-o.y, self.z-o.z)
    def __mul__(self, s): return _Vec(self.x*s, self.y*s, self.z*s)
    def __neg__(self): return _Vec(-self.x, -self.y, -self.z)
    @property
    def Length(self): return (self.x**2+self.y**2+self.z**2)**0.5
fc.Vector = _Vec
sys.modules["FreeCAD"] = fc

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.frep.sdf.sphere import SdfSphereField
from core.frep.sdf.box import SdfBoxField
from core.frep.frep_composer import UnionField
from core.frep.glsl_assembler import assemble_sdf_shader


def test_single_sphere():
    sphere = SdfSphereField(center=fc.Vector(1, 2, 3), radius=5.0)
    src, params = assemble_sdf_shader(sphere, "s1")
    assert "sdf_sphere_s1" in src, "Sphere function not found"
    assert "u_sphere_s1_center" in src, "Sphere center uniform not found"
    assert "u_sphere_s1_radius" in src, "Sphere radius uniform not found"
    assert params["u_sphere_s1_radius"] == 5.0
    assert params["u_sphere_s1_center"] == (1, 2, 3)
    print("PASS: test_single_sphere", flush=True)


def test_union_box_sphere():
    box = SdfBoxField(center=fc.Vector(0,0,0), size=fc.Vector(10,10,10))
    sphere = SdfSphereField(center=fc.Vector(5,5,5), radius=3.0)
    union = UnionField(box, sphere)
    src, params = assemble_sdf_shader(union, "u1")
    assert "sdf_union_u1" in src, "Union function not found"
    assert "sdf_box_u1_a" in src, "Box function not found"
    assert "sdf_sphere_u1_b" in src, "Sphere function not found"
    assert "min(" in src, "Union should use min()"
    # Check params from both children
    assert "u_box_u1_a_center" in params
    assert "u_sphere_u1_b_radius" in params
    print("PASS: test_union_box_sphere", flush=True)


def test_no_duplicate_uniforms():
    sphere = SdfSphereField(center=fc.Vector(0,0,0), radius=1.0)
    src, params = assemble_sdf_shader(sphere, "dup")
    # Each uniform name should appear exactly once in declarations
    lines = [l.strip() for l in src.split('\n') if l.strip().startswith('uniform')]
    names = [l.split()[-1].rstrip(';') for l in lines]
    assert len(names) == len(set(names)), f"Duplicate uniforms: {names}"
    print("PASS: test_no_duplicate_uniforms", flush=True)


if __name__ == "__main__":
    try:
        test_single_sphere()
        test_union_box_sphere()
        test_no_duplicate_uniforms()
        print("\nAll GLSL assembler tests passed.", flush=True)
    except Exception as e:
        print(f"Test FAILED: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
```

**Depends on:** S-002, S-003, S-006, S-007

---

## Tier 3 — Renderer Integration

### S-009: Add formulaic rendering path to `DMRayMarchRenderer`

**File:** `core/dm_ray_march_renderer.py`

**What:** Modify `DMRayMarchRenderer` to attempt formulaic rendering first. If the field
supports `to_glsl()`, use `assemble_sdf_shader()` to generate the fragment shader source
and push uniforms. Fall back to the existing atlas path if `to_glsl()` raises
`NotImplementedError`.

**Changes:**

1. Add `_use_formulaic` boolean flag and `_frag_shader` reference to `__init__`.
2. In `update(field, cell_size)`:
   ```python
   try:
       from core.frep.glsl_assembler import assemble_sdf_shader
       frag_src, params = assemble_sdf_shader(field)
       self._recompile_shader(frag_src)
       self._push_uniforms(params, field)
       self._use_formulaic = True
   except NotImplementedError:
       # Fall back to atlas baking
       self._use_atlas(field, cell_size)
       self._use_formulaic = False
   ```
3. `_recompile_shader(frag_src)` — set `self._frag_shader.sourceProgram.setValue(frag_src)`.
   Create new `SoShaderParameter*` nodes for each uniform in `params`, replace 
   `self._frag_shader.parameter` contents.
4. `_push_uniforms(params, field)` — for each `{name: value}`, create or update the
   appropriate `SoShaderParameter*` node based on value type:
   - `float` → `SoShaderParameter1f`
   - `(x, y, z)` tuple → `SoShaderParameter3f`
   - list of 16 floats → `SoShaderParameterMatrix` (mat4)
5. Add `u_bbox_min` and `u_bbox_max` uniforms from `field.bounding_box()`.

**Depends on:** S-007

---

### S-010: Add `RenderMode` option for formulaic SDF in settings

**File:** `core/dm_object.py`

**What:** Extend the render mode constants to include a 4th option:

```python
RENDER_MODE_MESH        = 0
RENDER_MODE_POINT_CLOUD = 1
RENDER_MODE_RAY_MARCH   = 2   # Voxel atlas (existing)
RENDER_MODE_FORMULAIC   = 3   # Exact SDF (new)
```

Update `get_render_mode()` default to `RENDER_MODE_FORMULAIC`.

**File:** `commands/cmd_settings.py`

**What:** Add "Formulaic SDF (GPU)" as a 4th option in the render mode combo box.
Update tooltip to explain the difference between voxel and formulaic rendering.

**Depends on:** S-009

---

### S-011: Wire formulaic mode into `DMViewProvider`

**File:** `core/dm_object.py`

**What:** In the `attach()` and `updateData()` frep branches, add handling for
`RENDER_MODE_FORMULAIC`. This should create a `DMRayMarchRenderer` (same class)
but the `update()` method will automatically use the formulaic path when
`to_glsl()` is available.

```python
elif mode == RENDER_MODE_FORMULAIC:
    from core.dm_ray_march_renderer import DMRayMarchRenderer
    self.ray_march_renderer = DMRayMarchRenderer(vobj)
```

In `updateData()`:
```python
elif rm is not None and field is not None:
    rm.update(field, get_meshing_cell_size())
```

The renderer internally dispatches between formulaic and atlas paths.

**Depends on:** S-009, S-010

---

## Tier 4 — Optimization & Polish

### S-012: Add AABB ray-box intersection for early termination

**File:** `core/frep/glsl_assembler.py` — in `assemble_sdf_shader()`

**What:** Add a ray-AABB intersection test at the start of `main()` to skip the
expensive SDF march loop for rays that completely miss the bounding box. This is
especially important for the full-screen quad approach where most pixels don't
intersect the object.

Add this GLSL function and call it before the march loop:

```glsl
vec2 ray_box(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
    vec3 inv_rd = 1.0 / rd;
    vec3 t0 = (bmin - ro) * inv_rd;
    vec3 t1 = (bmax - ro) * inv_rd;
    vec3 tmin = min(t0, t1);
    vec3 tmax = max(t0, t1);
    float tn = max(max(tmin.x, tmin.y), tmin.z);
    float tf = min(min(tmax.x, tmax.y), tmax.z);
    return vec2(tn, tf);
}

// In main():
vec2 tb = ray_box(ro, rd, u_bbox_min, u_bbox_max);
if (tb.y < 0.0 || tb.x > tb.y) discard;
float t = max(tb.x, 0.0);
```

**Depends on:** S-007

---

### S-013: Cache shader topology to avoid unnecessary recompiles

**File:** `core/dm_ray_march_renderer.py`

**What:** Track a "tree signature" hash to detect when the SDF tree topology has
actually changed (vs just parameter updates). Only recompile the shader when the
topology changes; otherwise just update uniform values.

Add to `DMRayMarchRenderer`:
```python
def _tree_signature(self, field):
    """Generate a hashable signature of the SDF tree structure (not values)."""
    def walk(f):
        name = type(f).__name__
        if hasattr(f, 'a') and hasattr(f, 'b'):  # ComposerField
            return f"{name}({walk(f.a)},{walk(f.b)})"
        return name
    return walk(field)
```

In `update()`, compare `self._last_sig` with `self._tree_signature(field)`.
If unchanged, skip `_recompile_shader()` and only call `_push_uniforms()`.

**Depends on:** S-009

---

### S-014: Smooth Union / Intersection / Subtraction support

**File:** `core/frep/frep_composer.py` — new classes `SmoothUnionField`, `SmoothIntersectionField`, `SmoothSubtractionField`

**What:** Add smooth CSG operations using polynomial smooth-min for fillets/blends.
These use the `k` parameter to control blend radius.

```glsl
// Smooth min for smooth union:
float sdf_smooth_union(float a, float b, float k) {
    float h = clamp(0.5 + 0.5*(b-a)/k, 0.0, 1.0);
    return mix(b, a, h) - k*h*(1.0-h);
}
```

Each smooth composer implements both `evaluate_grid()` and `to_glsl()`.

**Depends on:** S-006

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_formulaic_sdf` | `to_glsl()` contract, return format, GLSL naming conventions |
| `coin3d_shader_api` | `SoShaderProgram` setup, uniform node types, shader recompilation |
| `coin3d_fullscreen_quad` | Full-screen quad rendering pipeline and unprojection math |
| `dm_todo_format` | Task format and conventions for this project |

See `.agents/workflows/` for executable workflows:

| Workflow | Purpose |
|----------|---------| 
| `/fix-task` | Fix a single task from this list by ID (e.g. `/fix-task S-003`) |
