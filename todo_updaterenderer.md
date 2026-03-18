# todo_updaterenderer.md — Renderer Refactor & Meshing Settings Isolation

Tasks for AI coding agent (Gemini Flash).
Read `.agents/skills/dm_renderer_refactor/SKILL.md` before starting any task.

---

## Group A — Add RayMarchCellSize Setting + Fix Normal Kernel

### UR-001: Add `get/set_ray_march_cell_size` to `core/dm_object.py`

**File:** `core/dm_object.py`

After `get_near_clip_distance()` / `set_near_clip_distance()` (around line 136), add:

```python
def get_ray_march_cell_size():
    """Return the SDF baking cell size for GPU ray march renderer in mm (default 2.0)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("RayMarchCellSize", 2.0)

def set_ray_march_cell_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("RayMarchCellSize", float(val))
```

---

### UR-002: Use `get_ray_march_cell_size()` in scene ray march renderer

**File:** `core/dm_scene_ray_march_renderer.py`

Find line ~440 in `_bake_and_upload()`:
```python
from core.dm_object import get_meshing_cell_size
...
cell_size = get_meshing_cell_size()
```

Replace with:
```python
from core.dm_object import get_ray_march_cell_size
...
cell_size = get_ray_march_cell_size()
```

---

### UR-003: Use `get_ray_march_cell_size()` in single-object ray march renderer

**File:** `core/dm_ray_march_renderer.py`

Find the `update(self, field, cell_size)` call sites. Search for any call that passes
`get_meshing_cell_size()` as the `cell_size` argument and replace with
`get_ray_march_cell_size()`. Also update any internal default if present.

---

### UR-004: Fix normal kernel in single-object shader

**File:** `core/dm_ray_march_renderer.py`

In the embedded fragment shader string, find `sdf_normal()`:
```glsl
float h = cell * 0.5;
```
Change to:
```glsl
float h = cell * 2.0;
```

**Rationale:** Trilinear interpolation is C0 (not C1). Sampling at ±0.5 cells produces
sharp gradient discontinuities at cell boundaries, creating visible flat-shaded facets.
Sampling at ±2.0 cells averages across boundaries and produces smooth normals.

---

### UR-005: Fix normal kernel in scene shader

**File:** `core/dm_scene_ray_march_renderer.py`

In the embedded fragment shader string, find `sdf_normal_field()`:
```glsl
float h = cell * 0.5;
```
Change to:
```glsl
float h = cell * 2.0;
```

Same rationale as UR-004.

---

### UR-006: Add "Ray March Resolution" spinbox to settings dialog

**File:** `commands/cmd_settings.py`

1. In `__init__`: add import of `get_ray_march_cell_size`. Add a `QDoubleSpinBox`:
   ```python
   from core.dm_object import get_ray_march_cell_size
   self._rm_res_spin = QtGui.QDoubleSpinBox()
   self._rm_res_spin.setRange(0.1, 20.0)
   self._rm_res_spin.setSingleStep(0.5)
   self._rm_res_spin.setDecimals(1)
   self._rm_res_spin.setValue(get_ray_march_cell_size())
   self._rm_res_spin.setToolTip(
       "SDF baking resolution for GPU ray march renderer in mm.\n"
       "Smaller = smoother surface, higher GPU memory. Default: 2.0 mm."
   )
   layout.addRow("Ray March Resolution (mm):", self._rm_res_spin)
   ```
   Place this row after "Near Clip Distance" and before the button box.

2. In `_on_accept`: add import of `set_ray_march_cell_size` and call:
   ```python
   from core.dm_object import set_ray_march_cell_size
   set_ray_march_cell_size(self._rm_res_spin.value())
   ```

---

## Group B — Remove Meshing Settings from `core/dm_object.py`

### UR-007: Remove meshing setting functions from `core/dm_object.py`

**File:** `core/dm_object.py`

Remove the following functions entirely (they are only needed in the SDF-to-mesh tool,
which will define them locally or receive them as parameters):

- `get_meshing_type()` and `set_meshing_type(val)` (~lines 50-61)
- `get_meshing_cell_size()` and `set_meshing_cell_size(val)` (~lines 63-68)
- `get_frep_storage_type()` and `set_frep_storage_type(val)` (~lines 70-74)  ← deprecated aliases
- `get_curvature_threshold()` and `set_curvature_threshold(val)` (~lines 100-105)
- `get_decimate_enabled()` and `set_decimate_enabled(val)` (~lines 107-112)
- `get_deduplicate_enabled()` and `set_deduplicate_enabled(val)` (~lines 114-119)

