# Skill: DM Tool Refactor Patterns

Use this skill when implementing tasks from `todo_inputrefactor.md` or working on
`tools/dm_base.py`, `tools/edit_tool.py`, `tools/curve_tool.py`, `tools/primitive_tool.py`.

---

## Critical Invariants (do NOT break these)

### Event Pipeline
- **Coin3D `event_cb`** (`DMBase`) handles ALL tool logic (clicks, moves, keyboard, finish).
- **Qt `DMInputManager.eventFilter`** handles: modifier state, coordinate tracking, LMB/MMB state, right-click suppression, global hotkeys (D, E keys), SDF selection when no tool active.
- Right-click fires on BOTH paths. `on_button3_down` has a 50ms dedup guard (R-007) — do not remove it.
- **Do NOT** move tool logic into `eventFilter`. Do NOT move input tracking into `event_cb`.

### Drag Standard
Dragging uses **QTimer polling** (not Coin3D location events — those are suppressed during LMB hold).
```
on_button1_down → hit-test → set state=EDIT_STATE_DRAGGING → _start_drag_timer()
QTimer(16ms)    → _drag_update() → poll DMInputManager._last_qt_pos
                                 → check _left_mouse_down (self-terminate if released)
                                 → projector.get_mouse_world_pos(..., place_on_geometry=False)
on_button1_up   → _stop_drag_timer() → state=EDIT_STATE_IDLE
```
**Always** use `projector.get_mouse_world_pos(..., place_on_geometry=False)` during drags —
never the `DMBase.get_mouse_world_pos()` wrapper which may snap to geometry.

### Hit Testing
Always use perpendicular distance, not ray-sphere intersection:
```python
v = pt - ray_p
proj = v.dot(ray_d)
perp = (ray_p + ray_d * proj - pt).Length
```
For orthographic cameras, `get_ray()` origin is on the focal plane — t<0 for points
behind it, so positive-t sphere intersection gives false negatives.
Tolerance = `_compute_handle_radius()` world units (~8–15 screen pixels).

### Working Plane First Click
```python
result = self.projector.get_mouse_plane_pt(
    event_dict, place_on_geometry=False,
    working_plane=self.working_plane, skip_objects=[self._preview_obj]
)
pos, wp_hit = result if isinstance(result, tuple) else (result, None)
if self.state == TOOL_STATE_IDLE and wp_hit is not None:
    self.working_plane = getattr(wp_hit, "getGlobalPlacement", lambda: wp_hit.Placement)()
```

---

## DragTimerMixin (after R-001)

```python
class DragTimerMixin:
    def _start_drag_timer(self, interval_ms=16):
        self._stop_drag_timer()
        self._drag_timer = QtCore.QTimer()
        self._drag_timer.timeout.connect(self._drag_update)
        self._drag_timer.start(interval_ms)

    def _stop_drag_timer(self):
        if getattr(self, "_drag_timer", None):
            self._drag_timer.stop()
            self._drag_timer = None

    def _drag_check_lmb_released(self):
        """Returns True (and stops drag) if LMB was released under the hood."""
        from core.input_manager import DMInputManager
        if not DMInputManager.get_instance()._left_mouse_down:
            self._stop_drag_timer()
            self.state = EDIT_STATE_IDLE
            return True
        return False

    def _drag_update(self):
        pass  # Override in subclass
```

## Cursor Helpers (after R-002, in DMBase)

```python
def _set_cursor(self, cursor):
    if not getattr(self, "_cursor_active", False):
        QtGui.QApplication.setOverrideCursor(cursor)
        self._cursor_active = True

def _restore_cursor(self):
    if getattr(self, "_cursor_active", False):
        QtGui.QApplication.restoreOverrideCursor()
        self._cursor_active = False
```
`DMBase._do_terminate` calls `self._restore_cursor()` — subclasses don't need to.

## Throttle Helper (after R-009, in DMBase)

```python
def _schedule_update(self, callback, interval_ms=None):
    if getattr(self, "_update_pending", False):
        return
    if interval_ms is None:
        from core.dm_object import get_interactive_throttle_interval
        interval_ms = int(get_interactive_throttle_interval() * 1000)
    self._update_pending = True
    QtCore.QTimer.singleShot(interval_ms, callback)
```
The callback must set `self._update_pending = False` at its start.

## State Constants (after R-012)

```python
TOOL_STATE_IDLE   = 0
TOOL_STATE_PLACE1 = 1
TOOL_STATE_PLACE2 = 2
TOOL_STATE_DONE   = 3
EDIT_STATE_IDLE     = 0
EDIT_STATE_DRAGGING = 1
```

---

## `_do_terminate` Override Pattern

```python
def _do_terminate(self):
    # 1. Clean up tool-specific Coin3D nodes FIRST
    for dm_pt in self.dm_points:
        dm_pt.undraw()
    self.dm_points.clear()
    try:
        if self.view and self.view.getSceneGraph() and self.points_root:
            self.view.getSceneGraph().removeChild(self.points_root)
    except Exception:
        pass
    # 2. Let DMBase handle: event callback, dialog, cursor, unfinished obj cleanup
    super()._do_terminate()
```
After R-001: timer stop is automatic (DragTimerMixin._do_terminate hook).
After R-002: cursor restore is automatic (DMBase._do_terminate).

---

## Key File Locations

| File | Class | Role |
|------|-------|------|
| `tools/dm_base.py` | `DMBase` | Base for all tools; event dispatch, workplane helpers |
| `tools/dm_base.py` | `NURBSPrimitiveCreator` | NURBS/curve placement creator |
| `tools/primitive_tool.py` | `PrimitiveCreatorBase` | SDF primitive placement creator |
| `tools/edit_tool.py` | `EditTool` | Curve control-point editor |
| `tools/edit_tool.py` | `SdfEditTool` | SDF box corner editor |
| `tools/curve_tool.py` | `CurveCreator` | Click-to-place curve points |
| `core/input_manager.py` | `DMInputManager` | Qt event filter + ray/coordinate helpers |
