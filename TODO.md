# FreeCAD Direct Modeling — TODO

## How to Write a Task

Each task must be **self-contained** so an LLM or developer can complete it with
no prior context beyond the files listed. Follow this template:

```markdown
- [ ] **Task Title** (Minimum LLM: Gemini Flash/Low/High/Claude Sonnet/Opus)
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
- Sort by required LLM within each section (Flash first, Opus last).
- We have as options Gemini Flash, Low, High, and on occasion Claude Sonnet and Opus. Only use Claude for very difficult programming issues as I don't have a lot of credits for it.

---

## Architecture Overview

> **Principle**: `Part.BSplineSurface` is the native geometry — NOT BRep shells or solids. The three atoms are **DMPoint**, **DMCurve (BSplineCurve)**, and **DMPatch (BSplineSurface)**. BRep is only used for conversion/export. The primary workflow is: draw a curve → extrude into a surface → compose.

---


---

## Phase 1: Work Plane Fixes

### [x] 1.1 Fix Workplane Orientation (Minimum LLM: Gemini Low)

- **Goal**: Orient the work plane normal to the surface it's placed on, and ensure the X axis is parallel to any 2 points on the XY plane (so it doesn't twist relative to the surface).
- **Files to read**:
  - `FCDirectModeling/work_plane.py`
- **Files to modify**:
  - `FCDirectModeling/work_plane.py`
- **Steps**:
  1. Locate `WorkPlaneManager.update` or where the placement is calculated.
  2. Implement the logic to project the global X/Y axis onto the face tangent plane to define a consistent, non-twisting local coordinate system.
  3. Apply this to the work plane's rotation.
- **Acceptance**: The work plane appears normal to the hovered face, and moving across non-planar faces keeps the grid visually aligned to the global XY plane as much as possible without unpredictable spinning.

---

workplane snapping. I want to be able to snap to the center of a workplane, optionally at adjustable grid points, or at an adjustable radius from the center of the workplane.

---

### 1.2 Implement 3-Click Interaction Flow (Minimum LLM: Gemini High)

- **Goal**: Update the primitive tool interaction so the 1st click defines the workplane, 2nd click starts the tool, 3rd... continues, right click finishes, and ESC cancels.
- **Files to read**:
  - `FCDirectModeling/primitives/primitive_base.py`
  - `FCDirectModeling/primitives/curve_creator.py`
  - `FCDirectModeling/work_plane.py`
- **Files to modify**:
  - `FCDirectModeling/primitives/primitive_base.py`
  - `FCDirectModeling/primitives/curve_creator.py`
- **Steps**:
  1. Add a state in `PrimitiveBase` to wait for the 1st click to lock the workplane.
  2. While in this state, hovering updates the workplane visually.
  3. On 1st click, lock the workplane placement so it doesn't move.
  4. On 2nd click, pass the event to the actual tool (e.g. place the first curve point).
  5. Ensure Right Click calls `finish()` and ESC calls `cancel()`.
- **Acceptance**: Clicking once locks the grid. Clicking again starts drawing the curve. Right-click finishes successfully.

---

## Phase 2: Curve Tools & Geometry

### 2.1 Use Built-in BSplineCurve Rendering Instead of Manual Poles (Minimum LLM: Claude Sonnet)

- **Goal**: Replace `DMCurve._build_from_points()` with FreeCAD's native `Part.BSplineCurve.interpolate()` or `buildFromPolesMultsKnots()`. Replace manually-drawn control-point vertices and handle lines with Coin3D overlays.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py`
  - `FCDirectModeling/dm_object.py`
  - `FCDirectModeling/primitives/primitive_base.py`
- **Files to modify**:
  - `FCDirectModeling/nurbs_geometry.py`
  - `FCDirectModeling/dm_object.py`
- **Steps**:
  1. Refactor `_build_from_points()` to use `interpolate` for common cases.
  2. Refactor `to_shape()` to return `Part.Edge(self.bspline)` natively.
  3. Add a Coin3D control-cage overlay in `DMViewProvider.attach()`.
  4. Update `build_shape()` in `DMObjectProxy` to handle `Part.Edge` return type.
  5. Remove the manual crosshair object from `NURBSPrimitiveCreator`.
- **Acceptance**: Curve renders smoothly via native OCCT tessellator. Coin3D overlay handles the control points without Model tree clutter.


## Phase 3: Curve Extrusion & Editing

These tasks build the curve→surface workflow.

---

### [x] 1b. Create DMCurve class (Minimum LLM: Gemini Flash)
    - [x] Create `DMCurve` class in `nurbs_geometry.py`
    - [x] Basic interpolation and shape building

