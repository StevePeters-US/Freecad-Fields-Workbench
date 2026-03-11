# Direct Modeling Workbench — GPU Ray Marching Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The current F-Rep pipeline tessellates the SDF on CPU (Marching Cubes / Surface Nets / Dual
Contouring) and hands triangles to Coin3D's fixed-function renderer. This requires re-meshing
on every parameter change and produces a mesh that can never perfectly capture smooth curvature.

The goal is a **GPU ray marching renderer** that evaluates the SDF analytically in a GLSL
fragment shader — no triangles, instant parameter updates, pixel-perfect silhouettes.

### Architecture

1. Each `FRepField` subclass gains a `to_glsl(node_id)` method that emits a GLSL function
   and the corresponding CPU-side uniform values.
2. `GlslAssembler` (new file) walks the SDF tree, concatenates all GLSL snippets, and
   inserts them into a fragment shader template that sphere-traces the composed scene SDF.
3. `DMRayMarchRenderer` (new file) owns the Coin3D scene graph nodes: an AABB proxy geometry
   (8 world-space corners, 6 quad faces) plus a `SoShaderProgram` node. Calling `update(field)`
   recompiles the shader and refreshes uniforms.
4. `DMViewProvider` conditionally instantiates `DMRayMarchRenderer` instead of the mesh path
   when the `UseRayMarching` preference is set.

### Coordinate Space

All SDF coordinates are in **FreeCAD world space** (mm). The proxy geometry vertices are
set directly in world-space coordinates via `SoCoordinate3` (no `SoTransform`), so inside
the vertex shader `gl_Vertex.xyz` is already in world/SDF space. Camera world position is
recovered as `(gl_ModelViewMatrixInverse * vec4(0,0,0,1)).xyz`.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `FRepField` | `core/frep/frep_field.py:4` | Abstract base — add `to_glsl()` here |
| `SdfSphereField` | `core/frep/sdf/sphere.py:5` | Concrete primitive |
| `SdfBoxField` | `core/frep/sdf/box.py:5` | Concrete primitive (optional placement) |
| `SdfCylinderField` | `core/frep/sdf/cylinder.py:6` | Concrete primitive |
| `SdfPlaneField` | `core/frep/sdf/plane.py:4` | Half-space primitive |
| `UnionField` / `IntersectionField` / `SubtractionField` | `core/frep/frep_composer.py:35/47/59` | CSG composers |
| `DMViewProvider.attach()` | `core/dm_object.py:400` | Hook for new renderer |
| `DMViewProvider.updateData()` | `core/dm_object.py:435` | Hook for shader updates |
| `get_*` preference helpers | `core/dm_object.py:29–68` | Pattern for new preference |

### `to_glsl()` Return Format

Every `to_glsl(node_id: str) -> dict` must return exactly:

```python
{
    "functions": str,  # GLSL function defs (includes all children for composers)
    "uniforms":  str,  # GLSL uniform declarations for this node only
    "call":      str,  # GLSL expression: e.g. "sdf_sphere_root(p)"
    "params":    dict, # uniform_name → Python value (see type table below)
}
```

**`params` value types** (determines which Coin3D uniform node is created):

| Python type | GLSL type | Coin3D node |
|-------------|-----------|-------------|
| `float` or `int` | `float` | `SoUniformShaderParameter1f` |
| 3-tuple of float | `vec3` | `SoUniformShaderParameter3f` |
| 16-tuple of float (row-major) | `mat4` | `SoUniformShaderParameterMatrix` |

**Uniform naming convention**: `u_{type}_{node_id}_{field}`.
Replace any `-` in `node_id` with `_` before use.
Example: node_id=`"root_a"` → sphere center uniform = `u_sphere_root_a_center`.

### Agent Skills

See `.agents/skills/` for project-specific knowledge used by these tasks:

