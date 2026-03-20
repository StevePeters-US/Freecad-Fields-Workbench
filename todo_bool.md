# Direct Modeling Workbench — Boolean Tool Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.
> **Read `.agents/skills/dm_boolean_architecture/SKILL.md` before starting any task.**

---

## Background

The workbench has three existing boolean SDF commands (`DM_Add`, `DM_Subtract`,
`DM_Intersection`) implemented as separate `CommandDMBoolean` instances in
`commands/cmd_boolean.py`. Each operates on SDF objects (ShapeType == `"sdf"`).

The design uses **two colors** (implemented via `IsSubtractive`):
- **Orange** (`IsSubtractive=False`, default) — additive group
- **Blue** (`IsSubtractive=True`, set by Ctrl-drag) — subtractive group

Boolean commands group inputs by color and compose accordingly:

| Command | Inputs used | Operation |
|---------|------------|-----------|
| **Add** | all selected (orange + blue) | `union(all)` |
| **Subtract** | orange group − blue group | `subtract(union(orange), union(blue))` |
| **Intersection** | orange ∩ blue | `intersect(union(orange), union(blue))` |

The result is a **parent SDF object** with the inputs stored as children via
`App::PropertyLinkList`. When a child is edited, FreeCAD triggers `execute()` on
the parent, which recomposes the SDF tree and updates the GPU render.

After all tasks are complete:
- Selecting 3 orange shapes + 2 blue shapes and pressing Subtract correctly
  subtracts the blue union from the orange union.
- Modifying a child shape causes the parent boolean to update in the viewport.
- The Subtract icon shows a blue dashed circle. The Intersection icon shows the
  right circle in blue.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `CommandDMBoolean._sdf_boolean` | `commands/cmd_boolean.py:~61` | SDF boolean execution (per-operation class method) |
| `_fold_union` | `commands/cmd_boolean.py:~51` | Left-fold of multiple SDF fields into a UnionField |
| `_recompose_boolean` | `commands/cmd_boolean.py:~60` | Re-derives composed field from `BooleanInputs` list |
| `UnionField` | `core/sdf/sdf_composer.py` | min(a,b) SDF composition |
| `SubtractionField` | `core/sdf/sdf_composer.py` | max(a,-b) SDF composition |
| `IntersectionField` | `core/sdf/sdf_composer.py` | max(a,b) SDF composition |
| `DMObjectProxy.__init__` | `core/dm_object.py:140` | SDF property registration |
| `DMObjectProxy.execute` | `core/dm_object.py:306` | recompute entry point |
| `SdfRendererStrategy.update` | `core/dm_renderer.py:422` | registers field with scene renderer |
| `DMSceneRayMarchRenderer.update_field` | `core/dm_scene_ray_march_renderer.py:560` | GPU rebake |
| `DMViewProvider.claimChildren` | `core/dm_object.py:505` | returns `OutList` (auto-nests children) |
| `proxy.SdfField` | python attr on `DMObjectProxy` | stores the composed SDF field |

---

## Tier 1 — Multi-Input Color-Aware Boolean (Do First)

Replaces the existing two-input boolean with a multi-input color-aware version.
After this tier, Add/Subtract/Intersection work correctly on any number of
selected SDF objects, respecting orange/blue grouping.

### [x] B-001: Add `_fold_union` helper and update `_sdf_boolean` to group by color

**File:** `commands/cmd_boolean.py` — replace `_sdf_boolean` (~lines 61–133)

**What:** Extract fields from all selected objects, split into orange/blue groups
by `obj.IsSubtractive`, fold each group with `UnionField`, then apply the
operation. Result gets `IsSubtractive = False`.

