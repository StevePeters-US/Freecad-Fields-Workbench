# Direct Modeling Workbench — Primitive Flow Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and class/method to change.

---

## Background

The current primitive tools (Box, Sphere, Cylinder) use a multi-click creation flow:
Box requires 3 clicks, Sphere 2, Cylinder 3. This is verbose and unfamiliar to users
coming from direct-modeling tools.

The new flow is **single-click creation with immediate edit mode**:
1. User clicks once to place the primitive origin
2. A small default primitive spawns at the click point, sized relative to the current
   viewport (~15% of camera height)
3. All N control handles appear immediately; the tool enters edit mode
4. User drags any handle to reshape; Ctrl-drag translates the whole primitive;
   Shift-drag rotates it; Z toggles subtractive (blue) mode

After all tasks are complete:
- `BoxCreator`, `SphereCreator`, `CylinderCreator` each have a `_spawn_default_primitive`
  that fires on the first click and commits + enters edit immediately
- Multi-step state machines (`on_move_state_1`, etc.) are removed
- Edit mode has Z / Ctrl / Shift keybindings
- `SdfEditTool` in `edit_tool.py` has matching Z / Ctrl / Shift support

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `PrimitiveCreatorBase` | `tools/primitive_tool.py:28` | Base for all SDF primitive creators |
| `BoxCreator` | `tools/primitive_tool.py:473` | 8-corner box tool |
| `SphereCreator` | `tools/primitive_tool.py:731` | 2-handle sphere tool |
| `CylinderCreator` | `tools/primitive_tool.py:862` | 3-handle cylinder tool |
| `SdfEditTool` | `tools/edit_tool.py:477` | Standalone SDF box editor |
| `_commit_and_enter_edit(name)` | `tools/primitive_tool.py:378` | Commit preview + switch to edit mode |
| `_field_from_two_corners(a, b)` | `tools/primitive_tool.py:535` | BoxCreator: rebuild SdfBoxField from two opposite world corners |
| `_refresh_edit_corners(field)` | `tools/primitive_tool.py:554` | BoxCreator: recompute self.points[8] from field |
| `_compute_handle_radius(ref_pt)` | `tools/dm_base.py` | Screen-pixel-calibrated world radius for handle spheres |
| `DMPoint.draw_point(root, r, color)` | `core/dm_point.py` | Draw a sphere handle in Coin3D |
| `DMPoint.update_draw(radius)` | `core/dm_point.py` | Update radius (no color param; undraw+redraw to change color) |
| `DragTimerMixin._drag_check_lmb_released()` | `tools/dm_base.py` | Returns True + stops timer if LMB released |
| `ToolState` | `tools/dm_base.py` | IntEnum: IDLE=0, ACTIVE=1, DRAGGING=2, FINALIZED=3, EDIT_MODE=4 |

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `dm_primitive_flow` | Viewport size formula, spawn pattern, edit-mode key patterns, rotation approach |
| `dm_tool_refactor_pattern` | Drag standard (QTimer), hit-test, cursor helpers, invariants |
| `dm_sdf_primitive_pattern` | SdfField subclass rules |

---

## Tier 1 — Infrastructure (Do First)

Adds the viewport-size helper and spawn stub used by every primitive refactor below.

### PF-001: Add `PrimitiveCreatorBase._compute_default_size()`

**File:** `tools/primitive_tool.py` — add as a method of `PrimitiveCreatorBase` after
`_get_placement()` (line 185)

**What:** Returns a world-space length (~15% of viewport height) used to size the initial
spawned primitive.

```python
def _compute_default_size(self):
    """Return a world-space size for the initial primitive spawn (~15% of viewport height)."""
    try:
        from pivy import coin
        cam = self.view.getCameraNode()
        if isinstance(cam, coin.SoOrthographicCamera):
            h = cam.height.getValue()
        else:
            import math
            h = 2.0 * cam.focalDistance.getValue() * math.tan(cam.heightAngle.getValue() / 2.0)
        size = h * 0.15
    except Exception:
        size = 200.0
    return max(5.0, min(size, 500.0))
```

---

### PF-002: Add `PrimitiveCreatorBase._spawn_default_primitive(click_pt)` stub

