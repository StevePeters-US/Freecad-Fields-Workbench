# Changelog

All notable changes to the FreeCAD Fields workbench will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