```python
def _fold_union(fields):
    """Left-associative UnionField fold. Returns None if list is empty."""
    from core.sdf.sdf_composer import UnionField
    if not fields:
        return None
    result = fields[0]
    for f in fields[1:]:
        result = UnionField(result, f)
    return result

    def _sdf_boolean(self, sel):
    """Compose SdfField trees for SDF objects using two-color grouping."""
    from core.sdf.sdf_composer import UnionField, SubtractionField, IntersectionField
    from core.dm_object import create_dm_object

    try:
        orange_fields = []
        blue_fields   = []
        for obj in sel:
            proxy = getattr(obj, "Proxy", None)
            field = getattr(proxy, "SdfField", None)
            if field is None:
                dm_logger.error(
                    f"DM_{self.operation}: '{obj.Label}' has no SdfField."
                )
                return
            if getattr(obj, "IsSubtractive", False):
                blue_fields.append(field)
            else:
                orange_fields.append(field)

        orange = _fold_union(orange_fields)
        blue   = _fold_union(blue_fields)

        if self.operation == "Add":
            all_fields = orange_fields + blue_fields
            if not all_fields:
                dm_logger.error(f"DM_Add: No fields selected.")
                return
            result_field = _fold_union(all_fields)

        elif self.operation == "Subtract":
            if not orange:
                dm_logger.error("DM_Subtract: No orange (additive) fields selected.")
                return
            if not blue:
                dm_logger.error("DM_Subtract: No blue (subtractive) fields selected.")
                return
            result_field = SubtractionField(orange, blue)

        elif self.operation == "Intersection":
            if not orange:
                dm_logger.error("DM_Intersection: No orange (additive) fields selected.")
                return
            if not blue:
                dm_logger.error("DM_Intersection: No blue (subtractive) fields selected.")
                return
            result_field = IntersectionField(orange, blue)

        else:
            dm_logger.error(f"DM: Unknown operation '{self.operation}'.")
            return

        # Create result object
        new_name = f"{self.operation}"
        result = create_dm_object(name=new_name, shape_type="sdf")
        result.Proxy.SdfField = result_field
        result.IsSubtractive = False  # always orange

        doc = FreeCAD.activeDocument()
        for obj in sel:
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.Visibility = False

        doc.recompute()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(result)
        dm_logger.info(
            f"SDF {self.operation}: composed {len(sel)} fields "
            f"({len(orange_fields)} orange, {len(blue_fields)} blue)."
        )
    except Exception as e:
        dm_logger.error(f"DM_{self.operation} (SDF) failed: {e}")
```

---

### [x] B-002: Store `BooleanOp` and `BooleanInputs` on the result object

**File:** `commands/cmd_boolean.py` — inside `_sdf_boolean`, after `create_dm_object`

**What:** Add `BooleanOp` (string) and `BooleanInputs` (PropertyLinkList) to the
result so it can recompose itself when children change. These are set directly on
the object (not via `DMObjectProxy.__init__`) because they only apply to boolean
results.

```python
# After: result = create_dm_object(name=new_name, shape_type="sdf")
if not hasattr(result, "BooleanOp"):
    result.addProperty("App::PropertyString", "BooleanOp", "Boolean",
                       "Boolean operation: Add / Subtract / Intersection")
result.BooleanOp = self.operation

if not hasattr(result, "BooleanInputs"):
    result.addProperty("App::PropertyLinkList", "BooleanInputs", "Boolean",
                       "Child SDF objects that compose this boolean result")
result.BooleanInputs = list(sel)
```

This causes FreeCAD to show the input objects as children of the result in the
model tree (via `claimChildren → OutList`), and triggers `execute()` on the result
when any input changes.

**Depends on:** B-001

---

### [x] B-003: Add parametric recompute branch to `DMObjectProxy.execute`

**File:** `core/dm_object.py` — inside `execute` at ~line 313, within the `if st == "sdf":` block

**What:** If the SDF object has `BooleanInputs`, recompose the SDF tree from its
children and update the scene renderer. This makes boolean results live-update
when children are moved or edited.

```python
if st == "sdf":
    # Parametric boolean recompute
    if hasattr(fp, "BooleanInputs") and fp.BooleanInputs:
        try:
            from commands.cmd_boolean import _recompose_boolean
            new_field = _recompose_boolean(fp)
            if new_field is not None:
                self.SdfField = new_field
                # Push updated field to renderer
                from core.dm_renderer import SdfRendererStrategy
                vp = getattr(fp, "ViewObject", None)
                vp_proxy = getattr(vp, "Proxy", None) if vp else None
                strategy = getattr(vp_proxy, "_strategy", None) if vp_proxy else None
                if strategy and hasattr(strategy, "label") and strategy.label:
                    from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                    DMSceneRayMarchRenderer.get_instance().update_field(
                        strategy.label, new_field
                    )
        except Exception as e:
            from . import dm_logger
            dm_logger.error(f"Boolean recompute failed for {fp.Label}: {e}")
    fp.Shape = Part.Shape()
    return
```

**Depends on:** B-002

---

### [x] B-004: Add `_recompose_boolean` helper to `cmd_boolean.py`

**File:** `commands/cmd_boolean.py` — add as module-level function after `_fold_union`

**What:** Extract fields from `fp.BooleanInputs`, split by color, fold, and
return the composed `SdfField`. Called by `DMObjectProxy.execute()`.

