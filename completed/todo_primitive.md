# Direct Modeling Workbench — Primitive Additive/Subtractive & Double-Click Edit Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The workbench currently creates all SDF primitives as additive (orange, `vec3(1.0, 0.5, 0.0)`). There is no concept of subtractive primitives. All fields render in the same hardcoded color in the GLSL fragment shader.

This task list adds:

1. **Additive/Subtractive mode** — a `IsSubtractive` boolean property on each frep object. Additive = orange, subtractive = blue. During interactive placement, holding Ctrl switches the preview to subtractive.
2. **Per-field color in the shader** — a `u_is_subtractive[8]` uniform array so each field renders in its own color.
3. **Double-click to re-edit** — double-clicking an SDF primitive (when no tool is active) selects it and opens `FRepEditTool` for interactive corner-drag editing.

Goal state: users can place additive and subtractive primitives via Ctrl-toggle, see them in different colors, and double-click any primitive to re-enter edit mode.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `DMObjectProxy.__init__` | `core/dm_object.py:168` | Adds FreeCAD properties for frep objects |
| `create_dm_object` | `core/dm_object.py:550` | Factory for new DM objects |
| `DMSceneRayMarchRenderer._setup_nodes` | `core/dm_scene_ray_march_renderer.py:73` | Creates shader + uniforms |
| `DMSceneRayMarchRenderer._rebuild` | `core/dm_scene_ray_march_renderer.py:480` | Uploads per-field data to GPU |
| `PrimitiveCreatorBase.update_preview` | `tools/primitive_tool.py:51` | Called on every mouse move |
| `PrimitiveCreatorBase._apply_preview_field` | `tools/primitive_tool.py:76` | Applies field to preview object |
| `PrimitiveCreatorBase._finalize_object` | `tools/primitive_tool.py:92` | Commits the preview as final |
| `DMInputManager.is_ctrl_down` | `core/input_manager.py:202` | Returns Ctrl key state |
| `DMInputManager.eventFilter` | `core/input_manager.py:30` | Qt event filter (hotkeys, selection) |
| `FRepEditTool` | `tools/edit_tool.py:483` | Existing corner-drag edit tool for frep |
| `DMSelectionManager.try_sdf_selection` | `core/dm_selection_manager.py:19` | Selects SDF object under cursor |

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `dm_additive_subtractive` | Additive/subtractive property, shader uniform, Ctrl toggle, double-click edit pattern |
| `dm_primitive_tool` | Standardized pattern for interactive F-Rep primitive tools |
| `dm_scene_ray_march` | Scene-level ray march renderer architecture |
| `dm_event_pipeline` | Qt + Coin3D dual event pipeline |

---

## Tier 1 — Object Property (Do First)

Add the `IsSubtractive` property to frep objects. All later tiers depend on this.

### PR-001: Add `IsSubtractive` property to DMObjectProxy for frep objects

**File:** `core/dm_object.py` — inside `DMObjectProxy.__init__`, after the `DeduplicateEnabled` property block (line 269)

**What:** Add a boolean `IsSubtractive` property that defaults to `False`. This property is stored on the FreeCAD object and persists across saves.

**Implementation:**

Find this block at the end of the frep property section:

```python
            if not hasattr(obj, "DeduplicateEnabled"):
                obj.addProperty("App::PropertyBool", "DeduplicateEnabled", "FRep", "Enable vertex deduplication")
                obj.DeduplicateEnabled = get_deduplicate_enabled()
```

Immediately after it, add:

```python
            if not hasattr(obj, "IsSubtractive"):
                obj.addProperty("App::PropertyBool", "IsSubtractive", "FRep",
                                "If True, this primitive subtracts material (rendered blue)")
                obj.IsSubtractive = False
```

---

## Tier 2 — Shader Per-Field Color

Wire the `IsSubtractive` flag through to the GPU so each field renders in the correct color.

### PR-002: Add `u_is_subtractive` uniform array to scene ray march renderer

**File:** `core/dm_scene_ray_march_renderer.py` — inside `_setup_nodes()`, after the `per_field_vec3` list (line 355)

