# FreeCAD Direct Modeling — TODO

## How to Write a Task

Each task must be **self-contained** so an LLM or developer can complete it with
no prior context beyond the files listed. Follow this template:

```markdown
- [ ] **Task Title** (Minimum LLM: Gemini Flash/Low/High)
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
- Sort by required LLM within each section (Flash first, High last).
- We have as options Gemini Flash, Low, and High. Only use Claude for very difficult programming issues.

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

### 1c. 3-click interaction flow (Minimum LLM: Gemini High)

- **Goal**: Update tool interaction: 1st click locks workplane, 2nd click starts the tool, right-click finishes, Esc cancels.
- **Files to read**:
  - `tools/primitive_base.py` — event handling
  - `tools/curve_tool.py` — current curve creation flow
  - `core/work_plane.py`
- **Files to modify**:
  - `tools/primitive_base.py` — add workplane-lock state
  - `tools/curve_tool.py` — integrate new state
- **Steps**:
  1. Add state to `PrimitiveBase` for workplane-not-yet-locked.
  2. While in this state, hovering updates workplane visually.
  3. On 1st click, lock workplane placement.
  4. On 2nd click, pass event to the actual tool.
  5. Right-click calls `finish()`, Esc calls `cancel()`.
- **Acceptance**: Click once → grid locks. Click again → drawing starts. Right-click finishes.

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

---

## Phase N: Advanced Editing
- [ ] **Custom Control Points Effects**
  - **Goal**: Allow points to affect bevel radius, chamfer, and other localized curve parameters instead of just positioning.