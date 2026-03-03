# FreeCAD Direct Modeling — TODO

## How to Write a Task

Each task must be **self-contained** so an LLM or developer can complete it with
no prior context beyond the files listed. Follow this template:

```markdown
- **Task Title** (Minimum LLM: Gemini Flash/Low/High)
  - **Goal**: One sentence describing the desired outcome.
  - **Files to read**: List every file the implementer must read first.
  - **Files to modify/create**: List files that will change.
  - **Steps**:
    1. First concrete step…
    2. Second step…
  - **Acceptance**: How to verify the task is done.
```

- Move completed tasks to `COMPLETED.md` when a milestone is reached.
- Sort by required LLM within each section (Flash first, High last).
- We have as options Gemini Flash, Low, and High. Only use Claude for very difficult programming issues.

## General & Architecture

### Rename PrimitiveBase to DMBase (Minimum LLM: Gemini Flash)
- **Goal**: Rename `primitive_base.py` to `dm_base.py` to better reflect its role as the foundation for all DM interactive tools.
- **Files to read**: `tools/primitive_base.py`
- **Files to modify**: `tools/primitive_base.py`, `tools/curve_tool.py`, `tools/work_plane_tool.py`, `tools/edit_tool.py`, `tools/point_tool.py`
- **Steps**:
  1. Rename `tools/primitive_base.py` to `tools/dm_base.py`.
  2. Update all `from .primitive_base import PrimitiveBase` imports across the codebase.
  3. Rename the class `PrimitiveBase` to `DMBase`.
- **Acceptance**: All tools load and function correctly without import errors.

### Universal Tool Acceptance via Enter Key (Minimum LLM: Gemini Flash)
- **Goal**: Ensure that pressing the `Enter` or `Return` key consistently accepts and finishes the operation for *all* tools.
- **Files to read**: `tools/dm_base.py`
- **Files to modify**: `tools/dm_base.py`, `tools/*.py`
- **Steps**:
  1. Verify `handle_keyboard` natively captures `ENTER` and `RETURN` to trigger `self.finish()`.
  2. Check child tools to ensure they don't block this key event.
- **Acceptance**: Pressing Enter while any tool is active immediately completes the operation.

## Curve Tool & Editing

### Fix BSplineCurve Constructor Fallback Warning (Minimum LLM: Gemini Flash)
- **Goal**: Eliminate the console warning `DMCurve: Complex constructor failed: B-spline constructor accepts... Trying fallback buildFromPolesMultsKnots.`
- **Files to read**: `core/nurbs_geometry.py`
- **Files to modify**: `core/nurbs_geometry.py`
- **Steps**:
  1. Identify the incorrect argument signature being passed to `Part.BSplineCurve()`.
  2. Correctly format the arguments (e.g. `poles, periodic, degree, interpolate` or the fallback `buildFromPolesMultsKnots`) to satisfy the FreeCAD C++ APIs without throwing warnings.
- **Acceptance**: Curve creation is silent in the report view, without `Complex constructor failed` warnings.

### Curve Closure Point Management (Minimum LLM: Gemini Low)
- **Goal**: Prevent extraneous control points from lingering when closing an open curve, and clean up duplicate endpoints when re-opening it.
- **Files to read**: `core/dm_object.py`, `core/nurbs_geometry.py`
- **Files to modify**: `core/dm_object.py` or `core/nurbs_geometry.py`
- **Steps**:
  1. When changing `Closed` from `False` to `True`, prune the last point if it is spatially coincident with the first point.
  2. When changing `Closed` from `True` to `False`, ensure no duplicate points are left at the seam.
- **Acceptance**: Toggling the `Closed` property maintains a clean list of control points.

### Viewport Plane Focus Hotkey (Minimum LLM: Gemini Low)
- **Goal**: Add a hotkey to instantly orient the camera to face the active curve's plane (or a plane derived from 3 points for a 3D curve).
- **Files to read**: `tools/edit_tool.py`
- **Files to modify**: `tools/edit_tool.py`
- **Steps**:
  1. Intercept a hotkey (e.g., `F` or `Space`) during curve editing in `handle_keyboard`.
  2. Calculate the optimal plane normal for the curve.
  3. Use `view.setViewDirection()` to rotate the camera perpendicular to that plane.