**Do NOT remove:** `get_show_wireframe`, `get_line_width`, `get_point_size`,
`get_picking_radius`, `get_max_bounds`, `get_perf_profiler_enabled`,
`get_near_clip_distance`, `get_interactive_throttle_interval`, `get_ray_march_cell_size`
(added in UR-001), `get_render_debug_mode`.

---

### UR-008: Remove meshing FRep object properties from `DMObjectProxy.__init__`

**File:** `core/dm_object.py`

In `DMObjectProxy.__init__`, inside the `if shape_type == "frep":` block (~lines 248-273),
remove the property additions for:
- `MeshingCellSize` (PropertyFloat)
- `MeshingType` (PropertyEnumeration)
- `DecimateEnabled` (PropertyBool)
- `DeduplicateEnabled` (PropertyBool)

Keep only `IsSubtractive`.

The frep block should be reduced to just:
```python
if shape_type == "frep":
    if not hasattr(obj, "IsSubtractive"):
        obj.addProperty("App::PropertyBool", "IsSubtractive", "FRep",
                        "If True, this primitive subtracts material (rendered blue)")
        obj.IsSubtractive = False
```

---

### UR-009: Remove meshing updates from `refresh_all_dm_objects()`

**File:** `core/dm_object.py`

In `refresh_all_dm_objects()` (~lines 634-671), remove:
- `res = get_meshing_cell_size()` local variable
- `show_wire = get_show_wireframe()` — KEEP this one
- `m_type = get_meshing_type()` local variable
- The per-object updates for `MeshingCellSize`, `ShowWireframe` (KEEP), `MeshingType`,
  `DecimateEnabled`, `DeduplicateEnabled`

After: `refresh_all_dm_objects()` should only update `LineWidth`, `PointSize`,
`ShowWireframe`, and call `on_prefs_changed()` on each view proxy.

---

### UR-010: Remove `MeshingCellSize` from `DMViewProvider.updateData`

**File:** `core/dm_object.py`

Find `DMViewProvider.updateData` (~line 457):
```python
if prop in ["ShowWireframe", "MeshingCellSize", "IsSubtractive"]:
    self.on_prefs_changed()
```

Remove `"MeshingCellSize"` from the list:
```python
if prop in ["ShowWireframe", "IsSubtractive"]:
    self.on_prefs_changed()
```

---

## Group C — Remove Meshing Settings from Settings Dialog

### UR-011: Remove meshing controls from `commands/cmd_settings.py`

**File:** `commands/cmd_settings.py`

**In `__init__`:**
Remove imports of:
- `get_meshing_type`
- `get_meshing_cell_size`
- `get_curvature_threshold`
- `get_decimate_enabled`

Remove UI widgets:
- `self._meshing_combo` (Meshing Type ComboBox) and its `layout.addRow`
- `self._res_spin` (Meshing Cell Size spinbox) and its `layout.addRow`
- `self._curv_spin` (Curvature Threshold spinbox) and its `layout.addRow`
- `self._decimate_check` (Enable Decimation checkbox) and its `layout.addRow`

Remove local variables:
- `current_meshing = get_meshing_type()`
- `current_curv = get_curvature_threshold()`

**In `_on_accept`:**
Remove imports/calls of:
- `set_meshing_type`
- `set_meshing_cell_size`
- `set_curvature_threshold`
- `set_decimate_enabled`
- `meshing = self._meshing_combo.currentIndex()`
- `res = self._res_spin.value()`
- `curv = self._curv_spin.value()`
- `set_meshing_type(meshing)`
- `set_meshing_cell_size(self._res_spin.value())`
- `set_curvature_threshold(curv)`
- `set_decimate_enabled(self._decimate_check.isChecked())`

Also clean up the `dm_logger.info(...)` call at the end — remove `meshing=`, `res=`,
`curv=` from the format string.

Update the docstring at the top of the file:
```python
"""
DM Settings Command — configure renderer and UI settings.
"""
```

---

## Group D — Update `core/dm_mesher.py` to Not Read Global Settings

### UR-012: Pass decimate/deduplicate as parameters in `core/dm_mesher.py`

**File:** `core/dm_mesher.py`

Currently `MarchingCubesMesher.mesh()` (and other meshers) call `get_decimate_enabled()`
and `get_deduplicate_enabled()` directly.

1. Remove the imports of `get_decimate_enabled` and `get_deduplicate_enabled` from
   `core.dm_object` at the top of `dm_mesher.py`.

2. Update the `DMMesher.mesh()` abstract base signature to accept optional flags:
   ```python
   def mesh(self, field, cell_size, decimate=True, deduplicate=True):
   ```

