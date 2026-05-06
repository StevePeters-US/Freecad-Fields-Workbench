---
name: DM Primitive Tool Refactor
description: Coordinate frame contract, helper method signatures, and invariants for the primitive tool refactor (todo_refactor.md). Required reading before implementing RF-* tasks.
---

# DM Primitive Tool Refactor

## Problem Summary

Primitive creator tools (Box, Sphere, Cylinder) operate in two coordinate frames simultaneously:

1. **Working plane frame** (local) — used by `to_local()`/`to_global()` for ghost visuals (orange wireframe + control point spheres)
2. **SDF field placement** — stored as `field.placement` and used by the GPU ray marcher for rendering

These are supposed to be the same transform, but because the placement is extracted independently each time a field is created (via inline `if hasattr(wp, "getGlobalPlacement")` blocks), and the working plane can drift between clicks, they can diverge. This causes the ghost visuals to rotate away from the SDF render.

## Coordinate Frame Contract (After Refactor)

```
First click (state 0):
  1. _resolve_wp_click() sets self.working_plane
  2. self._field_placement = self._get_placement()   ← LOCKED
  
Subsequent clicks/drags:
  - to_local()/to_global() use self.working_plane     (unchanged)
  - _make_field()/_get_preview_field() use self._field_placement
  - Both reference the SAME transform (captured at the SAME moment)
  - working_plane is NOT updated after state 0

Commit:
  - __do_commit() uses self._field_placement for obj.Placement
  - Points are stored in LOCAL space (relative to _field_placement)
```

## Helper Methods Added to PrimitiveCreatorBase

### `_get_placement(self) -> FreeCAD.Placement | None`
Extracts a `FreeCAD.Placement` from `self.working_plane`, handling the three possible types:
- Object with `getGlobalPlacement()` method
- Object with `Placement` attribute
- Raw `FreeCAD.Placement` value

### `_box_corners_local(center, half_size) -> list[FreeCAD.Vector]`
Static method. Returns 8 corners of an axis-aligned box in local space. Standard ordering:
```
0: (-,-,-)  1: (+,-,-)  2: (+,+,-)  3: (-,+,-)
4: (-,-,+)  5: (+,-,+)  6: (+,+,+)  7: (-,+,+)
```

### `_update_handle_positions(self, world_pts, color=(1.0, 0.5, 0.0))`
Updates `self.dm_points` list to match given world-space positions. Creates new `DMPoint` objects as needed, updates existing ones with proper radius.

### `_height_drag_move(self, event_dict)`
Moves `self.current_point` along the workplane normal from `self._height_drag_base`. Used by both Box and Cylinder height drags.

## Invariants — Do NOT Break

1. **`to_local()`/`to_global()` must always agree with `_field_placement`** — If `_field_placement` is set, `self.working_plane` must be the same value.

2. **Points property stores LOCAL coords** — `_get_final_points()` returns points in local space (relative to `_field_placement`). `edit_object()` converts them back to world space via `self.working_plane.multVec()`.

3. **Ghost visuals are in WORLD space** — `DMPoint.position` and `DMLineSet` coordinates are always world-space. Tools compute world coords via `to_global(local_pt)`.

4. **SDF field center/params are in LOCAL space** — `SdfBoxField.center`, `SdfSphereField.center`, etc. are in the field's local coordinate frame. The `placement` transform maps them to world space for rendering.

5. **`_BOX_OPPOSITE` is the canonical source** — Defined in `tools/primitive_tool.py` line 24. `edit_tool.py` imports from there. Do not redefine.

## Files to Read Before Editing

1. `tools/primitive_tool.py` — full file (1114 lines) — all three creators + base class
2. `tools/dm_base.py:891-905` — `to_local()` and `to_global()` 
3. `core/sdf/sdf/box.py` — SdfBoxField constructor, placement handling
4. `tools/edit_tool.py:479-690` — SdfEditTool for reference (uses same patterns)
