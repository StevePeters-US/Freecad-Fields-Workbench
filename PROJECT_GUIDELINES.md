# FreeCAD Direct Modeling Workbench — Project Guidelines

## Project Description

FreeCAD Direct Modeling is a Python workbench for FreeCAD that provides fast, precise NURBS modeling. The core geometric representation is `Part.BSplineSurface` — not BRep shells or solids. Users draw NURBS curves in the viewport, extrude them into surfaces, and compose geometry through boolean-like operations. A conversion tool bridges between NURBS and BRep when needed for export or interop with standard FreeCAD tools.

The three fundamental types are **Point**, **Edge (BSplineCurve)**, and **Patch (BSplineSurface)**.

---

## Core Geometry Model

```
Point                          — FreeCAD.Vector + optional control handles
  │
  └─ Edge (BSplineCurve)       — NURBS curve through/from control points
       │
       └─ Patch (BSplineSurface) — NURBS surface from curves or control grids
```

### Design Principles

1. **`Part.BSplineSurface` is the native representation** — geometry lives as NURBS, not as BRep solids/shells.
2. **BRep is an export format** — a `NURBS → BRep` converter builds `Part.Shell`/`Part.Solid` when needed (STEP export, boolean ops). A `BRep → NURBS` converter goes the other direction for importing standard Parts.
3. **Curves first** — the primary workflow is: draw a curve → extrude it into a surface → compose surfaces. Primitives (box, sphere, etc.) can be added later as convenience wrappers.
4. **Everything builds on Point, Edge, Patch** — these three types are the atoms. Every higher-level tool composes them.

---

## Folder Structure

```
Freecad-Direct-Modeling/
├── InitGui.py                     # Workbench registration & toolbar/menu setup
├── PROJECT_GUIDELINES.md          # This file
├── TODO.md                        # Task breakdown (self-contained, LLM-friendly)
├── COMPLETED.md                   # Archive of completed tasks
├── README.md                      # Installation & quick-start
├── DirectModeling.FCStd           # Sample document
│
├── FCDirectModeling/              # Core Python package
│   ├── __init__.py                # Package init (exports dm_logger)
│   ├── dm_logger.py               # Centralized logging
│   ├── nurbs_primitives.py        # NURBS builders (curve, extrude surface)
│   ├── nurbs_geometry.py          # Core types: NurbsPoint, NurbsEdge, NurbsPatch [PLANNED]
│   ├── nurbs_brep_convert.py      # NURBS ↔ BRep conversion utilities [PLANNED]
│   ├── dm_object.py               # DMObjectProxy, DMViewProvider, factory
│   ├── dm_part.py                 # DM_Part FeaturePython wrapper (control point display)
│   ├── work_plane.py              # WorkPlaneManager — tangent plane detection & Coin3D grid
│   └── primitives/                # Interactive creators
│       ├── __init__.py            # Re-exports creator classes
│       ├── primitive_base.py      # PrimitiveBase & NURBSPrimitiveCreator
│       └── curve_creator.py       # CurveCreator (click-to-place BSpline points)
│
├── dm_commands/                   # FreeCADGui command definitions
│   ├── __init__.py
│   ├── command_create_curve.py    # DM_CreateCurve
│   ├── command_extrude.py         # DM_Extrude (curve → surface) [PLANNED]
│   ├── command_boolean.py         # DM_Fuse / DM_Cut / DM_Common
│   ├── command_array.py           # DM_Array / DM_PolarArray [PLANNED]
│   ├── command_instance.py        # DM_Instance / DM_Copy [PLANNED]
│   ├── command_convert.py         # DM_NurbsToBRep / DM_BRepToNurbs [PLANNED]
│   ├── command_dm_settings.py     # DM_Settings dialog
│   ├── command_open_sketcher.py   # DM_OpenSketcher
│   └── command_install_deps.py    # Dependency installer
│
└── Resources/
    ├── resources.qrc
    └── icons/                     # SVG icons for toolbar buttons
```

---

## Workbench Goals

1. **Principle**: `Part.BSplineSurface` is the native geometry — NOT BRep shells or solids. The three atoms are **DMPoint**, **DMCurve (BSplineCurve)**, and **DMPatch (BSplineSurface)**. BRep is only used for conversion/export. The primary workflow is: draw a curve → extrude into a surface → compose surfaces. This is the fundamental loop.
2. **Fast and precise** — radial menus and hotkeys for all operations. Minimal mouse travel.
3. **Tangent workplane** — automatically sits tangent to whatever surface the cursor is over.
4. **Instance and Copy system** — linked instances vs independent copies. Booleans operate on instances by default (toggleable), hiding the original.
5. **Real-time preview** — live NURBS curve/surface preview during interaction.
6. **NURBS ↔ BRep bridge** — convert to BRep for STEP/IGES export or interop; convert imported BRep back to NURBS for editing.

---

## Workbench Toolbar Layout