- **Acceptance**: Pressing the focal hotkey snaps the camera to a flat 2D viewing angle relative to the curve.

### Curve Handle Type Context Menu (Minimum LLM: Gemini Low)
- **Goal**: Provide a context menu on control points to toggle handle types between Tangent (Smooth), Split (V-shape), and Custom (Sharp).
- **Files to read**: `tools/edit_tool.py`
- **Files to modify**: `tools/edit_tool.py`
- **Steps**:
  1. In `EditTool.on_button2_down` or `on_button3_down` (Right Click), raycast to find the hovered control point.
  2. Display a `QtGui.QMenu` to select the type.
  3. Assign the chosen `PointType` back to the DM curve object.
- **Acceptance**: Right-clicking a control point allows instant continuity mode changes.

### Angle Snapping for Curve Handles (Minimum LLM: Gemini Low)
- **Goal**: Allow handles to snap to specific angular increments (default 15 degrees, via DM Settings) while dragging.
- **Files to read**: `tools/edit_tool.py`, `core/dm_object.py` (for settings)
- **Files to modify**: `tools/edit_tool.py`
- **Steps**:
  1. In `EditTool.handle_move`, check if a modifier key (Shift/Ctrl) is held during handle drag.
  2. Calculate the handle angle, round to the nearest increment, and enforce the output vector.
- **Acceptance**: Holding the modifier tightly snaps the handle angle.

## Curve Tool

### Fix curve point work plane snapping (Minimum LLM: Gemini Flash)
- **Goal**: Ensure curve points are consistently projected onto the active work plane, preventing unintended 3D drift.
- **Files to read**: `tools/curve_tool.py`, `tools/primitive_base.py`
- **Files to modify**: `tools/curve_tool.py`
- **Steps**:
  1. Enforce strict projection of `get_mouse_plane_pt` onto `self.working_plane` during curve creation to guarantee they remain coplanar.
- **Acceptance**: All curve points lie exactly on the selected or dynamically established work plane.

### Fix tool acceptance mapping and view plane reassessment (Minimum LLM: Gemini High)
- **Goal**: Prevent middle-mouse/right-click navigation chords from accepting the tool, and update the view plane when rotating the camera mid-operation.
- **Files to read**: `tools/primitive_base.py`, `tools/curve_tool.py`
- **Files to modify**: `tools/primitive_base.py`, `tools/curve_tool.py`
- **Steps**:
  1. In `PrimitiveBase.event_cb`, refine `BUTTON3` handling to ignore navigation chords (e.g., checking if `BUTTON2` is simultaneously held down).
  2. Ensure the dynamically established view plane (used before the first click) updates if the user rotates the view.
- **Acceptance**: Viewport rotation using FreeCAD navigation styles does not prematurely finish the tool. Drawing adapts to the new camera orientation if rotated before the first click.

---
## Phase 1: Workplane & Existing Tools

### 1a. Workplane snapping improvements (Minimum LLM: Gemini Low)

- **Goal**: Add snap-to-center, snap-to-grid, and snap-to-radius for the workplane.
- **Files to read**:
  - `core/work_plane.py` — `WorkPlaneManager`
  - `tools/primitive_base.py` — `get_point_on_plane()`
- **Files to modify**:
  - `core/work_plane.py` — add snap logic
  - `tools/primitive_base.py` — integrate snapping into mouse position
- **Steps**:
  1. Add `snap_to_grid(point, grid_size)` method to `WorkPlaneManager`.
  2. Add `snap_to_center(point)` — snaps to the workplane origin.
  3. Add `snap_to_radius(point, radius)` — snaps to a circle of given radius from center.
  4. Wire snapping into `get_point_on_plane()` with toggle via DM Settings.
