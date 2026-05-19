# FreeCAD Direct Modeling Workbench

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-ea4aaa?style=flat&logo=github-sponsors)](https://github.com/sponsors/StevePeters-US)

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

The project structure is organized as follows:

- **`core/`**: Central Signed Distance Field (SDF) and NURBS geometry engine, including dual-pipeline rendering (GPU ray marcher & CPU octree cache) and isosurface extractors (meshing/slicing).
- **`tools/`**: Interactive creation and editing tools (e.g., curves, primitives, workplane placement).
- **`commands/`**: FreeCAD command bindings and toolbar/menu integrations.
- **`tests/`**: Ad-hoc scripts for mathematics, pipeline, and integration verification.
- **`Resources/`**: UI icons and visual assets.
- **`InitGui.py`**: FreeCAD workbench startup and registration logic.

---

## Features & Toolbar Commands

### Creation Tools
| Tool | Command ID | Hotkey | Description |
|---|---|---|---|
| **Place Point** | `DM_CreatePoint` | `P` | Places interactive control points |
| **Draw Curve** | `DM_CreateCurve` | `C` | Draws NURBS BSpline curves |
| **Fill Curve** | `DM_FillCurve` | — | Fills a closed curve to create a surface patch |
| **Extrude Curve** | `DM_ExtrudeCurve` | — | Extrudes a profile along an axis |
| **Create Box** | `DM_CreateBox` | `B` | Creates an interactive SDF box primitive |
| **Create Sphere** | `DM_CreateSphere` | — | Creates an interactive SDF sphere primitive |
| **Create Cylinder** | `DM_CreateCylinder` | — | Creates an interactive SDF cylinder primitive |
| **Create Torus** | `DM_CreateTorus` | — | Creates an interactive SDF torus primitive |
| **Create Noise** | `DM_CreateNoiseModifier` | — | Adds a 3D fractional Brownian motion (fBm) noise modifier |

### Operations & Transformations
| Tool | Command ID | Hotkey | Description |
|---|---|---|---|
| **Field Union** | `DM_Add` | — | Combine fields with sharp seams or parametric fillets |
| **Field Cut** | `DM_Subtract` | — | Subtract/cut a field from another |
| **Field Intersect** | `DM_Intersection` | — | Keep the intersection of two fields |
| **Translate** | `DM_Translate` | `T` | Translate/move geometry |
| **SDF Export (Mesh)** | `DM_SDFToShape` | — | Convert function-based SDF to a standard polygon mesh |
| **SDF Slice** | `DM_SDFSlice` | — | Slice the SDF to extract 2D contours |

### Utilities
- **Work Plane** (`DM_WorkPlane`): Custom workplane creation and scaling.
- **Open Sketcher** (`DM_OpenSketcher`): Integrate with FreeCAD's Sketcher.
- **DM Settings** (`DM_Settings`): Configure settings like ray marching step sizes and quality thresholds.

---

## Interactive Modeling Workflow

### Dynamic Workplane System
All geometry creation is driven by a custom, dynamic workplane that auto-orients to geometry under the cursor:
- **Hover over a face**: The workplane aligns normal to the face at the hit point (indicated by a green tint).
- **Hover over empty space**: The workplane falls back to the camera-facing viewport plane (indicated by a blue tint).

### Interaction Flow
1. **First Click**: Locks the workplane in place.
2. **Second Click**: Starts drawing/placing geometry using the active tool.
3. **Subsequent Clicks**: Continues placement/drawing of control points.
4. **Right Click / Enter**: Completes the tool's action.
5. **Esc**: Cancels the active tool.

### Editing Geometry
Activate the **Edit Tool** (`T` or via the toolbar) to modify existing curves and primitives:
- Interactive control handles (points/gizmos) will appear.
- Click to select a handle, move the mouse to transform, and click again to place.
- Movement is automatically projected and constrained to the current workplane or the view plane.

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