| Skill | Purpose |
|-------|---------|
| `dm_glsl_codegen` | `to_glsl()` interface, return format, recursive composition pattern, naming rules |
| `coin3d_shader_api` | `SoShaderProgram` / uniform node API, proxy geometry pattern, built-in GLSL matrices |
| `dm_renderer_architecture` | How `DMViewProvider.attach()` and `updateData()` branch by ShapeType |

---

## Tier 1 — GLSL Code Generation (SDF → GLSL)

These tasks add `to_glsl()` to each SDF class. No new files; only new methods appended
to existing classes. Each method is independent — implement them in any order.

### R-001: Add `to_glsl()` abstract method to `FRepField`

**File:** `core/frep/frep_field.py` — append after line 72 (end of `curvature_grid`)

**What:** Add the following method to the `FRepField` class:

```python
def to_glsl(self, node_id: str) -> dict:
    """
    Generate GLSL source code and CPU-side parameter values for this SDF node.

    Args:
        node_id: Unique string identifier for this node within the tree.
                 Used to generate unique GLSL function and uniform names.
                 Must be safe as a GLSL identifier after replacing '-' with '_'.

    Returns dict with keys:
        "functions": str  — all GLSL function definitions needed (self + children)
        "uniforms":  str  — GLSL uniform declarations for this node only
        "call":      str  — GLSL expression evaluating this node, e.g. "sdf_sphere_0(p)"
        "params":    dict — maps uniform name → Python value:
                            float/int   → SoUniformShaderParameter1f
                            (x,y,z)     → SoUniformShaderParameter3f
                            (16 floats) → SoUniformShaderParameterMatrix (row-major mat4)
    """
    raise NotImplementedError("to_glsl() must be implemented by subclass.")
```

**Why first:** All other tasks in Tier 1 implement this interface.

---

### R-002: Implement `SdfSphereField.to_glsl()`

**File:** `core/frep/sdf/sphere.py` — append to `SdfSphereField` after `bounding_box()` (line 28)

**What:** Return GLSL for `length(p - center) - radius`.

```python
def to_glsl(self, node_id: str) -> dict:
    uid = f"sphere_{node_id.replace('-', '_')}"
    functions = (
        f"float sdf_{uid}(vec3 p) {{\n"
        f"    return length(p - u_{uid}_center) - u_{uid}_radius;\n"
        f"}}\n"
    )
    uniforms = (
        f"uniform vec3  u_{uid}_center;\n"
        f"uniform float u_{uid}_radius;\n"
    )
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"sdf_{uid}(p)",
        "params": {
            f"u_{uid}_center": (self.center.x, self.center.y, self.center.z),
            f"u_{uid}_radius": float(self.radius),
        },
    }
```

**Depends on:** R-001

---

### R-003: Implement `SdfBoxField.to_glsl()`

**File:** `core/frep/sdf/box.py` — append to `SdfBoxField` after `bounding_box()` (line 82)

**What:** Emit the standard box SDF. When `self.placement` is not `None`, also pass
the pre-computed `self.inv_matrix` as a `mat4` uniform to transform `p` into local space.

**No-placement case** (`self.placement is None`):

```glsl
float sdf_box_{uid}(vec3 p) {
    vec3 q = abs(p - u_box_{uid}_center) - u_box_{uid}_half;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}
```

Uniforms: `u_box_{uid}_center` (vec3), `u_box_{uid}_half` (vec3)

**With-placement case** (`self.placement is not None`):

```glsl
float sdf_box_{uid}(vec3 p) {
    vec4 lp = u_box_{uid}_inv_mat * vec4(p, 1.0);
    vec3 q  = abs(lp.xyz - u_box_{uid}_center) - u_box_{uid}_half;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}
```

Additional uniform: `u_box_{uid}_inv_mat` (mat4, value = `tuple(self.inv_matrix.flatten())`)

**Python implementation:**