**What:** Add `u_is_subtractive` as a per-field integer uniform array (8 elements), and register it with the fragment shader.

**Implementation:**

1. Find this line in `_setup_nodes()`:

```python
        per_field_vec3 = ["u_bbox_min", "u_bbox_max"]
```

Add `u_is_subtractive` to the per-field int list. Change:

```python
        per_field_int  = ["u_nx", "u_ny", "u_nz", "u_z_offset"]
```

to:

```python
        per_field_int  = ["u_nx", "u_ny", "u_nz", "u_z_offset", "u_is_subtractive"]
```

This automatically creates `u_is_subtractive[0]` through `u_is_subtractive[7]` as `SoShaderParameter1i` nodes in the loop below, and registers them with the fragment shader.

**Depends on:** PR-001

---

### PR-003: Add `u_is_subtractive` declaration to fragment shader

**File:** `core/dm_scene_ray_march_renderer.py` — inside the fragment shader string in `_setup_nodes()`, after line `uniform vec3  u_bbox_max[8];` (around line 163 in the GLSL string)

**What:** Declare the `u_is_subtractive` uniform in GLSL so the shader can read each field's mode.

**Implementation:**

Find this line in the fragment shader string:

```glsl
uniform vec3  u_bbox_max[8];
```

Add immediately after it:

```glsl
uniform int   u_is_subtractive[8];
```

**Depends on:** PR-002

---

### PR-004: Use per-field color in fragment shader shading

**File:** `core/dm_scene_ray_march_renderer.py` — inside the fragment shader `main()`, replace the hardcoded color line (the line containing `vec3(1.0,0.5,0.0)`)

**What:** Replace the hardcoded orange color with a conditional based on `u_is_subtractive[hit_field]`.

**Implementation:**

Find this line in the fragment shader:

```glsl
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;
```

Replace it with:

```glsl
    vec3 base_color = (u_is_subtractive[hit_field] == 1)
        ? vec3(0.3, 0.5, 1.0)
        : vec3(1.0, 0.5, 0.0);
    vec3 color = base_color * (0.15 + 0.75 * diff) + vec3(0.4) * spec;
```

**Depends on:** PR-003

---

### PR-005: Upload `IsSubtractive` flag in `_rebuild()`

**File:** `core/dm_scene_ray_march_renderer.py` — inside `_rebuild()`, in the per-field uniform upload loop (after the `u_bbox_max` upload, around line 537)

**What:** Read the FreeCAD object's `IsSubtractive` property and upload it as the `u_is_subtractive[fi]` uniform for each visible field.

**Implementation:**

Find this block inside the `for fi in range(self.MAX_FIELDS):` loop in `_rebuild()`:

```python
            if fi < n_fields:
                b = baked_list[fi]
                mn, mx = b["bbox_min"], b["bbox_max"]
                self._u[f"u_nx[{fi}]"].value.setValue(int(b["nx"]))
                self._u[f"u_ny[{fi}]"].value.setValue(int(b["ny"]))
                self._u[f"u_nz[{fi}]"].value.setValue(int(b["nz"]))
                self._u[f"u_z_offset[{fi}]"].value.setValue(int(z_offsets[fi]))
                self._u[f"u_bbox_min[{fi}]"].value.setValue(
                    coin.SbVec3f(mn.x, mn.y, mn.z))
                self._u[f"u_bbox_max[{fi}]"].value.setValue(
                    coin.SbVec3f(mx.x, mx.y, mx.z))
```

After the `u_bbox_max` line, add:

```python
                # Per-field subtractive flag
                label = visible[fi][0]
                doc = FreeCAD.activeDocument()
                field_obj = doc.getObject(label) if doc else None
                is_sub = getattr(field_obj, "IsSubtractive", False) if field_obj else False
                self._u[f"u_is_subtractive[{fi}]"].value.setValue(1 if is_sub else 0)
```

**Depends on:** PR-003

---

### PR-006: Trigger shader rebuild when `IsSubtractive` changes