**File:** `tools/primitive_tool.py` — add to `PrimitiveCreatorBase` after `PF-001`

**What:** No-op base method that subclasses override. Documents the contract.

```python
def _spawn_default_primitive(self, click_pt):
    """Spawn a small complete primitive at click_pt, then immediately enter edit mode.

    Subclasses must:
      1. Compute N world-space handle positions from click_pt + _compute_default_size()
      2. Populate self.points with those N positions
      3. Draw N DMPoint handles + optional wire frame
      4. Set self.state = ToolState.FINALIZED
      5. Call self._commit_and_enter_edit("PrimitiveName")
    """
    pass
```

**Depends on:** PF-001

---

## Tier 2 — Single-Click Creation Flow

Refactors each primitive's `on_button1_down` to call `_spawn_default_primitive` on the
first click, removing the old multi-step state machine. Each task also adds the
`_get_edit_preview_field` override so the edit-mode drag reconstructs the field correctly.

---

### PF-003: Add `BoxCreator._spawn_default_primitive(click_pt)`

**File:** `tools/primitive_tool.py` — add to `BoxCreator` after `_make_field()` (line 691)

**What:** Computes 8 default world corners centered on `click_pt`, draws all handles +
wire frame, then calls `_commit_and_enter_edit("Box")`.

```python
def _spawn_default_primitive(self, click_pt):
    s = self._compute_default_size() / 2.0  # half-size
    self._field_placement = self._get_placement()
    loc_center = self.to_local(click_pt)
    half = FreeCAD.Vector(s, s, s)
    pts_local = self._box_corners_local(loc_center, half)
    world_corners = [self.to_global(lc) for lc in pts_local]
    self.points = list(world_corners)

    r = self._compute_handle_radius(ref_pt=click_pt)
    for pt in world_corners:
        dm_pt = DMPoint(pt)
        dm_pt.draw_point(self.points_root, r, color=(1.0, 0.5, 0.0))
        self.dm_points.append(dm_pt)

    if self.dm_line_set is None:
        self.dm_line_set = DMLineSet(self.points_root, color=(1.0, 0.6, 0.2), pattern=0x0F0F)
    edges = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]
    line_pts = []
    for i, j in edges:
        line_pts.extend([world_corners[i], world_corners[j]])
    self.dm_line_set.update_lines(line_pts, segments=[2]*12)

    self.state = ToolState.FINALIZED
    self._commit_and_enter_edit("Box")
```

**Depends on:** PF-001, PF-002

---

### PF-004: Update `BoxCreator._get_final_field()` for 8-corner state

**File:** `tools/primitive_tool.py` — replace `BoxCreator._get_final_field()` (line 682)

**What:** The old method expected 3 click points; new state has 8 world corners in
`self.points`. Reconstruct from corners[0] and corners[6] (opposite diagonal).

```python
def _get_final_field(self):
    if len(self.points) < 8:
        return None
    return self._field_from_two_corners(self.points[0], self.points[6])
```

**Depends on:** PF-003

---

### PF-005: Update `BoxCreator._get_final_points()` for 8-corner state

**File:** `tools/primitive_tool.py` — replace `BoxCreator._get_final_points()` (line 713)

**What:** The old method computed local corners from 3 click points. Now `self.points`
already holds 8 world corners; convert to local space relative to `working_plane`.

```python
def _get_final_points(self):
    if len(self.points) < 8:
        return None
    wp = self.working_plane
    if wp is not None:
        inv = wp.inverse()
        return [inv.multVec(p) for p in self.points]
    return list(self.points)
```

**Depends on:** PF-004

---

### PF-006: Add `BoxCreator._get_edit_preview_field()`

**File:** `tools/primitive_tool.py` — add to `BoxCreator` after `_get_final_points()` (line 728)

**What:** During edit mode, `_drag_update` calls `_get_edit_preview_field()`. BoxCreator's
existing `_drag_update` calls `_field_from_two_corners` directly, but the base-class
Ctrl/Shift drag paths use `_get_edit_preview_field()`. Add this override so those paths work.

```python
def _get_edit_preview_field(self):
    if len(self.points) < 8:
        return None
    return self._field_from_two_corners(self.points[0], self.points[6])
```