```python
def to_glsl(self, node_id: str) -> dict:
    uid = f"box_{node_id.replace('-', '_')}"
    c = (self.center.x, self.center.y, self.center.z)
    h = (self.half_size.x, self.half_size.y, self.half_size.z)

    if self.placement is None:
        functions = (
            f"float sdf_{uid}(vec3 p) {{\n"
            f"    vec3 q = abs(p - u_{uid}_center) - u_{uid}_half;\n"
            f"    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);\n"
            f"}}\n"
        )
        uniforms = (
            f"uniform vec3 u_{uid}_center;\n"
            f"uniform vec3 u_{uid}_half;\n"
        )
        params = {
            f"u_{uid}_center": c,
            f"u_{uid}_half":   h,
        }
    else:
        functions = (
            f"float sdf_{uid}(vec3 p) {{\n"
            f"    vec4 lp = u_{uid}_inv_mat * vec4(p, 1.0);\n"
            f"    vec3 q  = abs(lp.xyz - u_{uid}_center) - u_{uid}_half;\n"
            f"    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);\n"
            f"}}\n"
        )
        uniforms = (
            f"uniform mat4 u_{uid}_inv_mat;\n"
            f"uniform vec3 u_{uid}_center;\n"
            f"uniform vec3 u_{uid}_half;\n"
        )
        params = {
            f"u_{uid}_inv_mat": tuple(float(v) for v in self.inv_matrix.flatten()),
            f"u_{uid}_center":  c,
            f"u_{uid}_half":    h,
        }

    return {"functions": functions, "uniforms": uniforms,
            "call": f"sdf_{uid}(p)", "params": params}
```

**Depends on:** R-001

---

### R-004: Implement `SdfCylinderField.to_glsl()`

**File:** `core/frep/sdf/cylinder.py` — append to `SdfCylinderField` after `bounding_box()` (line 49)

**GLSL:**

```glsl
float sdf_cylinder_{uid}(vec3 p) {
    vec3  pa    = p - u_{uid}_base;
    float h     = dot(pa, u_{uid}_axis);
    float d_r   = length(pa - u_{uid}_axis * h) - u_{uid}_radius;
    float d_a   = abs(h - u_{uid}_height * 0.5) - u_{uid}_height * 0.5;
    float out_d = length(vec2(max(d_r, 0.0), max(d_a, 0.0)));
    float in_d  = min(max(d_r, d_a), 0.0);
    return out_d + in_d;
}
```

Uniforms: `u_{uid}_base` (vec3), `u_{uid}_axis` (vec3, normalized), `u_{uid}_radius` (float),
`u_{uid}_height` (float).

```python
def to_glsl(self, node_id: str) -> dict:
    uid = f"cylinder_{node_id.replace('-', '_')}"
    functions = (
        f"float sdf_{uid}(vec3 p) {{\n"
        f"    vec3  pa    = p - u_{uid}_base;\n"
        f"    float h     = dot(pa, u_{uid}_axis);\n"
        f"    float d_r   = length(pa - u_{uid}_axis * h) - u_{uid}_radius;\n"
        f"    float d_a   = abs(h - u_{uid}_height * 0.5) - u_{uid}_height * 0.5;\n"
        f"    float out_d = length(vec2(max(d_r, 0.0), max(d_a, 0.0)));\n"
        f"    float in_d  = min(max(d_r, d_a), 0.0);\n"
        f"    return out_d + in_d;\n"
        f"}}\n"
    )
    uniforms = (
        f"uniform vec3  u_{uid}_base;\n"
        f"uniform vec3  u_{uid}_axis;\n"
        f"uniform float u_{uid}_radius;\n"
        f"uniform float u_{uid}_height;\n"
    )
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"sdf_{uid}(p)",
        "params": {
            f"u_{uid}_base":   (self.base_center.x, self.base_center.y, self.base_center.z),
            f"u_{uid}_axis":   (self.axis.x, self.axis.y, self.axis.z),
            f"u_{uid}_radius": float(self.radius),
            f"u_{uid}_height": float(self.height),
        },
    }
```