**File:** `core/dm_object.py` — inside `DMViewProvider.updateData()` (line 447), add a handler for the `IsSubtractive` property

**What:** When the `IsSubtractive` property changes on a frep object, trigger a scene renderer rebuild so the color updates immediately.

**Implementation:**

Find this block at the top of `updateData`:

```python
        if prop in ["ShowWireframe", "MeshingCellSize"]:
            self.on_prefs_changed()
```

Change it to:

```python
        if prop in ["ShowWireframe", "MeshingCellSize", "IsSubtractive"]:
            self.on_prefs_changed()
```

This calls `DMSceneRayMarchRenderer.on_prefs_changed()` which triggers a rebuild, causing `_rebuild()` to re-read the `IsSubtractive` property and update the uniform.

**Depends on:** PR-005

---

### PR-007: Ensure `on_prefs_changed` triggers a full rebuild

**File:** `core/dm_scene_ray_march_renderer.py` — inside `on_prefs_changed()` (line 469)

**What:** The current `on_prefs_changed()` only updates debug mode. It needs to also call `_rebuild()` so that property changes (like `IsSubtractive`) are reflected in the shader uniforms.

**Implementation:**

Find the `on_prefs_changed` method:

```python
    def on_prefs_changed(self):
        """Update renderer based on global prefs."""
        from core.dm_object import get_render_debug_mode
        debug = get_render_debug_mode()
        self._bbox_switch.whichChild = 0 if debug else -1
        # Also sync shader debug mode if we're in debug
        self.set_debug_mode(3 if debug else 0) # Use iterations mode by default for debug
```

Add at the end of this method:

```python
        # Rebuild to pick up any per-field property changes (e.g. IsSubtractive)
        if self._fields:
            self._rebuild()
```

**Depends on:** PR-005

---

## Tier 3 — Ctrl Toggle During Placement

Allow the user to hold Ctrl during drag to switch the preview between additive and subtractive.

### PR-008: Update preview object's `IsSubtractive` based on Ctrl key state

**File:** `tools/primitive_tool.py` — inside `PrimitiveCreatorBase._apply_preview_field()` (line 76)

**What:** On every preview update, read the Ctrl key state and set the preview object's `IsSubtractive` property accordingly. This gives real-time visual feedback during placement.

**Implementation:**

Find the `_apply_preview_field` method:

```python
    def _apply_preview_field(self, field):
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            proxy = self._preview_obj.Proxy
            if proxy is None:
                return
            proxy.FRepField = field
            self._preview_obj.touch()
            # Only recompute this one object for speed
            self._preview_obj.Document.recompute([self._preview_obj])
```

Replace it with:

```python
    def _apply_preview_field(self, field):
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            proxy = self._preview_obj.Proxy
            if proxy is None:
                return
            proxy.FRepField = field
            # Sync additive/subtractive mode with Ctrl key
            im = DMInputManager.get_instance()
            if hasattr(self._preview_obj, "IsSubtractive"):
                self._preview_obj.IsSubtractive = im.is_ctrl_down()
            self._preview_obj.touch()
            # Only recompute this one object for speed
            self._preview_obj.Document.recompute([self._preview_obj])
```

Note: `DMInputManager` is already imported at the top of `primitive_tool.py` (line 8).

**Depends on:** PR-006

---

## Tier 4 — Double-Click to Re-Edit

Allow double-clicking an SDF primitive to re-open it in edit mode.

### PR-009: Handle double-click in `DMInputManager.eventFilter`

**File:** `core/input_manager.py` — inside `eventFilter()`, after the LMB selection block (after line 63, the comment "Do NOT return True")

**What:** Detect `QEvent.MouseButtonDblClick` with `Qt.LeftButton` when no tool is active. If an SDF frep object is under the cursor, select it and launch `FRepEditTool`.

**Implementation:**

Find this block:

```python
            # [Event Owner: DMSelectionManager] SDF object selection on LMB press when no tool is active
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                from core.dm_tool_manager import DMToolManager
                if not DMToolManager.get_instance().has_active_tool():
                    from core.dm_selection_manager import DMSelectionManager
                    DMSelectionManager.get_instance().try_sdf_selection(
                        (event.pos().x(), event.pos().y())
                    )
                    # Do NOT return True — let FreeCAD's navigation also handle this click
```

Immediately after this block, add:

```python
            # [Event Owner: Qt Event Filter] Double-click to edit SDF object
            if event.type() == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
                from core.dm_tool_manager import DMToolManager
                if not DMToolManager.get_instance().has_active_tool():
                    from core.dm_selection_manager import DMSelectionManager
                    sel_mgr = DMSelectionManager.get_instance()
                    sel_mgr.try_sdf_selection((event.pos().x(), event.pos().y()))
                    sel = FreeCADGui.Selection.getSelection()
                    if sel:
                        obj = sel[0]
                        proxy_name = getattr(getattr(obj, "Proxy", None), "__class__", type(None)).__name__
                        if proxy_name == "DMObjectProxy" and getattr(obj, "ShapeType", "") == "frep":
                            from tools.edit_tool import FRepEditTool
                            tool = FRepEditTool()
                            tool.activate()
                            return True
```

---

## Tier 5 — Polish

### PR-010: Update `create_dm_object` to set ShapeColor based on `IsSubtractive`

**File:** `core/dm_object.py` — inside `create_dm_object()`, after the line `obj.ViewObject.ShapeColor = (1.0, 0.5, 0.0)` (line 597)

**What:** Set the FreeCAD ViewObject's `ShapeColor` to blue if the object is subtractive, so the color is correct in the object tree icon and any fallback rendering paths.

**Implementation:**

Find:

```python
            obj.ViewObject.ShapeColor = (1.0, 0.5, 0.0)
```

Replace with:

```python
            is_sub = getattr(obj, "IsSubtractive", False)
            if is_sub:
                obj.ViewObject.ShapeColor = (0.3, 0.5, 1.0)
            else:
                obj.ViewObject.ShapeColor = (1.0, 0.5, 0.0)
```

**Depends on:** PR-001

---

### PR-011: Update ShapeColor when `IsSubtractive` toggles on existing object

**File:** `core/dm_object.py` — inside `DMViewProvider.updateData()` (line 447), add handling for `IsSubtractive` property changes to also update the ViewObject color

**What:** When `IsSubtractive` changes, update `vobj.ShapeColor` to match the new mode so the object tree icon reflects the correct color.

**Implementation:**

Find the line added in PR-006:

```python
        if prop in ["ShowWireframe", "MeshingCellSize", "IsSubtractive"]:
            self.on_prefs_changed()
```

Add immediately after it:

```python
        if prop == "IsSubtractive" and hasattr(fp, "IsSubtractive"):
            try:
                if fp.IsSubtractive:
                    fp.ViewObject.ShapeColor = (0.3, 0.5, 1.0)
                else:
                    fp.ViewObject.ShapeColor = (1.0, 0.5, 0.0)
            except Exception:
                pass
```

**Depends on:** PR-006

---

## Summary

| Task | What | File |
|------|------|------|
| PR-001 | `IsSubtractive` property | `core/dm_object.py` |
| PR-002 | Uniform array registration | `core/dm_scene_ray_march_renderer.py` |
| PR-003 | GLSL uniform declaration | `core/dm_scene_ray_march_renderer.py` |
| PR-004 | GLSL per-field color | `core/dm_scene_ray_march_renderer.py` |
| PR-005 | Upload flag in `_rebuild()` | `core/dm_scene_ray_march_renderer.py` |
| PR-006 | Trigger rebuild on property change | `core/dm_object.py` |
| PR-007 | `on_prefs_changed` calls `_rebuild` | `core/dm_scene_ray_march_renderer.py` |
| PR-008 | Ctrl toggle during placement | `tools/primitive_tool.py` |
| PR-009 | Double-click to re-edit | `core/input_manager.py` |
| PR-010 | ShapeColor on creation | `core/dm_object.py` |
| PR-011 | ShapeColor on toggle | `core/dm_object.py` |