- **Acceptance**: Points placed near grid intersections snap to them. Snap modes are toggleable.


---

## Phase 2: BRep Primitives

### 2a. Box primitive on workplane (Minimum LLM: Gemini Low)

- **Goal**: Create a `Part.makeBox()` solid placed on the active workplane. Interactive 2-click creation: 1st click sets corner, drag sets footprint, 2nd click sets height.
- **Files to read**:
  - `tools/primitive_base.py` — base class pattern
  - `core/dm_object.py` — `create_dm_object()` factory
  - `core/work_plane.py` — `get_placement()`
- **Files to create**:
  - `tools/box_tool.py` — `BoxCreator`
  - `commands/cmd_box.py` — `DM_CreateBox`
- **Files to modify**:
  - `core/dm_object.py` — add `"box"` shape type with `Length`, `Width`, `Height` properties
  - `InitGui.py` — register `DM_CreateBox`
- **Steps**:
  1. Add `"box"` shape type to `DMObjectProxy.__init__()` and `build_shape()`.
  2. `build_shape()` for box: `Part.makeBox(length, width, height)` transformed by workplane placement.
  3. Create `BoxCreator` extending `NURBSPrimitiveCreator` — handles mouse events for 2-click box creation.
  4. Create `DM_CreateBox` command, register with hotkey `B`.
- **Acceptance**: Activate box tool → click on surface → drag to set footprint → click to set height → solid box appears.

---

### 2b. Sphere primitive on workplane (Minimum LLM: Gemini Low)

- **Goal**: Create a `Part.makeSphere()` solid centered on the workplane.
- **Files to read**:
  - Same as 2a — follow the box pattern
- **Files to create**:
  - `tools/sphere_tool.py` — `SphereCreator`
  - `commands/cmd_sphere.py` — `DM_CreateSphere`
- **Files to modify**:
  - `core/dm_object.py` — add `"sphere"` shape type with `Radius`
  - `InitGui.py` — register command
- **Steps**:
  1. Add `"sphere"` shape type to `DMObjectProxy`.
  2. `build_shape()` for sphere: `Part.makeSphere(radius)` at workplane center.
  3. Create `SphereCreator` — click sets center, drag sets radius.
  4. Register command.
- **Acceptance**: Click → drag → sphere appears at workplane with correct radius.

---

### 2c. Cylinder primitive on workplane (Minimum LLM: Gemini Low)

- **Goal**: Create a `Part.makeCylinder()` solid on the workplane.
- **Files to read**: Same pattern as 2a/2b.
- **Files to create**:
  - `tools/cylinder_tool.py` — `CylinderCreator`
  - `commands/cmd_cylinder.py` — `DM_CreateCylinder`
- **Files to modify**:
  - `core/dm_object.py` — add `"cylinder"` shape type with `Radius`, `Height`
  - `InitGui.py`
- **Steps**:
  1. Add `"cylinder"` shape type.
  2. `build_shape()`: `Part.makeCylinder(radius, height)` at workplane.
  3. Create `CylinderCreator` — click sets center, drag sets radius, 2nd drag sets height.
- **Acceptance**: Interactive cylinder creation on workplane produces a solid.

---

### 2d. Extrude curve to solid (Minimum LLM: Gemini High)

- **Goal**: Extrude a selected curve into a BRep solid along the workplane normal. Uses `Part.Wire` → `Part.Face` → `face.extrude()`.
- **Files to read**:
  - `core/dm_object.py` — `DMObjectProxy`, shape types
  - `tools/primitive_base.py` — event callback pattern
  - `tools/curve_tool.py` — how curves are finalized
- **Files to create**:
  - `commands/cmd_extrude.py` — `DM_Extrude`
- **Files to modify**:
  - `core/dm_object.py` — add `"extrude"` shape type with `SourceCurve`, `Direction`, `Distance`
  - `InitGui.py`