**Depends on:** R-001

---

### R-005: Implement `SdfPlaneField.to_glsl()`

**File:** `core/frep/sdf/plane.py` — append to `SdfPlaneField` after `bounding_box()` (line 20)

**GLSL:** Half-space signed distance = `dot(p - origin, normal)`.

```python
def to_glsl(self, node_id: str) -> dict:
    uid = f"plane_{node_id.replace('-', '_')}"
    functions = (
        f"float sdf_{uid}(vec3 p) {{\n"
        f"    return dot(p - u_{uid}_origin, u_{uid}_normal);\n"
        f"}}\n"
    )
    uniforms = (
        f"uniform vec3 u_{uid}_origin;\n"
        f"uniform vec3 u_{uid}_normal;\n"
    )
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"sdf_{uid}(p)",
        "params": {
            f"u_{uid}_origin": (self.origin.x, self.origin.y, self.origin.z),
            f"u_{uid}_normal": (self.normal.x, self.normal.y, self.normal.z),
        },
    }
```

Note: `SdfPlaneField.bounding_box()` returns a very large AABB (from `get_max_bounds()`).
The shader's AABB clipping naturally limits the ray to reasonable scene bounds.

**Depends on:** R-001

---

### R-006: Implement `to_glsl()` on all three `ComposerField` subclasses

**File:** `core/frep/frep_composer.py`

Add `to_glsl()` to `UnionField` (after line 45), `IntersectionField` (after line 57),
and `SubtractionField` (after line 69). Each recursively calls `to_glsl()` on children
and wraps their calls in `min`, `max`, or `max(a, -b)`.

**Pattern (shown for UnionField; adapt for the other two):**

```python
# UnionField — append after bounding_box() at line 45
def to_glsl(self, node_id: str) -> dict:
    uid = f"union_{node_id.replace('-', '_')}"
    a   = self.a.to_glsl(f"{node_id}_a")
    b   = self.b.to_glsl(f"{node_id}_b")
    functions = (
        a["functions"] + b["functions"] +
        f"float sdf_{uid}(vec3 p) {{\n"
        f"    return min({a['call']}, {b['call']});\n"
        f"}}\n"
    )
    return {
        "functions": functions,
        "uniforms":  a["uniforms"] + b["uniforms"],
        "call":      f"sdf_{uid}(p)",
        "params":    {**a["params"], **b["params"]},
    }

# IntersectionField — same structure, replace min() with max()
# SubtractionField  — same structure, replace body with:
#     return max({a['call']}, -({b['call']}));
```

**Depends on:** R-001 and the child node implementations (R-002 through R-005)

---

## Tier 2 — Shader Assembly

### R-007: Create `core/frep/glsl_assembler.py`

**File:** `core/frep/glsl_assembler.py` — new file

**What:** Contains two GLSL source string constants and the `assemble_shader()` function.

#### `VERT_TEMPLATE`

```glsl
#version 120
varying vec3 vWorldPos;

void main() {
    vWorldPos   = gl_Vertex.xyz;  // vertices set directly in world/SDF space
    gl_Position = ftransform();
}
```

#### `FRAG_TEMPLATE`

The complete fragment shader. Placeholders (replaced by `assemble_shader`):
- `// [GENERATED_UNIFORMS]` → replaced with all `uniform` declarations
- `// [GENERATED_FUNCTIONS]` → replaced with all SDF function definitions
- `[SCENE_SDF_CALL]` → replaced with the root node's `call` string

Full template text:

```glsl
#version 120
varying vec3 vWorldPos;

// [GENERATED_UNIFORMS]
uniform vec3 u_aabb_min;
uniform vec3 u_aabb_max;

// [GENERATED_FUNCTIONS]
float scene_sdf(vec3 p) {
    return [SCENE_SDF_CALL];
}

// AABB slab intersection. Returns (tmin, tmax); a hit occurs when tmin < tmax.
vec2 aabb_intersect(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
    vec3 inv_d = 1.0 / rd;
    vec3 t0    = (bmin - ro) * inv_d;
    vec3 t1    = (bmax - ro) * inv_d;
    vec3 tmin3 = min(t0, t1);
    vec3 tmax3 = max(t0, t1);
    float tmin = max(max(tmin3.x, tmin3.y), tmin3.z);
    float tmax = min(min(tmax3.x, tmax3.y), tmax3.z);
    return vec2(tmin, tmax);
}

const int   MAX_STEPS = 64;
const float EPSILON   = 0.001;

float sphere_trace(vec3 ro, vec3 rd, float t_start, float t_end) {
    float t = t_start;
    for (int i = 0; i < MAX_STEPS; i++) {
        float d = scene_sdf(ro + t * rd);
        if (d < EPSILON) return t;
        t += d;
        if (t > t_end) break;
    }
    return -1.0;
}

vec3 scene_normal(vec3 p) {
    float h = 0.001;
    return normalize(vec3(
        scene_sdf(p + vec3(h, 0, 0)) - scene_sdf(p - vec3(h, 0, 0)),
        scene_sdf(p + vec3(0, h, 0)) - scene_sdf(p - vec3(0, h, 0)),
        scene_sdf(p + vec3(0, 0, h)) - scene_sdf(p - vec3(0, 0, h))
    ));
}

void main() {
    // Camera world position (model matrix is identity — vertices in world space)
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0, 0.0, 0.0, 1.0)).xyz;

    // Ray direction: perspective vs orthographic
    bool is_ortho = (gl_ProjectionMatrix[3][3] > 0.5);
    vec3 ro, rd;
    if (is_ortho) {
        rd = normalize((gl_ModelViewMatrixInverse * vec4(0.0, 0.0, -1.0, 0.0)).xyz);
        ro = vWorldPos;
    } else {
        ro = cam;
        rd = normalize(vWorldPos - cam);
    }

    // Clip ray to SDF bounding box
    vec2 t_ivl   = aabb_intersect(ro, rd, u_aabb_min, u_aabb_max);
    float t_start = max(t_ivl.x, 0.0);
    float t_end   = t_ivl.y;
    if (t_start >= t_end) discard;

    // Sphere trace
    float t = sphere_trace(ro, rd, t_start, t_end);
    if (t < 0.0) discard;

    vec3 hit = ro + t * rd;
    vec3 N   = scene_normal(hit);

    // Phong shading — matches existing orange SoMaterial:
    //   diffuse=(1.0, 0.5, 0.0), specular=(0.3, 0.3, 0.3), shininess=0.3*128=38
    vec3  Kd       = vec3(1.0, 0.5, 0.0);
    vec3  Ks       = vec3(0.3, 0.3, 0.3);
    float shininess = 38.0;

    // Light 0: directional (w==0) or positional (w==1)
    vec3 L;
    if (gl_LightSource[0].position.w < 0.5) {
        L = normalize((gl_ModelViewMatrixInverse *
                       vec4(gl_LightSource[0].position.xyz, 0.0)).xyz);
    } else {
        L = normalize((gl_ModelViewMatrixInverse *
                       gl_LightSource[0].position).xyz - hit);
    }
    vec3 V = normalize(cam - hit);
    vec3 R = reflect(-L, N);

    float diff = max(dot(N, L), 0.0);
    float spec = pow(max(dot(R, V), 0.0), shininess);
    vec3  color = 0.2 * Kd + diff * Kd + spec * Ks;

    gl_FragColor = vec4(color, 1.0);

    // Write correct depth so the ray-marched surface occludes other scene objects
    vec4 clip = gl_ProjectionMatrix * gl_ModelViewMatrix * vec4(hit, 1.0);
    gl_FragDepth = (clip.z / clip.w + 1.0) * 0.5;
}
```

#### `assemble_shader(field)` function

