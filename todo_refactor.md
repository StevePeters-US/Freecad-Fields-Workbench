# Direct Modeling Workbench — Primitive Tool Refactor Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

Read `.agents/skills/dm_primitive_refactor/SKILL.md` before starting any task.

---

## Background

The primitive creation tools (Box, Sphere, Cylinder) share extensive duplicated logic that was copy-pasted rather than factored into the `PrimitiveCreatorBase` parent class. This causes several bugs:

1. **3rd-click rotation bug (Box):** During height drag (state 2), the ghost visual control points (Coin3D spheres/lines) diverge from the SDF render because the ghost uses `to_local`/`to_global` with the working plane while the SDF field has its own `placement` — both represent the same transform but any mismatch (e.g. the working plane is updated mid-tool) causes the visuals to rotate away from the SDF.

2. **Code duplication:** Placement extraction (checking `getGlobalPlacement`/`Placement`/raw) is repeated 6+ times across the file. The 8-corner box generation pattern appears 3 times. `_BOX_OPPOSITE` is defined in both `primitive_tool.py` and `edit_tool.py`.

3. **Obsolete code:** `NURBSPrimitiveCreator` in `dm_base.py` has duplicate line assignments (`self._last_shape_type = shape_type` twice, `active_placement = placement` twice) and is barely used.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `PrimitiveCreatorBase` | `tools/primitive_tool.py:28` | Base class for all SDF primitive tools |
| `DMBase` | `tools/dm_base.py:74` | Base class for all tools |
| `DragTimerMixin` | `tools/dm_base.py:28` | QTimer drag polling mixin |
| `NURBSPrimitiveCreator` | `tools/dm_base.py:948` | NURBS curve/surface creator base (obsolete?) |
| `BoxCreator` | `tools/primitive_tool.py:405` | 3-click box creator |
| `SphereCreator` | `tools/primitive_tool.py:721` | 2-click sphere creator |
| `CylinderCreator` | `tools/primitive_tool.py:868` | 3-click cylinder creator |
| `SdfEditTool` | `tools/edit_tool.py:479` | Standalone SDF corner editor |
| `SdfBoxField` | `core/sdf/sdf/box.py:5` | Box SDF field with placement |
| `to_local` | `tools/dm_base.py:891` | World→local via working_plane inverse |
| `to_global` | `tools/dm_base.py:900` | Local→world via working_plane matrix |

---

## Tier 1 — Extract Shared Helpers (Do First)

These tasks extract duplicated patterns into `PrimitiveCreatorBase` so subclasses inherit them.

### RF-001: Extract `_get_placement()` helper into PrimitiveCreatorBase

**File:** `tools/primitive_tool.py` — insert after `_init_working_plane()` (line 183)

**What:** The pattern `if wp: if hasattr(wp, "getGlobalPlacement"): placement = ... elif hasattr(wp, "Placement"): ...` is repeated 6+ times across BoxCreator, SphereCreator, and CylinderCreator. Extract it once.

**Implementation:**
```python
def _get_placement(self):
    """Return a FreeCAD.Placement from self.working_plane, or None."""
    wp = getattr(self, "working_plane", None)
    if wp is None:
        return None
    if hasattr(wp, "getGlobalPlacement"):
        return wp.getGlobalPlacement()
    elif hasattr(wp, "Placement"):
        return wp.Placement
    return wp
```

Then update all callers in BoxCreator (`_make_field`, `_field_from_two_corners`), SphereCreator (`_get_preview_field`), and CylinderCreator (`_get_preview_field`, `_get_final_field`) to call `self._get_placement()` instead of the inline pattern. Search for `hasattr(wp, "getGlobalPlacement")` to find all instances.

**Acceptance:** `grep -n "getGlobalPlacement" tools/primitive_tool.py` should return only the one inside `_get_placement()` plus the existing uses in `_init_working_plane` and `__do_commit`. Tool behavior unchanged — run `python tests/test_imports.py`.

---

### RF-002: Extract `_box_corners_local()` into PrimitiveCreatorBase

**File:** `tools/primitive_tool.py` — insert after `_get_placement()` (wherever RF-001 lands)

**What:** The 8-corner box computation from center + half_size appears in `BoxCreator._update_ghost_visuals` (lines 586-595), `BoxCreator._get_final_points` (lines 708-717), `BoxCreator._refresh_edit_corners` (lines 497-510), and `SdfEditTool._refresh_corners` (lines 540-548). Extract it once.

**Implementation:**
```python
@staticmethod
def _box_corners_local(center, half_size):
    """Return list of 8 FreeCAD.Vector corners in local space.
    
    Order: (-,-,-) (+,-,-) (+,+,-) (-,+,-) (-,-,+) (+,-,+) (+,+,+) (-,+,+)
    """
    c, h = center, half_size
    return [
        FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z - h.z),
        FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z - h.z),
        FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z - h.z),
        FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z - h.z),
        FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z + h.z),
        FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z + h.z),
        FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z + h.z),
        FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z + h.z),
    ]
```

