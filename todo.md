# Direct Modeling — Edit-Mode Controls Overhaul

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: Gemini Flash or Claude Sonnet.
> Each task includes exact file(s) and line numbers.
> Read `.agents/skills/dm_edit_gizmo/SKILL.md` before starting any task.

---

## Background

Edit mode currently has:
- A center dot (`_center_handle`) for free translation
- A single 2-D rotation dot + line (`_rot_handle`, `_rot_line`) that only rotates around the workplane normal
- A `DMTransformGizmo` with three translation arrows (X/Y/Z) in global space only
- No axis-dependent rotation controls
- Handles are depth-tested against the SDF mesh — they disappear when inside geometry

**Goal:** Consistent controls across all primitives: origin dot, three translation arrows,
three rotation rings, all always visible through geometry (x-ray), with local/world
transform-space cycling via `T`.

---

## Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `PrimitiveCreatorBase.__init__` | `tools/primitive_tool.py:230` | Init edit-mode state fields |
| `PrimitiveCreatorBase._init_gizmo` | `tools/primitive_tool.py:340` | Draws/redraws the gizmo |
| `PrimitiveCreatorBase._edit_on_mouse_press` | `tools/primitive_tool.py:387` | Hit-test and start drag |
| `PrimitiveCreatorBase._gizmo_drag_update` | `tools/primitive_tool.py:519` | Timer-driven translation drag |
| `PrimitiveCreatorBase._add_transform_handles` | `tools/primitive_tool.py:834` | Draws center + rot handles |
| `PrimitiveCreatorBase.handle_keyboard` | `tools/primitive_tool.py:1186` | Key dispatch |
| `PrimitiveCreatorBase.get_snapping_menu` | `tools/primitive_tool.py:1241` | Right-click context menu |
| `DMTransformGizmo` | `core/dm_gizmo.py:4` | Coin3D gizmo class |
| `DMTransformGizmo.draw` | `core/dm_gizmo.py:58` | Build Coin3D scene sub-graph |
| `DMTransformGizmo.update` | `core/dm_gizmo.py:119` | Move gizmo to new center |
| `DMTransformGizmo.undraw` | `core/dm_gizmo.py:143` | Remove from scene |
| `DMTransformGizmo.hit_test` | `core/dm_gizmo.py:154` | Ray vs. axis shafts |
| `_ray_segment_dist` | `core/dm_gizmo.py:172` | Ray-to-segment distance helper |

---

## Tier 1 — X-Ray Handle Rendering

### GIZMO-001: Use `SoAnnotation` for handle root node

**File:** `tools/primitive_tool.py` — line 242

**What:** `SoAnnotation` renders its children after all depth-sorted geometry, ignoring
the depth buffer, so handles always appear on top of (through) the SDF mesh.
The project uses custom ray math for hit-testing (not `SoRayPickAction`), so picking
is unaffected.

**Implementation:**

Replace:
```python
self.points_root = coin.SoSeparator()
```
With:
```python
self.points_root = coin.SoAnnotation()
```

No other changes. `SoAnnotation` is a subclass of `SoSeparator` and supports all the
same `addChild` / `removeChild` calls already in use.

**Depends on:** nothing

---

## Tier 2 — Rotation Rings in DMTransformGizmo

### GIZMO-002: Add rotation ring geometry to `DMTransformGizmo`

**File:** `core/dm_gizmo.py` — modify `__init__`, `draw`, `update`, `undraw`

**What:** Add three rotation rings (one per axis) drawn as closed polylines.
Each ring lies in the plane perpendicular to its axis, centered on the gizmo origin,
with radius `self._length * 0.85`. Ring colours match axis colours (X=red, Y=green, Z=blue).

**Implementation:**

**Step 1 — Add imports and constants at the top of the file** (after `import FreeCAD`):

```python
import math as _math

_RING_SEGMENTS = 48   # polyline vertices per ring
```

**Step 2 — Add to `__init__` after `self._cone_xforms = {}`** (line ~27):

```python
self._ring_seps   = {}   # axis → SoSeparator
self._ring_coords = {}   # axis → SoCoordinate3
```

**Step 3 — Add module-level helper** (insert before `class DMTransformGizmo`, i.e. before line 4):