```python
def assemble_shader(field):
    """
    Walk the SDF tree and produce (vert_src, frag_src, params).

    Args:
        field: FRepField root node.

    Returns:
        vert_src (str): Vertex shader GLSL source.
        frag_src (str): Fragment shader GLSL source (with SDF baked in).
        params (dict):  All uniform names → Python values (including u_aabb_min/max).
    """
    glsl = field.to_glsl("root")
    mn, mx = field.bounding_box()

    frag = FRAG_TEMPLATE
    frag = frag.replace("// [GENERATED_UNIFORMS]", glsl["uniforms"])
    frag = frag.replace("// [GENERATED_FUNCTIONS]",  glsl["functions"])
    frag = frag.replace("[SCENE_SDF_CALL]",          glsl["call"])

    aabb_params = {
        "u_aabb_min": (mn.x, mn.y, mn.z),
        "u_aabb_max": (mx.x, mx.y, mx.z),
    }
    return VERT_TEMPLATE, frag, {**glsl["params"], **aabb_params}
```

**Depends on:** R-001 through R-006 (any `to_glsl()` implementation)

---

## Tier 3 — Coin3D Ray March Renderer

### R-008: Create `core/dm_ray_march_renderer.py` — class skeleton + `setup_nodes()`

**File:** `core/dm_ray_march_renderer.py` — new file

**What:** `DMRayMarchRenderer` manages the Coin3D scene graph for ray marching.
Proxy geometry is a manually defined box (8 `SoCoordinate3` points + 6 quads)
so that vertices land in world space with no transform needed.

**Class skeleton + constructor + `setup_nodes()`:**

```python
"""
core/dm_ray_march_renderer.py

GPU ray-marching renderer for F-Rep SDF objects.
Owns a Coin3D SoSeparator containing:
    SoShapeHints      — no face culling (UNKNOWN_ORDERING)
    SoShaderProgram   — vertex + fragment GLSL shaders
    SoCoordinate3     — 8 AABB corners in world space (updated on SDF change)
    SoIndexedFaceSet  — 6 quads forming the proxy bounding box
"""
try:
    from pivy import coin
except ImportError:
    coin = None


class DMRayMarchRenderer:
    """Coin3D ray-marching renderer. One instance per F-Rep ViewObject."""

    def __init__(self, vobj):
        self._sep          = None   # SoSeparator — root
        self._shader_prog  = None   # SoShaderProgram
        self._vert_shader  = None   # SoVertexShader
        self._frag_shader  = None   # SoFragmentShader
        self._proxy_coords = None   # SoCoordinate3 (8 AABB corners)
        self._uniform_nodes = {}    # name → SoUniformShaderParameter* node
        self._current_frag_src = "" # last compiled fragment source
        self._vobj = vobj
        if coin:
            self.setup_nodes(vobj)

    def setup_nodes(self, vobj):
        """Build and attach the Coin3D scene graph to vobj.RootNode."""
        sep = coin.SoSeparator()

        # No face culling — proxy box visible from inside AND outside
        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        sep.addChild(hints)

        # Shader program
        prog = coin.SoShaderProgram()
        vert = coin.SoVertexShader()
        vert.sourceType.setValue(coin.SoShader.GLSL_PROGRAM)
        frag = coin.SoFragmentShader()
        frag.sourceType.setValue(coin.SoShader.GLSL_PROGRAM)
        prog.shaderObject.set1Value(0, vert)
        prog.shaderObject.set1Value(1, frag)
        sep.addChild(prog)

        # Proxy geometry: 8 vertices (world-space AABB corners), 6 quad faces
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, 8, [(0,0,0)] * 8)  # placeholder; updated by update()

        faces = coin.SoIndexedFaceSet()
        # 6 faces × (4 vertex indices + 1 sentinel) = 30 indices
        face_idx = [
            0, 1, 2, 3, -1,   # z-min face
            4, 7, 6, 5, -1,   # z-max face
            0, 4, 5, 1, -1,   # y-min face
            1, 5, 6, 2, -1,   # x-max face
            2, 6, 7, 3, -1,   # y-max face
            0, 3, 7, 4, -1,   # x-min face
        ]
        faces.coordIndex.setValues(0, len(face_idx), face_idx)

        sep.addChild(coords)
        sep.addChild(faces)

        self._sep          = sep
        self._shader_prog  = prog
        self._vert_shader  = vert
        self._frag_shader  = frag
        self._proxy_coords = coords

        vobj.RootNode.addChild(sep)

    def set_visible(self, visible: bool):
        if self._sep:
            self._sep.whichChild = 0 if visible else -1
```

