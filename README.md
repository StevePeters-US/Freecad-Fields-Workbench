# FreeCAD Direct Modeling Workbench

A Python workbench for FreeCAD that provides fast, interactive direct modeling using **Signed Distance Fields (Implicit Geometry)** driven by NURBS control geometry. The source of truth is always NURBS — points, curves, and surfaces — stored as lightweight `Part::FeaturePython` properties. The SDF is never stored; it is generated in two separate pipelines:

1. **GPU Analytical Preview** — Each SDF field compiles to GLSL and is ray-marched in realtime on the GPU via a multi-pass SSAO fragment shader. Zero memory overhead, unlimited resolution.
2. **CPU On-Demand Cache** — When a tool needs direct SDF access (meshing, slicing, hit-testing), a sparse octree evaluator generates the field only in a narrow band around the surface. This scales with surface area, not volume.

This dual-pipeline design enables topology-free modeling accurate to **0.05mm** within a **1m³** work area — a resolution that would require 32 TB of RAM with a naive dense grid, but is tractable with hierarchical evaluation.

---

## Core Concept: Signed Distance Fields from NURBS

Traditional CAD uses B-Rep (boundary representation): shells of faces, edges, and vertices that must form watertight manifolds. This workbench takes a different approach — **each NURBS point, curve, or surface is evaluated as a spatial discriminator**: a function `f(P)` that returns a signed scalar for any point in space, denoting inside/outside.

