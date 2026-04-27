# Direct Modeling Workbench — Interactive & Smooth Booleans Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

This roadmap focuses on maturing the Direct Modeling experience by adding precision snapping, advanced blending operations, and ensuring a consistent UX across all tools.

1. **Snapping**: Control points currently move freely. We need to implement a snapping engine that respects the workplane grid, other control points, and NURBS geometry surfaces.
2. **Smooth Booleans**: Sharp CSG is limiting for organic design. We are adding polynomial smooth-min/max (IQ's smin) to the boolean tree.
3. **Consistency**: Different tools (Primitive creators vs Edit tools) have slight variations in hotkey handling and drag lifecycle. We will unify them under the `PrimitiveCreatorBase` and `DMBase` patterns.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `ViewProjector` | `core/view_projector.py:8` | Handles ray-to-world projections |
| `PrimitiveCreatorBase` | `tools/primitive_tool.py:217` | Base class for interactive SDF tools |
| `ComposerField` | `core/sdf/sdf_composer.py:27` | Base for boolean tree operations |
| `DMBase` | `tools/dm_base.py:77` | Root base class for all DM tools |

---

## Tier 1 — Snapping Infrastructure

Add precision snapping to interactive point dragging.

### I-001: Implement `get_snapped_point` in `ViewProjector`

**File:** `core/view_projector.py` — append after `get_mouse_plane_pt` (line 385)

**What:** Create a centralized snapping helper that takes a world-space point and returns a snapped version based on grid, points, or geometry.

```python
    def get_snapped_point(self, pt, event_dict, grid_size=None, snap_to_points=None, tolerance=5.0):
        """
        Snaps a world-space point to the nearest grid intersection or control point.
        """
        if pt is None: return None
        
        # 1. Point Snapping (highest priority)
        if snap_to_points:
            best_pt = None
            min_dist = tolerance
            for target in snap_to_points:
                dist = (pt - target).Length
                if dist < min_dist:
                    min_dist = dist
                    best_pt = target
            if best_pt: return best_pt

        # 2. Grid Snapping
        # Read from DMBase or preference if grid_size is None
        if grid_size:
            # Simple grid snap in world space (relative to origin or plane?)
            # Usually better to snap in local workplane space
            pass
            
        return pt
```

### I-002: Add grid snapping to `PrimitiveCreatorBase._drag_update`

**File:** `tools/primitive_tool.py` — insert after `mods = QtGui.QApplication.keyboardModifiers()` (line 464)

**What:** Use the new projector helper to snap the dragged point to the grid if a modifier is held (e.g. no modifier = snap, Alt = free move, or vice versa).

```python
        # [NEW] Grid snapping logic
        grid_size = 5.0 # Default 5mm, should eventually be a preference
        if not bool(mods & QtCore.Qt.AltModifier): # Snap by default unless Alt held
            new_pt = self.projector.get_snapped_point(new_pt, {"Position": mouse_pos}, grid_size=grid_size)
```

---

## Tier 2 — Smooth Booleans

Extend the boolean tree with blending operations.

### I-003: Add Smooth Boolean field classes to `sdf_composer.py`

**File:** `core/sdf/sdf_composer.py` — append to end of file

**What:** Implement polynomial smooth-min and smooth-max for Union, Subtraction, and Intersection.

```python
class SmoothUnionField(ComposerField):
    """Smooth union (min) with smoothing factor k."""
    def __init__(self, field_a: SdfField, field_b: SdfField, k: float = 5.0):
        super().__init__(field_a, field_b)
        self.k = k

    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        # Polynomial smin
        return f"smin({a}, {b}, {self.k})"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        a = self.a.evaluate(point)
        b = self.b.evaluate(point)
        h = max(self.k - abs(a - b), 0.0) / self.k
        return min(a, b) - h * h * self.k * 0.25
```

### I-004: Update GLSL assembler to include `smin` helper

**File:** `core/sdf/glsl_assembler.py` (or where common functions are defined)

**What:** Ensure the shader has the `smin` and `smax` functions available.

```glsl
float smin(float a, float b, float k) {
    float h = max(k - abs(a - b), 0.0) / k;
    return min(a, b) - h * h * k * 0.25;
}

float smax(float a, float b, float k) {
    float h = max(k - abs(a - b), 0.0) / k;
    return max(a, b) + h * h * k * 0.25;
}
```

---

## Tier 3 — Tool Consistency

Unify behaviors across the workbench.

### I-005: Standardize 'Z' key for group toggle in `DMBase`

**File:** `tools/dm_base.py` — update `handle_keyboard` (line 687)

**What:** Move the Group toggle logic (for additive/subtractive colors) from individual tools into the base class so it works identically everywhere.

### I-006: Unify Right-Click behavior in `DMBase`

**File:** `tools/dm_base.py` — update `on_button3_down` (line 641)

**What:** Check if the mouse is over a handle first. If yes, show context menu. If no, call `finish()`. This prevents tools from closing when the user just wanted to right-click a handle.
