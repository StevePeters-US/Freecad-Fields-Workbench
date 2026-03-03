# FreeCAD Direct Modeling Workbench

A Python workbench for FreeCAD that provides fast, interactive direct modeling using BRep geometry. Users draw points and curves on a dynamic workplane, create solid primitives, and combine them with boolean operations — all without leaving the 3D viewport.

---

## Architecture

```
Workplane (tangent to surface or camera-aligned)
    ↓
Point / Curve tools (draw geometry on the workplane)
    ↓
BRep Primitives (box, sphere, cylinder, extrude — placed on workplane)
    ↓
Boolean Operations (fuse, cut, intersect)
    ↓
Part::FeaturePython → FreeCAD viewport (OCCT display)
```

**Core principle**: The workplane is the foundation for all creation. Click a surface to place the workplane tangent to it, then use tools to create geometry relative to that plane.

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
│   └── work_plane.py              # WorkPlaneManager — Coin3D grid & snapping
│
├── tools/                         # Interactive creation tools
│   ├── __init__.py
│   ├── primitive_base.py          # Base class for interactive tools
│   ├── point_tool.py              # Point placement
│   ├── curve_tool.py              # BSpline curve drawing
│   └── translate_tool.py          # Move/translate
│
├── commands/                      # FreeCADGui command definitions
│   ├── __init__.py
│   ├── cmd_point.py               # DM_CreatePoint
│   ├── cmd_curve.py               # DM_CreateCurve
│   ├── cmd_boolean.py             # DM_Fuse / DM_Cut / DM_Common
│   ├── cmd_workplane.py           # DM_WorkPlane
│   ├── cmd_sketcher.py            # DM_OpenSketcher
│   ├── cmd_settings.py            # DM_Settings dialog
│   └── cmd_translate.py           # DM_Translate
│
└── resources/
    ├── resources.qrc
    └── icons/                     # SVG icons for toolbar buttons
```

> **Note**: The folder structure above is the target layout. The codebase is currently organized as `FCDirectModeling/` + `dm_commands/` and will be reorganized incrementally.

---

## Toolbar

### Creation
| Command | ID | Hotkey | Status |
|---------|----|--------|--------|
| Place Point | `DM_CreatePoint` | `P` | ✅ |
| Draw Curve | `DM_CreateCurve` | `D` | ✅ |
| Fill Curve | `DM_FillCurve` | — | ✅ |
| Create Box | `DM_CreateBox` | `B` | Planned |
| Create Sphere | `DM_CreateSphere` | — | Planned |
| Create Cylinder | `DM_CreateCylinder` | — | Planned |
| Extrude | `DM_Extrude` | `E` | Planned |

### Operations
| Command | ID | Hotkey | Status |
|---------|----|--------|--------|
| Fuse | `DM_Fuse` | `Ctrl+F` | ✅ |
| Cut | `DM_Cut` | `Ctrl+X` | ✅ |
| Common | `DM_Common` | `Ctrl+I` | ✅ |
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
2. **2nd click** → Starts the active tool (or selects an element in Edit Tool)
3. **Subsequent clicks** → Continues tool use (or drops the element in Edit Tool)
4. **Right click / Enter** → Finishes the tool
5. **Esc** → Cancels the tool

### Edit Tool (BSpline Manipulation)
To modify an existing curve:
1. Select the curve and activate the **Edit Tool** (`T` or via toolbar).
2. **Control Points** (orange) and **Handles** (blue) will become visible.
3. **Pick and Drop**: Click an element once to select it, move the mouse, and click again to drop it at the new location.
4. Movement is automatically constrained to the curve's plane or the current view plane.

---

## Code Conventions

### Style
- **Python 3.8+** (FreeCAD 0.21+ / 1.0)
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

Set `DEBUG_DM_CRASH=1` to also write logs to `~/.FreeCAD/DirectModeling.log` for crash debugging.

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
| FreeCAD 0.21+ / 1.0 | ✅ | Host application |
| NumPy | ✅ (bundled) | Linear algebra |
| Shapely | ✅ | Geometric operations |
