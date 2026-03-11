---
name: DM GLSL Code Generation
description: Reference for the to_glsl() interface that converts the Python SDF tree into GLSL source code. Required reading before implementing to_glsl() on any FRepField subclass, or before modifying GlslAssembler.
---

# DM GLSL Code Generation

The GPU ray marching renderer needs a GLSL `scene_sdf(vec3 p)` function that exactly mirrors
the Python SDF tree. Each `FRepField` subclass implements `to_glsl(node_id)` to emit its
piece of the GLSL program. `GlslAssembler` assembles those pieces into a complete shader.

---

## The `to_glsl()` Interface

**Defined in:** `core/frep/frep_field.py:FRepField` (abstract method at end of class)

```python
def to_glsl(self, node_id: str) -> dict:
    """
    Returns:
        "functions": str  — GLSL function definitions (self + all children for composers)
        "uniforms":  str  — GLSL uniform declarations for this node only
        "call":      str  — GLSL expression: e.g. "sdf_sphere_root(p)"
        "params":    dict — uniform_name → Python value (see type table)
    """
```

**`params` value types** (determines which Coin3D uniform node is created by `DMRayMarchRenderer`):

| Python value | GLSL type | Coin3D class |
|--------------|-----------|--------------|
| `float` or `int` | `float` | `SoUniformShaderParameter1f` |
| 3-tuple `(x, y, z)` | `vec3` | `SoUniformShaderParameter3f` |
| 16-tuple of float (row-major) | `mat4` | `SoUniformShaderParameterMatrix` |

---

## Naming Convention

Uniform and function names are generated from `node_id`. The node_id is passed down the
tree and extended at each composer level. Replace `-` with `_` before use.

```
node_id="root"     → uid = "sphere_root"
  child node_ids:
    "root_a"       → uid = "union_root_a"
    "root_a_a"     → uid = "sphere_root_a_a"
    "root_a_b"     → uid = "cylinder_root_a_b"
    "root_b"       → uid = "box_root_b"
```

Function names: `sdf_{uid}(vec3 p)`
Uniform names: `u_{uid}_{field}` (e.g. `u_sphere_root_a_a_center`)

---

## Primitive Example: SdfSphereField

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

---

## Composer Example: UnionField

Composers recursively call `to_glsl()` on children and concatenate their output.
The final `"functions"` string is: children's functions first, then self's function.
This ensures called functions are declared before the caller in the GLSL source.

```python
def to_glsl(self, node_id: str) -> dict:
    uid = f"union_{node_id.replace('-', '_')}"
    a   = self.a.to_glsl(f"{node_id}_a")
    b   = self.b.to_glsl(f"{node_id}_b")
    functions = (
        a["functions"] + b["functions"] +        # children first
        f"float sdf_{uid}(vec3 p) {{\n"
        f"    return min({a['call']}, {b['call']});\n"
        f"}}\n"
    )
    return {
        "functions": functions,
        "uniforms":  a["uniforms"] + b["uniforms"],  # concatenated, no duplicates possible
        "call":      f"sdf_{uid}(p)",
        "params":    {**a["params"], **b["params"]},  # merged dict
    }
```

CSG GLSL bodies:

| Class | GLSL body |
|-------|-----------|
| `UnionField` | `return min({a}, {b});` |
| `IntersectionField` | `return max({a}, {b});` |
| `SubtractionField` | `return max({a}, -({b}));` |

---

## Assembled Shader Structure

`GlslAssembler.assemble_shader(field)` in `core/frep/glsl_assembler.py`:

1. Calls `field.to_glsl("root")` to get all GLSL pieces.
2. Gets `(mn, mx) = field.bounding_box()` and appends `u_aabb_min`/`u_aabb_max` to params.
3. Substitutes three placeholders in `FRAG_TEMPLATE`:
   - `// [GENERATED_UNIFORMS]` → `glsl["uniforms"]`
   - `// [GENERATED_FUNCTIONS]` → `glsl["functions"]`
   - `[SCENE_SDF_CALL]` → `glsl["call"]`
4. Returns `(VERT_TEMPLATE, completed_frag_src, all_params_dict)`.

The caller (`DMRayMarchRenderer.update()`) passes the params dict to `_build_uniform_node()`
which dispatches on value type (float / 3-tuple / 16-tuple).

---

## Key Files

| File | Role |
|------|------|
| `core/frep/frep_field.py` | Abstract `to_glsl()` definition (end of FRepField class) |
| `core/frep/sdf/sphere.py` | Reference primitive implementation |
| `core/frep/sdf/box.py` | Box implementation (handles optional placement mat4) |
| `core/frep/sdf/cylinder.py` | Cylinder implementation |
| `core/frep/sdf/plane.py` | Half-space implementation |
| `core/frep/frep_composer.py` | UnionField / IntersectionField / SubtractionField |
| `core/frep/glsl_assembler.py` | `assemble_shader()`, `VERT_TEMPLATE`, `FRAG_TEMPLATE` |
| `core/dm_ray_march_renderer.py` | Consumes `assemble_shader()` output |

---

## Common Mistakes

- **Missing double-braces in f-strings**: GLSL braces `{` and `}` must be escaped as `{{`
  and `}}` inside Python f-strings.
- **Wrong child order**: Always emit children's `"functions"` before self's function.
  GLSL requires functions to be declared before they are called.
- **Hyphen in node_id**: Always call `.replace('-', '_')` before embedding in identifiers.
- **Forgetting to import `to_glsl()` changes**: Python caches modules; restart FreeCAD
  after modifying SDF classes during development.
