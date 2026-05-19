---
name: DM Edit-Mode Gizmo
description: Architecture reference for the unified edit-mode transform gizmo (translation arrows + rotation rings, transform spaces, x-ray rendering). Required reading before implementing any task in todo.md whose ID begins with GIZMO-.
---

# DM Edit-Mode Gizmo

## Overview

The edit-mode gizmo is owned by `PrimitiveCreatorBase` in `tools/primitive_tool.py`.
It lives at `self._gizmo` (a `DMTransformGizmo` instance from `core/dm_gizmo.py`).
All gizmo geometry is parented under `self.points_root`, a `SoAnnotation` node added
to the scene graph root — this makes handles always render on top of (through) geometry.

---

## X-Ray Rendering

Replace `coin.SoSeparator()` with `coin.SoAnnotation()` for `self.points_root`
(`tools/primitive_tool.py`, line 242).

`SoAnnotation` renders its sub-graph after all depth-sorted geometry, ignoring depth,
so handles are always visible even when inside the SDF mesh. Hit-testing is unaffected
because the project uses custom ray-math, not Coin3D SoRayPickAction.

```python
# primitive_tool.py __init__(), was SoSeparator:
self.points_root = coin.SoAnnotation()
```

---

## DMTransformGizmo Architecture

File: `core/dm_gizmo.py`

### What it draws

| Element | Coin3D nodes | Purpose |
|---------|-------------|---------|
| Shaft (per axis) | `SoTransform` + `SoCylinder` | Translation drag handle |
| Cone tip (per axis) | `SoTransform` + `SoCone` | Arrow head |
| Ring (per axis) | `SoCoordinate3` + `SoLineSet` | Rotation drag handle |

### Key instance attributes

```python
self._center      # FreeCAD.Vector — current gizmo origin
self._axes        # dict: 'x','y','z' → FreeCAD.Vector (unit, world-space)
self._length      # float — overall scale in mm
self._ring_seps   # dict: 'x','y','z' → SoSeparator (the ring sub-node)
self._ring_coords # dict: 'x','y','z' → SoCoordinate3 (ring vertex buffer)
```

### Ring radius

```python
def _ring_radius(self):
    return self._length * 0.85
```

### Drawing a rotation ring

For axis `ax_vec`, find two perpendicular vectors `ref` and `tang`:

```python
import math as _math

def _perp_pair(ax_vec):
    up = FreeCAD.Vector(0, 0, 1)
    if abs(ax_vec.dot(up)) > 0.98:
        up = FreeCAD.Vector(1, 0, 0)
    ref  = ax_vec.cross(up); ref.normalize()
    tang = ref.cross(ax_vec); tang.normalize()
    return ref, tang
```

Then build a closed polyline of N+1 points (last == first to close the loop):

```python
N = 48
R = self._ring_radius()
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

ring_sep = coin.SoSeparator()
mat = coin.SoMaterial()
mat.diffuseColor.setValue(*AXIS_COLORS[axis])
mat.emissiveColor.setValue(*AXIS_COLORS[axis])
ring_sep.addChild(mat)
ring_sep.addChild(ds)
ring_sep.addChild(coords)
ring_sep.addChild(ls)
self._root.addChild(ring_sep)
self._ring_seps[axis]   = ring_sep
self._ring_coords[axis] = coords
```

### Updating rings on move

```python
# In update(center, axes):
for axis in ('x', 'y', 'z'):
    ax_vec = self._axes[axis]
    ref, tang = _perp_pair(ax_vec)
    R = self._ring_radius()
    ring_pts = []
    for i in range(N + 1):
        theta = 2.0 * _math.pi * i / N
        pt = center + ref * (_math.cos(theta) * R) + tang * (_math.sin(theta) * R)
        ring_pts.append((pt.x, pt.y, pt.z))
    self._ring_coords[axis].point.setValues(0, len(ring_pts), ring_pts)
```

---

## Hit-Testing Rotation Rings

Module-level helper in `core/dm_gizmo.py`:

```python
def _ray_ring_dist(ray_p, ray_d, center, ax_vec, ring_r):
    """Minimum distance from ray to the ring circle.
    Projects ray onto ring plane, returns |radial_dist - ring_r|.
    """
    denom = ray_d.dot(ax_vec)
    if abs(denom) < 1e-8:
        return float('inf')
    t = (center - ray_p).dot(ax_vec) / denom
    if t < 0.0:
        return float('inf')
    pt = ray_p + ray_d * t
    return abs((pt - center).Length - ring_r)
```

In `DMTransformGizmo.hit_test(ray_p, ray_d, tolerance)`, after testing shafts,
also test rings:

```python
for axis in ('x', 'y', 'z'):
    dist = _ray_ring_dist(ray_p, ray_d, self._center,
                          self._axes[axis], self._ring_radius())
    if dist < best_dist:
        best_dist = dist
        best_axis = f'rot_{axis}'
```

Return value is now one of: `'x'`, `'y'`, `'z'`, `'rot_x'`, `'rot_y'`, `'rot_z'`, or `None`.

---

## Transform Space

`PrimitiveCreatorBase` owns `_gizmo_space: str` (`'world'` | `'local'`).

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

Cycle with the `T` key in `handle_keyboard()`:

```python
if self._is_editing and key == QtCore.Qt.Key_T:
    self._gizmo_space = 'local' if self._gizmo_space == 'world' else 'world'
    if self._gizmo:
        self._gizmo.update(self.working_plane.Base, axes=self._gizmo_axes())
    if self.view:
        self.view.redraw()
    return True
```

Pass axes when drawing/updating the gizmo:

```python
# _init_gizmo():
self._gizmo.draw(self.points_root, self.working_plane.Base,
                 axes=self._gizmo_axes(), length=length)

# _gizmo_drag_update() and any other update call:
self._gizmo.update(self.working_plane.Base, axes=self._gizmo_axes())
```

---

## Rotation Ring Drag

### Starting the drag (`_edit_on_mouse_press`, existing gizmo block)

When `hit_test` returns `'rot_x'`, `'rot_y'`, or `'rot_z'`:

```python
elif axis.startswith('rot_'):
    self._dragging_idx = f'gizmo_{axis}'  # e.g. 'gizmo_rot_z'
    pivot = FreeCAD.Vector(self.working_plane.Base)
    self._edit_pivot = pivot
    self._drag_constraint_base = pivot
    # Compute initial reference angle in the ring plane
    ax_key  = axis[4:]   # 'x', 'y', or 'z'
    ax_vec  = self._gizmo._axes[ax_key]
    ref, tang = _dm_perp_pair(ax_vec)   # same helper as gizmo draw
    # Project current mouse onto ring plane to get initial angle
    click_pos = event_dict.get("Position")
    if click_pos:
        pt = self.projector.get_mouse_world_pos(
            {"Position": click_pos}, ax_vec, pivot, place_on_geometry=False)
        if pt:
            v = pt - pivot
            self._edit_last_angle = _math.atan2(v.dot(tang), v.dot(ref))
        else:
            self._edit_last_angle = 0.0
    self._rot_ring_ref  = ref
    self._rot_ring_tang = tang
    self._start_drag_timer()
    return True
```

Store `_rot_ring_ref` and `_rot_ring_tang` as instance attributes (set in `__init__` to `None`).

### Drag update (`_gizmo_rot_drag_update`)

```python
def _gizmo_rot_drag_update(self, axis_key):
    ax_vec = self._gizmo._axes[axis_key]
    pivot  = self._edit_pivot
    ref    = self._rot_ring_ref
    tang   = self._rot_ring_tang

    mouse_pos = DMInputManager.get_instance()._last_qt_pos
    pt = self.projector.get_mouse_world_pos(
        {"Position": mouse_pos}, ax_vec, pivot, place_on_geometry=False)
    if not pt:
        return

    v  = pt - pivot
    angle = _math.atan2(v.dot(tang), v.dot(ref))
    da    = angle - self._edit_last_angle
    rot   = FreeCAD.Rotation(ax_vec, _math.degrees(da))

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

### Dispatching from `_gizmo_drag_update`

```python
def _gizmo_drag_update(self):
    axis = self._dragging_idx[len('gizmo_'):]  # e.g. 'rot_z' or 'x'
    if axis.startswith('rot_'):
        self._gizmo_rot_drag_update(axis[4:])   # strip 'rot_' → 'z'
        return
    # ... existing translation logic ...
```

---

## Dependency Map

```
GIZMO-001  (SoAnnotation x-ray)
GIZMO-002  (ring geometry in dm_gizmo.py)    ← no deps
GIZMO-003  (ring hit-test in dm_gizmo.py)    ← GIZMO-002
GIZMO-004  (_gizmo_space + T key)            ← no deps
GIZMO-005  (pass axes to gizmo draw/update)  ← GIZMO-004
GIZMO-006  (rotation drag start)             ← GIZMO-002, GIZMO-003
GIZMO-007  (_gizmo_rot_drag_update)          ← GIZMO-006
GIZMO-008  (hover highlight rings)           ← GIZMO-002, GIZMO-003
GIZMO-009  (space toggle in context menu)    ← GIZMO-004
```
