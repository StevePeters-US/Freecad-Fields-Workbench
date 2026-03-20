---
name: DM Primitive Tool Implementation Pattern
description: Standardized pattern for creating interactive SDF primitive tools in the Direct Modeling workbench.
---

# DM Primitive Tool Implementation Pattern

This skill defines the canonical pattern for implementing interactive SDF primitive creator tools (e.g., Box, Sphere, Cylinder) in the Direct Modeling workbench. All new primitive tools must follow this pattern to ensure consistent UX, workplane support, and visual feedback.

## Core Principles

1.  **Workplane Awareness**: Tools must respect the current working plane (`self.working_plane`).
2.  **Projection over Selection**: Use `self.projector.get_mouse_plane_pt` instead of `get_mouse_world_pos` to ensure input is projected onto the active plane.
3.  **State Retention**: Tools should remember the last used workplane across invocations (using a class variable like `_last_working_plane`).
4.  **Visual Feedback**: Use `DMPoint` to show where the user has clicked during the multi-step creation process.
5.  **Clean Termination**: Explicitly override `_do_terminate` to clean up Coin3D visuals (points, etc.).

## Implementation Checklist

### 1. Class Header and State
```python
class MyPrimitiveCreator(PrimitiveCreatorBase):

    def __init__(self):
        super().__init__()
        # PrimitiveCreatorBase.__init__ already handles:
        #   self.dm_points, self.points_root, self._init_working_plane()
        # Only add tool-specific state here:
        self.points = []
        self.current_point = None
```

### 2. Input Handling
- Use `self.projector.get_mouse_plane_pt(event_dict, working_plane=self.working_plane)` in `on_button1_down`.
- Capture `wp_hit` from the projector result to lock onto a specific plane on the first click.
- For height/extrude states, use `DMInputManager.get_instance().get_axis_point`.

### 3. Visuals (DMPoint)
```python
# In on_button1_down or similar:
dm_pt = DMPoint(pos)
dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pos))
self.dm_points.append(dm_pt)
```

### 4. Cleanup
```python
def _do_terminate(self):
    for pt in self.dm_points: pt.undraw()
    if self.points_root:
        self.view.getSceneGraph().removeChild(self.points_root)
    super()._do_terminate()
```

## Reference Implementation
See `BoxCreator` in `tools/primitive_tool.py` as the "Gold Standard" implementation.