```python
def _perp_pair(ax_vec):
    """Return two unit vectors perpendicular to ax_vec and to each other."""
    up = FreeCAD.Vector(0, 0, 1)
    if abs(ax_vec.dot(up)) > 0.98:
        up = FreeCAD.Vector(1, 0, 0)
    ref  = ax_vec.cross(up)
    ref.normalize()
    tang = ref.cross(ax_vec)
    tang.normalize()
    return ref, tang
```

**Step 4 — Add `_ring_radius` method** (insert after `_cone_h`, line ~53):

```python
def _ring_radius(self):
    return self._length * 0.85
```

**Step 5 — Add `_draw_ring` helper method** (insert before `draw`, line ~58):

```python
def _draw_ring(self, axis, center):
    from pivy import coin
    ax_vec       = self._axes[axis]
    ref, tang    = _perp_pair(ax_vec)
    R            = self._ring_radius()
    color        = self.AXIS_COLORS[axis]
    N            = _RING_SEGMENTS

    ring_pts = []
    for i in range(N + 1):
        theta = 2.0 * _math.pi * i / N
        pt = center + ref * (_math.cos(theta) * R) + tang * (_math.sin(theta) * R)
        ring_pts.append((pt.x, pt.y, pt.z))

    coords = coin.SoCoordinate3()
    coords.point.setValues(0, len(ring_pts), ring_pts)

    ls = coin.SoLineSet()
    ls.numVertices.setValue(len(ring_pts))

    ds = coin.SoDrawStyle()
    ds.lineWidth.setValue(2.5)

    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*color)
    mat.emissiveColor.setValue(*color)

    ring_sep = coin.SoSeparator()
    ring_sep.addChild(mat)
    ring_sep.addChild(ds)
    ring_sep.addChild(coords)
    ring_sep.addChild(ls)

    self._root.addChild(ring_sep)
    self._ring_seps[axis]   = ring_sep
    self._ring_coords[axis] = coords
```

**Step 6 — Call `_draw_ring` inside `draw()`** — append at the end of the `draw` method
body, after `self._root.addChild(ax_sep)` loop (after line ~115):

```python
        for axis in ('x', 'y', 'z'):
            self._draw_ring(axis, self._center)
```

**Step 7 — Update rings in `update()`** — append at the end of `update()` body
(after the existing `xf_c.rotation.setValue(sb_rot)` block, around line ~140):

```python
        for axis in ('x', 'y', 'z'):
            coords = self._ring_coords.get(axis)
            if coords is None:
                continue
            ax_vec = self._axes[axis]
            ref, tang = _perp_pair(ax_vec)
            R = self._ring_radius()
            N = _RING_SEGMENTS
            ring_pts = []
            for i in range(N + 1):
                theta = 2.0 * _math.pi * i / N
                pt = center + ref * (_math.cos(theta) * R) + tang * (_math.sin(theta) * R)
                ring_pts.append((pt.x, pt.y, pt.z))
            coords.point.setValues(0, len(ring_pts), ring_pts)
```

**Step 8 — Clean up rings in `undraw()`** — after the existing `try/except` block
(after line ~149):

```python
        self._ring_seps.clear()
        self._ring_coords.clear()
```

**Depends on:** GIZMO-001 (SoAnnotation — rings live in points_root)

---

### GIZMO-003: Add rotation ring hit-test

**File:** `core/dm_gizmo.py` — add module helper + modify `hit_test`

**What:** `hit_test` should return `'rot_x'`, `'rot_y'`, or `'rot_z'` when the ray
passes within tolerance of a rotation ring. Translation hits (`'x'`, `'y'`, `'z'`)
take priority over ring hits if both are within tolerance.

**Implementation:**

**Step 1 — Add `_ray_ring_dist` module helper** (insert after `_ray_segment_dist`,
after line ~194):

```python
def _ray_ring_dist(ray_p, ray_d, center, ax_vec, ring_r):
    """Signed distance from ray to a circle: |radial_distance - ring_radius|."""
    denom = ray_d.dot(ax_vec)
    if abs(denom) < 1e-8:
        return float('inf')
    t = (center - ray_p).dot(ax_vec) / denom
    if t < 0.0:
        return float('inf')
    pt = ray_p + ray_d * t
    return abs((pt - center).Length - ring_r)
```

