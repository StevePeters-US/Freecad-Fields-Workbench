# FreeCAD Direct Modeling Workbench — Project Guidelines

## Project Description

FreeCAD Direct Modeling is a Python workbench for FreeCAD that provides fast, intuitive, drag-and-drop 3D modeling using **NURBS (Non-Uniform Rational B-Splines)** as the core geometric representation. Instead of the traditional parametric tree workflow, users sketch and define shapes interactively in the 3D viewport. Primitives are created as native NURBS surfaces via FreeCAD's `Part.BSplineSurface` API — producing smooth, resolution-independent geometry suitable for STEP/IGES export with no faceting artifacts.

---

## Folder Structure

```
Freecad-Direct-Modeling/
├── InitGui.py                     # Workbench registration & toolbar/menu setup
├── PROJECT_GUIDELINES.md          # This file
├── TODO.md                        # Task breakdown by difficulty
├── COMPLETED.md                   # Archive of completed tasks
├── README.md                      # Installation & quick-start
├── DirectModeling.FCStd           # Sample document
│
├── FCDirectModeling/              # Core Python package
│   ├── __init__.py                # Package init (exports dm_logger)
│   ├── dm_logger.py               # Centralized logging
│   ├── nurbs_primitives.py        # NURBS primitive builders (box, sphere, cone, torus)
│   ├── nurbs_boolean.py           # Boolean operations on NURBS/BRep shapes
│   ├── dm_object.py               # DMObjectProxy, NURBS shape dispatch, factory
│   ├── mesh_features.py           # Adjacency, segmentation, face normals
│   ├── surface_fitting.py         # Primitive fitting (plane, sphere, cylinder, cone)
│   ├── dm_part.py                 # DM_Part FeaturePython wrapper
│   ├── task_panel.py              # Base task panel utilities
│   ├── curve_tools.py             # Freeform 3D curve drawing utilities
│   └── primitives/                # Interactive primitive creators
│       ├── __init__.py            # Re-exports all creator classes
│       ├── base.py                # PrimitiveCreatorBase & NURBSPrimitiveCreator
│       ├── box_creator.py         # BoxCreator (3-click Place → Size → Set)
│       ├── box_task_panel.py      # BoxTaskPanel (dimension inputs)
│       ├── sphere_creator.py      # SphereCreator
│       ├── cone_creator.py        # ConeCreator
│       └── torus_creator.py       # TorusCreator
│
├── dm_commands/                   # FreeCADGui command definitions
│   ├── __init__.py
│   ├── command_create_box.py      # DM_CreateBox
│   ├── command_create_primitives.py  # DM_CreateSphere / Cone / Torus
│   ├── command_boolean.py         # DM_Fuse / DM_Cut / DM_Common (renamed from SDF booleans)
│   ├── command_tweak.py           # DM_Tweak
│   ├── command_dm_settings.py     # DM_Settings dialog
│   ├── command_open_sketcher.py   # DM_OpenSketcher
│   ├── command_open_task_panel.py # DM_OpenTaskPanel
│   ├── command_draw_curve.py      # DM_DrawCurve (freeform 3D curve tool)
│   └── command_install_deps.py    # Dependency installer (shapely, etc.)
│
└── Resources/
    ├── resources.qrc
    └── icons/                     # SVG icons for toolbar buttons
```

---

## Workbench Goals

The Direct Modeling workbench aims to:

1. **Eliminate tree-management overhead** — create, combine, and edit 3D shapes directly without managing a feature tree.
2. **Use NURBS as the core representation** — all primitives are created as native `Part::Feature` B-spline surfaces via FreeCAD's OpenCASCADE kernel. This produces exact, smooth, resolution-independent geometry.
3. **Provide real-time 3D preview** — as the user drags to define a shape, a live wireframe or shaded preview updates in the viewport.
4. **Export-ready geometry** — NURBS shapes export directly to STEP/IGES without meshing artifacts or faceting.
5. **Boolean operations on BRep shapes** — Fuse, Cut, and Common operate on `Part.Shape` objects via OpenCASCADE boolean solvers.
6. **Freeform curve drawing** — users can draw 3D B-spline curves directly in the viewport for lofting, sweeping, and boundary patches.
7. **Integrate with existing FreeCAD tools** — sketcher profiles, constraints, and standard Part operations remain accessible.

