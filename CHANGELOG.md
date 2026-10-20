# Changelog

All notable changes to the FreeCAD Fields workbench will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Releases are named by month and year (e.g. 9-26); the manifest version is YY.M.D, the release date (e.g. 26.10.20).
Releases before 9-26 used semantic versioning.

## [10-26] - 2026-10-20

Version 26.10.20.

> **Breaking:** documents saved in 9-26 that depend on removed backward-compatibility shims will not load, and preferences from previous releases are not carried over. Sculpt layer sidecars are renamed from `.dmsculpt` to `.fldsculpt`.

### Security
- Formulas saved in documents (the Noise `Formula` and Array `*Formula` properties) could execute arbitrary Python code when a document opened. All formula evaluation sites are now strictly AST-validated via `safe_compile` before compilation.

### Added
- **Universal Transform Gizmo:** interactive axis, plane, rotation, and uniform scale handles for primitive creators, work plane, noise, heightmap, array, slicer, cage, and curve editors.
- **Context Menu Tree:** tool operations and shortcuts accessible via right-click in the viewport with visible shortcut keys.
- **Matcap Shading & Material Library:** real-time matcap preview and editable material variants.
- **Panel Modifier:** parametric wooden board panel modifier (`Fields_Panel`) filling part bounds with automatic or typed count and spacing.
- **Slice on Panel:** slicing tool integration directly referencing panel plane layout.
- **Typed Numeric Values:** direct numeric entry for sliders and gizmo drag dimensions with readout display.
- **Noise Node Editor & Graph:** visual nodal formula editor for procedural noise with world-space node vocabulary (Part, Direction Transform, Origin, Polar, Vector Math, Surface Normal, Front Face) and strict AST-validated sandboxing.

### Changed
- Preferences moved to one group named after the package: `User parameter:BaseApp/Preferences/fields` (keymap under `fields/Keymap`). Values in the old `Preferences/Mod/Fields` and `FCFields` groups are not carried over.
- Sculpt layer sidecar directory renamed from `.dmsculpt` to `.fldsculpt`.
- Shader debug dump files are only written when the `render` debug category is enabled and are placed beside `Fields.log`.

### Fixed
- Fixed torus primitive reload anchor height so tori reload at their drawn height resting on the workplane.
- Populated `ControlNodes` property immediately upon curve object creation.

### Removed
- Backward-compatibility shims for old documents: the `IsSubtractive` → `Group`
  migration, the `patch_assembly` field type, the `VoxelData` → sidecar migration, the
  Array `Linear`/`Radial` mode and `Radial*`/`OverlapSafe` properties, and the
  `fld_object.FldObjectProxy` import alias. Documents that still depend on these no
  longer load.

## [9-26] - 2026-09-27

Version 26.9.27.

### Changed
- Renamed workbench from **Direct Modeling** to **Fields** (`FieldsWorkbench`).
- Python package root moved from `freecad.directmodeling` to `freecad.fields`.
- Class prefix renamed from `DM` to `Fld` (`FldObjectProxy`, `FldViewProvider`, etc.).
- Module file prefix renamed from `dm_` to `fld_`.
- **Breaking:** documents saved by the old **Direct Modeling** workbench do not
  restore under this release. Scripted objects persist their `Proxy` by module
  path, and saved documents still name `freecad.directmodeling.*`, which no
  longer exists — FreeCAD reports `ModuleNotFoundError` per object on load and
  the parts come back with dead proxies.
- Preferences parameter groups migrated: `Mod/DirectModeling` -> `Mod/Fields` (ray-march
  and tolerance settings) and the separate top-level `FCDirectModeling` -> `FCFields`
  (crash log and debug categories). Both migrate on first read.
- Crash log renamed from `DirectModeling.log` to `Fields.log`.
- Toolbar icons with FreeCAD-generic names (`CreateBox`, `MakeAdd`, `EditTool`,
  etc.) renamed with a `Fields_` prefix so they cannot collide with other
  addons in FreeCAD's global icon search path.
- `FreeCADGui.addIconPath()` moved out of module scope into `Initialize()`, so
  the workbench no longer affects icon lookup unless it is activated.
- Added explicit `__init__.py` to the two packages that previously relied on
  implicit PEP 420 namespace packages (`freecad.fields.core.sdf` and
  `freecad.fields.core.sdf.sdf`).

### Removed
- Dead `Resources/resources.qrc` (never loaded by any code path, and stale —
  it still referenced a pre-rename icon file).
- `Resources/icons/SketcherWorkbench.svg` and `Resources/icons/OpenSketcher.svg`,
  which duplicated/shadowed a built-in FreeCAD icon and were never referenced,
  respectively.

## [1.0.0] - 2026-08-11

### Added
- Real-time NURBS and BRep direct modeling driven by GPU-accelerated signed distance fields (SDF).
- Cage-based deformation with half-edge subdivision topology.
- GPU voxel scene renderer with incremental region rebake.
- SDF-to-mesh export, marching-squares slicer, and CAM-ready profile export.
- Addon manifest `package.xml`.