**Depends on:** PF-004

---

### PF-007: Refactor `BoxCreator.on_button1_down` to single-click

**File:** `tools/primitive_tool.py` — replace `BoxCreator.on_button1_down()` (line 564)

**What:** Replace the 3-click state machine with a single-click spawn. Also remove the
now-dead `on_move_state_1`, `on_move_state_2`, `_update_ghost_visuals`, `_get_preview_field`
methods from `BoxCreator` (the creation preview is no longer needed; edit mode has its own
live update).

Replace `on_button1_down`:
```python
def on_button1_down(self, event_dict):
    if self._is_editing:
        return self._edit_on_mouse_press(event_dict)

    skip = [self._preview_obj] if self._preview_obj else None
    pos = self._resolve_wp_click(event_dict, skip_objects=skip)
    if pos is None:
        return True

    if self.state == ToolState.IDLE:
        self._spawn_default_primitive(pos)
    return True
```

Delete these methods from `BoxCreator` (they are no longer called):
- `_update_ghost_visuals` (line 618)
- `on_move_state_1` (line 661)
- `on_move_state_2` (line 664)
- `_get_preview_field` (line 667)

**Depends on:** PF-003, PF-004, PF-005

---

### PF-008: Add `SphereCreator._spawn_default_primitive(click_pt)`

**File:** `tools/primitive_tool.py` — add to `SphereCreator` after `_sync_edit_points()` (line 764)

**What:** Places 2 handles (center + radius point), then enters edit mode.

```python
def _spawn_default_primitive(self, click_pt):
    r = self._compute_default_size() * 0.3
    loc_center = self.to_local(click_pt)
    radius_pt = self.to_global(loc_center + FreeCAD.Vector(r, 0, 0))
    self.points = [click_pt, radius_pt]

    r_handle = self._compute_handle_radius(ref_pt=click_pt)
    for pt in self.points:
        dm_pt = DMPoint(pt)
        dm_pt.draw_point(self.points_root, r_handle, color=(1.0, 0.5, 0.0))
        self.dm_points.append(dm_pt)

    self.state = ToolState.FINALIZED
    self._commit_and_enter_edit("Sphere")
```

**Depends on:** PF-001, PF-002

---

### PF-009: Add `SphereCreator._get_edit_preview_field()`

**File:** `tools/primitive_tool.py` — add to `SphereCreator` after `_spawn_default_primitive()` (after PF-008)

**What:** `SphereCreator._get_preview_field()` reads `self.current_point` which is not
updated during edit-mode dragging. This override reads from `self.points` directly.

```python
def _get_edit_preview_field(self):
    if len(self.points) < 2:
        return None
    loc_c = self.to_local(self.points[0])
    loc_r = self.to_local(self.points[1])
    radius = (loc_r - loc_c).Length
    if radius < 0.01:
        return None
    return SdfSphereField(loc_c, radius, placement=self._get_placement())
```

**Depends on:** PF-008

---

### PF-010: Refactor `SphereCreator.on_button1_down` to single-click

**File:** `tools/primitive_tool.py` — replace `SphereCreator.on_button1_down()` (line 769)

**What:** Replace 2-click state machine with single click. Also remove `on_move_state_1`
and `_get_preview_field` from `SphereCreator` (dead code after this change).

Replace `on_button1_down`:
```python
def on_button1_down(self, event_dict):
    if self._is_editing:
        return self._edit_on_mouse_press(event_dict)

    skip = [self._preview_obj] if self._preview_obj else None
    pos = self._resolve_wp_click(event_dict, skip_objects=skip)
    if pos is None:
        return True

    if self.state == ToolState.IDLE:
        self._spawn_default_primitive(pos)
    return True
```

Delete from `SphereCreator`:
- `_update_ghost_visuals` (line 798)
- `on_move_state_1` (line 833)
- `_get_preview_field` (line 836)

**Depends on:** PF-008, PF-009

---

### PF-011: Add `CylinderCreator._spawn_default_primitive(click_pt)`

**File:** `tools/primitive_tool.py` — add to `CylinderCreator` after `_sync_edit_points()` (line 900)

**What:** Places 3 handles (base center, radius point, height point), then enters edit mode.

