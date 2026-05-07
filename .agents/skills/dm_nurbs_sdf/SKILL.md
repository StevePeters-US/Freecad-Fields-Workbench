---
name: DM NURBS SDF Fields
description: FreeCAD BSpline API reference and to_glsl() contract for SdfNurbsCurveField and SdfNurbsSurfaceField — tasks NS-001..NS-008 in todo_arch_refactor.md.
---

# DM NURBS SDF Fields

Reference for NS-001..NS-008 in `todo_arch_refactor.md`.

## New Files Created by NS Tasks

| File | Class | Task |
|------|-------|------|
| `core/sdf/sdf/nurbs_curve.py` | `SdfNurbsCurveField(SdfField)` | NS-001, NS-004 |
| `core/sdf/sdf2d/nurbs_curve.py` | `Sdf2dNurbsCurveField(Sdf2dField)` | NS-002, NS-005 |
| `core/sdf/sdf/nurbs_surface.py` | `SdfNurbsSurfaceField(SdfField)` | NS-003, NS-006 |

## FreeCAD BSpline API

### Part.BSplineCurve (3D curve)
```python
curve = fp.Shape.Edges[0].Curve       # extract from DMObject shape
poles = curve.getPoles()              # list of FreeCAD.Vector
u0, u1 = curve.FirstParameter, curve.LastParameter
pt = curve.value(u)                   # FreeCAD.Vector at parameter u
param = curve.parameter(point)        # parameter of nearest point (raises if fails)
tangent = curve.tangent(u)[0]         # tangent FreeCAD.Vector at u
```

### Part.BSplineSurface (NURBS surface)
```python
surface = fp.Shape.Faces[0].Surface   # extract from DMObject shape
u, v = surface.UVProjPoint(point)     # (u,v) of nearest surface point
pt = surface.value(u, v)              # FreeCAD.Vector
normal = surface.normal(u, v)         # outward normal FreeCAD.Vector
poles = surface.getPoles()            # list of lists of FreeCAD.Vector
```

### Gotcha: parameter() can raise
`curve.parameter(point)` raises if `point` is far from the curve or the curve is degenerate.
Always wrap in `try/except` and return `float('inf')` on failure.

### Gotcha: UVProjPoint parameter order
`UVProjPoint` returns `(u, v)` as floats (not a tuple in all FreeCAD versions). Check:
```python
result = surface.UVProjPoint(point)
u, v = float(result[0]), float(result[1])
```

## to_glsl() Contract

All NURBS SDF fields must implement `to_glsl(ctx, point_var="p")` following the GlslContext
pattern used by existing primitives. See `core/sdf/sdf/sphere.py:41` for a working example.

```python
def to_glsl(self, ctx, point_var="p"):
    ctx.need_helper("helper_name")              # request a GLSL helper function
    u = ctx.uniform("vec3", (x, y, z))          # register a uniform, returns GLSL name
    r = ctx.uniform("float", self.radius)
    return f"helper_name({point_var}, {u}, {r})"  # return GLSL expression string
```

Key rules:
- Return a **GLSL expression string** (not a statement), e.g. `"sdf_sphere(p, u_abc, u_def)"`
- All constants go through `ctx.uniform()` — never hardcode numbers in the expression
- Helper functions go in `GLSL_HELPERS` dict in `core/sdf/glsl_compiler.py`
- Arrays: `ctx.uniform("vec3[16]", flat_list)` where `flat_list` is a Python list of floats

## GlslContext API

```python
class GlslContext:
    def uniform(self, glsl_type: str, value) -> str:
        """Register uniform, return its GLSL name (e.g. 'u_a3f2b_0')."""

    def need_helper(self, name: str):
        """Mark a GLSL_HELPERS entry as needed in the compiled shader."""

    def add_custom_helper(self, name: str, body: str):
        """Register an inline GLSL function not in GLSL_HELPERS."""
```

File: `core/sdf/glsl_compiler.py:11`

## GLSL Helper Template for Array Uniforms

GLSL does not allow variable-size arrays. Use a fixed max size:

```glsl
// Max 32 control points — declare as fixed array
float sdf_nurbs_curve(vec3 p, vec3 poles[32], int n, float r) {
    // ... iterate i from 0 to n-1
}
```

When registering: `ctx.uniform("vec3[32]", flat_list_padded_to_96_floats)`

## Inverse Matrix Pattern (for Placement transforms)

Copy from `SdfSphereField` (`core/sdf/sdf/sphere.py:12-21`):
```python
self.inv_matrix = None
if self.placement is not None:
    m = self.placement.toMatrix()
    m.invert()
    self.inv_matrix = np.array([
        [m.A11, m.A12, m.A13, m.A14],
        ...
    ], dtype=np.float32)
```
In `to_glsl()`:
```python
if self.inv_matrix is not None:
    ctx.need_helper("apply_inv_mat")
    m = ctx.uniform("mat4", self.inv_matrix.tolist())
    return f"helper(apply_inv_mat({m}, {point_var}), ...)"
```

## Sdf2dField Base Class

`core/sdf/sdf2d/sdf2d_field.py` — `Sdf2dField` abstract base requires:
- `evaluate_2d(x: float, y: float) → float`
- `to_glsl_2d(ctx, point_var="p2") → str`  (optional, raises NotImplementedError by default)

## SdfExtrusionField Integration

`core/sdf/sdf_extrusion.py` — `SdfExtrusionField(profile: Sdf2dField, height: float, placement)`

The `Sdf2dNurbsCurveField` created by NS-002 slots directly into `SdfExtrusionField` as a
profile. The extrusion handles the 2D→3D transform; the profile only needs `evaluate_2d()`.

## Files to Read Before Editing

1. `core/sdf/sdf_field.py:1-80` — `SdfField` abstract base
2. `core/sdf/sdf/sphere.py` — complete working `to_glsl()` example with `apply_inv_mat`
3. `core/sdf/glsl_compiler.py:11-44` — `GlslContext` class
4. `core/sdf/glsl_compiler.py:46-120` — `GLSL_HELPERS` dict (add new entries here)
5. `core/sdf/sdf2d/sdf2d_field.py` — `Sdf2dField` abstract base
6. `core/sdf/sdf_extrusion.py` — how 2D profiles are consumed
7. `todo_arch_refactor.md` — NS-001..NS-008 full task implementations