Replace inline 8-corner constructions in `_update_ghost_visuals`, `_get_final_points`, and `_refresh_edit_corners` with calls to `self._box_corners_local(center, half_size)`.

**Acceptance:** All inline 8-corner lists replaced. `python tests/test_imports.py` passes.

---

### RF-003: Unify `_BOX_OPPOSITE` — single definition

**File:** `tools/primitive_tool.py` (line 24) and `tools/edit_tool.py` (line 476)

**What:** The `_BOX_OPPOSITE` / `_SDF_OPPOSITE` dictionary is defined identically in both files. Move it to `primitive_tool.py` and import from there in `edit_tool.py`.

**Implementation:**
1. Keep `_BOX_OPPOSITE` in `tools/primitive_tool.py` at line 24 (already there).
2. In `tools/edit_tool.py`, delete `_SDF_OPPOSITE` dict (line 476) and add at the top imports:
   ```python
   from tools.primitive_tool import _BOX_OPPOSITE
   ```
3. In `SdfEditTool._start_drag` (line 587), change `_SDF_OPPOSITE[idx]` → `_BOX_OPPOSITE[idx]`.

**Acceptance:** `grep -n "_SDF_OPPOSITE" tools/edit_tool.py` returns 0 results. `python tests/test_imports.py` passes.

---

## Tier 2 — Fix Local/Global Coordinate Bug

These tasks fix the 3rd-click rotation bug by ensuring control points and SDF fields always agree on the coordinate frame.

### RF-004: Lock placement at first click in BoxCreator

**File:** `tools/primitive_tool.py` — `BoxCreator.on_button1_down` (line 512)

**What:** Currently the working_plane can drift between clicks because `_resolve_wp_click` updates `self.working_plane` even after state 0. The field's `placement` should be captured once at state 0 and not changed. Store `self._field_placement` at the first click.

**Implementation:**
1. In `on_button1_down`, state 0 block (line 524), after `self.state = 1`, add:
   ```python
   self._field_placement = self._get_placement()
   ```
2. In `_make_field` (line 665), replace the entire `placement = None; if wp: ...` block (lines 683-691) with:
   ```python
   placement = getattr(self, "_field_placement", self._get_placement())
   ```
3. In `_field_from_two_corners` (line 467), replace the `placement = None; if wp: ...` block (lines 482-490) with:
   ```python
   placement = getattr(self, "_field_placement", self._get_placement())
   ```

**Depends on:** RF-001

**Acceptance:** Box tool creates a box whose ghost visuals (orange wireframe) align pixel-perfectly with the SDF render at all 3 clicks, especially on rotated workplanes.

---

### RF-005: Lock placement at first click in CylinderCreator

**File:** `tools/primitive_tool.py` — `CylinderCreator.on_button1_down` (line 924)

**What:** Same fix as RF-004 but for cylinders.

**Implementation:**
1. In `on_button1_down`, state IDLE block (line 934), after `self.state = self.ToolState.PICK_RADIUS`, add:
   ```python
   self._field_placement = self._get_placement()
   ```
2. In `_get_preview_field` (line 1036), replace the `field_placement = None; if wp: ...` block (lines 1064-1071) with:
   ```python
   field_placement = getattr(self, "_field_placement", self._get_placement())
   ```
3. Same change in `_get_final_field` (lines 1095-1102).

**Depends on:** RF-001

**Acceptance:** Cylinder ghost visuals align with SDF render during height drag on rotated workplanes.

---

## Tier 3 — Ghost Visual Deduplication

### RF-006: Extract `_update_handle_positions()` into PrimitiveCreatorBase

**File:** `tools/primitive_tool.py` — insert in `PrimitiveCreatorBase`

**What:** Every subclass (`BoxCreator._update_ghost_visuals`, `SphereCreator._update_ghost_visuals`, `CylinderCreator._update_ghost_visuals`) has the same pattern for updating `dm_points`: check length, append new DMPoints, compute handle radius, update positions. Extract the common update loop.

**Implementation:**
```python
def _update_handle_positions(self, world_pts, color=(1.0, 0.5, 0.0)):
    """Update dm_points to match the given world-space positions.
    
    Creates new DMPoint objects as needed, updates existing ones.
    """
    while len(self.dm_points) < len(world_pts):
        self.dm_points.append(DMPoint(world_pts[len(self.dm_points)]))
    
    r = self._compute_handle_radius(ref_pt=world_pts[0] if world_pts else None)
    for i, pt in enumerate(world_pts):
        if i >= len(self.dm_points):
            break
        self.dm_points[i].position = pt
        if self.dm_points[i]._point_sep is None:
            self.dm_points[i].draw_point(self.points_root, radius=r, color=color)
        else:
            self.dm_points[i].update_draw(radius=r)
```

