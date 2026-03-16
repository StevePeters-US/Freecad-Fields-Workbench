---
name: DM Additive/Subtractive Primitive Pattern
description: How primitives store and render their additive/subtractive mode. Required reading before implementing any additive/subtractive toggle or per-field color rendering.
---

# DM Additive/Subtractive Primitive Pattern

Every F-Rep primitive is either **additive** (default) or **subtractive**. Additive primitives render orange `(1.0, 0.5, 0.0)`. Subtractive primitives render blue `(0.3, 0.5, 1.0)`. The mode is stored as a FreeCAD property on the object and passed to the GPU shader as a per-field uniform.

---

## Object Property

`DMObjectProxy.__init__` adds this property for `shape_type == "frep"`:

```python
if not hasattr(obj, "IsSubtractive"):
    obj.addProperty("App::PropertyBool", "IsSubtractive", "FRep",
                     "If True, this primitive subtracts material")
    obj.IsSubtractive = False
```

---

## Shader Uniform

The scene ray march renderer (`DMSceneRayMarchRenderer`) passes a per-field int array:

```glsl
uniform int u_is_subtractive[8];
```

In `_rebuild()`, for each visible field:

```python
obj = ... # the FreeCAD object for this field
is_sub = getattr(obj, "IsSubtractive", False)
self._u[f"u_is_subtractive[{fi}]"].value.setValue(1 if is_sub else 0)
```

In the fragment shader, after computing `hit_field`:

```glsl
vec3 base_color = (u_is_subtractive[hit_field] == 1)
    ? vec3(0.3, 0.5, 1.0)   // blue = subtractive
    : vec3(1.0, 0.5, 0.0);  // orange = additive
vec3 color = base_color * (0.15 + 0.75 * diff) + vec3(0.4) * spec;
```

---

## Ctrl Toggle During Drag

During primitive placement (in `PrimitiveCreatorBase` subclasses), the Ctrl key toggles the preview object between additive and subtractive:

```python
# In update_preview() or _apply_preview_field():
im = DMInputManager.get_instance()
if self._preview_obj and hasattr(self._preview_obj, "IsSubtractive"):
    self._preview_obj.IsSubtractive = im.is_ctrl_down()
```

The user holds Ctrl during drag to switch to subtractive. Releasing Ctrl switches back. The final value at commit time is persisted.

---

## Label Mapping for Per-Field Object Lookup

`DMSceneRayMarchRenderer._fields` stores `label -> (field, visible)`. To look up the FreeCAD object for a label during `_rebuild()`:

```python
doc = FreeCAD.activeDocument()
obj = doc.getObject(label) if doc else None
```

This works because `create_dm_object` names the FreeCAD object with the same label used in `update_field(label, field)`.

---

## Double-Click to Re-Edit

When no tool is active, `DMInputManager.eventFilter` handles `QEvent.MouseButtonDblClick` with `Qt.LeftButton`:

1. Call `DMSelectionManager.try_sdf_selection(qt_pos)` to select the SDF object under the cursor.
2. If an SDF frep object is now selected, launch `FRepEditTool` and call `activate()`.

This reuses the existing `FRepEditTool` which reads the selected object's `FRepField` and allows corner-drag editing.

---

## Files to Read Before Editing

1. `core/dm_object.py:167` — `DMObjectProxy.__init__` (property registration for frep)
2. `core/dm_scene_ray_march_renderer.py:73` — `_setup_nodes` (uniform registration)
3. `core/dm_scene_ray_march_renderer.py:480` — `_rebuild` (per-field uniform upload)
4. `tools/primitive_tool.py:51` — `PrimitiveCreatorBase.update_preview` (preview update loop)
5. `core/input_manager.py:202` — `is_ctrl_down()` accessor
6. `core/input_manager.py:30` — `eventFilter` (where to add double-click handler)
7. `tools/edit_tool.py:483` — `FRepEditTool` (existing edit tool for frep objects)