Note: `SoSeparator` does not have `whichChild`; use `SoSwitch` if visibility toggling is
needed. Replace the separator with `SoSwitch` if required (see `DMRenderer` pattern).

**Depends on:** None (standalone new file)

---

### R-009: Implement `DMRayMarchRenderer.update(field)` — shader compile + uniform management

**File:** `core/dm_ray_march_renderer.py` — add `update()` and helper methods to `DMRayMarchRenderer`

**What:** `update(field)` calls `assemble_shader(field)`, compares source with the currently
compiled shader, recompiles if changed, then refreshes all uniform values.

**Three helpers needed:**

1. `_build_uniform_node(name, value)` — creates the right `SoUniformShaderParameter*` node.
2. `_update_uniforms(params)` — creates new nodes for new uniforms, updates values for existing.
3. `_update_proxy_box(field)` — sets 8 AABB corner vertices on `_proxy_coords`.

**Implementation:**

```python
def update(self, field):
    """Recompile shader and refresh uniforms from the given FRepField."""
    if not coin:
        return
    from core.frep.glsl_assembler import assemble_shader
    vert_src, frag_src, params = assemble_shader(field)

    # Recompile only if source changed (avoid unnecessary GPU recompile)
    if frag_src != self._current_frag_src:
        self._vert_shader.sourceProgram.setValue(vert_src)
        self._frag_shader.sourceProgram.setValue(frag_src)
        self._current_frag_src = frag_src
        # Rebuild uniform node list to match new shader
        self._uniform_nodes = {}
        for idx, (name, value) in enumerate(params.items()):
            node = self._build_uniform_node(name, value)
            self._frag_shader.parameter.set1Value(idx, node)
            self._uniform_nodes[name] = node
    else:
        # Same topology — just push new values
        self._update_uniforms(params)

    self._update_proxy_box(field)

def _build_uniform_node(self, name: str, value):
    """Create a new SoUniformShaderParameter* node for the given value type."""
    if isinstance(value, float) or isinstance(value, int):
        node = coin.SoUniformShaderParameter1f()
        node.name.setValue(name)
        node.value.setValue(float(value))
    elif isinstance(value, tuple) and len(value) == 3:
        node = coin.SoUniformShaderParameter3f()
        node.name.setValue(name)
        node.value.setValue(coin.SbVec3f(*value))
    elif isinstance(value, tuple) and len(value) == 16:
        node = coin.SoUniformShaderParameterMatrix()
        node.name.setValue(name)
        node.value.setValue(coin.SbMatrix(*value))
    else:
        raise ValueError(f"Unsupported uniform type for '{name}': {type(value)}")
    return node

def _update_uniforms(self, params: dict):
    """Update values of existing uniform nodes (no recompile path)."""
    for name, value in params.items():
        node = self._uniform_nodes.get(name)
        if node is None:
            continue  # new uniform not present in old shader — skip
        if isinstance(value, float) or isinstance(value, int):
            node.value.setValue(float(value))
        elif isinstance(value, tuple) and len(value) == 3:
            node.value.setValue(coin.SbVec3f(*value))
        elif isinstance(value, tuple) and len(value) == 16:
            node.value.setValue(coin.SbMatrix(*value))

def _update_proxy_box(self, field):
    """Resize the proxy bounding box to match the field's AABB."""
    mn, mx = field.bounding_box()
    # Add a small margin (1% of max dimension) to avoid surface-at-boundary artifacts
    dims = [mx.x - mn.x, mx.y - mn.y, mx.z - mn.z]
    margin = max(dims) * 0.01 if dims else 0.1
    x0, y0, z0 = mn.x - margin, mn.y - margin, mn.z - margin
    x1, y1, z1 = mx.x + margin, mx.y + margin, mx.z + margin
    corners = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    self._proxy_coords.point.setValues(0, 8, corners)
```