---

## Workbench Toolbar Layout

The toolbar and menu are registered in `InitGui.py` and contain the following groups:

### Creation (Primitives)
| Command | ID | Description |
|---------|----|-------------|
| Box | `DM_CreateBox` | Interactive 3-click box creation (Place → Size → Set) |
| Sphere | `DM_CreateSphere` | Click-drag sphere creation |
| Cone | `DM_CreateCone` | Click-drag cone creation |
| Torus | `DM_CreateTorus` | Click-drag torus creation |
| New Sketch | `DM_OpenSketcher` | Launch FreeCAD Sketcher for profile creation |
| Draw Curve | `DM_DrawCurve` | Freeform 3D B-spline curve drawing |

### Operations
| Command | ID | Description |
|---------|----|-------------|
| Fuse | `DM_Fuse` | Boolean union of two NURBS/BRep shapes |
| Cut | `DM_Cut` | Boolean subtraction |
| Common | `DM_Common` | Boolean intersection |
| Tweak | `DM_Tweak` | Direct vertex/face manipulation |

### Future Operations (Not Yet Implemented)
| Planned Tool | Description |
|--------------|-------------|
| Array | Repeat a shape along a vector or pattern |
| Transform | Move / rotate / scale a shape |
| Loft | Create a surface from cross-section curves |
| Sweep | Sweep a profile along a curve |

### Settings
| Command | ID | Description |
|---------|----|-------------|
| DM Settings | `DM_Settings` | Opens the settings dialog |

---

## DM Settings (User-Facing)

Accessed via the **DM Settings** toolbar button (`DM_Settings` command). Configured in `dm_commands/command_dm_settings.py`. Settings are persisted in `FreeCAD.ParamGet("User parameter:FCDirectModeling")`.

| Setting | Type | Range/Options | Default | Description |
|---------|------|---------------|---------|-------------|
| Show Wireframe | Checkbox | on/off | off | Overlay wireframe on NURBS objects |
| Preview Quality | Dropdown | `low`, `medium`, `high` | `medium` | Tessellation density for viewport preview |

---

## NURBS Rendering Pipeline

```
User Drag → NURBS surface builder (box/sphere/cone/torus)
         → Part.BSplineSurface / Part.makeBox / Part.makeSphere / etc.
         → Part::Feature Shape
         → FreeCAD native rendering (OpenCASCADE tessellation)
```

### Key Files
- **`nurbs_primitives.py`** — NURBS surface builders for each primitive type.
- **`dm_object.py`** — `DMObjectProxy`, factory, and shape dispatch.
- **`primitives/base.py`** — `NURBSPrimitiveCreator` drives live preview updates.

### Preview Strategy
During interactive creation (drag), a lightweight wireframe or low-tessellation preview is shown. On commit (3rd click / Set), the final `Part::Feature` with full NURBS geometry is created.

---

## Code Formatting & Style (FreeCAD Standards)

1. **Python 3.8+** — target the Python bundled with FreeCAD 0.21+/1.0.
2. **PEP 8** with the following project conventions:
   - 4-space indentation, no tabs.
   - Max line length: 100 characters (soft limit).
   - Use `snake_case` for functions and variables, `PascalCase` for classes.
   - Private helpers prefixed with `_` (e.g., `_build_nurbs_box`, `_process_preview_queue`).
3. **Imports**:
   - FreeCAD modules first (`import FreeCAD`, `import FreeCADGui`).
   - Then PySide (`from PySide import QtCore, QtGui`).
   - Then project imports (`from FCDirectModeling import dm_logger`).
   - Then standard library (`import os`, `import numpy as np`).