```python
def _recompose_boolean(fp):
    """Re-compose the SDF tree for a boolean result FP object from its BooleanInputs.
    
    Returns the composed SdfField, or None on failure.
    Called from DMObjectProxy.execute() when BooleanInputs are present.
    """
    from core.sdf.sdf_composer import UnionField, SubtractionField, IntersectionField
    op = getattr(fp, "BooleanOp", None)
    inputs = getattr(fp, "BooleanInputs", [])
    if not inputs or not op:
        return None

    orange_fields = []
    blue_fields   = []
    for child in inputs:
        if child is None:
            continue
        proxy = getattr(child, "Proxy", None)
        field = getattr(proxy, "SdfField", None)
        if field is None:
            continue
        if getattr(child, "IsSubtractive", False):
            blue_fields.append(field)
        else:
            orange_fields.append(field)

    if op == "Add":
        return _fold_union(orange_fields + blue_fields)
    elif op == "Subtract":
        orange = _fold_union(orange_fields)
        blue   = _fold_union(blue_fields)
        if orange is None or blue is None:
            return None
        return SubtractionField(orange, blue)
    elif op == "Intersection":
        orange = _fold_union(orange_fields)
        blue   = _fold_union(blue_fields)
        if orange is None or blue is None:
            return None
        return IntersectionField(orange, blue)
    return None
```

**Depends on:** B-001

---

## Tier 2 — Icon Updates

Update the two boolean icons to show the two-color (orange/blue) distinction.
No code changes — SVG file edits only.

### [x] B-005: Update MakeSubtract.svg — blue dashed cutter circle

**File:** `Resources/icons/MakeSubtract.svg` — replace entire file

**What:** The right circle (the shape being subtracted = blue) should use a blue
dashed stroke (`#4488ff`). The left crescent (result = stays orange) keeps orange.

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
    <!-- Orange crescent: the surviving solid region -->
    <path d="M 36 16 A 18 18 0 1 0 36 48 A 18 18 0 0 1 36 16 Z"
          style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
    <!-- Blue dashed circle: the shape being subtracted away -->
    <circle cx="44" cy="32" r="18"
            style="fill:none;stroke:#4488ff;stroke-width:2;stroke-dasharray:4,4"/>
</svg>
```

---

### [x] B-006: Update MakeIntersection.svg — right circle in blue

**File:** `Resources/icons/MakeIntersection.svg` — replace entire file

**What:** The left circle keeps orange stroke (additive group). The right circle
uses blue stroke (`#4488ff`, solid). The overlap fill stays gray.

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
    <!-- Left circle: orange (additive group) -->
    <circle cx="28" cy="32" r="18" style="fill:none;stroke:#ffaa00;stroke-width:2"/>
    <!-- Right circle: blue (subtractive group) -->
    <circle cx="44" cy="32" r="18" style="fill:none;stroke:#4488ff;stroke-width:2"/>
    <!-- Filled overlap region -->
    <path d="M 36 15 A 18 18 0 0 1 36 49 A 18 18 0 0 1 36 15"
          style="fill:#e0e0e0;stroke:none"/>
</svg>
```

---

## Tier 3 — Error Handling and Guard Rails

Improves user-facing error messages to guide toward the correct input pattern.

### [x] B-007: Add tooltip updates to reflect two-color semantics

**File:** `commands/cmd_boolean.py` — `GetResources` method

**What:** Update tooltips to explain that orange = additive, blue (Ctrl-drag) =
subtractive, and what each operation does with those groups.

```python
_TOOLTIPS = {
    "Add":          "Union of all selected SDF objects (orange and blue combined). Result is orange.",
    "Subtract":     "Subtract blue (Ctrl-drag) objects from orange objects. Select at least one orange and one blue.",
    "Intersection": "Intersection of orange and blue groups. Select at least one orange and one blue.",
}

def GetResources(self):
    return {
        'Pixmap':   self._ICONS.get(self.operation, 'Part_Booleans.svg'),
        'MenuText': f"{self.operation}",
        'ToolTip':  self._TOOLTIPS.get(self.operation, f"Boolean {self.operation}."),
    }
```

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_boolean_architecture` | Two-color boolean system architecture, field grouping, parent-child tree |
| `dm_additive_subtractive` | How IsSubtractive is stored on objects and passed to the GPU shader |
| `dm_sdf_primitive_pattern` | SdfField subclass template |

See `.agents/workflows/` for executable workflows:

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task by ID (e.g. `/fix-task B-001`) |
