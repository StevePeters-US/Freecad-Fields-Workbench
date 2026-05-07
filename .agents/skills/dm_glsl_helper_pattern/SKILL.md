---
name: DM GLSL Helper Pattern (per-primitive registration)
description: Convention for registering primitive-specific GLSL inside the primitive's own .py file. Required reading before editing any to_glsl/to_glsl_2d method.
---

# DM GLSL Helper Pattern

The compiler at `core/sdf/glsl_compiler.py` does not know about specific
primitives. Each primitive owns the GLSL it emits.

## Rules

1. **GLSL bodies live as module-level constants** in the primitive's `.py`
   file, named `_GLSL_<NAME>` (e.g. `_GLSL_SDF_BOX`).
2. **`to_glsl` registers helpers via `ctx.add_custom_helper(name, body)`**,
   not `ctx.need_helper(name)`. The latter is removed.
3. **Names are deduplication keys**. Two primitives that need the same
   helper (e.g. `apply_inv_mat`) must use the **same** name in
   `add_custom_helper` so the compiler emits only one definition.
4. **Helper bodies must not depend on uniforms**. Pass everything as
   function arguments. Uniform names are per-call (`ctx.uniform`), helper
   bodies are reused.
5. **`get_unique_name()` is for per-call helpers** (e.g. one helper per
   set of polygon vertices). The unique name is then registered as the
   helper's primary name, not deduplicated against any other primitive.
6. **`apply_inv_mat`** is shared across many primitives. Its GLSL string
   lives in `core/sdf/sdf_field.py` as `_GLSL_APPLY_INV_MAT`. Primitives
   that need it import it from there.

## Reference Implementations

- **Static helper, used by many primitives:** `core/sdf/sdf_field.py`
  defines `_GLSL_APPLY_INV_MAT`; each primitive calls
  `ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)` when its
  `inv_matrix is not None`.

- **Static helper, used by one primitive:** `core/sdf/sdf/sphere.py`
  defines `_GLSL_SDF_SPHERE` and registers it with name `"sdf_sphere"`.

- **Per-instance helper (unique name):** `core/sdf/sdf2d/polygon.py`
  generates a unique name via `ctx.get_unique_name("sdf_poly")` because
  each polygon's helper inlines its vertex literals.

- **Cross-cutting shared helpers:** `core/sdf/sdf2d/bezier_curve.py`
  registers three named helpers (`sd_solve_cubic`, `sd_cubic_bez_2d`,
  `sd_cubic_winding`) that any other 2D Bezier-using primitive can also
  reference by registering with the same names.

## Template — single-primitive helper

```python
# At module top of core/sdf/sdf/<name>.py:
from core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT

_GLSL_SDF_FOO = """
float sdf_foo(vec3 p, vec3 center, float param) {
    // ... formula ...
    return d;
}
"""

# In the class body:
def to_glsl(self, ctx, point_var="p"):
    ctx.add_custom_helper("sdf_foo", _GLSL_SDF_FOO)
    c     = ctx.uniform("vec3", (self.center.x, self.center.y, self.center.z))
    param = ctx.uniform("float", self.param)
    if self.inv_matrix is not None:
        ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
        m = ctx.uniform("mat4", self.inv_matrix.tolist())
        return f"sdf_foo(apply_inv_mat({m}, {point_var}), {c}, {param})"
    return f"sdf_foo({point_var}, {c}, {param})"
```

## Template — per-instance helper (unique name, vertex literals)

```python
def to_glsl(self, ctx, point_var="p"):
    func_name = ctx.get_unique_name("sdf_my_helper")
    body = f"float {func_name}(vec3 p) {{\n"
    # ... build body using self.<inlined_data> as numeric literals ...
    body += "}\n"
    ctx.add_custom_helper(func_name, body)
    return f"{func_name}({point_var})"
```

## What the compiler does after the refactor

`core/sdf/glsl_compiler.py:build_compute_shader` and
`build_multi_raymarch_fragment_shader` collect helpers by iterating
`ctx._custom_helpers.values()` (insertion-ordered dict). Static helpers
no longer come from `GLSL_HELPERS` — that dict is removed in OPT-011.

## Common mistakes

1. **Using `need_helper` after the refactor.** The method is removed.
   Always use `add_custom_helper`.
2. **Different names for the same helper.** If primitive A registers
   `"apply_inv_mat"` and primitive B registers `"apply_invmat"`, the
   shader has two duplicate function definitions and may fail to compile.
3. **Helper body referencing uniforms by name.** Helpers must take their
   parameters as function arguments. The uniform names are per-call.
4. **Forgetting to import `_GLSL_APPLY_INV_MAT`** in primitives that
   support placement matrices. It must be imported from
   `core.sdf.sdf_field`.

## Files to Read Before Editing

1. `core/sdf/sdf2d/bezier_curve.py:8-93,202-226` — the cleanest existing
   implementation; mirror its structure.
2. `core/sdf/sdf2d/polygon.py:79-103` — example of `get_unique_name`
   for inlined-data helpers.
3. `core/sdf/glsl_compiler.py` — read after OPT-011 to confirm the
   compiler API surface (`add_custom_helper`, `uniform`,
   `get_unique_name` are the only context methods).