```python
def _spawn_default_primitive(self, click_pt):
    s = self._compute_default_size()
    r = s * 0.3
    h = s * 0.6
    self._field_placement = self._get_placement()
    loc_base = self.to_local(click_pt)
    radius_pt = self.to_global(loc_base + FreeCAD.Vector(r, 0, 0))
    height_pt = self.to_global(loc_base + FreeCAD.Vector(0, 0, h))
    self.points = [click_pt, radius_pt, height_pt]
    self.current_point = height_pt  # needed by _get_final_field fallback

    r_handle = self._compute_handle_radius(ref_pt=click_pt)
    for pt in self.points:
        dm_pt = DMPoint(pt)
        dm_pt.draw_point(self.points_root, r_handle, color=(1.0, 0.5, 0.0))
        self.dm_points.append(dm_pt)

    self.state = ToolState.FINALIZED
    self._commit_and_enter_edit("Cylinder")
```

**Depends on:** PF-001, PF-002

---

### PF-012: Add `CylinderCreator._get_edit_preview_field()`

**File:** `tools/primitive_tool.py` — add to `CylinderCreator` after `_spawn_default_primitive()` (after PF-011)

**What:** Reconstructs `SdfCylinderField` from `self.points[0, 1, 2]` for use during
edit-mode dragging.

```python
def _get_edit_preview_field(self):
    if len(self.points) < 3:
        return None
    import math
    loc_base = self.to_local(self.points[0])
    loc_rad  = self.to_local(self.points[1])
    loc_h    = self.to_local(self.points[2])
    radius = math.sqrt((loc_rad.x - loc_base.x)**2 + (loc_rad.y - loc_base.y)**2)
    height = loc_h.z - loc_base.z
    if radius < 0.01:
        return None
    if abs(height) < 0.01:
        height = 0.01 if height >= 0 else -0.01
    fp = getattr(self, "_field_placement", self._get_placement())
    return SdfCylinderField(loc_base, FreeCAD.Vector(0, 0, 1), radius, height, placement=fp)
```

**Depends on:** PF-011

---

### PF-013: Refactor `CylinderCreator.on_button1_down` to single-click

**File:** `tools/primitive_tool.py` — replace `CylinderCreator.on_button1_down()` (line 908)

**What:** Replace 3-click state machine with single click. Remove dead creation-preview
methods.

Replace `on_button1_down`:
```python
def on_button1_down(self, event_dict):
    if self._is_editing:
        return self._edit_on_mouse_press(event_dict)

    skip = [self._preview_obj] if self._preview_obj else None
    pos = self._resolve_wp_click(event_dict, skip_objects=skip)
    if pos is None:
        return True

    if self.state == ToolState.IDLE:
        self._spawn_default_primitive(pos)
    return True
```

Delete from `CylinderCreator`:
- `_update_ghost_visuals` (line 948)
- `on_move_state_1` (line 996)
- `on_move_state_2` (line 999)
- `_get_preview_field` (line 1002)

**Depends on:** PF-011, PF-012

---

## Tier 3 — Edit Mode Keys

Adds keyboard and modifier-key controls that operate while a primitive is in edit mode.
All tasks in Tier 3 are independent of each other (no intra-tier dependencies).

---

### PF-014: Add `PrimitiveCreatorBase.handle_keyboard` — Z key toggles IsSubtractive

**File:** `tools/primitive_tool.py` — add to `PrimitiveCreatorBase` after `reset_state()` (line 434)

**What:** Pressing Z in edit mode toggles `IsSubtractive` on the preview object and
updates handle color (orange = additive, blue = subtractive). Must call `super()` so
Escape/Enter from `DMBase` still work.

