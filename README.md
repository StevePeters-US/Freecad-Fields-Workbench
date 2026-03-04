# FreeCAD Direct Modeling Workbench

A Python workbench for FreeCAD that provides fast, interactive direct modeling using **F-Rep (Function Representation)** with NURBS-derived signed distance fields. Users draw curves and surfaces on a dynamic workplane, convert them into implicit fields, and combine them with field-based boolean operations — all without leaving the 3D viewport.

---

## Core Concept: F-Rep from NURBS

Traditional CAD uses B-Rep (boundary representation): shells of faces, edges, and vertices that must form watertight manifolds. This workbench takes a different approach — **each NURBS surface becomes a spatial discriminator**: a function `f(P)` that returns a signed scalar for any point in space.

```
                    NURBS Surface
                         │
              ┌──────────┴──────────┐
              │  Closest-Point      │
              │  Projection (CPP)   │
              └──────────┬──────────┘
                         │
               sign = dot(P − Q, n̂)
              ┌──────────┴──────────┐
              │  f(P) > 0  outside  │
              │  f(P) = 0  on surf  │
              │  f(P) < 0  inside   │
              └─────────────────────┘
```

**How it works:**

1. **Projection** — For any query point `P`, find the closest point `Q` on the NURBS surface.
2. **Signing** — Compute `dot(P − Q, n̂)` where `n̂` is the surface normal at `Q`. Positive = outside (aligned with normal), negative = inside, zero = on the surface.
3. **Bounding** — A single surface defines a field extending to infinity. Clip it with bounding planes via `max(f_nurbs, f_bound)` to create a finite influence region.
4. **Composition** — Combine multiple bounded fields using min/max trees:
   - **Union**: `min(f_A, f_B)`
   - **Intersection**: `max(f_A, f_B)`
   - **Subtraction**: `max(f_A, −f_B)`
   - **Smooth blend (R-Union)**: parametric blending function for fillets and transitions

This gives you **NURBS-quality surface control** with **F-Rep operational flexibility** — enabling lattice infills, smooth blends, and hollowing operations that are mathematically impossible or crash-prone in standard B-Rep CAD.

---

## Architecture

```
Workplane (tangent to surface or camera-aligned)
    ↓
Point / Curve tools (draw geometry on the workplane)
    ↓
NURBS Surfaces (patches from curves, primitives, lofts)
    ↓
F-Rep Field Engine (NURBS → signed distance field per surface)
    ↓
Field Composition (boolean union/cut/intersect via min/max)
    ↓
Isosurface Extraction (field → mesh/BRep for display)
    ↓
Part::FeaturePython → FreeCAD viewport
```

### Key Components

| Component | Role |
|-----------|------|
| **NURBS Surface** | Defines local curvature and the "face" of the object |
| **Field Function** | Converts the surface + normal into a signed scalar field |
| **Bounding Planes** | Restricts each surface's influence to a finite region |
| **Composition Operator** | Decides how segments combine (sharp seam, smooth fillet, etc.) |
| **Isosurface Extractor** | Samples the composed field and extracts a renderable mesh |

---

## Folder Structure

```
Freecad-Direct-Modeling/
├── InitGui.py                     # Workbench registration & toolbar/menu setup
├── README.md                      # This file
├── TODO.md                        # Task breakdown (self-contained, LLM-friendly)
├── COMPLETED.md                   # Archive of completed tasks
│
├── core/                          # Core logic
│   ├── __init__.py
│   ├── dm_logger.py               # Centralized logging (FreeCAD Console + file)
│   ├── dm_object.py               # DMObjectProxy, DMViewProvider, factory
│   ├── dm_part.py                 # DM_Part FeaturePython wrapper
│   ├── nurbs_geometry.py          # DMPoint, DMCurve — NURBS primitives
│   ├── work_plane.py              # WorkPlaneManager — Coin3D grid & snapping
│   ├── dm_workplane.py            # DMWorkPlane FeaturePython object
│   ├── input_manager.py           # Global input event routing
│   ├── frep_field.py              # [NEW] F-Rep field functions & CPP engine
│   ├── frep_composer.py           # [NEW] Boolean composition tree (min/max/blend)
│   └── frep_mesher.py             # [NEW] Isosurface extraction (marching cubes / DC)
│
├── tools/                         # Interactive creation tools
│   ├── __init__.py
│   ├── dm_base.py                 # Base class for all interactive tools
│   ├── point_tool.py              # Point placement
│   ├── curve_tool.py              # BSpline curve drawing
│   ├── edit_tool.py               # Control point editing
│   ├── translate_tool.py          # Move/translate
│   ├── work_plane_tool.py         # Workplane creation & scaling
│   ├── surface_tool.py            # [NEW] NURBS surface from curves
│   └── primitive_tool.py          # [NEW] Parametric primitives (box, sphere, etc.)
│
├── commands/                      # FreeCADGui command definitions
│   ├── __init__.py
│   ├── cmd_point.py               # DM_CreatePoint
│   ├── cmd_curve.py               # DM_CreateCurve
│   ├── cmd_fill_curve.py          # DM_FillCurve
│   ├── cmd_edit.py                # DM_EditObject
│   ├── cmd_boolean.py             # DM_Fuse / DM_Cut / DM_Common
│   ├── cmd_workplane.py           # DM_WorkPlane
│   ├── cmd_sketcher.py            # DM_OpenSketcher
│   ├── cmd_settings.py            # DM_Settings dialog
│   ├── cmd_translate.py           # DM_Translate
│   ├── cmd_surface.py             # [NEW] DM_CreateSurface
│   └── cmd_primitive.py           # [NEW] DM_CreateBox / Sphere / Cylinder
│
└── resources/
    ├── resources.qrc
    └── icons/                     # SVG icons for toolbar buttons
```

