---
name: DM Primitive Single-Click Flow
description: Patterns for the new single-click primitive creation flow (spawn default + immediate edit) and edit-mode controls (Z/Ctrl/Shift). Required reading before implementing any task in todo_primitive_flow.md.
---

# DM Primitive Single-Click Flow

All new primitive tools use a **single-click** creation model:
1. User clicks → small default primitive spawns at the click point
2. Tool immediately enters edit mode with all N handles visible
3. User drags handles to adjust shape, with modifier-key overrides

---

## Viewport-Relative Default Size

```python
def _compute_default_size(self):
    """Return a world-space size for initial primitive spawn (~15% of viewport height)."""
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

## Control Points Per Primitive

| Primitive | N | Points[i] description |
|-----------|---|----------------------|
| Box       | 8 | World-space corners, order: `(-,-,-) (+,-,-) (+,+,-) (-,+,-) (-,-,+) (+,-,+) (+,+,+) (-,+,+)` |
| Sphere    | 2 | `[center, radius_pt]` — radius = `(pt[1]-pt[0]).Length` |
| Cylinder  | 3 | `[base_center, radius_pt, height_pt]` — radius = XY distance, height = `(pt[2]-pt[0]).z` in local |

`_BOX_OPPOSITE = {0:6, 1:7, 2:4, 3:5, 4:2, 5:3, 6:0, 7:1}` — for fixed-corner drag.

---

## Single-Click Spawn Pattern (subclass template)

```python
def _spawn_default_primitive(self, click_pt):
    s = self._compute_default_size()
    # ... build N handle world positions from click_pt + s ...
    self.points = [...]  # N world-space FreeCAD.Vector
    r = self._compute_handle_radius(ref_pt=click_pt)
    for pt in self.points:
        dm_pt = DMPoint(pt)
        dm_pt.draw_point(self.points_root, r, color=(1.0, 0.5, 0.0))
        self.dm_points.append(dm_pt)
    # Draw optional wire frame via self.dm_line_set
    self.state = ToolState.FINALIZED
    self._commit_and_enter_edit("PrimitiveName")
```

`_commit_and_enter_edit` calls `_get_final_field()` and `_get_final_points()` before transitioning —
make sure those are correct before calling it.

---

## Box Local Corner Layout

```python
# Use PrimitiveCreatorBase._box_corners_local(center, half_size)
# Always build in local space, then transform to world via self.to_global(lc)
# For spawn: center = self.to_local(click_pt), half_size = FreeCAD.Vector(s/2, s/2, s/2)
```

Box field reconstruction from two opposite corners:
```python
field = self._field_from_two_corners(self.points[0], self.points[6])
# Uses self.working_plane (or self._field_placement) for local space transform
```

---

## `_get_edit_preview_field` — Edit Mode Field Reconstruction

Each primitive must override this to rebuild the SDF field from the CURRENT `self.points`
(not from `self.current_point` or multi-click creation state):

**Box** — add to `BoxCreator`:
```python
def _get_edit_preview_field(self):
    if len(self.points) < 8:
        return None
    return self._field_from_two_corners(self.points[0], self.points[6])
```

**Sphere** — add to `SphereCreator`:
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

**Cylinder** — add to `CylinderCreator`:
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

---

## `_sync_edit_points` — Sphere and Cylinder

Sphere syncs all `self.dm_points[i]` → `self.points[i]` via the base class loop — **but**
`SphereCreator._get_preview_field` reads `self.current_point` not `self.points[1]`.
This is why each subclass needs `_get_edit_preview_field`; do NOT rely on `_get_preview_field`
for the edit-mode path.

---

## Z Key — Toggle IsSubtractive

```python
# In handle_keyboard (PrimitiveCreatorBase):
from PySide.QtCore import Qt
if key == Qt.Key_Z and self._is_editing and self._preview_obj:
    is_sub = not getattr(self._preview_obj, "IsSubtractive", False)
    self._preview_obj.IsSubtractive = is_sub
    color = (0.3, 0.5, 1.0) if is_sub else (1.0, 0.5, 0.0)
    r = self._compute_handle_radius()
    for dm_pt in self.dm_points:
        dm_pt.undraw()
        dm_pt.draw_point(self.points_root, r, color=color)
    # Push field update to renderer:
    from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
    field = getattr(self._preview_obj.Proxy, "SdfField", None)
    if field:
        label = f"{self._preview_obj.Document.Name}.{self._preview_obj.Name}"
        DMSceneRayMarchRenderer.get_instance().update_field(label, field)
    if self.view:
        self.view.redraw()
    return True
