# FreeCAD Direct Modeling — TODO

## How to Write a Task

Each task must be **self-contained** so an LLM or developer can complete it with
no prior context beyond the files listed. Follow this template:

```markdown
- [ ] **Task Title** (Complexity: N/10)
  - **Goal**: One sentence describing the desired outcome.
  - **Files to read**: List every file the implementer must read first.
  - **Files to modify/create**: List files that will change.
  - **Steps**:
    1. First concrete step…
    2. Second step…
  - **Acceptance**: How to verify the task is done.
```

- Mark in-progress tasks `[/]`, completed tasks `[x]`.
- Move completed tasks to `COMPLETED.md` when a milestone is reached.
- Sort by complexity within each section (lowest first).

---

## Architecture Overview

> **Principle**: `Part.BSplineSurface` is the native geometry — NOT BRep shells or solids. The three atoms are **Point**, **Edge (BSplineCurve)**, and **Patch (BSplineSurface)**. BRep is only used for conversion/export. The primary workflow is: draw a curve → extrude into a surface → compose.

---

## Phase 0: Strip Legacy Code

These tasks remove code that no longer fits the pure-NURBS architecture.

---

### 0a. Delete primitive creators and commands (Complexity: 2/10)

- **Goal**: Remove all primitive-specific creators (box, sphere, cone, torus) and their commands. Only `CurveCreator` and `command_create_curve.py` survive.
- **Files to read**:
  - `FCDirectModeling/primitives/__init__.py` — see what's exported.
  - `InitGui.py` — see which commands are registered.
- **Files to delete**:
  - `FCDirectModeling/primitives/box_creator.py`
  - `FCDirectModeling/primitives/box_task_panel.py`
  - `FCDirectModeling/primitives/sphere_creator.py`
  - `FCDirectModeling/primitives/cone_creator.py`
  - `FCDirectModeling/primitives/torus_creator.py`
  - `dm_commands/command_create_box.py`
  - `dm_commands/command_create_primitives.py`
- **Files to modify**:
  - `FCDirectModeling/primitives/__init__.py` — remove all imports except `PrimitiveBase`, `NURBSPrimitiveCreator`, `CurveCreator`.
  - `InitGui.py` — remove imports of `command_create_box`, `command_create_primitives`. Remove `DM_CreateBox`, `DM_CreateSphere`, `DM_CreateCone`, `DM_CreateTorus` from toolbar and menu lists.
- **Steps**:
  1. Delete the 7 files listed above.
  2. Edit `FCDirectModeling/primitives/__init__.py`:
     ```python
     from .primitive_base import PrimitiveBase, NURBSPrimitiveCreator
     from .curve_creator import CurveCreator

     __all__ = ["PrimitiveBase", "NURBSPrimitiveCreator", "CurveCreator"]
     ```
  3. Edit `InitGui.py`:
     - Remove the lines `from dm_commands import command_create_box` and `from dm_commands import command_create_primitives`.
     - Remove `'DM_CreateBox'`, `'DM_CreateSphere'`, `'DM_CreateCone'`, `'DM_CreateTorus'` from both `self.appendToolbar(...)` and `self.appendMenu(...)` lists.
  4. Verify no other files import the deleted modules. Search for `box_creator`, `sphere_creator`, `cone_creator`, `torus_creator`, `command_create_box`, `command_create_primitives` across the whole project.
- **Acceptance**: FreeCAD loads the workbench without errors. Only `DM_CreateCurve` appears in the Creation section of the toolbar. No import errors in the FreeCAD console.

---

### 0b. Delete command_tweak (Complexity: 1/10)

- **Goal**: Remove the Tweak command entirely.
- **Files to delete**:
  - `dm_commands/command_tweak.py`
- **Files to modify**:
  - `InitGui.py` — remove `from dm_commands import command_tweak` and `'DM_Tweak'` from toolbar/menu lists.
- **Steps**:
  1. Delete `dm_commands/command_tweak.py`.
  2. In `InitGui.py`, remove the tweak import line and `'DM_Tweak'` from both `self.appendToolbar(...)` and `self.appendMenu(...)`.
- **Acceptance**: Workbench loads without errors or tweak references.

---

### 0c. Delete mesh_features.py and surface_fitting.py (Complexity: 1/10)

- **Goal**: Remove all meshing and surface fitting code.
- **Files to delete**:
  - `FCDirectModeling/mesh_features.py`
  - `FCDirectModeling/surface_fitting.py`
