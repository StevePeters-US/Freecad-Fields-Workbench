# FreeCAD Direct Modeling Workbench — Project Guidelines

## Project Description

FreeCAD Direct Modeling is a Python workbench for FreeCAD that provides fast, intuitive, drag-and-drop 3D modeling using **Signed Distance Fields (SDFs)**. Instead of the traditional parametric tree workflow, users sketch and define shapes interactively in the 3D viewport. Meshes are generated on-the-fly from SDF distance functions using Surface Nets, Dual Contouring, or Marching Cubes — all implemented in pure NumPy with no native/C++ dependencies beyond FreeCAD itself.

---

## Folder Structure

```
Freecad-Direct-Modeling/
├── InitGui.py                     # Workbench registration & toolbar/menu setup
├── PROJECT_GUIDELINES.md          # This file
├── TODO.md                        # Task breakdown by difficulty
├── README.md                      # Installation & quick-start
├── DirectModeling.FCStd           # Sample document
│
├── FCDirectModeling/              # Core Python package
│   ├── __init__.py                # Package init (exports sdf_logger)
│   ├── sdf_logger.py              # Centralized logging
│   ├── sdf_mesher.py              # Meshing algorithms (Surface Nets / QEF)
│   ├── sdf_object.py              # SDFObjectProxy, SDF functions, mesh dispatch
│   ├── sdf_utils.py               # SDFObjectFactory, common properties
│   ├── mesh_features.py           # Adjacency, segmentation, face normals
│   ├── surface_fitting.py         # Primitive fitting (plane, sphere, cylinder, cone)
│   ├── dm_part.py                 # DM_Part FeaturePython wrapper
│   ├── task_panel.py              # Base task panel utilities
│   └── primitives/                # Interactive primitive creators
│       ├── __init__.py            # Re-exports all creator classes
│       ├── base.py                # PrimitiveCreatorBase & SDFMeshPrimitiveCreator
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
│   ├── command_draw_box.py        # DM_DrawBox (legacy)
│   ├── command_boolean.py         # DM_Fuse / DM_Cut / DM_Common
│   ├── command_tweak.py           # DM_Tweak
│   ├── command_dm_settings.py     # DM_Settings dialog
│   ├── command_open_sketcher.py   # DM_OpenSketcher
│   ├── command_open_task_panel.py # DM_OpenTaskPanel
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
2. **Use SDF as the core representation** — all primitives and booleans operate on distance fields, enabling smooth blending, fast preview, and easy boolean composition.
3. **Provide real-time 3D preview** — as the user drags to define a shape, a live mesh preview updates in the viewport.
4. **Convert SDF to BRep only when needed** — keep geometry in SDF/mesh form for speed; export to BRep (STEP/BREP) on demand.
5. **Integrate with existing FreeCAD tools** — sketcher profiles, constraints, and standard Part operations remain accessible.

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

### Future Creation Tools (Not Yet Implemented)
| Planned Tool | Description |
|--------------|-------------|
| BMesh to SDF | Convert mesh/bmesh data into an SDF representation |
| SDF to Curves | Extract feature curves from an SDF zero-surface |
| SDF to Mesh | Export the current SDF to a final high-res mesh |

### Operations
| Command | ID | Description |
|---------|----|-------------|
| Fuse | `DM_Fuse` | Boolean union of two SDF objects |
| Cut | `DM_Cut` | Boolean subtraction |
| Common | `DM_Common` | Boolean intersection |
| Array | *(planned)* | Repeat a shape along a vector or pattern |
| Transform | *(planned)* | Move / rotate / scale an SDF object |
| Tweak | `DM_Tweak` | Direct vertex/face manipulation |

### Settings
| Command | ID | Description |
|---------|----|-------------|
| DM Settings | `DM_Settings` | Opens the settings dialog (algorithm, resolution, wireframe) |

---

## DM Settings (User-Facing)

Accessed via the **DM Settings** toolbar button (`DM_Settings` command). Configured in `dm_commands/command_dm_settings.py`. Settings are persisted in `FreeCAD.ParamGet("User parameter:FCDirectModeling")`.

| Setting | Type | Range/Options | Default | Description |
|---------|------|---------------|---------|-------------|
| Meshing Algorithm | Dropdown | `surface_nets`, `dual_contouring`, `marching_cubes` | `surface_nets` | Which algorithm meshes the SDF |
| Resolution | SpinBox | 8 – 128 (step 4) | 48 | Voxel grid resolution (higher = more detail, slower) |
| Show Wireframe | Checkbox | on/off | off | Overlay triangle wireframe on SDF objects |

### Preview vs Final Resolution
- **Preview** (during drag): resolution ~15–20 for real-time feedback.
- **Final** (on Set): resolution from the DM Settings value (default 48).

---

## SDF Rendering Pipeline

```
User Drag → SDF function (box/sphere/cone/torus)
         → Voxel grid evaluation (NumPy)
         → Meshing algorithm (Surface Nets / Dual Contouring / Marching Cubes)
         → Vertex + Triangle arrays
         → Mesh.Mesh() facet list
         → Assign to preview Mesh::Feature or final Mesh::FeaturePython