```

**Note on `DMPoint.update_draw`**: `update_draw(radius=r)` does NOT support a `color` parameter.
To change color, call `dm_pt.undraw()` then `dm_pt.draw_point(root, r, color=color)`.

---

## Ctrl Drag — Translate All Handles (Reposition)

Applied **inside** `_drag_update`, checked before the normal single-handle path:

```python
im = DMInputManager.get_instance()
if im.is_ctrl_down():
    old_pos = self.dm_points[self._edit_sel_idx].position
    delta = new_pos - old_pos
    for i, dm_pt in enumerate(self.dm_points):
        dm_pt.position = dm_pt.position + delta
        dm_pt.update_draw()
    self._edit_drag_o = self._edit_drag_o + delta  # keep drag plane with the grab point
else:
    # normal single-handle move
    self.dm_points[self._edit_sel_idx].position = new_pos
    self.dm_points[self._edit_sel_idx].update_draw()
```

After this block, always call `self._sync_edit_points()`.

**BoxCreator._drag_update** has its own implementation; add the Ctrl branch there too:
```python
if im.is_ctrl_down():
    delta = new_world - self.points[self._edit_sel_idx]
    self.points = [p + delta for p in self.points]
    self._edit_drag_o = self._edit_drag_o + delta
    new_field = self._field_from_two_corners(self.points[0], self.points[6])
    r = self._compute_handle_radius()
    for i, pt in enumerate(self.points):
        self.dm_points[i].position = pt
        self.dm_points[i].update_draw(radius=r)
    self._apply_preview_field(new_field)
    if self.view:
        self.view.redraw()
    return
# else: existing single-corner drag code
```

---

## Shift Drag — Rotate Around Center

Rotation uses the **view-plane normal** as the rotation axis and the **primitive center**
(average of all handle positions) as the pivot.

### State variables (add to `__init__` in `PrimitiveCreatorBase`):
```python
self._rotate_mode = False
self._rotate_center = None          # FreeCAD.Vector — pivot
self._rotate_base_angle = 0.0       # float (radians) — angle at drag start
self._rotate_base_pts = None        # list[FreeCAD.Vector] — handle positions at drag start
```

### Detecting Shift in `_edit_on_mouse_press`:
```python
im = DMInputManager.get_instance()
if im.is_shift_down() and self._is_editing:
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
    # Compute initial angle
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
    self._edit_sel_idx = 0  # sentinel to keep drag running
    self.state = ToolState.DRAGGING
    self._start_drag_timer()
    self._set_cursor(QtCore.Qt.SizeAllCursor)
    return True
```

### Rotation update in `_drag_update`:
```python
if self._rotate_mode and self._rotate_base_pts is not None:
    if self._drag_check_lmb_released():
        self._rotate_mode = False
        self._rotate_base_pts = None
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
    if self.view:
        self.view.redraw()
    return