- **Steps**:
  1. Delete both files.
  2. Search the entire project for `mesh_features` and `surface_fitting` imports. Remove any found.
  3. Delete the test file `FCDirectModeling/tests/test_nurbs_primitives.py` (tests box/sphere/cone/torus builders we're removing).
- **Acceptance**: No files import `mesh_features` or `surface_fitting`. No test files reference deleted modules.

---

### 0d. Strip primitive shape types from dm_object.py (Complexity: 3/10)

- **Goal**: Remove box, sphere, cone, and torus shape types from `DMObjectProxy`. Only `curve` (and later `surface`) shape types should remain.
- **Files to read**:
  - `FCDirectModeling/dm_object.py` — the full file, especially `DMObjectProxy.__init__()` (lines 42-84) and `build_shape()` (lines 86-103).
- **Files to modify**:
  - `FCDirectModeling/dm_object.py`
- **Steps**:
  1. In `DMObjectProxy.__init__()`, delete the `if shape_type == "box":`, `elif shape_type == "sphere":`, `elif shape_type == "cone":`, and `elif shape_type == "torus":` blocks (lines 58-79). Keep only the `elif shape_type == "curve":` block.
  2. In `build_shape()`, delete the `if st == "box":`, `elif st == "sphere":`, `elif st == "cone":`, and `elif st == "torus":` branches (lines 91-98). Keep only `elif st == "curve":`.
  3. Add a comment: `# Future: "surface" shape type for BSplineSurface patches`.
- **Acceptance**: `create_dm_object("Curve", "curve", {"points": [...]})` still works. Passing `shape_type="box"` returns an empty shape (not an error).

---

### 0e. Strip primitive builders from nurbs_primitives.py (Complexity: 2/10)

- **Goal**: Remove `build_box()`, `build_sphere()`, `build_cone()`, `build_torus()` from `nurbs_primitives.py`. Keep only `build_curve()`.
- **Files to modify**:
  - `FCDirectModeling/nurbs_primitives.py` — delete lines 12-66 (the four builder functions).
- **Steps**:
  1. Delete the `build_box()`, `build_sphere()`, `build_cone()`, `build_torus()` functions.
  2. Keep `build_curve()` (lines 68-111) intact.
  3. Update the module docstring to say "NURBS curve builders" instead of "NURBS primitive builders".
- **Acceptance**: `nurbs_primitives.build_curve(points)` still works. No functions named `build_box`, `build_sphere`, `build_cone`, `build_torus` exist.

---

### 0f. Clean up primitive_base.py (Complexity: 3/10)

- **Goal**: Remove dead code from `primitive_base.py` — specifically the `_update_preview_object` branches for box/sphere/cone/torus shape types in `NURBSPrimitiveCreator`.
- **Files to read**:
  - `FCDirectModeling/primitives/primitive_base.py` — especially `_update_preview_object()` (lines 480-570), which has `if shape_type == "box":`, `elif shape_type == "sphere":`, etc.
- **Files to modify**:
  - `FCDirectModeling/primitives/primitive_base.py`
- **Steps**:
  1. In `_update_preview_object()`, replace the shape type dispatch (lines 494-505) with only the curve branch:
     ```python
     if shape_type == "curve":
         shape = nurbs_primitives.build_curve(params.get("points", []))
     else:
         shape = Part.Shape()
     ```
  2. Remove imports or references to deleted builder functions if any exist.
  3. The base classes `PrimitiveBase` and `NURBSPrimitiveCreator` remain — they provide the event loop, work plane integration, and preview system that `CurveCreator` uses.
- **Acceptance**: Drawing a curve with `CurveCreator` still works (click points → preview curve → Enter to finalize). No errors in console.

---

## Phase 1: Core NURBS Geometry + Extrude

These tasks build the curve→surface workflow.

---

### 1a. Create NurbsPoint class (Complexity: 2/10)

- **Goal**: Define a `NurbsPoint` class wrapping a `FreeCAD.Vector` with optional control handles for smooth/sharp corners.
- **Files to create**:
  - `FCDirectModeling/nurbs_geometry.py`
- **Steps**:
  1. Create `FCDirectModeling/nurbs_geometry.py`.
  2. Define:
     ```python
     import FreeCAD

     class NurbsPoint:
         """3D point with optional control handles for NURBS curves."""
         def __init__(self, position, handle_in=None, handle_out=None, weight=1.0):
             self.position = FreeCAD.Vector(position)
             self.handle_in = handle_in     # FreeCAD.Vector or None
             self.handle_out = handle_out   # FreeCAD.Vector or None
             self.weight = weight

         def to_vector(self):
             return FreeCAD.Vector(self.position)

         def is_sharp(self):
             return self.handle_in is None and self.handle_out is None

         def __repr__(self):
             return f"NurbsPoint({self.position.x:.2f}, {self.position.y:.2f}, {self.position.z:.2f})"
     ```
- **Acceptance**: `NurbsPoint(FreeCAD.Vector(1,2,3)).to_vector()` returns `Vector(1,2,3)`. `.is_sharp()` returns `True` when no handles set.

---

### 1b. Create NurbsEdge class (Complexity: 3/10)

- **Goal**: A `NurbsEdge` class that builds a `Part.BSplineCurve` from `NurbsPoint` objects.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — the `NurbsPoint` class (task 1a).
  - FreeCAD `Part.BSplineCurve` docs — `buildFromPolesMultsKnots()`, `interpolate()`.
- **Files to modify**:
  - `FCDirectModeling/nurbs_geometry.py` — add `NurbsEdge`.
- **Steps**:
  1. Add class `NurbsEdge`:
     ```python
     import Part

     class NurbsEdge:
         """NURBS curve from a sequence of NurbsPoint objects."""
         def __init__(self, points, degree=3):
             self.points = list(points)  # List[NurbsPoint]
             self.degree = degree
     ```
  2. Method `to_bspline_curve() -> Part.BSplineCurve`:
     - If all points `.is_sharp()`: use `Part.BSplineCurve()` + `interpolate()` with the position vectors. For degree-1 (straight segments), use `buildFromPolesMultsKnots` with `degree=1`.
     - If handles exist: build poles array = `[p.handle_in, p.position, p.handle_out, ...]` and use `buildFromPolesMultsKnots` with appropriate multiplicity.
  3. Method `to_shape() -> Part.Shape`: calls `self.to_bspline_curve().toShape()`.
  4. Property `is_closed`: returns `True` if first and last positions within 0.001 distance.
- **Acceptance**: `NurbsEdge([NurbsPoint(V(0,0,0)), NurbsPoint(V(10,0,0))]).to_shape()` returns a valid `Part.Edge`.

---

### 1c. Create NurbsPatch class (Complexity: 4/10)

- **Goal**: A `NurbsPatch` class that builds a `Part.BSplineSurface` from a control point grid.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — `NurbsPoint`, `NurbsEdge` (tasks 1a, 1b).
  - FreeCAD `Part.BSplineSurface` docs — `buildFromPolesMultsKnots()`.
- **Files to modify**:
  - `FCDirectModeling/nurbs_geometry.py` — add `NurbsPatch`.
- **Steps**:
  1. Add class `NurbsPatch`:
     ```python
     class NurbsPatch:
         """NURBS surface from a control point grid."""
         def __init__(self, control_grid, u_degree=1, v_degree=1):
             self.control_grid = control_grid  # List[List[NurbsPoint]] — rows x cols
             self.u_degree = u_degree
             self.v_degree = v_degree
     ```
  2. Class method `from_corners(p1, p2, p3, p4)` — flat degree-1 surface from 4 corners. Grid = `[[p1,p2],[p4,p3]]`.
  3. Method `to_bspline_surface() -> Part.BSplineSurface`:
     - Extract poles: 2D list of `FreeCAD.Vector` from `self.control_grid[row][col].position`.
     - Extract weights: 2D list of floats from `self.control_grid[row][col].weight`.
     - Compute knots/mults for the given degree (uniform clamped: knots `[0, 1]`, mults `[degree+1, degree+1]` for each direction, adjusted for grid size).
     - Call `bs = Part.BSplineSurface()` then `bs.buildFromPolesMultsKnots(poles, umults, vmults, uknots, vknots, False, False, udeg, vdeg, weights)`.
     - Return `bs`.
  4. Method `to_face() -> Part.Face`: `Part.Face(self.to_bspline_surface().toShape())`. This creates a `Part.Face` for display but the **canonical representation is the BSplineSurface itself**, not the face.
- **Acceptance**: `NurbsPatch.from_corners(p1,p2,p3,p4).to_bspline_surface()` returns a valid `Part.BSplineSurface`. `.to_face()` returns a displayable `Part.Face`.

---

### 1d. Implement curve extrusion to BSplineSurface (Complexity: 5/10)

- **Goal**: Extrude a `Part.BSplineCurve` (edge) along a direction vector to produce a `Part.BSplineSurface`. This is the core curve→surface operation. The result is a BSplineSurface, NOT a BRep solid.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — `NurbsEdge`, `NurbsPatch` (tasks 1b, 1c).
  - `FCDirectModeling/nurbs_primitives.py` — `build_curve()` for how curves are made.
  - FreeCAD `Part.BSplineSurface` and `Part.BSplineCurve` API.
- **Files to modify**:
  - `FCDirectModeling/nurbs_primitives.py` — add `extrude_curve_to_surface()`.
- **Steps**:
  1. Add function `extrude_curve_to_surface(curve, direction, distance)`:
     ```python
     def extrude_curve_to_surface(bspline_curve, direction, distance):
         """
         Extrude a BSplineCurve along a direction to produce a BSplineSurface.

         Args:
             bspline_curve: Part.BSplineCurve — the profile curve
             direction: FreeCAD.Vector — extrusion direction (normalized)
             distance: float — extrusion distance

         Returns:
             Part.BSplineSurface
         """
     ```
  2. Implementation strategy:
     - Get the curve's poles: `poles_bottom = bspline_curve.getPoles()`.
     - Compute the extrusion offset: `offset = direction.normalize() * distance`.
     - Create top poles: `poles_top = [p + offset for p in poles_bottom]`.
     - Build a degree-1 surface in the extrusion direction (V), using the curve's degree in the profile direction (U):
       ```python
       # poles_2d[v_row][u_col]
       poles_2d = [poles_bottom, poles_top]
       weights = bspline_curve.getWeights()
       weights_2d = [weights, weights]  # Same weights for both rows

       u_knots = bspline_curve.getKnots()
       u_mults = bspline_curve.getMultiplicities()
       v_knots = [0.0, 1.0]
       v_mults = [2, 2]  # degree 1 → mult = degree + 1 = 2

       bs = Part.BSplineSurface()
       bs.buildFromPolesMultsKnots(
           poles_2d, u_mults, v_mults, u_knots, v_knots,
           bspline_curve.isPeriodic(), False,
           bspline_curve.Degree, 1,  # u_degree from curve, v_degree = 1
           weights_2d
       )
       return bs
       ```
  3. Add a convenience wrapper `extrude_curve(points, direction, distance)`:
     - Calls `build_curve(points)` to get the edge, extracts the BSplineCurve, then calls `extrude_curve_to_surface()`.
- **Acceptance**: Draw 4 points → `build_curve(points)` → get curve → `extrude_curve_to_surface(curve, Vector(0,0,1), 10)` → returns a valid `Part.BSplineSurface`. Converting to a face via `Part.Face(bs.toShape())` displays correctly in FreeCAD.

---

### 1e. Create DM_Extrude command (Complexity: 5/10)

- **Goal**: An interactive command that takes a selected curve and extrudes it into a `BSplineSurface` by dragging along the work plane normal.
- **Files to read**:
  - `FCDirectModeling/nurbs_primitives.py` — `extrude_curve_to_surface()` (task 1d).
  - `FCDirectModeling/primitives/primitive_base.py` — understand the event callback pattern.
  - `FCDirectModeling/primitives/curve_creator.py` — how curves are finalized.
  - `FCDirectModeling/dm_object.py` — `create_dm_object()`.
- **Files to create**:
  - `dm_commands/command_extrude.py`
- **Files to modify**:
  - `InitGui.py` — register `DM_Extrude` command and add to toolbar.
  - `FCDirectModeling/dm_object.py` — add `"surface"` shape type to `DMObjectProxy.__init__()` and `build_shape()`.
  - `FCDirectModeling/nurbs_primitives.py` — ensure `extrude_curve_to_surface()` exists (task 1d).
- **Steps**:
  1. In `dm_object.py`, add `"surface"` shape type:
     ```python
     elif shape_type == "surface":
         if not hasattr(obj, "SourceCurvePoints"):
             obj.addProperty("App::PropertyVectorList", "SourceCurvePoints", "Surface", "Source curve points")
         if not hasattr(obj, "ExtrudeDirection"):
             obj.addProperty("App::PropertyVector", "ExtrudeDirection", "Surface", "Extrusion direction")
         if not hasattr(obj, "ExtrudeDistance"):
             obj.addProperty("App::PropertyFloat", "ExtrudeDistance", "Surface", "Extrusion distance")
         obj.SourceCurvePoints = params.get("source_curve_points", [])
         obj.ExtrudeDirection = params.get("extrude_direction", FreeCAD.Vector(0,0,1))
         obj.ExtrudeDistance = params.get("extrude_distance", 10.0)
     ```
  2. In `build_shape()`, add:
     ```python
     elif st == "surface":
         curve_shape = np_builders.build_curve(fp.SourceCurvePoints)
         if curve_shape.isNull():
             return Part.Shape()
         bspline_curve = curve_shape.Edges[0].Curve.toBSpline()
         bs = np_builders.extrude_curve_to_surface(bspline_curve, fp.ExtrudeDirection, fp.ExtrudeDistance)
         return Part.Face(bs.toShape())  # Wrap in Face for display
     ```
  3. Create `dm_commands/command_extrude.py`:
     ```python
     class DM_Extrude:
         def GetResources(self):
             return {
                 'MenuText': 'Extrude Curve',
                 'ToolTip': 'Extrude a curve into a NURBS surface',
                 'Accel': 'E'
             }

         def Activated(self):
             # Get the selected curve object
             sel = FreeCADGui.Selection.getSelection()
             if not sel or not hasattr(sel[0], 'ShapeType'):
                 FreeCAD.Console.PrintError("Select a curve first\n")
                 return
             obj = sel[0]
             if obj.ShapeType != "curve":
                 FreeCAD.Console.PrintError("Selected object is not a curve\n")
                 return
             # Enter interactive extrude mode (drag to set distance)
             # ... create ExtrudeInteractor(obj) ...

         def IsActive(self):
             return FreeCAD.activeDocument() is not None
     ```
  4. The `ExtrudeInteractor` class:
     - Registers a mouse event callback.
     - On mouse move: compute extrusion distance from screen Y delta.
     - Show live preview: build the BSplineSurface, assign to preview object.
     - On click: finalize via `create_dm_object("Surface", "surface", params)`.
  5. Register in `InitGui.py`: add `from dm_commands import command_extrude` and `'DM_Extrude'` to toolbar/menu.
- **Acceptance**: Draw a curve with `DM_CreateCurve` → select it → press `E` → drag to set extrusion height → click to finalize → a `Part::FeaturePython` with a `BSplineSurface` face appears in the document.

---

## Phase 2: NURBS ↔ BRep Conversion

---

### 2a. NURBS to BRep converter (Complexity: 4/10)

- **Goal**: Convert a DM object's `BSplineSurface` patches into a `Part.Shell` or `Part.Solid` for STEP export or boolean operations.
- **Files to read**:
  - FreeCAD `Part.Shell`, `Part.Solid`, `Part.Face` API.
  - `FCDirectModeling/dm_object.py` — understand `DMObjectProxy` shape storage.
- **Files to create**:
  - `FCDirectModeling/nurbs_brep_convert.py`
  - `dm_commands/command_convert.py`
- **Files to modify**:
  - `InitGui.py` — register `DM_NurbsToBRep`.
- **Steps**:
  1. Create `FCDirectModeling/nurbs_brep_convert.py`:
     ```python
     def nurbs_to_brep(nurbs_shape):
         """Convert a shape containing BSplineSurface faces into a BRep solid.

         Args:
             nurbs_shape: Part.Shape — must have at least one Face with a BSplineSurface.

         Returns:
             Part.Solid if the faces form a closed shell, Part.Shell otherwise.
         """
         faces = nurbs_shape.Faces
         shell = Part.Shell(faces)
         if shell.isClosed():
             solid = Part.Solid(shell)
             solid.fix(0.001, 0.001, 0.001)
             return solid
         return shell
     ```
  2. Add `brep_to_nurbs(brep_shape)`:
     ```python
     def brep_to_nurbs(brep_shape):
         """Convert a BRep shape's faces to BSplineSurface representation.

         Returns:
             Part.Shape — the .toNurbs() version of the input.
         """
         return brep_shape.toNurbs()
     ```
  3. Create `dm_commands/command_convert.py` with `DM_NurbsToBRep` and `DM_BRepToNurbs` commands:
     - `DM_NurbsToBRep`: get selected object → call `nurbs_to_brep(obj.Shape)` → create a new `Part::Feature` with the result.
     - `DM_BRepToNurbs`: get selected Part::Feature → call `brep_to_nurbs(obj.Shape)` → create a new DM object with the NURBS result.
  4. Register in `InitGui.py`.
- **Acceptance**: Create a surface via extrude → select it → `NURBS → BRep` → a `Part::Feature` solid/shell appears. Select a standard FreeCAD Part → `BRep → NURBS` → a DM object appears with BSplineSurface faces.

---

## Phase 3: Instance, Boolean, Array

---

### 3a. Implement Instance and Copy commands (Complexity: 4/10)

- **Goal**: Create linked instances (`App::Link`) and independent copies of DM objects.
- **Files to read**:
  - `FCDirectModeling/dm_object.py` — `create_dm_object()`.
  - FreeCAD `App::Link` API.
- **Files to create**:
  - `dm_commands/command_instance.py`
- **Files to modify**:
  - `InitGui.py` — register commands.
- **Steps**:
  1. `DM_Instance`: select object → `link = doc.addObject("App::Link", name)` → `link.LinkedObject = obj` → offset placement.
  2. `DM_Copy`: select object → read its `ShapeType` and properties → call `create_dm_object()` with same params → offset placement.
  3. Register with hotkeys `I` and `Ctrl+D`.
- **Acceptance**: Instance updates when original changes. Copy does not.

---

### 3b. Update booleans to use instance system (Complexity: 4/10)

- **Goal**: Booleans auto-create instance of second operand and hide original (toggleable).
- **Files to read**:
  - `dm_commands/command_boolean.py` — current boolean implementation.
  - `dm_commands/command_instance.py` — instance creation (task 3a).
- **Files to modify**:
  - `dm_commands/command_boolean.py`
- **Steps**:
  1. Before boolean op, if `BoolUseInstance` setting is `True`: create `App::Link` of second operand, hide original.
  2. Perform boolean on the instance.
  3. Store setting in `FreeCAD.ParamGet("User parameter:FCDirectModeling").SetBool("BoolUseInstance", True)`.
- **Acceptance**: Fuse two objects → original second object hidden, instance used for result.

---

### 3c. Linear Array command (Complexity: 4/10)

- **Goal**: Repeat a shape along a direction vector.
- **Files to create**:
  - `dm_commands/command_array.py`
- **Files to modify**:
  - `InitGui.py`
- **Steps**:
  1. Dialog: count, direction vector, spacing, fuse checkbox.
  2. For each copy: `shape.translated(direction * spacing * i)`.
  3. If fuse: `result.fuse(copy)` iteratively, then `removeSplitter()`.
  4. Create result as `Part::Feature`.
- **Acceptance**: Array a surface 5 times along X → 5 translated copies (or one fused shape).

---

### 3d. Polar Array command (Complexity: 4/10)

- **Goal**: Repeat a shape around a central axis.
- **Files to create/modify**:
  - `dm_commands/command_array.py` — add `DM_PolarArray`.
  - `InitGui.py`
- **Steps**:
  1. Dialog: count, axis center, axis direction, angle span, fuse checkbox.
  2. `copy = shape.rotated(center, axis, angle_per_copy * i)`.
  3. Fuse or keep separate.
- **Acceptance**: Polar array 6 copies around Z → circular pattern.

---

## Phase 4: Radial Menu & Hotkeys

---

### 4a. Register hotkeys for all commands (Complexity: 2/10)

- **Goal**: Add `'Accel'` entries to all command `GetResources()` methods.
- **Files to modify**:
  - `dm_commands/command_create_curve.py` — add `'Accel': 'D'`.
  - `dm_commands/command_extrude.py` — add `'Accel': 'E'`.
  - `dm_commands/command_boolean.py` — `'Accel': 'Ctrl+F'` (fuse), `'Ctrl+X'` (cut), `'Ctrl+I'` (common).
- **Steps**:
  1. In each command class's `GetResources()`, add `'Accel': '<key>'`.
- **Acceptance**: Each hotkey activates the correct command when workbench is active.

---

### 4b. Radial menu system (Complexity: 6/10)

- **Goal**: Coin3D-based radial menu at cursor position, activated by `Space`.
- **Files to create**:
  - `FCDirectModeling/radial_menu.py`
  - `dm_commands/command_radial_menu.py`
- **Files to modify**:
  - `InitGui.py`
- **Steps**:
  1. `RadialMenu` class: Coin3D `SoSeparator` with `SoText2` labels at angular intervals.
  2. Mouse event callback: highlight nearest sector, click to activate command, ESC to dismiss.
  3. `DM_RadialMenu` command creates RadialMenu with configurable items.
  4. Bind to `Space`.
- **Acceptance**: Press Space → radial menu appears → click item → command activates → ESC dismisses.

---

Open sketcher tool should create a new sketch on the workplane, not switch to the sketcher workbench.