```python
def handle_keyboard(self, event_dict):
    from PySide.QtCore import Qt
    key = event_dict.get("Key")
    if key == Qt.Key_Z and self._is_editing and self._preview_obj:
        is_sub = not getattr(self._preview_obj, "IsSubtractive", False)
        self._preview_obj.IsSubtractive = is_sub
        color = (0.3, 0.5, 1.0) if is_sub else (1.0, 0.5, 0.0)
        r = self._compute_handle_radius()
        # DMPoint.update_draw does not support color; undraw + redraw
        for dm_pt in self.dm_points:
            dm_pt.undraw()
            dm_pt.draw_point(self.points_root, r, color=color)
        # Push updated IsSubtractive to the renderer
        from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
        field = getattr(self._preview_obj.Proxy, "SdfField", None)
        if field:
            label = f"{self._preview_obj.Document.Name}.{self._preview_obj.Name}"
            DMSceneRayMarchRenderer.get_instance().update_field(label, field)
        if self.view:
            self.view.redraw()
        return True
    return super().handle_keyboard(event_dict)
```

---

### PF-015: Add Ctrl-drag reposition to `PrimitiveCreatorBase._drag_update`

**File:** `tools/primitive_tool.py` — modify `PrimitiveCreatorBase._drag_update()` (line 121)

**What:** When Ctrl is held during a handle drag, translate ALL handles by the same
delta instead of moving only the grabbed handle. Applies to Sphere and Cylinder (Box
has its own `_drag_update`; see PF-016).

Replace the section that moves the selected handle with:
```python
im = DMInputManager.get_instance()
if im.is_ctrl_down():
    # Reposition: translate ALL handles by the grabbed handle's delta
    old_pos = self.dm_points[self._edit_sel_idx].position
    delta = new_pos - old_pos
    for dm_pt in self.dm_points:
        dm_pt.position = dm_pt.position + delta
        dm_pt.update_draw()
    self._edit_drag_o = self._edit_drag_o + delta  # keep drag plane with grab point
else:
    self.dm_points[self._edit_sel_idx].position = new_pos
    self.dm_points[self._edit_sel_idx].update_draw()
```

The call to `self._sync_edit_points()` and the field rebuild that follows remain
unchanged — they run after this block regardless of which branch was taken.

---

### PF-016: Add Ctrl-drag reposition to `BoxCreator._drag_update`

**File:** `tools/primitive_tool.py` — modify `BoxCreator._drag_update()` (line 503)

**What:** Same translate-all logic for Box's overridden `_drag_update`. After translation,
rebuild the field from the new corners[0] and corners[6].

Add a Ctrl branch at the top of the per-frame update block (after `new_world` is
computed, before the normal single-corner logic):
```python
im = DMInputManager.get_instance()
if im.is_ctrl_down():
    delta = new_world - self.points[self._edit_sel_idx]
    self.points = [p + delta for p in self.points]
    self._edit_drag_o = self._edit_drag_o + delta
    new_field = self._field_from_two_corners(self.points[0], self.points[6])
    self._refresh_edit_corners(new_field)
    r = self._compute_handle_radius()
    for i, pt in enumerate(self.points):
        self.dm_points[i].position = pt
        self.dm_points[i].update_draw(radius=r)
    self._apply_preview_field(new_field)
    self._update_pending = False
    if self.view:
        self.view.redraw()
    return
# else: existing single-corner drag code follows unchanged
```

**Depends on:** PF-003

---

### PF-017: Add Shift-drag rotate state variables to `PrimitiveCreatorBase.__init__`

**File:** `tools/primitive_tool.py` — add to `PrimitiveCreatorBase.__init__()` (line 33), in the
block after `self._edit_sel_idx = None` (line 48)

**What:** Rotation mode needs four instance variables tracked across the drag lifetime.

```python
# Shift-drag rotation state
self._rotate_mode = False
self._rotate_center = None       # FreeCAD.Vector — rotation pivot (avg of handles)
self._rotate_base_angle = 0.0    # float (radians) — angle at drag start
self._rotate_base_pts = None     # list[FreeCAD.Vector] — handle positions snapshot
```

---

### PF-018: Detect Shift in `PrimitiveCreatorBase._edit_on_mouse_press` — enter rotate mode

**File:** `tools/primitive_tool.py` — modify `PrimitiveCreatorBase._edit_on_mouse_press()` (line 99)

**What:** If Shift is held when the user clicks in edit mode, enter rotation mode instead
of the normal single-handle drag. Insert this block at the top of the method, before
the LMB check:

