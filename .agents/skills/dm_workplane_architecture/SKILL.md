---
name: DM WorkPlane Architecture
description: Reference for the three parallel workplane systems and how they interact. Required reading before touching workplane placement, projection, or the WorkPlaneCreator tool.
---

# DM WorkPlane Architecture

There are **three separate workplane concepts** in this codebase. They overlap but have no shared interface.

---

## The Three Systems

### 1. `WorkPlaneManager` — Transient Coin3D Grid
**File**: `core/work_plane.py`

- Created per-tool, not persisted to the document
- Renders a live preview grid that follows the mouse (surface-snapping or camera-facing fallback)
- Exposes `get_placement()` → `FreeCAD.Placement`
- Shows/hides via `show()` / `hide()`
- Calls `projector.get_geometry_info(event_dict)` to detect geometry hits and snap its normal
- **Destroyed when the tool exits**

### 2. `DMWorkPlane` — Persistent Document Object
**File**: `core/dm_workplane.py`

- A `Part::FeaturePython` object stored in the FreeCAD document
- Has `Placement`, `Length`, `Width`, `GridSpacing` properties
- `ViewProviderDMWorkPlane.attach()` builds its own separate Coin3D grid (duplicates `WorkPlaneManager`'s visual code)
- Retrieved via `FreeCADGui.Selection` or by scanning `doc.Objects` for `Proxy.__class__.__name__ == "DMWorkPlane"`
- **Survives save/load**

### 3. `DMBase.working_plane` — Per-Tool Placement
**File**: `tools/dm_base.py`

- A plain `FreeCAD.Placement` stored on each tool instance
- Set in `_detect_selected_workplane()` from a selected `DMWorkPlane`, or from `WorkPlaneManager.get_placement()` during the tool session
- Used by `ViewProjector.get_mouse_plane_pt()` and `get_projected_point()` to constrain mouse clicks to the plane
- **Ephemeral — lost when tool exits**

---

## How They Interact (Data Flow)

```
User selects a DMWorkPlane in scene
    → DMBase._detect_selected_workplane()
        → reads obj.Placement
        → sets self.working_plane = obj.Placement

Mouse moves during tool
    → WorkPlaneManager.update(event_dict)
        → ViewProjector.get_geometry_info() → surface snap
        → DMInputManager.get_view_transform() → camera-facing fallback
        → updates self.current_placement

User clicks
    → ViewProjector.get_mouse_plane_pt(event_dict)
        → queries all visible DMWorkPlane objects (doc.Objects scan)
        → ray-casts against each plane
        → returns closest hit OR camera-plane fallback
    → tool uses self.working_plane for local↔global transform
```

**Problem**: `working_plane` (step 1) and `WorkPlaneManager.current_placement` (step 2) and `get_mouse_plane_pt()` (step 3) may all disagree if the user hasn't explicitly selected a workplane.

---

## Key Methods

| Method | File | What it does |
|--------|------|-------------|
| `WorkPlaneManager.update(event_dict)` | `core/work_plane.py:98` | Raycasts to update live grid visual |
| `WorkPlaneManager.get_placement()` | `core/work_plane.py:169` | Returns current transient placement |
| `DMBase._detect_selected_workplane()` | `tools/dm_base.py:78` | Reads selection → sets `self.working_plane` |
| `ViewProjector.get_mouse_plane_pt()` | `core/view_projector.py` | Finds point on nearest visible DMWorkPlane |
| `ViewProjector.get_geometry_info()` | `core/view_projector.py` | Ray-casts to find surface hit + normal |

---

## Grid Visual Duplication

Both `WorkPlaneManager` and `ViewProviderDMWorkPlane` build the same Coin3D grid structure independently:

```
WorkPlaneManager._setup_grid(size, steps)
    coin.SoSeparator → SoMaterial + SoCoordinate3 + SoLineSet + SoFaceSet

ViewProviderDMWorkPlane._setup_grid(length, width, spacing)
    coin.SoSeparator → SoMaterial + SoCoordinate3 + SoLineSet + SoFaceSet + center cross
```

The persistent workplane adds a red center cross (`center_sep`); the transient one does not.

---

## Refactor Goal

Replace all three systems with a single `ActiveWorkPlane` class:

```python
class ActiveWorkPlane:
    placement: FreeCAD.Placement

    def to_local(self, world_pt: FreeCAD.Vector) -> FreeCAD.Vector: ...
    def to_global(self, local_pt: FreeCAD.Vector) -> FreeCAD.Vector: ...
    def project_ray(self, ray_origin: FreeCAD.Vector, ray_dir: FreeCAD.Vector) -> FreeCAD.Vector: ...
    def normal(self) -> FreeCAD.Vector: ...
```

- `WorkPlaneManager` becomes a factory/cache that returns `ActiveWorkPlane` instances
- `DMBase.working_plane` becomes `DMBase.active_workplane: ActiveWorkPlane`
- All coordinate math calls `self.active_workplane.project_ray(...)` instead of inline math

---

## Files to Read Before Editing

1. `core/work_plane.py` — transient manager
2. `core/dm_workplane.py` — persistent object + view provider
3. `tools/dm_base.py` — per-tool placement + `_detect_selected_workplane()`
4. `tools/work_plane_tool.py` — workplane creation tool (uses all three systems)
5. `core/view_projector.py` — `get_mouse_plane_pt()`, `get_geometry_info()`