### Creation
| Command | ID | Hotkey | Description |
|---------|----|--------|-------------|
| Draw Curve | `DM_CreateCurve` | `D` | Click-to-place BSpline curve points |
| Extrude | `DM_Extrude` | `E` | Extrude a curve into a BSplineSurface |

### Operations
| Command | ID | Hotkey | Description |
|---------|----|--------|-------------|
| Fuse | `DM_Fuse` | `Ctrl+F` | Boolean union |
| Cut | `DM_Cut` | `Ctrl+X` | Boolean subtraction |
| Common | `DM_Common` | `Ctrl+I` | Boolean intersection |
| Array | `DM_Array` | — | Linear repetition |
| Polar Array | `DM_PolarArray` | — | Circular repetition |
| Instance | `DM_Instance` | `I` | Create linked instance |
| Copy | `DM_Copy` | `Ctrl+D` | Create independent copy |

### Conversion
| Command | ID | Description |
|---------|----|-------------|
| NURBS → BRep | `DM_NurbsToBRep` | Convert BSplineSurface patches to Part.Solid |
| BRep → NURBS | `DM_BRepToNurbs` | Convert Part.Shape faces to BSplineSurface patches |

### Settings
| Command | ID | Description |
|---------|----|-------------|
| DM Settings | `DM_Settings` | Opens the settings dialog |

### Radial Menu (Planned)
Coin3D-based radial menu activated by `Space`. Displays tools in a pie layout around the cursor.

---

## Workplane System

| Cursor Over | Workplane Orientation | Visual Color |
|-------------|----------------------|-------------|
| A face on existing geometry | Tangent to that face (normal = face normal at hit point) | Green tint |
| Empty space | XY plane at origin, Z+ normal | Blue tint |

Implementation: `FCDirectModeling/work_plane.py` — `WorkPlaneManager` class using Coin3D overlay.

---

## Instance and Copy System (Planned)

- **Instance** = `App::Link` pointing to original. Changes propagate. Own `Placement`.
- **Copy** = independent duplicate via `create_dm_object()` with same params. No linkage.
- **Boolean default**: creates instance of second operand, hides original (toggleable via DM Settings).

---

## DM Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| Show Wireframe | Checkbox | off | Overlay wireframe on NURBS objects |
| Preview Quality | Dropdown | `medium` | Tessellation density for viewport preview |
| Bool Use Instance | Checkbox | on | Create instance for boolean operands |

---

## NURBS Pipeline

```
Draw Curve → Part.BSplineCurve (interpolate through clicked points)
          → Extrude → Part.BSplineSurface
          → Part::FeaturePython .Shape
          → FreeCAD viewport (OpenCASCADE tessellation for display only)

Export: NURBS → BRep converter → Part.Shell/Part.Solid → STEP/IGES
Import: STEP/IGES → Part.Shape → BRep → NURBS converter → BSplineSurface patches
```

---

## Code Formatting & Style

1. **Python 3.8+** — FreeCAD 0.21+/1.0.
2. **PEP 8**: 4-space indent, 100-char soft limit, `snake_case` functions, `PascalCase` classes, `_` prefix for private helpers.
3. **Imports**: FreeCAD → PySide → project → stdlib.
4. **Docstrings**: Google-style. Every public class and function.
5. **FreeCAD Properties**: `App::Property*` for persistent data.

---

## Logging

Use `FCDirectModeling/dm_logger.py`. Never bare `print()`.

```python
from FCDirectModeling import dm_logger
dm_logger.debug("message")
dm_logger.info("message")
dm_logger.warn("message")
dm_logger.error("message")
```

| Mode | Console | File | Enable |
|------|---------|------|--------|
| Normal | ✅ | ❌ | Default |
| Crash | ✅ | ✅ | `export DEBUG_DM_CRASH=1` |

---

## Event Safety

**ALL document mutations** must be deferred via `QTimer.singleShot(0, fn)`. This includes assigning `.Shape`, creating/deleting objects, `doc.recompute()`, and closing dialogs.

---

## Terminology

| Term | Meaning |
|------|---------|
| Point | `FreeCAD.Vector` + optional control handles |
| Edge | `Part.BSplineCurve` through control points |
| Patch | `Part.BSplineSurface` — the core geometry atom |
| Place / Size / Set | 3-click creation flow (1st=origin, 2nd=footprint, 3rd=commit) |
| Instance | `App::Link` — linked duplicate |
| Copy | Independent duplicate |

### DM Object (Tree View)
```
🟧 Curve                        ← Part::FeaturePython + DMObjectProxy (orange icon)
 ├── ShapeType = "curve"          (App::PropertyString)
 └── Points = [...]               (App::PropertyVectorList)
```

---

## Dependencies

| Package | Required | Purpose |
|---------|----------|---------|
| NumPy | ✅ (bundled) | Linear algebra |
| FreeCAD 0.21+ / 1.0 | ✅ | Host application |