```python
im = DMInputManager.get_instance()
# Check for shift modifier to enter rotation mode
try:
    shift_down = im.is_shift_down()
except AttributeError:
    from PySide.QtGui import QApplication
    from PySide.QtCore import Qt as _Qt
    shift_down = bool(QApplication.keyboardModifiers() & _Qt.ShiftModifier)

if shift_down and self._is_editing and self.dm_points:
    pts = [dm_pt.position for dm_pt in self.dm_points]
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    cz = sum(p.z for p in pts) / len(pts)
    self._rotate_center = FreeCAD.Vector(cx, cy, cz)
    self._rotate_base_pts = [FreeCAD.Vector(p.x, p.y, p.z) for p in pts]
    vd = self.view.getViewDirection()
    self._edit_drag_n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
    self._edit_drag_n.normalize()
    self._edit_drag_o = self._rotate_center
    # Compute initial mouse angle around the center in the view plane
    mouse_pos = im._last_qt_pos
    init_world = self.projector.get_mouse_world_pos(
        {"Position": mouse_pos}, self._edit_drag_n, self._edit_drag_o,
        place_on_geometry=False
    )
    if init_world:
        import math
        rel = init_world - self._rotate_center
        n = self._edit_drag_n
        rel_flat = rel - n * rel.dot(n)
        self._rotate_base_angle = math.atan2(rel_flat.y, rel_flat.x)
    else:
        self._rotate_base_angle = 0.0
    self._rotate_mode = True
    self._edit_sel_idx = 0  # sentinel — keeps drag-timer logic running
    self.state = ToolState.DRAGGING
    self._start_drag_timer()
    self._set_cursor(QtCore.Qt.SizeAllCursor)
    return True
```

**Depends on:** PF-017

---

### PF-019: Add rotation update to `PrimitiveCreatorBase._drag_update`

**File:** `tools/primitive_tool.py` — modify `PrimitiveCreatorBase._drag_update()` (line 121)

**What:** At the very top of `_drag_update`, before the `_edit_sel_idx is None` guard,
add a rotation branch that fires when `_rotate_mode` is True. On LMB release, clear
rotate state. Applies to Sphere and Cylinder (Box overrides `_drag_update`).

```python
# Rotation mode takes full control of the update
if self._rotate_mode and self._rotate_base_pts is not None:
    if self._drag_check_lmb_released():
        self._rotate_mode = False
        self._rotate_base_pts = None
        self._rotate_center = None
        return
    mouse_pos = DMInputManager.get_instance()._last_qt_pos
    cur_world = self.projector.get_mouse_world_pos(
        {"Position": mouse_pos}, self._edit_drag_n, self._edit_drag_o,
        place_on_geometry=False
    )
    if cur_world is None:
        return
    import math
    rel = cur_world - self._rotate_center
    n = self._edit_drag_n
    rel_flat = rel - n * rel.dot(n)
    cur_angle = math.atan2(rel_flat.y, rel_flat.x)
    delta_deg = math.degrees(cur_angle - self._rotate_base_angle)
    rot = FreeCAD.Rotation(n, delta_deg)
    r = self._compute_handle_radius()
    for i, base_pt in enumerate(self._rotate_base_pts):
        new_pt = self._rotate_center + rot.multVec(base_pt - self._rotate_center)
        self.dm_points[i].position = new_pt
        self.dm_points[i].update_draw(radius=r)
    self._sync_edit_points()
    field = self._get_edit_preview_field()
    if field and self._preview_obj:
        self._apply_preview_field(field)
        self._update_pending = False
    if self.view:
        self.view.redraw()
    return
```

**Depends on:** PF-017, PF-018

---

### PF-020: BoxCreator Shift-rotate — working_plane approach

**File:** `tools/primitive_tool.py` — modifications to `BoxCreator`

**What:** Rotating the 8 world corner positions directly then calling
`_field_from_two_corners(corners[0], corners[6])` produces an axis-aligned box in
the old working plane, discarding the rotation. The correct approach is to rotate
`self.working_plane` and recompute corners from local space.

Add to `BoxCreator.__init__()` (after `self._height_drag_base = None`, line 481):
```python
self._rotate_wp_base_rot = None      # FreeCAD.Rotation snapshot at rotate-drag start
self._rotate_local_corners = None    # list[FreeCAD.Vector] local corners snapshot
```