```

### Key Files
- **`sdf_object.py`** — SDF distance functions (`_sdf_box`, `_sdf_sphere`, etc.), `mesh_sdf()` dispatcher, `SDFObjectProxy`.
- **`sdf_mesher.py`** — `extract_mesh_numpy()` (Surface Nets + QEF), `compute_normals_from_sdf()`.
- **`primitives/base.py`** — `SDFMeshPrimitiveCreator.update_sdf_preview()` and `_process_preview_queue()` drive live mesh updates.

### Marching Cubes
Currently **not implemented** inside `sdf_mesher.py`. The `mesh_sdf()` dispatcher in `sdf_object.py` has a branch for `"marching_cubes"` but it falls through to Surface Nets. Implementing Marching Cubes is an active TODO.

---

## Code Formatting & Style (FreeCAD Standards)

1. **Python 3.8+** — target the Python bundled with FreeCAD 0.21+/1.0.
2. **PEP 8** with the following project conventions:
   - 4-space indentation, no tabs.
   - Max line length: 100 characters (soft limit).
   - Use `snake_case` for functions and variables, `PascalCase` for classes.
   - Private helpers prefixed with `_` (e.g., `_sdf_box`, `_process_preview_queue`).
3. **Imports**:
   - FreeCAD modules first (`import FreeCAD`, `import FreeCADGui`).
   - Then PySide (`from PySide import QtCore, QtGui`).
   - Then project imports (`from FCDirectModeling import sdf_logger`).
   - Then standard library (`import os`, `import numpy as np`).
4. **Docstrings**: Google-style or NumPy-style. Every public class and function must have a docstring.
5. **Type hints**: Encouraged but not required on all functions (FreeCAD's own API is untyped).
6. **FreeCAD Properties**: Use `App::Property*` types (e.g., `App::PropertyFloat`, `App::PropertyString`) for persistent data on FeaturePython objects. Access via `obj.PropertyName`.

---

## Logging

All logging goes through **`FCDirectModeling/sdf_logger.py`**. Never use bare `print()` or `FreeCAD.Console.Print*` directly in new code.

```python
from FCDirectModeling import sdf_logger

sdf_logger.debug("message")   # Verbose tracing
sdf_logger.info("message")    # Normal operational info
sdf_logger.warn("message")    # Potential issues
sdf_logger.error("message")   # Errors and failures
```

### Logging Policies
| Mode | Console | File (`~/sdf_debug.log`) | How to enable |
|------|---------|--------------------------|---------------|
| Normal | ✅ | ❌ | Default |
| Crash investigation | ✅ | ✅ | `export DEBUG_SDF_CRASH=1` before launching FreeCAD |

### Logging Best Practices
- **Be concise.** One-line messages with key variable values.
- **No per-frame spam.** Avoid logging on every `mouseMoveEvent` once logic is verified — use once-per-state-change or gate behind a flag.
- **Tag log lines** with the module or function name for easy grep: `sdf_logger.debug("BoxCreator._on_move: w=%.1f h=%.1f" % (w, h))`.

---

## Event Safety

**ALL scene-graph and document-mutating operations** must be deferred via `QTimer.singleShot(0, fn)`. Never mutate the FreeCAD document from inside a Coin3D event callback. This includes:
- Assigning `.Mesh`
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

### Preview vs Final
- **Preview**: Temporary `Mesh::Feature` (no Proxy). Low-res, replaced in-place each frame.
- **Final**: Persistent `Mesh::FeaturePython` with `SDFObjectProxy`. High-res, triggers `doc.recompute()`.

---

## Dependencies

| Package | Required | Purpose |
|---------|----------|---------|
| NumPy | ✅ (bundled with FreeCAD) | SDF evaluation, meshing, linear algebra |
| Shapely | ✅ (user-installed) | 2D geometry operations |
| FreeCAD 0.21+ / 1.0 | ✅ | Host application |

---

## Future Work

### Curve Extraction from SDF
- **2D** — Adaptive contouring: fit Bézier/Catmull-Rom segments to zero-crossings.
- **3D** — Implicit surface → spline: Hermite data at crossings → B-spline / T-spline fit.
- **3D** — Level-set → CSG: approximate SDF by primitive hierarchy.
- **3D** — Direct analytic: for known-form SDFs, derive exact NURBS / analytic patches.

### Higher-Quality Rendering
- Ray-marched preview for smooth SDF surfaces before meshing.
- Ambient occlusion and curvature shading in the viewport.