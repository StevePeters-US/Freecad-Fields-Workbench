---
name: DM Renderer Architecture
description: Reference for how Coin3D rendering is structured across the three geometry types (NURBS curves, F-Rep SDF, WorkPlane). Required reading before adding a new display type or modifying DMViewProvider.
---

# DM Renderer Architecture

Rendering is split between **FreeCAD's native Part shape renderer** (for NURBS/BRep) and **custom Coin3D nodes** (for F-Rep meshes and control cages). There is no shared abstraction — geometry type is detected via `ShapeType` string in `DMViewProvider`.

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

### 2. F-Rep SDF (`ShapeType == "frep"`)

**Shape source**: `DMObjectProxy.execute()` calls `MarchingCubesMesher.mesh(field, cell_size)` → `(verts, flat_idx)` stored as `_frep_verts` / `_frep_idx` on the proxy. Then sets `fp.Shape = Part.Shape()` (empty, to trigger `updateData`).

**FreeCAD renderer**: **suppressed** — `vobj.PointSize = 0`, `vobj.LineWidth = 0`, display mode forced to `"Shaded"`

**Coin3D rendering** via `DMRenderer.setup_frep_mesh_nodes()`:
- `_frep_coords` + `_frep_faces` — filled triangle mesh (orange, `SoIndexedFaceSet`)
- `_frep_wide_switch` → `_frep_wire_faces` — optional wireframe overlay (same indices, `LINES` draw style)
- `_frep_corner_coords` + `_frep_corner_pts` — 8 corner points of bounding box
- `_frep_handle_coords` + `_frep_handle_lines` — 12 bounding box edges

Updated via `DMRenderer.update_frep_mesh(verts, flat_idx)` and `update_frep_corners(field)`.

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
elif ShapeType == "frep":
    renderer.setup_frep_mesh_nodes()
# surface/point: nothing extra — Part renderer handles it
```

**`DMViewProvider.updateData(fp, prop)`** (`core/dm_object.py:408`):
```python
if prop == "Shape" and ShapeType == "frep":
    renderer.update_frep_mesh(...)
    renderer.update_frep_corners(...)
elif prop == "DisplayMode" and ShapeType == "frep":
    renderer.set_frep_display_mode(...)
elif prop in ["Points", "HandleIn", "HandleOut", "Closed", "EditMode"]:
    renderer.rebuild_control_cage(fp)
```

**`DMViewProvider.setup_view()`** (`core/dm_object.py:333`):
```python
if ShapeType == "surface":
    vobj.DisplayMode = "Shaded"
elif ShapeType == "frep":
    # suppress Part renderer
    vobj.PointSize = 0.0; vobj.LineWidth = 0.0
```

---

## DMRenderer Class Layout

`core/dm_renderer.py` — single class holding **all** geometry-specific Coin3D nodes:

```
DMRenderer
├── vis_switch          — SoSwitch wrapping everything (visibility)
├── F-Rep nodes
│   ├── _frep_sep           — root separator
│   ├── _frep_draw_style    — FILLED / LINES toggle
│   ├── _frep_coords        — vertex buffer
│   ├── _frep_faces         — SoIndexedFaceSet (triangles)
│   ├── _frep_wide_switch   — wireframe on/off switch
│   ├── _frep_wire_faces    — SoIndexedFaceSet (wireframe, same coords)
│   ├── _frep_corner_coords — bounding box corners
│   ├── _frep_corner_pts    — SoPointSet
│   ├── _frep_handle_coords — bounding box edges
│   └── _frep_handle_lines  — SoLineSet
└── NURBS overlay nodes
    ├── _ctrl_cage_sep      — root separator
    ├── _style              — SoDrawStyle (dashed lines)
    ├── _ctrl_coords        — all points (knots + handles + line endpoints)
    ├── _ctrl_lines         — SoLineSet (handle stems)
    ├── _ctrl_points        — SoPointSet (orange knot markers, edit mode)
    ├── _ctrl_handle_points — SoPointSet (blue handle markers)
    └── _knot_points        — SoPointSet (small white knot markers)
```

All F-Rep nodes are `None` until `setup_frep_mesh_nodes()` is called.
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
class FRepRendererStrategy(DMRendererStrategy): ...
```

`DMViewProvider.attach()` selects the strategy:
```python
self._strategy = FRepRendererStrategy() if ShapeType == "frep" else NURBSRendererStrategy()
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
4. `core/dm_mesher.py` — produces `(verts, flat_idx)` consumed by `update_frep_mesh()`
