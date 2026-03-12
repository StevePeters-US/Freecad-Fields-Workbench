---
name: DM Formulaic SDF Rendering
description: Reference for the to_glsl() contract, return format, GLSL naming conventions, and how the formulaic SDF rendering pipeline works. Required reading before implementing any to_glsl() method or modifying glsl_assembler.py.
---

# DM Formulaic SDF Rendering

The formulaic SDF pipeline compiles FRep expression trees directly into GLSL code.
Each `FRepField` subclass emits its exact analytical SDF formula as GLSL fragments,
which are assembled into a complete fragment shader for GPU ray marching.

---

## `to_glsl()` Contract

Every `FRepField` subclass that supports formulaic rendering must implement:

```python
def to_glsl(self, node_id: str) -> dict:
```

### Arguments

| Arg | Type | Description |
|-----|------|-------------|
| `node_id` | `str` | Unique ID for name-mangling. May contain hyphens — replace with underscores for GLSL identifiers using `node_id.replace('-', '_')`. |

### Return Dict

| Key | Type | Description |
|-----|------|-------------|
| `functions` | `str` | One or more GLSL function definitions. Must be valid standalone (no forward declarations needed). |
| `uniforms` | `str` | GLSL `uniform` declarations. One per line, with trailing `\n`. |
| `call` | `str` | GLSL expression that evaluates this SDF at variable `p` (a `vec3`). Example: `"sdf_sphere_0(p)"` |
| `params` | `dict` | `{uniform_name: value}` mapping. Values are Python types that map to Coin3D uniform nodes. |

### Value Types in `params`

| Python value | GLSL type | Coin3D node |
|-------------|-----------|-------------|
| `float` | `float` | `SoShaderParameter1f` |
| `(x, y, z)` tuple | `vec3` | `SoShaderParameter3f` |
| `int` | `int` | `SoShaderParameter1i` |
| list of 16 floats | `mat4` | `SoShaderParameterMatrix` (column-major) |

---

## GLSL Naming Conventions

All generated names must be unique across the tree. Use `node_id` as a suffix:

| Pattern | Example |
|---------|---------|
| Function name | `sdf_sphere_{uid}` |
| Uniform name | `u_sphere_{uid}_center` |
| Composer function | `sdf_union_{uid}` |

Where `uid = node_id.replace('-', '_')`.

### Primitive prefixes

| Primitive | Prefix |
|-----------|--------|
| Sphere | `sphere` |
| Box | `box` |
| Cylinder | `cyl` |
| Plane | `plane` |
| Union | `union` |
| Intersection | `intersect` |
| Subtraction | `subtract` |

---

## Composer Pattern

CSG operations (Union, Intersection, Subtraction) call `to_glsl()` on both children,
passing modified `node_id` values to ensure uniqueness:

```python
a = self.a.to_glsl(f"{node_id}_a")
b = self.b.to_glsl(f"{node_id}_b")
```

The composer's `functions` string concatenates children's functions + its own wrapper:

```python
functions = a["functions"] + b["functions"] + f"float sdf_union_{uid}(vec3 p) {{ ... }}"
```

The composer's `params` dict merges children: `{**a["params"], **b["params"]}`.

---

## `glsl_assembler.py` Output

`assemble_sdf_shader(field, node_id="root")` returns `(frag_source, params)`:

- `frag_source` — complete GLSL fragment shader including:
  - All uniforms (from tree + `u_bbox_min`, `u_bbox_max`)
  - All SDF functions
  - `sdf(vec3 p)` master function calling root's `call` expression
  - `sdf_normal(vec3 p)` using tetrahedron gradient
  - `main()` with NDC unprojection, sphere trace loop, Phong shading, depth write
- `params` — merged params dict from the entire tree

The vertex shader is fixed (full-screen quad, `gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0)`).

---

## Shader Recompilation Strategy

- **Tree topology change** (add/remove primitive): recompile shader
- **Parameter change** (move, resize): update uniforms only
- **Detection**: `_tree_signature(field)` walks the tree and produces a structure hash

---

## GLSL Version Requirements

All generated GLSL must use **GLSL 1.10/1.20** constructs only:
- `texture2D` (not `texture`)
- `varying` (not `in`/`out`)
- No `uvec`, `uint`, bitwise operations
- No `#version` directive (Coin3D defaults to compatibility profile)

---

## Files to Read Before Editing

1. `core/frep/frep_field.py` — base class with `to_glsl()` abstract method
2. `core/frep/sdf/sphere.py` — reference primitive implementation
3. `core/frep/frep_composer.py` — CSG operations
4. `core/frep/glsl_assembler.py` — shader assembly (created in S-007)
5. `core/dm_ray_march_renderer.py` — consumer of assembled shaders
6. `todo_sdf.md` — task list for the full pipeline