Override `_edit_on_mouse_press` in `BoxCreator` to snapshot local state before
delegating to base class:
```python
def _edit_on_mouse_press(self, event_dict):
    im = DMInputManager.get_instance()
    try:
        shift = im.is_shift_down()
    except AttributeError:
        from PySide.QtGui import QApplication
        from PySide.QtCore import Qt as _Qt
        shift = bool(QApplication.keyboardModifiers() & _Qt.ShiftModifier)
    if shift and self._is_editing and self.working_plane and len(self.points) == 8:
        wp = self.working_plane
        self._rotate_wp_base_rot = FreeCAD.Rotation(wp.Rotation)
        inv = wp.inverse()
        self._rotate_local_corners = [inv.multVec(p) for p in self.points]
    return super()._edit_on_mouse_press(event_dict)
```

Override `_sync_edit_points` in `BoxCreator` to regenerate world corners from the
rotated working plane when in rotate mode:
```python
def _sync_edit_points(self):
    if self._rotate_mode and self._rotate_local_corners is not None and self.working_plane:
        # Apply working_plane rotation then recompute world corners
        n = self._edit_drag_n
        import math
        cur_world = self.projector.get_mouse_world_pos(
            {"Position": DMInputManager.get_instance()._last_qt_pos},
            self._edit_drag_n, self._edit_drag_o, place_on_geometry=False
        )
        if cur_world and self._rotate_center:
            rel = cur_world - self._rotate_center
            rel_flat = rel - n * rel.dot(n)
            delta_deg = math.degrees(
                math.atan2(rel_flat.y, rel_flat.x) - self._rotate_base_angle
            )
            delta_rot = FreeCAD.Rotation(n, delta_deg)
            new_rot = delta_rot.multiply(self._rotate_wp_base_rot)
            self.working_plane = FreeCAD.Placement(self.working_plane.Base, new_rot)
            self.points = [self.working_plane.multVec(lc) for lc in self._rotate_local_corners]
            r = self._compute_handle_radius()
            for i, pt in enumerate(self.points):
                self.dm_points[i].position = pt
                self.dm_points[i].update_draw(radius=r)
```

**Depends on:** PF-006, PF-017, PF-018, PF-019

---

### PF-021: Add Z key to `SdfEditTool.handle_keyboard` (edit_tool.py)

**File:** `tools/edit_tool.py` — modify `SdfEditTool.handle_keyboard()` (line 677)

**What:** Pressing Z while `SdfEditTool` is active toggles `IsSubtractive` on the
target object and triggers a recompute.

Add before `return False` at line 682:
```python
if key_code == QtCore.Qt.Key_Z and self._target_obj:
    is_sub = not getattr(self._target_obj, "IsSubtractive", False)
    self._target_obj.IsSubtractive = is_sub
    self._target_obj.touch()
    if self._target_obj.Document:
        self._target_obj.Document.recompute([self._target_obj])
    return True
```

---

### PF-022: Add Ctrl-drag reposition to `SdfEditTool._drag_update`

**File:** `tools/edit_tool.py` — modify `SdfEditTool._drag_update()` (line 596)

**What:** When Ctrl is held, translate ALL 8 corners by the drag delta rather than moving
just the grabbed corner. After translation, rebuild `self._field` from new corners[0]
and corners[6] and push to the renderer.

Add after `if new_world is None: return` and before `new_field = self._field_from_corners(...)`:
```python
im = DMInputManager.get_instance()
if im.is_ctrl_down():
    delta = new_world - self._world_corners[self._dragging_idx]
    self._world_corners = [wc + delta for wc in self._world_corners]
    self._fixed_world = self._world_corners[_BOX_OPPOSITE[self._dragging_idx]]
    self._drag_plane_o = self._drag_plane_o + delta
    new_field = self._field_from_corners(
        self._world_corners[self._dragging_idx],
        self._world_corners[_BOX_OPPOSITE[self._dragging_idx]]
    )
    self._field = new_field
    obj = self._target_obj
    if obj and obj.Document:
        obj.Proxy.SdfField = new_field
        obj.touch()
        obj.Document.recompute([obj])
    return
```

---

### PF-023: Add Shift-drag rotate to `SdfEditTool`