- **Steps**:
  1. Add `"extrude"` shape type.
  2. `build_shape()`: get source curve → build Wire → make Face → `face.extrude(direction * distance)`.
  3. `DM_Extrude`: select curve → enter interactive mode → drag to set distance → finalize.
  4. Register with hotkey `E`.
- **Acceptance**: Draw a curve → select → press `E` → drag → solid extrusion appears.

---

## Phase 3: Boolean Operations (Improve)

### 3a. Boolean workflow — interactive two-object selection (Minimum LLM: Gemini High)

- **Goal**: Improve the boolean commands to support interactive selection: activate tool → click first object → click second object → operation executes.
- **Files to read**:
  - `commands/cmd_boolean.py` — current implementation
  - `core/dm_object.py` — how objects are created
- **Files to modify**:
  - `commands/cmd_boolean.py` — add interactive selection mode
- **Steps**:
  1. Add a `BooleanInteractor` class that registers mouse callbacks.
  2. First click highlights and selects object A.
  3. Second click selects object B and executes the boolean.
  4. Show preview highlighting during hover.
- **Acceptance**: Activate Fuse → click first solid → click second → fused solid appears.

---

## Phase 4: Polish & UX

### 4a. Register hotkeys for all commands (Minimum LLM: Gemini Flash)

- **Goal**: Add `'Accel'` entries to all command `GetResources()` methods.
- **Files to modify**: All `cmd_*.py` files in `commands/`
- **Steps**:
  1. Add `'Accel': '<key>'` to each command's `GetResources()`.
  2. Point: `P`, Curve: `D`, Box: `B`, Extrude: `E`, Fuse: `Ctrl+F`, Cut: `Ctrl+X`, Common: `Ctrl+I`.
- **Acceptance**: Each hotkey activates the correct command.

---

### 4b. Instance and Copy commands (Minimum LLM: Gemini Low)

- **Goal**: Create linked instances (`App::Link`) and independent copies of DM objects.
- **Files to read**:
  - `core/dm_object.py` — `create_dm_object()`
- **Files to create**:
  - `commands/cmd_instance.py` — `DM_Instance`, `DM_Copy`
- **Files to modify**:
  - `InitGui.py`
- **Steps**:
  1. `DM_Instance`: select object → `App::Link` → offset placement. Hotkey `I`.
  2. `DM_Copy`: select object → `create_dm_object()` with same params. Hotkey `Ctrl+D`.
- **Acceptance**: Instance updates when original changes. Copy does not.

---

### 4c. Radial menu system (Minimum LLM: Gemini High)

- **Goal**: Right-click radial menu at cursor position for quick tool access.
- **Files to create**:
  - `core/radial_menu.py` — Coin3D-based pie menu
  - `commands/cmd_radial_menu.py`
- **Files to modify**:
  - `InitGui.py`
- **Steps**:
  1. `RadialMenu` class: Coin3D `SoSeparator` with text labels at angular intervals.
  2. Mouse callback: highlight nearest sector, click to activate, Esc to dismiss.
  3. Bind to `Space`.
- **Acceptance**: Press Space → radial menu → click item → command activates.

---

## Phase N: Advanced Editing
- [ ] **Custom Control Points Effects**
  - **Goal**: Allow points to affect bevel radius, chamfer, and other localized curve parameters instead of just positioning.


### Radial Menu Raycast Selection Tool (Minimum LLM: Gemini High)
- **Goal**: Add a tool to the radial menu that lists all FreeCAD objects under the cursor, allowing selection of hidden topology.
- **Files to read**: `core/radial_menu.py`, `tools/dm_base.py`
- **Files to modify**: `core/radial_menu.py`, `commands/cmd_radial_menu.py`
- **Steps**:
  1. Add a "Select Under Cursor" action to the radial menu.
  2. Perform a deep raycast (`view.getObjectsInfo`) at the activation coordinates.
  3. Present a popup or visual list to force specific selection.
- **Acceptance**: Triggering the tool successfully lists and selects objects hidden behind other geometry.

---
draw curves to surface
---
combine workplanes into surface
---


---