- **Goal**: A `DMCurve` class that builds a `Part.BSplineCurve` from `DMPoint` objects.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — the `DMPoint` class (task 1a).
  - FreeCAD `Part.BSplineCurve` docs — `buildFromPolesMultsKnots()`, `interpolate()`.
- **Files to modify**:
  - `FCDirectModeling/nurbs_geometry.py` — add `DMCurve`.
- **Steps**:
  1. Add class `DMCurve`:
     ```python
     import Part

     class DMCurve:
         """NURBS curve from a sequence of DMPoint objects."""
         def __init__(self, points, degree=3):
             self.points = list(points)  # List[DMPoint]
             self.degree = degree
     ```
  2. Method `to_bspline_curve() -> Part.BSplineCurve`:
     - If all points `.is_sharp()`: use `Part.BSplineCurve()` + `interpolate()` with the position vectors. For degree-1 (straight segments), use `buildFromPolesMultsKnots` with `degree=1`.
     - If handles exist: build poles array = `[p.handle_in, p.position, p.handle_out, ...]` and use `buildFromPolesMultsKnots` with appropriate multiplicity.
  3. Method `to_shape() -> Part.Shape`: calls `self.to_bspline_curve().toShape()`.
  4. Property `is_closed`: returns `True` if first and last positions within 0.001 distance.
- **Acceptance**: `DMCurve([DMPoint(V(0,0,0)), DMPoint(V(10,0,0))]).to_shape()` returns a valid `Part.Edge`.

---

### 1c. Create DMSurface class (Minimum LLM: Gemini Low)

- **Goal**: A `DMSurface` class that builds a `Part.BSplineSurface` from a control point grid.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — `DMPoint`, `DMCurve` (tasks 1a, 1b).
  - FreeCAD `Part.BSplineSurface` docs — `buildFromPolesMultsKnots()`.
- **Files to modify**:
  - `FCDirectModeling/nurbs_geometry.py` — add `DMSurface`.