```
                    NURBS Geometry (Points/Curves/Surfaces)
                         │
              ┌──────────┴──────────┐
              │  Signed Distance Field     │
              │  Evaluation (GPU Realtime) │
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
2. **Realtime Preview** — GPU Fragment shaders ray-march the analytic distance to render the surface instantly without meshing.
3. **On-Demand Caching** — When meshing or slicing is required, a sparse hierarchical distance field is evaluated exactly where needed in local CPU memory to support 0.05mm precision over 1m³ areas.
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
├── AGENTS.md                      # Shared AI agent guidance
├── INDEX.md                       # Fast-lookup file/class/skill index
├── arch_report.md                 # Architecture report & task roadmap
│
├── core/                          # Core logic
│   ├── __init__.py
│   ├── dm_logger.py               # Centralized logging (FreeCAD Console + file)
│   ├── dm_object.py               # DMObjectProxy, DMViewProvider, factory
│   ├── dm_part.py                 # DM_Part FeaturePython wrapper
│   ├── dm_curve.py                # DMCurve — NURBS curve primitive
│   ├── dm_line.py                 # DM line primitive
│   ├── dm_point.py                # DMPoint — control point primitive
│   ├── dm_surface.py              # DMSurface — NURBS surface primitive
│   ├── dm_workplane.py            # DMWorkPlane FeaturePython object
│   ├── dm_noise_object.py         # DMNoiseProxy FeaturePython object
│   ├── dm_mesher.py               # Isosurface extraction (MC, Surface Nets, DC, Adaptive MC)
│   ├── dm_renderer.py             # Coin3D control cage (spheres, lines)
│   ├── dm_ray_march_renderer.py   # Per-object GPU ray march renderer
│   ├── dm_scene_ray_march_renderer.py  # Scene-level SSAO GPU ray march compositor (singleton)
│   ├── dm_gizmo.py                # 3D interactive gizmo handles
│   ├── dm_selection_manager.py    # Selection state management
│   ├── dm_tool_manager.py         # Tool lifecycle management
│   ├── dm_menu.py                 # Context menu helpers
│   ├── input_manager.py           # Global input event routing (Qt event filter)
│   ├── view_projector.py          # Ray-casting, geometry picking, workplane projection
│   ├── work_plane.py              # WorkPlaneManager — Coin3D grid & snapping
│   ├── gl_program.py              # GLProgram — GLSL shader wrapper (ctypes OpenGL)
│   ├── gl_framebuffer.py          # GLFramebuffer — FBO management (ctypes OpenGL)
│   ├── gl_texture3d.py            # GL 3D texture upload (ctypes OpenGL)
│   ├── gl_compute.py              # GL compute shader dispatch (ctypes OpenGL)
│   └── sdf/                       # Signed Distance Field Engine
│       ├── sdf_field.py           # Abstract base SdfField
│       ├── sdf_composer.py        # Boolean composition tree (min/max/smooth blend)
│       ├── sdf_baker.py           # Dense grid SDF baking (to be replaced by octree)
│       ├── sdf_slicer.py          # 2D cross-section extraction (marching squares + DC)
│       ├── sdf_extrusion.py       # 2D profile → 3D extrusion field
│       ├── sdf_revolution.py      # 2D profile → 3D revolution field
│       ├── curve_sampler.py       # NURBS curve discretization & Bezier extraction
│       ├── glsl_compiler.py       # SDF tree → GLSL shader compiler
│       ├── marching_cubes/        # MC lookup tables
│       ├── sdf/                   # 3D SDF primitives
│       │   ├── box.py             # SdfBoxField
│       │   ├── sphere.py          # SdfSphereField
│       │   ├── cylinder.py        # SdfCylinderField
│       │   ├── torus.py           # SdfTorusField
│       │   ├── plane.py           # SdfPlaneField
│       │   └── noise.py           # SdfNoiseField (fBm modifier)
│       └── sdf2d/                 # 2D SDF primitives (for profiles)
│           ├── sdf2d_field.py     # Abstract base Sdf2dField
│           ├── bezier_curve.py    # Sdf2dBezierCurve (exact cubic Bezier SDF)
│           ├── polygon.py         # Sdf2dPolygon (sharp-corner polygon SDF)
│           ├── circle.py          # Sdf2dCircle
│           └── box.py             # Sdf2dBox
│
├── tools/                         # Interactive creation tools
│   ├── __init__.py
│   ├── dm_base.py                 # Base class for all interactive tools
│   ├── curve_tool.py              # BSpline curve drawing
│   ├── edit_tool.py               # Control point editing (NURBS + SDF)
│   ├── noise_tool.py              # Interactive noise modifier
│   ├── point_tool.py              # Point placement
│   ├── primitive_tool.py          # Parametric primitives (box, sphere, cylinder, torus)
│   ├── translate_tool.py          # Move/translate
│   └── work_plane_tool.py         # Workplane creation & scaling
│
├── commands/                      # FreeCADGui command definitions
│   ├── __init__.py
│   ├── cmd_boolean.py             # DM_Fuse / DM_Cut / DM_Common / DM_SmoothFuse / etc.
│   ├── cmd_curve.py               # DM_CreateCurve
│   ├── cmd_curve_sdf.py           # DM_CreateCurveSdf (curve → extrusion)
│   ├── cmd_edit.py                # DM_EditObject
│   ├── cmd_fill_curve.py          # DM_FillCurve
│   ├── cmd_install_deps.py        # Dependency installation script
│   ├── cmd_noise.py               # DM_CreateNoise
│   ├── cmd_point.py               # DM_CreatePoint
│   ├── cmd_primitive.py           # DM_CreateBox / Sphere / Cylinder / Torus
│   ├── cmd_sdf_export.py          # DM_SdfExport (SDF → mesh)
│   ├── cmd_sdf_slice.py           # DM_SdfSlice (SDF → 2D contours)
│   ├── cmd_settings.py            # DM_Settings dialog
│   ├── cmd_sketcher.py            # DM_OpenSketcher
│   ├── cmd_translate.py           # DM_Translate
│   └── cmd_workplane.py           # DM_WorkPlane
│
├── tests/                         # Ad-hoc test scripts
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
| Create Curve SDF | `DM_CreateCurveSdf` | — | ✅ |
| Create Box | `DM_CreateBox` | `B` | ✅ |
| Create Sphere | `DM_CreateSphere` | — | ✅ |
| Create Cylinder | `DM_CreateCylinder` | — | ✅ |
| Create Torus | `DM_CreateTorus` | — | ✅ |
| Create Noise | `DM_CreateNoise` | — | ✅ |

### Operations
| Command | ID | Hotkey | Status |
|---------|----|--------|--------|
| Field Union | `DM_Fuse` | — | ✅ |
| Field Cut | `DM_Cut` | — | ✅ |
| Field Intersect | `DM_Common` | — | ✅ |
| Smooth Union | `DM_SmoothFuse` | — | ✅ |
| Smooth Cut | `DM_SmoothCut` | — | ✅ |
| Smooth Intersect | `DM_SmoothCommon` | — | ✅ |
| Translate | `DM_Translate` | `T` | ✅ |
| SDF Export (Mesh) | `DM_SdfExport` | — | ✅ |
| SDF Slice | `DM_SdfSlice` | — | ✅ |

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
