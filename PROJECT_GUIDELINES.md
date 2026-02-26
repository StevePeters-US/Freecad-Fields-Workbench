# FreeCAD Direct Modeling - Project Guidelines

## ProjecModel Quota
t Description
FreeCAD Direct Modeling is a workbench for FreeCAD that aims to provide a fast, intuitive, drag-and-drop 3D modeling experience inside FreeCAD, similar to standard direct modeling workflows. By utilizing Signed Distance Fields (SDFs) and Naive Surface Nets (with QEF edge-preservation), the toolkit allows users to quickly sketch and define shapes without the typical constraints and tree-management overhead associated with standard Parametric CAD modeling.

## Current Goals
- ~~Replace the legacy `libfive` C++ dependency with a native NumPy-based SDF evaluation and meshing solution to avoid complex compilation and dependency issues.~~ ✅ Done — `sdf_mesher.py` is pure NumPy + Surface Nets + QEF.
- ~~Fix all remaining crashes and segfaults caused by Python/C++ boundary errors or FreeCAD UI double-frees.~~ ✅ Done — all event callbacks are deferred via `QTimer.singleShot(0, ...)` to safely run outside Coin3D event traversal.
- ~~Provide interactive 3D view dragging for shape creation and dimensioning.~~ ✅ Done — `base.py` provides `get_point_on_plane`, orthographic/perspective support, and live mesh preview via `_process_preview_queue`.
- Stabilize the core primitive creation tools (Box, Sphere, Cone, Torus).
- Implement Boolean operations (Fuse, Cut, Common) that do not needlessly convert to BRep until explicitly requested.

## Todo List
- [ ] **Box Primitive**: Fix the crash that occurs when finishing the box creation.
- [ ] **Cone Primitive**: Fix the bug preventing the cone from being drawn/rendered properly.
- [ ] **Torus Primitive**: Resolve visual and rendering issues specific to the toroid.
- [ ] **SDF Base Operations**: Consolidate common meshing and object spawning logic across all primitives to ensure consistent behavior.
- [ ] **Boolean Operations**: Implement non-destructive (or pure-mesh) boolean operations utilizing the SDF primitives before converting to final BRep forms.
- [ ] **UI TaskPanels**: Complete the standardization of the property panels for all shapes.
- [ ] **Sketcher Integration**: Add an icon/toolbar button and command (`DM_OpenSketcher`) to launch the FreeCAD Sketcher workbench, allowing users to define sketch profiles that can then drive SDF extrusions or serve as cutting planes.

## Meshing — Known Issue
The preview mesh is generated correctly by `_process_preview_queue()` in `base.py` (verts/tris counts are non-zero as confirmed by debug logs), but the FreeCAD `ViewObject` does not always visually update when `.Mesh` is replaced in-place. The current fix attempts:
1. `self._preview_obj.Mesh = mesh` — direct property assignment
2. `self._preview_obj.purgeTouched()`
3. `self._preview_obj.ViewObject.update()`
4. `FreeCADGui.updateGui()`

**Root cause**: FreeCAD's built-in `Mesh::Feature` view provider may not repaint when `.Mesh` is replaced without a `recompute()`. However, calling `doc.recompute()` during the event loop risks re-entering Coin3D. The safest fix is to call `doc.recompute()` **also** deferred via `QTimer.singleShot(0, ...)` after assigning the mesh, rather than skipping it entirely.

**Proposed fix**: In `_process_preview_queue()`, after assigning `.Mesh`, schedule a deferred `doc.recompute()` instead of relying solely on `ViewObject.update()`.

## Terminology

### Box / Primitive Creation — 3-Click Flow
| Click | Term | Description |
|-------|------|-------------|
| 1st click | **Place** | Set the origin corner of the shape on the working plane. |
| 2nd click | **Size** | Lock the base footprint (length × width). Dragging now controls height. |
| 3rd click | **Set** | Commit ("set") the shape to the document at the current dimensions. The tool closes, the preview is removed, and the final object is created. |

The **Set** action finalizes the shape. The term is chosen to mirror physical direct-modeling — you place, size, then *set* the piece in position.

## General Notes
- **SDF Mesher**: The project now uses a custom `sdf_mesher.py` (NumPy + Surface Nets + QEF) instead of `libfive`. This generates vertices and triangles from distance functions.
- **Preview vs Final**: During creation, a lightweight preview mesh is shown via a temporary `Mesh::FeaturePython` object (`SDF_Preview`). This allows for native FreeCAD rendering without external dependencies. Upon completion, the preview is either relabeled or a new `Mesh::FeaturePython` (`SDFObject`) is created.
- **Logging & Debugging**: 
  - ALWAYS use the centralized logger: `from FCDirectModeling import sdf_logger`.
  - Use `sdf_logger.debug()`, `sdf_logger.info()`, etc.
  - This logs to both the FreeCAD console and `~/sdf_debug.log`. 
  - Using a physical log file is mandatory for debugging segfaults to ensure the last Breadcrumb is captured.

- **FreeCAD API**: Proceed with caution when dealing with `FreeCADGui.Control.closeDialog()`, `Part` conversions, and `Mesh` instantiation, as FreeCAD's C++ back-end is prone to segfaulting on improperly typed Python arguments or double-frees.
- **Coordinate Systems**: The `get_point_on_plane` and related functions translate screen mouse coordinates into 3D world/local space to provide a 1:1 sketching feel. Always mindful of local vs global coordinates when generating SDF geometry off a working plane.
- **Event Safety**: ALL scene-graph and document-mutating operations (mesh updates, object creation, dialog closes) MUST be deferred via `QtCore.QTimer.singleShot(0, fn)`. Never mutate the document from inside a Coin3D event callback directly.


## Future Work
### Rendering
Consider rendering options for higher quality display of SDF objects.

### Curve Extraction from SDF
- **2D** — Adaptive contouring: Recursively subdivide cells where the sign changes, fitting Bézier or Catmull‑Rom segments to the local zero‑crossing.
- **3D** — Implicit surface to spline conversion: Sample the SDF on a sparse grid, compute Hermite data (position + gradient) at zero‑crossings, then fit B‑splines or T‑splines to those data points using least‑squares or variational methods.
- **3D** — Level‑set to CSG: Approximate the SDF by a hierarchy of primitive primitives (spheres, cylinders, boxes) using optimization or greedy fitting. The resulting CSG tree can be exported as a B‑Rep.
- **3D** — Direct analytic extraction: For SDFs with a known closed‑form (e.g., sphere, torus, super‑ellipsoid), derive the exact parametric equations and output them as NURBS or analytic patches.