- **Steps**:
  1. Add class `DMSurface`:
     ```python
     class DMSurface:
         """NURBS surface from a control point grid."""
         def __init__(self, control_grid, u_degree=1, v_degree=1):
             self.control_grid = control_grid  # List[List[DMPoint]] — rows x cols
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
- **Acceptance**: `DMSurface.from_corners(p1,p2,p3,p4).to_bspline_surface()` returns a valid `Part.BSplineSurface`. `.to_face()` returns a displayable `Part.Face`.

---

### 1d. Implement curve extrusion to BSplineSurface (Minimum LLM: Gemini High)

- **Goal**: Extrude a `Part.BSplineCurve` (edge) along a direction vector to produce a `Part.BSplineSurface`. This is the core curve→surface operation. The result is a BSplineSurface, NOT a BRep solid.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — `DMCurve`, `DMPatch` (tasks 1b, 1c).
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

### 1e. Create DM_Extrude command (Minimum LLM: Gemini High)

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

## Phase 4: NURBS ↔ BRep Conversion

---

### 2a. NURBS to BRep converter (Minimum LLM: Gemini Low)

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

## Phase 5: Instance, Boolean, Array

---

### 3a. Implement Instance and Copy commands (Minimum LLM: Gemini Low)

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

### 3b. Update booleans to use instance system (Minimum LLM: Gemini Low)

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

- [x] **1a. Create DMPoint class** (Minimum LLM: Gemini Flash)
- [x] **1f. Create Point tool** (Minimum LLM: Gemini Flash)
- [x] **1g. Improve Curve Tool Visibility and Functionality** (Minimum LLM: Gemini Low)
- [x] **1h. Fix Curve Plane Projection and Interpolation** (Minimum LLM: Gemini Flash)
- [ ] **1j. Unify Preview and Final Objects** (Minimum LLM: Gemini High)
    - **Goal**: Eliminate the separate `DM_Preview` object. Use a real `DMObject` that updates its properties in real-time during creation.
    - **Files to read**: `FCDirectModeling/primitives/primitive_base.py`, `FCDirectModeling/primitives/curve_creator.py`, `FCDirectModeling/dm_object.py`.
    - **Files to modify**: `FCDirectModeling/primitives/primitive_base.py`, `FCDirectModeling/primitives/curve_creator.py`, `FCDirectModeling/dm_object.py`.
    - **Steps**:
      1. Refactor `NURBSPrimitiveCreator` to manage a single "live" `DMObject` from the first click.
      2. Update `update_active_object` so it sets properties on the live object and triggers `doc.recompute()` every mouse move.
      3. Maintain an `_is_finalized` flag; `terminate()` deletes the object if `_is_finalized` is `False`.
      4. The `_do_finish()` method simply sets `_is_finalized = True` and calls `terminate()` — no separate cleanup needed.
      5. Simplify `CurveCreator._do_finish` to remove the `_active_obj = None` hack (which was needed to prevent deletion).
    - **Acceptance**: Draw a curve — the orange curve appears from the first point. Pressing ESC or right-click while drawing removes the unfinished curve. Completing the curve leaves a permanent object.

---



### 3d. Polar Array command (Minimum LLM: Gemini Low)

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

## Phase 6: Radial Menu & Hotkeys

---

### 4a. Register hotkeys for all commands (Minimum LLM: Gemini Flash)

- **Goal**: Add `'Accel'` entries to all command `GetResources()` methods.
- **Files to modify**:
  - `dm_commands/command_create_curve.py` — add `'Accel': 'D'`.
  - `dm_commands/command_extrude.py` — add `'Accel': 'E'`.
  - `dm_commands/command_boolean.py` — `'Accel': 'Ctrl+F'` (fuse), `'Ctrl+X'` (cut), `'Ctrl+I'` (common).
- **Steps**:
  1. In each command class's `GetResources()`, add `'Accel': '<key>'`.
- **Acceptance**: Each hotkey activates the correct command when workbench is active.

---

### 4b. Radial menu system (Minimum LLM: Claude Sonnet)

- **Goal**: Coin3D-based radial menu at cursor position, activated by `right click`.

look into built in quick access menu
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

### 4c. Open Sketcher on Workplane (Minimum LLM: Gemini Flash)

- **Goal**: The "Open Sketcher" action should create a new `Sketcher::SketchObject` attached to the current working plane and open it in sketch-edit mode **without** switching the active workbench. The user stays in the DM workbench.
- **Files to read**:
  - `FCDirectModeling/work_plane.py` — `WorkPlaneManager.get_placement()` to get the WP placement.
  - FreeCAD `Sketcher.makeSketch` / `doc.addObject("Sketcher::SketchObject")` API.
- **Files to create**:
  - `dm_commands/command_open_sketcher.py`
- **Files to modify**:
  - `InitGui.py` — register `DM_OpenSketcher`.
- **Steps**:
  1. `DM_OpenSketcher.Activated()`:
     - Get the current WP placement from `WorkPlaneManager`.
     - `sketch = doc.addObject("Sketcher::SketchObject", "Sketch")`.
     - `sketch.Placement = wp_placement`.
     - `FreeCADGui.ActiveDocument.setEdit(sketch)` to open sketch editing without switching workbench.
  2. Register in `InitGui.py` with hotkey `'K'`.
- **Acceptance**: Press `K` → a new sketch opens in edit mode on the working plane. The active workbench remains DM.

---

### 4d. Radial Menu for Spline Points (Minimum LLM: Gemini High)

- **Goal**: Right-clicking on a spline control point shows a small radial menu with options: **Smooth** (auto-tangent), **Corner** (break tangents), **Split** (insert point), **Custom Angle** (set handle angle numerically).
- **Files to read**:
  - `FCDirectModeling/radial_menu.py` — the base radial menu system (task 4b).
  - `FCDirectModeling/nurbs_geometry.py` — `DMPoint` handle structure.
  - `FCDirectModeling/dm_object.py` — property access for `HandleIn`/`HandleOut`.
- **Files to create**:
  - `FCDirectModeling/spline_point_menu.py`
- **Steps**:
  1. On right-click in the DM view, detect if cursor is within snapping distance of a `DMCurve` control point.
  2. If so, open a radial menu at the cursor with the 4 options above.
  3. **Smooth**: zero both handles (forces auto-tangent on next recompute).
  4. **Corner**: set `handle_in` and `handle_out` to the point position (G0 continuity).
  5. **Split**: insert a new `DMPoint` at the midpoint of the adjacent segment, updating `Points`, `HandleIn`, `HandleOut`.
  6. **Custom Angle**: show a numeric input dialog for angle/magnitude; compute new handle vectors.
- **Acceptance**: Right-click on a spline point → radial menu appears → each action modifies the curve correctly.

---
<!-- spherical sprite for points

import FreeCAD, FreeCADGui
from pivy import coin

doc = FreeCAD.ActiveDocument

# -------------------------------------------------
# 1. Load the image as a texture
# -------------------------------------------------
image_path = "/full/path/to/your/sprite.png"   # <-- change this
tex = coin.SoTexture2()
tex.filename = image_path
tex.model = coin.SoTexture2.MODULATE   # respects image alpha

# -------------------------------------------------
# 2. Create a simple geometry (a square) that will hold the texture
# -------------------------------------------------
size = 10.0                     # sprite size in mm (half‑extent)
coords = coin.SoCoordinate3()
coords.point.setValues([
    (-size, -size, 0), ( size, -size, 0),
    ( size,  size, 0), (-size,  size, 0)
])

# Quad (two triangles) – FreeCAD uses SoIndexedFaceSet
indices = coin.SoIndexedFaceSet()
indices.coordIndex.setValues([0, 1, 2, 3, -1])

# -------------------------------------------------
# 3. Billboard node – makes the square always face the camera
# -------------------------------------------------
billboard = coin.SoBillboard()
billboard.axisOfRotation.setValue(coin.SbVec3f(0, 0, 0))  # rotate around all axes

# -------------------------------------------------
# 4. Assemble the scene graph
# -------------------------------------------------
root = coin.SoSeparator()
root.addChild(billboard)   # billboard must be before geometry
root.addChild(tex)
root.addChild(coords)
root.addChild(indices)

# -------------------------------------------------
# 5. Insert the node into the active view
# -------------------------------------------------
view = FreeCADGui.ActiveDocument.ActiveView
view.getSceneGraph().addChild(root)

# -------------------------------------------------
# 6. (Optional) Position the sprite in 3‑D space
# -------------------------------------------------
# Create a transform node to move the billboard
transform = coin.SoTransform()
transform.translation.setValue(FreeCAD.Vector(30, 20, 0))  # change as needed
root.insertChild(0, transform)   # put before the billboard -->

---

### 1n. Connect Point to Curve (Minimum LLM: Gemini Low)

- **Goal**: Allow the user to snap-connect a `DMPoint` object onto a `DMCurve`, constraining the point to lie on the curve. This enables parametric point placement along a curve.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — `DMCurve.value(t)` for evaluating curve position.
  - `FCDirectModeling/dm_object.py` — `DMObjectProxy`, `create_dm_object()`.
- **Files to create**:
  - `dm_commands/command_connect_point.py`
- **Files to modify**:
  - `FCDirectModeling/dm_object.py` — add `"App::PropertyLink"` + `"App::PropertyFloat"` (`CurveParam`) to the `point` shape type.
  - `InitGui.py` — register `DM_ConnectPoint`.
- **Steps**:
  1. Add `SourceCurve` (PropertyLink) and `CurveParam` (PropertyFloat, 0–1) properties to a point's shape type in `DMObjectProxy.__init__`.
  2. In `build_shape()` for `"point"`: if `SourceCurve` is set, evaluate `DMCurve.value(fp.CurveParam)` and use that as the position.
  3. `DM_ConnectPoint`: user selects a point + curve → sets `SourceCurve` and nearest `CurveParam` on the point object.
- **Acceptance**: A `DMPoint` with `SourceCurve` set always lies on the curve. Moving curve control points repositions the connected point.

---

### 1p. Surface Image / Noise Displacement (Minimum LLM: Claude Sonnet)

- **Goal**: Apply a height-map (image file or procedural noise) to displace the control grid of a `DMSurface` along its normal.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — `DMSurface.control_grid`.
  - `FCDirectModeling/dm_object.py` — `"surface"` shape type properties.
- **Files to create**:
  - `dm_commands/command_displace.py`
- **Files to modify**:
  - `FCDirectModeling/dm_object.py` — add `DisplacementImage` (PropertyFile) and `DisplacementScale` (PropertyFloat) to surface type.
  - `FCDirectModeling/nurbs_geometry.py` — add `DMSurface.displace(image_path, scale)` method.
- **Steps**:
  1. Add `DisplacementImage` and `DisplacementScale` properties to the surface shape type.
  2. In `DMSurface.displace(image_path, scale)`: load image with PIL/Pillow, sample at UV coordinates of each grid point, offset each point along the surface normal by `pixel_value * scale`.
  3. In `build_shape()` for `"surface"`: if `DisplacementImage` is set, apply displacement before building the BSplineSurface.
  4. `DM_DisplaceSurface` command: opens file picker for image, sets properties on selected surface object.
- **Acceptance**: Select a surface → `DM_DisplaceSurface` → pick a PNG → the surface control grid is displaced by the image heights.

---

### 1q. Join (Heal) Curves (Minimum LLM: Gemini High)

- **Goal**: Merge two selected `DMCurve` objects end-to-end into a single continuous `DMCurve`, ensuring G1 continuity at the join point.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — `DMCurve` points/handles structure.
  - `FCDirectModeling/dm_object.py` — curve property access.
- **Files to create**:
  - `dm_commands/command_join_curves.py`
- **Files to modify**:
  - `InitGui.py` — register `DM_JoinCurves`.
- **Steps**:
  1. User selects two `DMCurve` objects. Detect which endpoints are closest.
  2. If endpoints are within tolerance: concatenate `Points`, `HandleIn`, `HandleOut` arrays, ensuring the join-point tangents are averaged for G1 continuity.
  3. If endpoints are not coincident: optionally insert a bridging segment to close the gap.
  4. Create a new `DMObject` of type `"curve"` with the merged point list. Optionally delete source curves.
- **Acceptance**: Select two curves that share a near endpoint → `DM_JoinCurves` → one smooth curve object results.

---

### 1r. Extend Surface (Minimum LLM: Claude Sonnet)

- **Goal**: Grow a `DMSurface` beyond its current boundary by adding new rows/columns of control points that follow the surface's existing tangent direction, producing a smooth extension.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — `DMSurface.control_grid`, `to_bspline_surface()`.
  - FreeCAD `Part.BSplineSurface` API — `getPoles()`, `getUKnots()`, `getVKnots()`, `insertUKnot()`, `insertVKnot()`.
- **Files to create**:
  - `dm_commands/command_extend_surface.py`
- **Files to modify**:
  - `InitGui.py` — register `DM_ExtendSurface`.
- **Steps**:
  1. User selects a surface and an edge (U-min, U-max, V-min, or V-max) to extend.
  2. Read the two outermost rows/columns of control points.
  3. Extrapolate new control points by mirroring the tangent of the last segment: `new_pt = last_pt + (last_pt - second_last_pt)`.
  4. Append the new row/column to the grid and rebuild the `DMSurface`.
  5. Interactive mode: drag a handle to set extension distance.
- **Acceptance**: Select a surface edge → `DM_ExtendSurface` → drag → the surface grows smoothly in the chosen direction.

---

### 1o. Create DM_FillCurve command (Minimum LLM: Gemini Low)

- **Goal**: Create a `DMSurface` that fills a selected closed `DMCurve`.
- **Files to read**:
  - `FCDirectModeling/nurbs_geometry.py` — `DMCurve.to_shape()`, `DMSurface`.
  - `FCDirectModeling/dm_object.py` — surface shape type properties.
- **Files to create**:
  - `dm_commands/command_fill_curve.py`
- **Files to modify**:
  - `InitGui.py` — register `DM_FillCurve`.
  - `FCDirectModeling/dm_object.py` — ensure `"surface"` type can accept a `SourceCurve` link.
- **Steps**:
  1. Detect that the selected `DMCurve` is closed (`fp.Closed == True`).
  2. Call `Part.makeFace([curve.to_shape()])` then `.toNurbs()` to get an initial `BSplineSurface` from OCCT's filling algorithm.
  3. Extract the pole grid, weights, knots, and mults from the resulting `BSplineSurface`.
  4. Build a `DMSurface` object from that data and finalize as a `"surface"` type `DMObject`.
- **Acceptance**: Select a closed curve → `DM_FillCurve` → a new orange surface object appears filling the curve interior.

---

### 1l. Snap Workplane to Camera View (Minimum LLM: Gemini Flash)

- **Goal**: Add a command/hotkey to snap the working plane to the current camera's view direction, and ensure the curve tool defaults to a camera-facing plane when no face is snapped.
- **Files to read**:
  - `FCDirectModeling/work_plane.py` — `WorkPlaneManager`.
  - `FCDirectModeling/primitives/primitive_base.py` — `get_base_plane()`, `handle_move()` state 0.
- **Files to create**:
  - `dm_commands/command_snap_wp.py`
- **Files to modify**:
  - `FCDirectModeling/primitives/primitive_base.py` — fallback plane in `handle_move()` state 0 when no face is under cursor.
  - `InitGui.py` — register `DM_SnapWPToView`.
- **Steps**:
  1. `DM_SnapWPToView.Activated()`: get camera orientation from `view.getCameraNode()`, extract the view-direction vector, build a `FreeCAD.Placement` with that rotation, call `WorkPlaneManager.set_placement()`.
  2. In `PrimitiveBase.handle_move()` state 0: if `wp_manager.update()` finds no face, fall back to a plane normal = view direction, origin = scene center at a sensible depth (use `view.getPoint()` as depth reference).
  3. Register with hotkey `'V'`.
- **Acceptance**: Press `V` → WP snaps to the current view. Drawing without hovering a face draws on the view-aligned plane.