Then simplify the `_update_ghost_visuals` methods in all three creators to call `self._update_handle_positions(world_pts)` instead of the inline loop.

**Acceptance:** All three primitive creators use `_update_handle_positions`. `python tests/test_imports.py` passes.

---

### RF-007: Extract `_height_drag_move()` shared method

**File:** `tools/primitive_tool.py` — insert in `PrimitiveCreatorBase`

**What:** `BoxCreator.on_move_state_2` and `CylinderCreator.on_move_state_2` are identical — they both constrain mouse movement to the workplane normal. Extract this into the base class.

**Implementation:**
```python
def _height_drag_move(self, event_dict):
    """Move current_point along workplane normal from _height_drag_base."""
    if getattr(self, "_height_drag_base", None) is None:
        return
    wp = getattr(self, "working_plane", None)
    normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
    self.current_point = DMInputManager.get_instance().get_axis_point(
        self.view, self._height_drag_base, normal, event_dict
    )
```

Then in `BoxCreator` and `CylinderCreator`, replace `on_move_state_2` bodies with:
```python
def on_move_state_2(self, event_dict):
    self._height_drag_move(event_dict)
```

**Acceptance:** `BoxCreator.on_move_state_2` and `CylinderCreator.on_move_state_2` are each 2 lines. `python tests/test_imports.py` passes.

---

## Tier 4 — Dead Code Cleanup

### RF-008: Fix duplicate assignments in NURBSPrimitiveCreator

**File:** `tools/dm_base.py` — `NURBSPrimitiveCreator.update_active_object` (lines 968-1039)

**What:** Two duplicate lines exist:
- Line 976: `self._last_shape_type = shape_type` (duplicate of line 975)
- Line 982: `active_placement = placement` (duplicate of line 980)

**Implementation:**
1. Delete line 976 (`self._last_shape_type = shape_type` — the second occurrence).
2. Delete line 982 (`active_placement = placement` — the second occurrence). Also delete the duplicate comment `# Track placement` on line 981.

**Acceptance:** `grep -c "_last_shape_type = shape_type" tools/dm_base.py` returns 1. `grep -c "active_placement = placement" tools/dm_base.py` returns 1.

---

### RF-009: Clean up CylinderCreator duplicate workplane init

**File:** `tools/primitive_tool.py` — `CylinderCreator.__init__` (lines 880-893)

**What:** CylinderCreator manually re-implements workplane pre-loading (lines 887-891) even though `PrimitiveCreatorBase.__init__` already calls `self._init_working_plane()`. The `_last_working_plane` class variable pattern should be moved to `_init_working_plane` or the duplication removed.

**Implementation:**
1. Remove lines 887-891 (the inline workplane init block in CylinderCreator.__init__).
2. If `_last_working_plane` persistence is needed, add it to `_init_working_plane` in `PrimitiveCreatorBase` as a class-level fallback (all primitives benefit from remembering the last WP).

**Acceptance:** CylinderCreator.__init__ no longer references `_last_working_plane` directly. `python tests/test_imports.py` passes.

---

### RF-010: Remove ToolState enum duplication

**File:** `tools/dm_base.py` (lines 61-73)

**What:** Both `ToolState` (IntEnum) and `STATE_IDLE`/`STATE_ACTIVE`/`STATE_DRAGGING`/`STATE_FINALIZED` (bare integers) are defined. The bare integers are imported in `primitive_tool.py` (`from tools.dm_base import ... STATE_IDLE, STATE_DRAGGING`). Migrate all users to `ToolState` enum values and delete the deprecated constants.

**Implementation:**
1. In `tools/primitive_tool.py`, change imports: remove `STATE_IDLE, STATE_DRAGGING`, add `ToolState`.
2. Replace all uses of `STATE_IDLE` with `ToolState.IDLE` and `STATE_DRAGGING` with `ToolState.DRAGGING` in `primitive_tool.py`.
3. Delete lines 68-72 in `dm_base.py` (the deprecated constants and comment).

**Acceptance:** `grep -rn "STATE_IDLE\|STATE_DRAGGING\|STATE_ACTIVE\|STATE_FINALIZED" tools/` returns 0 results for bare constants (only ToolState.* enum uses). `python tests/test_imports.py` passes.

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_primitive_refactor` | Coordinate frame contract, helper method signatures, and invariants for this refactor |
| `dm_primitive_tool` | Primitive tool implementation pattern (needs update after this refactor) |
| `dm_tool_refactor_pattern` | Drag timer, cursor helpers, throttle patterns |

See `.agents/workflows/` for executable workflows:

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task by ID (e.g. `/fix-task RF-003`) |
