---
name: DM Boolean Architecture (Two-Color)
description: Reference for the two-color (orange/blue) boolean system where IsSubtractive classifies inputs into groups and boolean operations combine them into a parent-child tree. Required reading before implementing any task in todo_bool.md.
---

# DM Boolean Architecture — Two-Color Boolean System

## Color Semantics

Every SDF primitive has `IsSubtractive` (bool FreeCAD property):

| `IsSubtractive` | Display Color | Role |
|-----------------|---------------|------|
| `False` (default) | Orange `(1.0, 0.5, 0.0)` | Additive group |
| `True` (Ctrl held during drag) | Blue `(0.3, 0.5, 1.0)` | Subtractive group |

The user sets a field's color/role by holding **Ctrl** while dragging it into place.

---

## Boolean Operations on Grouped Inputs

Boolean commands accept **any number** of selected SDF objects. Before composing,
split the selection by color:

```python
orange = [f for obj, f in fields if not obj.IsSubtractive]
blue   = [f for obj, f in fields if     obj.IsSubtractive]
```

Then compose with `UnionField` to fold each group, then apply the operation:

| Command | Result field |
|---------|-------------|
| Add | `union(orange + blue)` — all fields merged |
| Subtract | `subtract(union(orange), union(blue))` — orange minus blue |
| Intersection | `intersect(union(orange), union(blue))` — orange ∩ blue |

Result object always gets `IsSubtractive = False` (rendered orange).

Helper to fold a list with UnionField:
```python
from core.sdf.sdf_composer import UnionField

def _fold_union(fields):
    """Fold a list of SdfFields into a single UnionField tree (left-associative)."""
    if not fields:
        return None
    result = fields[0]
    for f in fields[1:]:
        result = UnionField(result, f)
    return result
```

---

## Parent-Child Tree in FreeCAD Document

The result object is the **parent**. Input objects are **children**, hidden.

FreeCAD links are established with `App::PropertyLinkList`:

```python
# On the result proxy object:
if not hasattr(result, "BooleanInputs"):
    result.addProperty("App::PropertyLinkList", "BooleanInputs", "Boolean",
                       "Child SDF objects composing this boolean")
result.BooleanInputs = list(sel)          # stores references

# Hide each input
for obj in sel:
    obj.ViewObject.Visibility = False
```

`DMViewProvider.claimChildren()` already returns `self.Object.OutList`, which
FreeCAD populates automatically when `BooleanInputs` links are set. **No extra
work needed** for tree display — the children will nest under the parent.

---

## Parametric Recompute

`DMObjectProxy.execute()` is called by FreeCAD when any linked object changes.
For a boolean result object, `execute()` should re-read `BooleanInputs`, extract
their `SdfField` attributes, and recompose.

Add a `shape_type == "boolean_sdf"` branch (or reuse `"sdf"`) inside `execute()`:

```python
if st == "sdf" and hasattr(fp, "BooleanInputs") and fp.BooleanInputs:
    # Re-compose from children
    from commands.cmd_boolean import _recompose_boolean
    field = _recompose_boolean(fp)
    if field is not None:
        self.SdfField = field
        # Trigger renderer update
        from core.dm_renderer import SdfRendererStrategy
        # ... re-register field with scene renderer
fp.Shape = Part.Shape()
```

The recompose helper reads `obj.BooleanOp` (string: "Add", "Subtract",
"Intersection") stored on the result object.

---

## Result Object Properties

The result object (`shape_type == "sdf"`) needs these extra FreeCAD properties:

```python
# Set in DMObjectProxy.__init__ when shape_type == "sdf" AND this is a boolean:
obj.addProperty("App::PropertyString",  "BooleanOp",     "Boolean", "Add/Subtract/Intersection")
obj.addProperty("App::PropertyLinkList","BooleanInputs", "Boolean", "Child SDF inputs")
```

Only add these when the object is actually a boolean result (pass `is_boolean=True`
to `create_dm_object` or set them in `cmd_boolean._sdf_boolean()`).

---

## Field Attribute Name

`proxy.SdfField` is a plain Python attribute — not a FreeCAD property. It is lost on document save/load.

**Always use `get_sdf_field(obj)` to obtain a child's field**, never access `.SdfField` directly.
This triggers lazy reconstruction on load:

```python
proxy = getattr(child, "Proxy", None)
field = (proxy.get_sdf_field(child) if proxy and hasattr(proxy, "get_sdf_field")
         else getattr(proxy, "SdfField", None))
```

## Serialization Requirements

Boolean result objects must store `SdfType = "boolean"` so they can be reconstructed on load:

```python
if not hasattr(result, "SdfType"):
    result.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
result.SdfType = "boolean"
```

`DMObjectProxy._reconstruct_field()` has a `"boolean"` branch that calls `_recompose_boolean(fp)`.
`_recompose_boolean` uses `get_sdf_field(child)` on each input, which recursively reconstructs
primitive inputs if they are not yet loaded. No ordering dependency between parent and children.

---

## Icon Color Conventions

| Icon | Left shape | Right shape |
|------|------------|-------------|
| Add | orange stroke, gray fill | orange stroke, gray fill |
| Subtract | orange stroke (solid, left crescent) | **blue** dashed stroke (circle to cut away) |
| Intersection | orange stroke, no fill | **blue** stroke, no fill; gray fill in overlap |

Colors to use in SVG:
- Orange: `#ffaa00`
- Blue:   `#4488ff`
- Gray fill: `#e0e0e0`

---

## Files to Read Before Editing

1. `commands/cmd_boolean.py` — current boolean command (lines 51–101: `_sdf_boolean`)
2. `core/sdf/sdf_composer.py` — `UnionField`, `SubtractionField`, `IntersectionField`
3. `core/dm_object.py:140` — `DMObjectProxy.__init__` (sdf property registration at line 221)
4. `core/dm_object.py:306` — `DMObjectProxy.execute()` (add boolean recompute branch)
5. `core/dm_object.py:505` — `DMViewProvider.claimChildren()` (already returns `OutList`)
6. `core/dm_scene_ray_march_renderer.py:519` — `register_field` / `update_field` API
7. `core/dm_renderer.py` — `SdfRendererStrategy.setup()` (how fields are registered)
8. `Resources/icons/MakeSubtract.svg` — subtract icon (update to blue dashed circle)
9. `Resources/icons/MakeIntersection.svg` — intersection icon (update right circle to blue)