**Depends on:** R-007, R-008

---

## Tier 4 — Integration

### R-010: Add `get_use_ray_march()` preference to `core/dm_object.py`

**File:** `core/dm_object.py` — add after `set_meshing_cell_size()` (after line 68)

**What:** Follow the exact pattern of the existing boolean preference `get_show_wireframe()`.

```python
def get_use_ray_march():
    """Return whether to use GPU ray marching instead of CPU meshing for F-Rep objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("UseRayMarching", False)

def set_use_ray_march(val: bool):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("UseRayMarching", bool(val))
```

The preference path `_PARAM_PATH` is already defined at line 26:
`"User parameter:BaseApp/Preferences/Mod/DirectModeling"`.

**Depends on:** None

---

### R-011: Wire `DMRayMarchRenderer` into `DMViewProvider`

**File:** `core/dm_object.py`

**What:** Two edits:

**Edit 1 — `DMViewProvider.attach()` at line 414:**

After the existing `self.renderer.setup_frep_mesh_nodes()` call, add:
```python
            elif hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "frep":
                from core.dm_object import get_use_ray_march
                if get_use_ray_march():
                    from core.dm_ray_march_renderer import DMRayMarchRenderer
                    self.ray_march_renderer = DMRayMarchRenderer(vobj)
                else:
                    self.renderer.setup_frep_mesh_nodes()
```

Replace the existing plain `self.renderer.setup_frep_mesh_nodes()` line with the block above
so that only one path is active at a time.

**Edit 2 — `DMViewProvider.updateData()` at line 441:**

Inside the `if prop == "Shape" and ... ShapeType == "frep":` block (lines 441–451), add a
branch that calls `ray_march_renderer.update(field)` instead of `update_frep_mesh()`:

```python
        if prop == "Shape" and hasattr(fp, "ShapeType") and fp.ShapeType == "frep":
            proxy = getattr(fp, "Proxy", None)
            field = getattr(proxy, "FRepField", None) if proxy else None

            ray_march = getattr(self, "ray_march_renderer", None)
            if ray_march and field:
                ray_march.update(field)
            elif proxy and self.renderer:
                self.renderer.update_frep_mesh(
                    getattr(proxy, "_frep_verts", None),
                    getattr(proxy, "_frep_idx",   None),
                )
                if field:
                    self.renderer.update_frep_corners(field)
```

Replace the existing lines 441–451 with this block.

**Important:** `proxy.FRepField` must already be set on the proxy before `fp.Shape` is
assigned in `DMObjectProxy.execute()`. Verify this is the case; if not, store the field on
the proxy before calling `fp.Shape = Part.Shape()`.

**Depends on:** R-008, R-009, R-010

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_glsl_codegen` | `to_glsl()` interface contract, return dict format, recursive composition, naming rules |
| `coin3d_shader_api` | Pivy shader nodes, uniform parameter types, proxy geometry setup, built-in GLSL matrices |
| `dm_renderer_architecture` | Full Coin3D scene graph layout, ShapeType branching, where to hook new renderers |
| `dm_logging` | Logging conventions (`dm_logger.info`, `dm_logger.debug`) |

See `.agents/workflows/` for executable workflows:

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task from this list by ID (e.g. `/fix-task R-003`) |