```

### BoxCreator rotation — working_plane approach

Because `BoxCreator._field_from_two_corners` assumes corners[0] and corners[6] are
axis-aligned in the working plane's local space, rotating corner positions directly
produces shear. Correct approach: **rotate `working_plane.Rotation` instead**.

Add these state variables to `BoxCreator.__init__`:
```python
self._rotate_wp_base_rot = None    # FreeCAD.Rotation snapshot at drag start
self._rotate_local_corners = None  # list[FreeCAD.Vector] local corners snapshot
```

In `BoxCreator`, override `_edit_on_mouse_press` to intercept shift BEFORE calling super:
```python
def _edit_on_mouse_press(self, event_dict):
    im = DMInputManager.get_instance()
    if im.is_shift_down() and self._is_editing and self.working_plane:
        wp = self.working_plane
        # Snapshot working plane and local corners
        self._rotate_wp_base_rot = FreeCAD.Rotation(wp.Rotation)
        inv = wp.inverse()
        self._rotate_local_corners = [inv.multVec(p) for p in self.points]
        # Let base class finish setting up _rotate_center, _rotate_base_angle, etc.
    return super()._edit_on_mouse_press(event_dict)
```

Override `_sync_edit_points` in `BoxCreator` to also update the working_plane rotation
when in rotate mode:
```python
def _sync_edit_points(self):
    """After rotate drag: recompute world corners from rotated working_plane."""
    if self._rotate_mode and self._rotate_local_corners is not None:
        # Recompute self.points from updated working_plane + local snapshot
        self.points = [self.working_plane.multVec(lc) for lc in self._rotate_local_corners]
        r = self._compute_handle_radius()
        for i, pt in enumerate(self.points):
            self.dm_points[i].position = pt
            self.dm_points[i].update_draw(radius=r)
```

Then also update working_plane.Rotation inside the rotation update loop (just before
`self._sync_edit_points()`):
```python
# In _drag_update rotation branch, before calling _sync_edit_points:
if hasattr(self, '_rotate_wp_base_rot') and self._rotate_wp_base_rot is not None:
    delta_rot = FreeCAD.Rotation(n, delta_deg)
    self.working_plane = FreeCAD.Placement(
        self.working_plane.Base,
        delta_rot.multiply(self._rotate_wp_base_rot)
    )
```

---

## DMInputManager Modifier Accessors

```python
im = DMInputManager.get_instance()
im.is_ctrl_down()    # bool — Ctrl key
im.is_shift_down()   # bool — Shift key (verify exists; fallback below)
```

Fallback if `is_shift_down()` doesn't exist:
```python
from PySide.QtGui import QApplication
from PySide.QtCore import Qt
bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
```

---

## SdfEditTool (edit_tool.py) — Z / Ctrl / Shift

`SdfEditTool` is a standalone box editor (activated from outside the creator). It needs:

**Z key** — add to `SdfEditTool.handle_keyboard`:
```python
if key_code == QtCore.Qt.Key_Z and self._target_obj:
    is_sub = not getattr(self._target_obj, "IsSubtractive", False)
    self._target_obj.IsSubtractive = is_sub
    self._target_obj.touch()
    if self._target_obj.Document:
        self._target_obj.Document.recompute([self._target_obj])
    return True
```

**Ctrl drag** — add to `SdfEditTool._drag_update` (same delta-all-corners pattern as above).
`SdfEditTool` stores corners in `self._world_corners`; translate all + re-set
`self._fixed_world = self._world_corners[_BOX_OPPOSITE[self._dragging_idx]]`.

**Shift rotate** — add to `SdfEditTool.on_button1_down` and `_drag_update` using the same
working_plane rotation approach. `SdfEditTool` stores `self._placement`; rotate that.

---

## Files to Read Before Editing

1. `tools/primitive_tool.py` — `PrimitiveCreatorBase`, `BoxCreator`, `SphereCreator`, `CylinderCreator`
2. `tools/edit_tool.py` — `SdfEditTool` (standalone box editor)
3. `tools/dm_base.py` — `DMBase`, `DragTimerMixin`, `ToolState`
4. `core/dm_point.py` — `DMPoint.draw_point(root, radius, color)`, `update_draw(radius)`, `undraw()`
5. `.agents/skills/dm_tool_refactor_pattern/SKILL.md` — drag invariants, hit-test, QTimer pattern