4. **Docstrings**: Google-style or NumPy-style. Every public class and function must have a docstring.
5. **Type hints**: Encouraged but not required on all functions (FreeCAD's own API is untyped).
6. **FreeCAD Properties**: Use `App::Property*` types (e.g., `App::PropertyFloat`, `App::PropertyString`) for persistent data on FeaturePython objects. Access via `obj.PropertyName`.

---

## Logging

All logging goes through **`FCDirectModeling/dm_logger.py`**. Never use bare `print()` or `FreeCAD.Console.Print*` directly in new code.

```python
from FCDirectModeling import dm_logger

dm_logger.debug("message")   # Verbose tracing
dm_logger.info("message")    # Normal operational info
dm_logger.warn("message")    # Potential issues
dm_logger.error("message")   # Errors and failures
```

### Logging Policies
| Mode | Console | File (`~/dm_debug.log`) | How to enable |
|------|---------|--------------------------|---------------|
| Normal | ✅ | ❌ | Default |
| Crash investigation | ✅ | ✅ | `export DEBUG_DM_CRASH=1` before launching FreeCAD |

### Logging Best Practices
- **Be concise.** One-line messages with key variable values.
- **No per-frame spam.** Avoid logging on every `mouseMoveEvent` once logic is verified — use once-per-state-change or gate behind a flag.
- **Tag log lines** with the module or function name for easy grep: `dm_logger.debug("BoxCreator._on_move: w=%.1f h=%.1f" % (w, h))`.

---

## Event Safety

**ALL scene-graph and document-mutating operations** must be deferred via `QTimer.singleShot(0, fn)`. Never mutate the FreeCAD document from inside a Coin3D event callback. This includes:
- Assigning `.Shape`
- Creating / deleting document objects
- Calling `doc.recompute()`
- Closing dialogs (`FreeCADGui.Control.closeDialog()`)

---

## Terminology

### Primitive Creation — 3-Click Flow
| Click | Term | Description |
|-------|------|-------------|
| 1st | **Place** | Set the origin corner on the working plane |
| 2nd | **Size** | Lock the base footprint; dragging now controls height |
| 3rd | **Set** | Commit the shape to the document |

### DM Part Object (Tree View)

Every Direct Modeling part appears as a single `Part::FeaturePython` with the orange icon. No child mesh is needed — the NURBS shape renders natively via FreeCAD's OpenCASCADE tessellation.

```
🟧 Box                          ← Part::FeaturePython + DMObjectProxy (orange icon)
```

During **preview** (dragging), a temporary lightweight shape is shown in the viewport.

On **finalize** (3rd click), the full NURBS `Part::Feature` is committed:

```
🟧 Box                          ← Part::FeaturePython + DMObjectProxy
 ├── ShapeType = "box"            (App::PropertyString)
 ├── Length = 10.0                (App::PropertyFloat)
 ├── Width = 10.0                 (App::PropertyFloat)
 └── Height = 10.0                (App::PropertyFloat)
```

**Key invariant**: The parametric properties (Length, Width, Height, Radius, etc.) are the source of truth. The `Part.Shape` is regenerated from `build_shape()` whenever properties change.

---

## Dependencies

| Package | Required | Purpose |
|---------|----------|---------|
| NumPy | ✅ (bundled with FreeCAD) | Linear algebra, point operations |
| Shapely | ✅ (user-installed) | 2D geometry operations |
| FreeCAD 0.21+ / 1.0 | ✅ | Host application (OpenCASCADE NURBS kernel) |

---

## Future Work

### Curve-Based Modeling
- **Freeform 3D Curves** — interactive B-spline curve drawing in the viewport.
- **Loft** — create NURBS surfaces from cross-section curves.
- **Sweep** — sweep a profile along a guide curve.
- **Boundary Patch** — fill a closed boundary of curves with a NURBS surface.

### Advanced Operations
- **Array** — repeat shapes along vectors, circular patterns, or grids.
- **Transform** — SDF-level translate/rotate/scale (now BRep-level transforms).
- **Sketch-driven extrusion** — convert Sketcher profiles to extruded NURBS solids.

### Import/Export
- **Mesh to NURBS** — fit NURBS patches to imported meshes (STL/OBJ) for clean CAD geometry.
- **Blender Live Link** — import curves from Blender Geometry Nodes as NURBS for reconstruction.

### Higher-Quality Rendering
- Ambient occlusion and curvature shading in the viewport.