**File:** `tools/edit_tool.py` — modifications to `SdfEditTool`

**What:** Add the same working_plane-rotation pattern used in `BoxCreator` (PF-020) to
`SdfEditTool`. The tool stores `self._placement` (equivalent to `working_plane` in
the creator tools).

Add state variables to `SdfEditTool.__init__()` (after `self._is_editing = True`, line 492):
```python
self._rotate_mode = False
self._rotate_center = None
self._rotate_base_angle = 0.0
self._rotate_wp_base_rot = None
self._rotate_local_corners = None
self._rotate_drag_n = None
self._rotate_drag_o = None
```

Add a Shift-detection block at the top of `SdfEditTool.on_button1_down()` (line 649),
before `idx = self._hit_test_corners(event_dict)`:
```python
im = DMInputManager.get_instance()
try:
    shift = im.is_shift_down()
except AttributeError:
    from PySide.QtGui import QApplication
    from PySide.QtCore import Qt as _Qt
    shift = bool(QApplication.keyboardModifiers() & _Qt.ShiftModifier)

if shift and self._world_corners and self._placement:
    # Enter rotation mode
    pts = self._world_corners
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    cz = sum(p.z for p in pts) / len(pts)
    self._rotate_center = FreeCAD.Vector(cx, cy, cz)
    self._rotate_wp_base_rot = FreeCAD.Rotation(self._placement.Rotation)
    inv = self._placement.inverse()
    self._rotate_local_corners = [inv.multVec(wc) for wc in self._world_corners]
    vd = self.view.getViewDirection()
    self._rotate_drag_n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
    self._rotate_drag_n.normalize()
    self._rotate_drag_o = self._rotate_center
    mouse_pos = im._last_qt_pos
    init_world = self.projector.get_mouse_world_pos(
        {"Position": mouse_pos}, self._rotate_drag_n, self._rotate_drag_o,
        place_on_geometry=False
    )
    if init_world:
        import math
        rel = init_world - self._rotate_center
        n = self._rotate_drag_n
        rel_flat = rel - n * rel.dot(n)
        self._rotate_base_angle = math.atan2(rel_flat.y, rel_flat.x)
    else:
        self._rotate_base_angle = 0.0
    self._rotate_mode = True
    self._dragging_idx = 0  # sentinel
    self.state = 1
    self._start_drag_timer()
    return True
```

Add rotation update at the top of `SdfEditTool._drag_update()` (line 596):
```python
if self._rotate_mode and self._rotate_local_corners is not None:
    if self._drag_check_lmb_released():
        self._rotate_mode = False
        self._rotate_local_corners = None
        return
    mouse_pos = DMInputManager.get_instance()._last_qt_pos
    cur_world = self.projector.get_mouse_world_pos(
        {"Position": mouse_pos}, self._rotate_drag_n, self._rotate_drag_o,
        place_on_geometry=False
    )
    if cur_world is None:
        return
    import math
    rel = cur_world - self._rotate_center
    n = self._rotate_drag_n
    rel_flat = rel - n * rel.dot(n)
    cur_angle = math.atan2(rel_flat.y, rel_flat.x)
    delta_deg = math.degrees(cur_angle - self._rotate_base_angle)
    delta_rot = FreeCAD.Rotation(n, delta_deg)
    new_rot = delta_rot.multiply(self._rotate_wp_base_rot)
    self._placement = FreeCAD.Placement(self._placement.Base, new_rot)
    self._world_corners = [self._placement.multVec(lc) for lc in self._rotate_local_corners]
    new_field = self._field_from_corners(
        self._world_corners[0], self._world_corners[_BOX_OPPOSITE[0]]
    )
    new_field = type(new_field)(new_field.center, new_field.half_size,
                                placement=self._placement)
    self._field = new_field
    obj = self._target_obj
    if obj and obj.Document:
        obj.Proxy.SdfField = new_field
        obj.touch()
        obj.Document.recompute([obj])
    return
```

**Note**: `_field_from_corners` uses `self._placement` for local conversion. Since we
update `self._placement` before calling it, the resulting field inherits the new rotation.
Verify that `SdfBoxField.__init__` stores and uses the `placement` kwarg.

**Depends on:** PF-021, PF-022
