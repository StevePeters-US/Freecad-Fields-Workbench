# FreeCAD Direct Modeling Workbench

A Python workbench for FreeCAD that provides fast, interactive direct modeling using **Signed Distance Fields (Implicit Geometry)**. Users draw curves and surfaces on a dynamic workplane, convert them into SDF functions, and combine them with field-based boolean operations — all without leaving the 3D viewport.

---

## Core Concept: Signed Distance Fields from NURBS

Traditional CAD uses B-Rep (boundary representation): shells of faces, edges, and vertices that must form watertight manifolds. This workbench takes a different approach — **each NURBS surface is evaluated as a spatial discriminator**: a function `f(P)` that returns a signed scalar for any point in space, denoting inside/outside.

```
                    NURBS Surface
                         │
              ┌──────────┴──────────┐
              │  Signed Distance Field     │
              │  Evaluation         │
              └──────────┬──────────┘
                         │
               sign = analytic_eval(P)
              ┌──────────┴──────────┐
              │  f(P) > 0  outside  │
              │  f(P) = 0  on surf  │
              │  f(P) < 0  inside   │
              └─────────────────────┘
```

**How it works:**

1. **Evaluation** — For any query point `P`, the signed distance field assesses its position relative to the root geometry.
2. **Signing** — Returns a scalar. Positive = outside, negative = inside, zero = on the surface boundaries.
3. **Bounding** — A single surface defines a field extending to infinity. Clip it with bounding planes via `max(f_field, f_bound)` to create a finite influence region.
4. **Composition** — Combine multiple bounded fields using min/max trees:
   - **Union**: `min(f_A, f_B)`
   - **Intersection**: `max(f_A, f_B)`
   - **Subtraction**: `max(f_A, −f_B)`
   - **Smooth blend (R-Union)**: parametric blending function for fillets and transitions

This gives you **NURBS-quality surface control** with **SDF operational flexibility** — enabling lattice infills, smooth blends, and hollowing operations that are mathematically impossible or crash-prone in standard B-Rep CAD.

---

## Architecture

```
Workplane (tangent to surface or camera-aligned)
    ↓
Point / Curve tools (draw geometry on the workplane)
    ↓
NURBS Surfaces (patches from curves, primitives, lofts)
    ↓
Signed Distance Field Engine (NURBS → SDF function per surface)
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
├── AntiGravity_Skills/            # [NEW] Agent Skills
│   ├── readme_folder_structure_updater.md # Skill to update this listing
│   ├── readme_generator.md        # Skill to generate this README
│   ├── todo_generator.md          # Skill to generate task outlines
│   └── todo_task_completed.md     # Skill to move completed tasks
│
├── core/                          # Core logic
│   ├── __init__.py
│   ├── dm_logger.py               # Centralized logging (FreeCAD Console + file)
│   ├── dm_object.py               # DMObjectProxy, DMViewProvider, factory
│   ├── dm_part.py                 # DM_Part FeaturePython wrapper
│   ├── dm_workplane.py            # DMWorkPlane FeaturePython object
│   ├── sdf_mesher.py             # Isosurface extraction (marching cubes / DC)
│   ├── input_manager.py           # Global input event routing
│   ├── nurbs_geometry.py          # DMPoint, DMCurve — NURBS primitives
│   ├── work_plane.py              # WorkPlaneManager — Coin3D grid & snapping
│   └── sdf/                  # Signed Distance Field Engine
│       ├── sdf_field.py      # Abstract base SdfField
│       ├── field_composer.py      # Boolean composition tree (min/max/blend)
│       └── primitives/            # Specialized primitive fields (sphere, box, etc.)
│
├── tools/                         # Interactive creation tools
│   ├── __init__.py
│   ├── curve_tool.py              # BSpline curve drawing
│   ├── dm_base.py                 # Base class for all interactive tools
│   ├── edit_tool.py               # Control point editing
│   ├── point_tool.py              # Point placement
│   ├── primitive_tool.py          # Parametric primitives (box, sphere, etc.)
│   ├── translate_tool.py          # Move/translate
│   └── work_plane_tool.py         # Workplane creation & scaling
│
├── commands/                      # FreeCADGui command definitions
│   ├── __init__.py
│   ├── cmd_boolean.py             # DM_Fuse / DM_Cut / DM_Common
│   ├── cmd_curve.py               # DM_CreateCurve
│   ├── cmd_edit.py                # DM_EditObject
│   ├── cmd_fill_curve.py          # DM_FillCurve
│   ├── cmd_install_deps.py        # Dependency installation script
│   ├── cmd_point.py               # DM_CreatePoint
│   ├── cmd_primitive.py           # DM_CreateBox / Sphere / Cylinder
│   ├── cmd_settings.py            # DM_Settings dialog
│   ├── cmd_sketcher.py            # DM_OpenSketcher
│   ├── cmd_translate.py           # DM_Translate
│   └── cmd_workplane.py           # DM_WorkPlane
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
| Field Union | `DM_Add` | `Ctrl+F` | Planned (SDF) |
| Field Cut | `DM_Subtract` | `Ctrl+X` | Planned (SDF) |
| Field Intersect | `DM_Intersection` | `Ctrl+I` | Planned (SDF) |
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

### Input & Fallbacks

- **No Silent Fallbacks**: We should never have silent fallbacks that unexpectedly change state based on hidden history.
- **Workplane Fallback**: The viewport aligned plane (camera-facing) is the ONLY acceptable fallback when a point is clicked in empty space without an active workplane. NEVER use the "last working plane" as a fallback.

---

## Future: CNC Tool Path Generation

One of the longer-term goals of this project is to generate CNC tool paths directly from the function-based SDF representation — without any intermediate meshing step.

Because SDFs encode geometry as a continuous function rather than a polygon mesh, several CAM problems map onto them naturally:

- **Cutter-radius compensation** — the tool center path for a ball-end mill of radius `r` is the isosurface `f(p) = r`. No mesh offsetting needed; just change the evaluation threshold.
- **Gouge detection** — a tool at position `p` is gouging if `f(p) < r`. A single function evaluation, testable at any point along the path.
- **Surface normals** — `∇f` gives exact, smooth normals everywhere. Ideal for 5-axis tool orientation without mesh normal interpolation artifacts.
- **Adaptive stepover** — principal curvatures can be derived from the Hessian of `f`, enabling scallop-height equalization analytically.
- **Rest machining / pocket detection** — regions accessible to a small tool but not a large one are `{p : f_large(p) > 0 and f_small(p) ≤ r}`. Pure SDF logic.
- **Z-slice roughing** — a 2D cut boundary at height `z_i` is the zero-crossing of `f(x, y, z_i)`, extracted via marching squares on a 2D slice.

This approach only works cleanly with **analytic/function-based** SDFs (not voxel grids), which is why the engine in this workbench evaluates fields procedurally rather than baking them to a volume.

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