3. In `MarchingCubesMesher.mesh()`, replace:
   ```python
   if get_decimate_enabled():
       flat_verts, flat_idx = decimate_flat_tris(flat_verts, flat_idx)
   if get_deduplicate_enabled():
       flat_verts, flat_idx = deduplicate_verts(flat_verts, flat_idx)
   ```
   With:
   ```python
   if decimate:
       flat_verts, flat_idx = decimate_flat_tris(flat_verts, flat_idx)
   if deduplicate:
       flat_verts, flat_idx = deduplicate_verts(flat_verts, flat_idx)
   ```

4. Apply the same parameter threading to `AdaptiveMCMesher.mesh()`,
   `SurfaceNetsMesher.mesh()`, `DualContouringMesher.mesh()`.

---

### UR-013: Remove `get_meshing_type()` from `get_active_mesher()` in `core/dm_mesher.py`

**File:** `core/dm_mesher.py`

In `get_active_mesher(type_override=None)` (~line 1323):

Remove the global fallback that calls `get_meshing_type()`. The `type_override`
parameter is now required. If `type_override is None`, default to index 0
(MarchingCubes) without reading any global setting:

```python
def get_active_mesher(type_override=None):
    """Return the active mesher instance. type_override selects algorithm."""
    idx = type_override if type_override is not None else 0
    # ... rest of dispatch logic unchanged ...
```

Remove the `from core.dm_object import get_meshing_type` import if present.

---

## Group E — Update SDF-to-Shape Tool to Own Its Settings

### UR-014: Add local settings dialog to `commands/cmd_sdf_export.py`

**File:** `commands/cmd_sdf_export.py`

Replace the current `Activated()` method which reads `get_meshing_cell_size()` and
`obj.MeshingCellSize` with one that shows a small dialog first:

```python
def Activated(self):
    sel = FreeCADGui.Selection.getSelection()
    if len(sel) != 1:
        dm_logger.error("SDF to Shape: Select exactly one F-Rep object.")
        return

    obj = sel[0]
    proxy = getattr(obj, "Proxy", None)
    field = getattr(proxy, "FRepField", None) if proxy else None
    if field is None:
        dm_logger.error(f"SDF to Shape: '{obj.Label}' has no FRepField.")
        return

    dlg = _SDFToShapeDialog(FreeCADGui.getMainWindow())
    if dlg.exec_() != QtGui.QDialog.Accepted:
        return

    cell_size  = dlg.cell_size()
    m_type     = dlg.meshing_type()
    decimate   = dlg.decimate()
    deduplicate = dlg.deduplicate()

    try:
        from core.dm_mesher import get_active_mesher
        mesher = get_active_mesher(type_override=m_type)
        dm_logger.info(f"SDF to Shape: meshing '{obj.Label}' at {cell_size:.2f} mm...")
        result = mesher.mesh(field, cell_size=cell_size,
                             decimate=decimate, deduplicate=deduplicate)
        if result is None:
            dm_logger.error("SDF to Shape: mesher returned None.")
            return

        flat_verts, flat_idx = result
        shape = _triangles_to_shape(flat_verts, flat_idx)
        if shape is None or shape.isNull():
            dm_logger.error("SDF to Shape: failed to build Part.Shape from mesh.")
            return

        doc = FreeCAD.activeDocument()
        new_obj = doc.addObject("Part::Feature", f"{obj.Label}_Mesh")
        new_obj.Shape = shape
        if hasattr(new_obj, "ViewObject") and new_obj.ViewObject:
            new_obj.ViewObject.ShapeColor = (1.0, 0.5, 0.0)
            try:
                new_obj.ViewObject.DisplayMode = "Shaded"
            except Exception:
                pass
        doc.recompute()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(new_obj)
        n_tris = len(flat_idx) // 4
        dm_logger.info(f"SDF to Shape: created '{new_obj.Label}' ({n_tris} triangles, cell_size={cell_size:.2f} mm)")
    except Exception as e:
        dm_logger.error(f"SDF to Shape failed: {e}")
        import traceback; traceback.print_exc()
```

Also add the `_SDFToShapeDialog` class before `_triangles_to_shape`:

```python
class _SDFToShapeDialog(QtGui.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SDF to Shape — Settings")
        self.setMinimumWidth(280)
        layout = QtGui.QFormLayout(self)

        self._cell_spin = QtGui.QDoubleSpinBox()
        self._cell_spin.setRange(0.01, 100.0)
        self._cell_spin.setSingleStep(0.5)
        self._cell_spin.setDecimals(2)
        self._cell_spin.setValue(2.0)
        self._cell_spin.setToolTip("Triangle mesh resolution in mm. Smaller = more detail, slower.")
        layout.addRow("Cell Size (mm):", self._cell_spin)

        self._type_combo = QtGui.QComboBox()
        self._type_combo.addItems([
            "Marching Cubes",
            "Adaptive Marching Cubes",
            "Surface Nets",
            "Dual Contouring",
        ])
        layout.addRow("Meshing Algorithm:", self._type_combo)

        self._decimate_check = QtGui.QCheckBox()
        self._decimate_check.setChecked(True)
        self._decimate_check.setToolTip("Remove redundant triangles on flat faces")
        layout.addRow("Decimate Flat Triangles:", self._decimate_check)

        self._dedup_check = QtGui.QCheckBox()
        self._dedup_check.setChecked(True)
        self._dedup_check.setToolTip("Merge duplicate vertices to reduce mesh size")
        layout.addRow("Deduplicate Vertices:", self._dedup_check)

        btn_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def cell_size(self):    return self._cell_spin.value()
    def meshing_type(self): return self._type_combo.currentIndex()
    def decimate(self):     return self._decimate_check.isChecked()
    def deduplicate(self):  return self._dedup_check.isChecked()
```

Add `from PySide import QtGui` at the top if not already present.

---

### UR-015: Update tooltip in `cmd_sdf_export.py`

**File:** `commands/cmd_sdf_export.py`

Update `GetResources` tooltip to remove the mention of `MeshingCellSize` property:
```python
'ToolTip': (
    'Generate a triangle mesh from the selected F-Rep object\n'
    'and create a Part.Shape solid.\n\n'
    'Resolution and algorithm are set via a dialog on activation.'
),
```

---

## Group F — Update Other Consumers

### UR-016: Remove meshing settings from `tools/primitive_tool.py`

**File:** `tools/primitive_tool.py`

1. Remove `get_meshing_cell_size` from the import on line 8:
   ```python
   # Before:
   from core.dm_object import create_dm_object, get_meshing_cell_size, get_interactive_throttle_interval
   # After:
   from core.dm_object import create_dm_object, get_interactive_throttle_interval
   ```

2. Remove `_get_final_cell_size()` function (~lines 20-21):
   ```python
   # Remove these two lines:
   def _get_final_cell_size():
       return get_meshing_cell_size()
   ```

3. Find any `obj.MeshingCellSize = ...` assignment (around line 155) and remove it.

4. Find any call to `_get_final_cell_size()` in the tool and remove it
   (if it was used to set cell size on the object at commit time, that line is now dead).

5. `_PREVIEW_CELL_SIZE = 20.0` can stay as-is — it is a local constant only used if
   there is a mesh-based preview path. If no code references it after the above removals,
   delete it too.

---

### UR-017: Remove `get_meshing_cell_size()` from `commands/cmd_sdf_slice.py`

**File:** `commands/cmd_sdf_slice.py`

Find ~line 24 where `get_meshing_cell_size()` is used to initialize a resolution.
Replace with a local constant default of `2.0`:
```python
# Before (approximately):
from core.dm_object import get_meshing_cell_size
...
resolution = get_meshing_cell_size()

# After:
resolution = 2.0   # Default slice resolution in mm
```

Remove the `get_meshing_cell_size` import.

---

### UR-018: Remove `core/frep_mesher.py`

**File:** `core/frep_mesher.py`

This file was a lightweight render-pipeline mesher that predates the GPU ray march renderer.
With GPU ray marching as the sole render path, it is dead code.

1. Search the entire codebase for any `import` of `frep_mesher` or `from core.frep_mesher`:
   - If callers exist: update them to use `core.dm_mesher` instead, then delete the file.
   - If no callers: delete the file directly.

2. Delete `core/frep_mesher.py`.

---

## Completion Checklist

After all tasks:
- [ ] `grep -r "get_meshing_cell_size\|get_meshing_type\|get_decimate_enabled\|get_deduplicate_enabled\|get_curvature_threshold" core/ tools/ commands/` returns no results (except inside `cmd_sdf_export.py` — which should have zero results after UR-014)
- [ ] `grep -r "MeshingCellSize\|MeshingType\|DecimateEnabled\|DeduplicateEnabled" core/ tools/ commands/` returns no results
- [ ] `grep -r "frep_mesher" .` returns no results
- [ ] Settings dialog shows: Show Wireframe, Crash Logs, Performance Profiler, Line Width, Picking Radius, Point Size, Max Bounds, Interactive Throttle, Near Clip Distance, **Ray March Resolution**
- [ ] SDF-to-Shape command shows its own dialog with cell size, algorithm, decimate, deduplicate
- [ ] Ray march renderer uses `get_ray_march_cell_size()` (default 2.0 mm)
- [ ] Normal kernel `h = cell * 2.0` in both shader strings