**Step 2 — Update `hit_test`** — append ring tests after the existing shaft loop
(after the `for axis in ('x', 'y', 'z'):` shaft loop at line ~163):

```python
        # Test rotation rings (lower priority than shafts)
        ring_r = self._ring_radius()
        for axis in ('x', 'y', 'z'):
            dist = _ray_ring_dist(ray_p, ray_d, self._center,
                                  self._axes[axis], ring_r)
            if dist < best_dist:
                best_dist = dist
                best_axis = f'rot_{axis}'
```

The method already returns `best_axis` at the end, so no further change needed.

**Depends on:** GIZMO-002

---

## Tier 3 — Transform Space

### GIZMO-004: Add `_gizmo_space` state and `T` key cycle

**File:** `tools/primitive_tool.py`

**What:** Add `_gizmo_space` field (default `'world'`), a `_gizmo_axes()` method
that returns the correct world-space axis dict for the active space, and a `T` key
binding that cycles between `'world'` and `'local'`.

Also add `_rot_ring_ref` and `_rot_ring_tang` fields for the rotation drag (used in GIZMO-006).

**Implementation:**

**Step 1 — Add fields in `__init__`** — insert after `self._edit_snap_mode = 'off'`
(line 268):

```python
        self._gizmo_space    = 'world'   # 'world' | 'local'
        self._rot_ring_ref   = None      # FreeCAD.Vector, set at rotation drag start
        self._rot_ring_tang  = None      # FreeCAD.Vector, set at rotation drag start
```

**Step 2 — Add `_gizmo_axes` method** — insert after `_init_gizmo` (after line ~349):

```python
    def _gizmo_axes(self):
        """Return axes dict for the current transform space."""
        if self._gizmo_space == 'local' and self.working_plane:
            rot = self.working_plane.Rotation
            return {
                'x': rot.multVec(FreeCAD.Vector(1, 0, 0)),
                'y': rot.multVec(FreeCAD.Vector(0, 1, 0)),
                'z': rot.multVec(FreeCAD.Vector(0, 0, 1)),
            }
        return {
            'x': FreeCAD.Vector(1, 0, 0),
            'y': FreeCAD.Vector(0, 1, 0),
            'z': FreeCAD.Vector(0, 0, 1),
        }
```

**Step 3 — Add `T` key binding in `handle_keyboard`** — insert before the existing
`Key_X/Y/Z` block (before line 1192), inside the `if self._is_editing:` guard:

```python
        if self._is_editing and key == QtCore.Qt.Key_T:
            self._gizmo_space = 'local' if self._gizmo_space == 'world' else 'world'
            if self._gizmo:
                self._gizmo.update(self.working_plane.Base, axes=self._gizmo_axes())
            dm_logger.debug(f"Gizmo space: {self._gizmo_space}")
            if self.view:
                self.view.redraw()
            return True
```

**Depends on:** nothing

---

### GIZMO-005: Pass space-aware axes when drawing and updating the gizmo

**File:** `tools/primitive_tool.py` — modify `_init_gizmo` (line 340) and
`_gizmo_drag_update` (line 519)

**What:** Every call to `gizmo.draw()` or `gizmo.update()` should pass
`axes=self._gizmo_axes()` so the gizmo reflects the active transform space.

**Implementation:**

**Step 1 — Update `_init_gizmo`**: replace the `self._gizmo.draw(...)` call
(line ~349) with:

```python
        self._gizmo.draw(self.points_root, self.working_plane.Base,
                         axes=self._gizmo_axes(), length=length)
```

**Step 2 — Update `_gizmo_drag_update`**: replace the `self._gizmo.update(...)` call
(line ~534) with:

```python
            self._gizmo.update(self.working_plane.Base if self.working_plane else new_pt,
                               axes=self._gizmo_axes())
```

**Step 3 — Update `_drag_update` center and rot branches**: in the `'center'` drag
branch (around line 597) and the `is_ctrl` branch (around line 620), replace:

```python
                if self._gizmo:
                    self._gizmo.update(new_pt)
```
with:
```python
                if self._gizmo:
                    self._gizmo.update(new_pt, axes=self._gizmo_axes())
```
And similarly for the `'rot'` branch (around line 611):
```python
                if self._gizmo:
                    self._gizmo.update(self.working_plane.Base, axes=self._gizmo_axes())
```

**Depends on:** GIZMO-004

---

## Tier 4 — Rotation Ring Drag

### GIZMO-006: Wire rotation ring drag start in `_edit_on_mouse_press`

**File:** `tools/primitive_tool.py` — modify the gizmo hit-test block
(lines 419–430)

**What:** When `hit_test` returns `'rot_x'`, `'rot_y'`, or `'rot_z'`, start a
rotation drag instead of a translation drag. Store the ring plane vectors and
initial angle so `_gizmo_rot_drag_update` can compute angle deltas.

**Implementation:**

Replace the entire gizmo hit-test block (lines 419–430):

```python
        # Test gizmo axes (lower priority than center dot, higher than control points)
        if self._gizmo and self.working_plane:
            tol  = self._compute_handle_radius() * 2.5
            axis = self._gizmo.hit_test(ray_p, ray_d, tol)
            if axis:
                self._dragging_idx = f'gizmo_{axis}'
                if axis.startswith('rot_'):
                    # Rotation ring drag
                    from core.dm_gizmo import _perp_pair
                    ax_key  = axis[4:]   # 'x', 'y', or 'z'
                    ax_vec  = self._gizmo._axes[ax_key]
                    pivot   = FreeCAD.Vector(self.working_plane.Base)
                    self._edit_pivot        = pivot
                    self._drag_constraint_base = pivot
                    ref, tang = _perp_pair(ax_vec)
                    self._rot_ring_ref  = ref
                    self._rot_ring_tang = tang
                    # Seed the initial angle from the click position
                    pt = self.projector.get_mouse_world_pos(
                        event_dict, ax_vec, pivot, place_on_geometry=False)
                    if pt:
                        v = pt - pivot
                        self._edit_last_angle = math.atan2(v.dot(tang), v.dot(ref))
                    else:
                        self._edit_last_angle = 0.0
                else:
                    # Translation axis drag
                    ax_vec = self._gizmo._axes[axis]
                    click_pt = DMInputManager.get_instance().get_axis_point(
                        self.view, FreeCAD.Vector(self.working_plane.Base), ax_vec, event_dict)
                    self._drag_constraint_base = click_pt if click_pt else FreeCAD.Vector(self.working_plane.Base)
                self._start_drag_timer()
                return True
```

**Depends on:** GIZMO-002, GIZMO-003

---

### GIZMO-007: Implement `_gizmo_rot_drag_update` and dispatch from `_gizmo_drag_update`

**File:** `tools/primitive_tool.py` — add method + modify `_gizmo_drag_update` (line 519)

**What:** Add a new `_gizmo_rot_drag_update(axis_key)` method that projects the mouse
onto the ring plane, computes the angle delta since the last frame, and rotates all
points + the working_plane rotation by that delta. Then make `_gizmo_drag_update`
dispatch to it for `rot_` prefixed dragging indices.

**Implementation:**

**Step 1 — Add `_gizmo_rot_drag_update`** — insert immediately before `_gizmo_drag_update`
(before line 519):

```python
    def _gizmo_rot_drag_update(self, axis_key):
        """Rotate all control points and working_plane around a gizmo ring axis."""
        if not self._gizmo or axis_key not in self._gizmo._axes:
            return
        ax_vec = self._gizmo._axes[axis_key]
        pivot  = self._edit_pivot
        ref    = self._rot_ring_ref
        tang   = self._rot_ring_tang
        if pivot is None or ref is None or tang is None:
            return

        mouse_pos = DMInputManager.get_instance()._last_qt_pos
        pt = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos}, ax_vec, pivot, place_on_geometry=False)
        if not pt:
            return

        v     = pt - pivot
        angle = math.atan2(v.dot(tang), v.dot(ref))
        da    = angle - self._edit_last_angle
        if abs(da) < 1e-8:
            return

        rot = FreeCAD.Rotation(ax_vec, math.degrees(da))
        self.points = [pivot + rot.multVec(p - pivot) for p in self.points]
        if self.working_plane:
            self.working_plane.Rotation = self.working_plane.Rotation.multiply(rot)
        self._edit_last_angle = angle

        self._gizmo.update(pivot, axes=self._gizmo_axes())
        self._update_handle_positions(self.points)
        if hasattr(self, "panel") and self.panel:
            self.panel.update_ui()
        self.update_preview()
        if self.view:
            self.view.redraw()
```

