---
name: DM Renderer Architecture
description: Reference for how Coin3D rendering is structured across the three geometry types (NURBS curves, SDF SDF, WorkPlane). Required reading before adding a new display type or modifying DMViewProvider.
---

# DM Renderer Architecture

Rendering is split between **FreeCAD's native Part shape renderer** (for NURBS/BRep) and **custom Coin3D nodes** (for SDF meshes and control cages). There is no shared abstraction — geometry type is detected via `ShapeType` string in `DMViewProvider`.

---

## The Three Strategies

### 1. NURBS/BRep (curves, points, surfaces)

**`ShapeType`**: `"curve"`, `"point"`, `"surface"`

**Shape source**: `DMObjectProxy.build_shape()` returns a real `Part.Shape`

**FreeCAD renderer**: draws the shape normally (edges, faces, vertices)

**Coin3D overlay** (curves only): `DMRenderer.setup_coin_overlay()` adds control cage
- `_ctrl_lines` — dashed lines from knot → handle in/out
- `_ctrl_points` — orange square markers at knots (edit mode)
- `_ctrl_handle_points` — blue markers at handle endpoints
- `_knot_points` — small white markers at all knots

Overlay is rebuilt on every `updateData()` call for `["Points", "HandleIn", "HandleOut", "Closed", "EditMode"]`.

### 2. SDF SDF (`ShapeType == "sdf"`)

**Shape source**: `DMObjectProxy.execute()` calls `MarchingCubesMesher.mesh(field, cell_size)` → `(verts, flat_idx)` stored as `_sdf_verts` / `_sdf_idx` on the proxy. Then sets `fp.Shape = Part.Shape()` (empty, to trigger `updateData`).

**FreeCAD renderer**: **suppressed** — `vobj.PointSize = 0`, `vobj.LineWidth = 0`, display mode forced to `"Shaded"`

**Coin3D rendering** via `DMRenderer.setup_sdf_mesh_nodes()`:
- `_sdf_coords` + `_sdf_faces` — filled triangle mesh (orange, `SoIndexedFaceSet`)
- `_sdf_wide_switch` → `_sdf_wire_faces` — optional wireframe overlay (same indices, `LINES` draw style)
- `_sdf_corner_coords` + `_sdf_corner_pts` — 8 corner points of bounding box
- `_sdf_handle_coords` + `_sdf_handle_lines` — 12 bounding box edges

Updated via `DMRenderer.update_sdf_mesh(verts, flat_idx)` and `update_sdf_corners(field)`.

### 3. WorkPlane (`DMWorkPlane`)

**Not a `DMObjectProxy`** — uses `ViewProviderDMWorkPlane` in `core/dm_workplane.py`

**FreeCAD renderer**: suppressed (no Part shape)

**Coin3D rendering**: built directly in `ViewProviderDMWorkPlane.attach()`:
- `grid_sep` → `SoCoordinate3` + `SoLineSet` — grid lines
- `face_sep` → `SoFaceSet` — semi-transparent fill
- `center_sep` — red crosshair at origin
- `SoTransform` at index 0 (FreeCAD's Part renderer drives the transform)

---

## Where ShapeType Branching Happens

**`DMViewProvider.attach()`** (`core/dm_object.py:377`):
```python
if ShapeType == "curve":
    renderer.setup_coin_overlay()
    renderer.rebuild_control_cage(obj)
elif ShapeType == "sdf":
    renderer.setup_sdf_mesh_nodes()
# surface/point: nothing extra — Part renderer handles it
```

**`DMViewProvider.updateData(fp, prop)`** (`core/dm_object.py:408`):
```python
if prop == "Shape" and ShapeType == "sdf":
    renderer.update_sdf_mesh(...)
    renderer.update_sdf_corners(...)
elif prop == "DisplayMode" and ShapeType == "sdf":
    renderer.set_sdf_display_mode(...)
elif prop in ["Points", "HandleIn", "HandleOut", "Closed", "EditMode"]:
    renderer.rebuild_control_cage(fp)
```

**`DMViewProvider.setup_view()`** (`core/dm_object.py:333`):
```python
if ShapeType == "surface":
    vobj.DisplayMode = "Shaded"
elif ShapeType == "sdf":
    # suppress Part renderer
    vobj.PointSize = 0.0; vobj.LineWidth = 0.0
```

---

## DMRenderer Class Layout

`core/dm_renderer.py` — single class holding **all** geometry-specific Coin3D nodes:

```
DMRenderer
├── vis_switch          — SoSwitch wrapping everything (visibility)
├── SDF nodes
│   ├── _sdf_sep           — root separator
│   ├── _sdf_draw_style    — FILLED / LINES toggle
│   ├── _sdf_coords        — vertex buffer
│   ├── _sdf_faces         — SoIndexedFaceSet (triangles)
│   ├── _sdf_wide_switch   — wireframe on/off switch
│   ├── _sdf_wire_faces    — SoIndexedFaceSet (wireframe, same coords)
│   ├── _sdf_corner_coords — bounding box corners
│   ├── _sdf_corner_pts    — SoPointSet
│   ├── _sdf_handle_coords — bounding box edges
│   └── _sdf_handle_lines  — SoLineSet
└── NURBS overlay nodes
    ├── _ctrl_cage_sep      — root separator
    ├── _style              — SoDrawStyle (dashed lines)
    ├── _ctrl_coords        — all points (knots + handles + line endpoints)
    ├── _ctrl_lines         — SoLineSet (handle stems)
    ├── _ctrl_points        — SoPointSet (orange knot markers, edit mode)
    ├── _ctrl_handle_points — SoPointSet (blue handle markers)
    └── _knot_points        — SoPointSet (small white knot markers)
```

All SDF nodes are `None` until `setup_sdf_mesh_nodes()` is called.
All NURBS nodes are `None` until `setup_coin_overlay()` is called.

---

## Refactor Goal

Replace `ShapeType` branching with a strategy pattern:

```python
class DMRendererStrategy:
    def setup(self, renderer: DMRenderer, vobj) -> None: ...
    def update(self, renderer: DMRenderer, fp, prop: str) -> None: ...
    def set_display_mode(self, renderer: DMRenderer, mode: str) -> None: ...

class NURBSRendererStrategy(DMRendererStrategy): ...
class SdfRendererStrategy(DMRendererStrategy): ...
```

`DMViewProvider.attach()` selects the strategy:
```python
self._strategy = SdfRendererStrategy() if ShapeType == "sdf" else NURBSRendererStrategy()
self._strategy.setup(self.renderer, vobj)
```

`DMViewProvider.updateData()` delegates:
```python
self._strategy.update(self.renderer, fp, prop)
```

Adding a 4th geometry type requires only a new `DMRendererStrategy` subclass.

---

## Files to Read Before Editing

1. `core/dm_renderer.py` — all Coin3D node creation and update logic
2. `core/dm_object.py` — `DMViewProvider.attach()`, `updateData()`, `setup_view()` (the branching points)
3. `core/dm_workplane.py` — `ViewProviderDMWorkPlane` (separate renderer, not using `DMRenderer`)
4. `core/dm_mesher.py` — produces `(verts, flat_idx)` consumed by `update_sdf_mesh()`