---

## Toolbar

### Creation
| Command | ID | Hotkey | Status |
|---------|----|--------|--------|
| Place Point | `DM_CreatePoint` | `P` | ✅ |
| Draw Curve | `DM_CreateCurve` | `C` | ✅ |
| Fill Curve | `DM_FillCurve` | — | ✅ |
| Create Surface | `DM_CreateSurface` | `S` | Planned |
| Create Box | `DM_CreateBox` | `B` | Planned |
| Create Sphere | `DM_CreateSphere` | — | Planned |
| Create Cylinder | `DM_CreateCylinder` | — | Planned |

### Operations
| Command | ID | Hotkey | Status |
|---------|----|--------|--------|
| Field Union | `DM_Fuse` | `Ctrl+F` | Planned (F-Rep) |
| Field Cut | `DM_Cut` | `Ctrl+X` | Planned (F-Rep) |
| Field Intersect | `DM_Common` | `Ctrl+I` | Planned (F-Rep) |
| Smooth Blend | `DM_Blend` | `Ctrl+B` | Planned |
| Translate | `DM_Translate` | `T` | ✅ |

### Utilities
| Command | ID | Status |
|---------|----|--------|
| Work Plane | `DM_WorkPlane` | ✅ |
| Open Sketcher | `DM_OpenSketcher` | ✅ |
| DM Settings | `DM_Settings` | ✅ |

---

## Workplane System

The workplane drives all geometry creation. It auto-orients to geometry under the cursor.

| Cursor Over | Workplane Behavior | Visual |
|-------------|-------------------|--------|
| A face on existing geometry | Normal to face at hit point | Green tint |
| Empty space | XY plane at origin | Blue tint |

### Interaction Flow
1. **1st click** → Locks the workplane
2. **2nd click** → Starts the active tool
3. **Subsequent clicks** → Continues tool use
4. **Right click / Enter** → Finishes the tool
5. **Esc** → Cancels the tool

### Edit Tool (BSpline Manipulation)
1. Select a curve and activate the **Edit Tool** (`T` or via toolbar).
2. **Control Points** (orange) and **Handles** (blue) become visible.
3. **Pick and Drop**: Click to select, move, click again to place.
4. Movement is constrained to the curve's plane or the current view plane.

---

## Code Conventions

### Style
- **Python 3.8+** (FreeCAD 0.21+ / 1.0 / 1.2)
- **PEP 8**: 4-space indent, 100-char soft limit
- `snake_case` functions, `PascalCase` classes, `_` prefix for private
- **Imports**: FreeCAD → PySide → project → stdlib
- **Docstrings**: Google-style on all public classes and functions

### Logging

Use `dm_logger` — **never** bare `print()` or stdlib `logging`.

```python
from core import dm_logger
dm_logger.debug("message")   # → FreeCAD.Console.PrintMessage
dm_logger.info("message")    # → FreeCAD.Console.PrintMessage
dm_logger.warn("message")    # → FreeCAD.Console.PrintWarning
dm_logger.error("message")   # → FreeCAD.Console.PrintError
```

Set `DEBUG_DM_CRASH=1` to also write logs to `~/.FreeCAD/DirectModeling.log`.

### Event Safety

**All document mutations** must be deferred via `QTimer.singleShot(0, fn)`:
- Assigning `.Shape`
- Creating/deleting objects
- `doc.recompute()`
- Closing dialogs

---

## Installation

1. Clone or symlink this repo into your FreeCAD `Mod` directory:
   - **Linux**: `~/.FreeCAD/Mod/`
   - **macOS**: `~/Library/Application Support/FreeCAD/Mod/`
   - **Windows**: `%APPDATA%\FreeCAD\Mod\`
2. Restart FreeCAD
3. Select **Direct Modeling** from the workbench dropdown

### Dependencies

| Package | Required | Purpose |
|---------|----------|---------|
| FreeCAD 0.21+ / 1.0 / 1.2 | ✅ | Host application |
| NumPy | ✅ (bundled) | Linear algebra, field sampling |
| Shapely | ✅ | 2D geometric operations |
| SciPy | Optional | Accelerated closest-point queries (KD-tree) |