**Step 2 — Dispatch in `_gizmo_drag_update`** — replace the first two lines of
`_gizmo_drag_update` body (lines 521–523):

```python
    def _gizmo_drag_update(self):
        """Translate or rotate all points + working_plane along/around the clicked gizmo handle."""
        axis = self._dragging_idx[len('gizmo_'):]
        if axis.startswith('rot_'):
            self._gizmo_rot_drag_update(axis[4:])   # 'rot_z' → 'z'
            return
        if not self._gizmo or axis not in self._gizmo._axes:
            return
        # ... remainder of existing translation logic unchanged ...
```

**Depends on:** GIZMO-006

---

## Tier 5 — Consistency and Polish

### GIZMO-008: Highlight rotation rings on hover

**File:** `tools/primitive_tool.py` — modify `_edit_hover` (line 372)

**What:** When the mouse hovers over a rotation ring, show `PointingHandCursor`.
The current `_edit_hover` only checks control point dots; extend it to also
test the gizmo (which already returns ring hits via the updated `hit_test`).

**Implementation:**

Add a gizmo hover check at the end of `_edit_hover`, after the existing
`idx, _ = self._hit_test_perp(...)` block (after line 383):

```python
        # Also test gizmo handles (translation arrows + rotation rings)
        if idx is None and self._gizmo and self.working_plane:
            tol  = self._compute_handle_radius() * 2.5
            axis = self._gizmo.hit_test(ray_p, ray_d, tol)
            if axis:
                from PySide.QtCore import Qt
                self._set_cursor(Qt.PointingHandCursor)
                return
```

**Depends on:** GIZMO-002, GIZMO-003

---

### GIZMO-009: Add transform space toggle to context menu

**File:** `tools/primitive_tool.py` — modify `get_snapping_menu` (line 1241)

**What:** Add space-cycle entries to the right-click context menu so the user
can switch transform space without using the keyboard.

**Implementation:**

Replace `get_snapping_menu` body:

```python
    def get_snapping_menu(self):
        items = [
            ("Snap Off",            lambda: self._set_edit_snap('off'),           self._edit_snap_mode == 'off'),
            ("Snap: Workplane+SDF", lambda: self._set_edit_snap('workplane_sdf'), self._edit_snap_mode == 'workplane_sdf'),
            ("Snap: All Geometry",  lambda: self._set_edit_snap('all'),           self._edit_snap_mode == 'all'),
        ]
        if getattr(self, "_is_editing", False):
            items += [
                None,  # separator
                ("Transform: World",  lambda: self._set_gizmo_space('world'), self._gizmo_space == 'world'),
                ("Transform: Local",  lambda: self._set_gizmo_space('local'), self._gizmo_space == 'local'),
            ]
        return items

    def _set_gizmo_space(self, space):
        self._gizmo_space = space
        if self._gizmo and self.working_plane:
            self._gizmo.update(self.working_plane.Base, axes=self._gizmo_axes())
        if self.view:
            self.view.redraw()
```

**Note:** the base-class `_build_context_menu` must handle `None` entries as
separators. Check `tools/dm_base.py` — if it doesn't, add:
```python
if item is None:
    menu.addSeparator()
    continue
```
to the menu-building loop before adding this task.

**Depends on:** GIZMO-004

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `dm_edit_gizmo` | Full architecture reference: x-ray rendering, ring geometry, transform spaces, rotation drag math (required for all GIZMO- tasks) |
| `dm_event_pipeline` | Qt + Coin3D dual input pipeline — check before editing `handle_keyboard` or `_edit_on_mouse_press` |
| `dm_sdf_primitive_pattern` | SdfField subclass template — not required for gizmo work but useful context |
