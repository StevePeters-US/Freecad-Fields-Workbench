# Input & Tool Architecture Refactor Tasks
## Scope
Consolidate the three separate tool patterns (NURBSPrimitiveCreator, PrimitiveCreatorBase,
EditTool/FRepEditTool) into a consistent architecture. All tasks are sized for a single-file
focused edit. Resolve in order where dependencies exist (marked with "Requires:").

---

## R-001 — Extract DragTimerMixin
**File:** `tools/dm_base.py`
**Problem:** The QTimer-based drag polling pattern is copy-pasted between `EditTool` and
`FRepEditTool`. Both implement `_start_drag_timer()`, `_stop_drag_timer()`, `_drag_update()`,
and the LMB self-termination check identically.

**Task:** Create `class DragTimerMixin` in `tools/dm_base.py` (above `DMBase`).
- `_start_drag_timer(interval_ms=16)` → creates `self._drag_timer`, connects to `self._drag_update`, starts
- `_stop_drag_timer()` → stops and clears `self._drag_timer`
- `_drag_update()` → default no-op; subclasses override
- Add LMB self-termination check as `_drag_check_lmb_released()` → returns `True` if released (stops timer, resets `self.state=0`)
- `_do_terminate` hook: call `_stop_drag_timer()` if `_drag_timer` exists

**In `EditTool`:** inherit `DragTimerMixin`, remove duplicated timer methods, call `self._drag_check_lmb_released()` at start of `_drag_update`.
**In `FRepEditTool`:** same — inherit `DragTimerMixin`, remove duplicated timer methods.

---

## R-002 — Cursor Restore Consolidation
**Files:** `tools/edit_tool.py`, `tools/curve_tool.py`, `tools/dm_base.py`
**Problem:** `_cursor_active` flag + `QApplication.restoreOverrideCursor()` is manually
managed in `EditTool._do_terminate`, `FRepEditTool._do_terminate`, and `CurveCreator._do_terminate`.
If any tool raises before cleanup, the cursor stays overridden.

**Task:** Add to `DMBase`:
```python
def _set_cursor(self, cursor):
    """Set override cursor, tracking state."""
    if not getattr(self, "_cursor_active", False):
        QtGui.QApplication.setOverrideCursor(cursor)
        self._cursor_active = True

def _restore_cursor(self):
    """Restore cursor if we set it."""
    if getattr(self, "_cursor_active", False):
        QtGui.QApplication.restoreOverrideCursor()
        self._cursor_active = False
```
Add `self._restore_cursor()` call to `DMBase._do_terminate()` (before `super()`).
Remove manual cursor restore from `EditTool._do_terminate`, `FRepEditTool._do_terminate`,
`CurveCreator._do_terminate`. Replace `setOverrideCursor` calls with `self._set_cursor(...)`.

---

## R-003 — Unified Perpendicular Hit-Test Helper
**Files:** `tools/edit_tool.py`, `tools/curve_tool.py`, `tools/dm_base.py`
**Problem:** Perpendicular-distance hit-test is implemented three separate times:
- `EditTool._hit_test` (elements: Point/HandleIn/HandleOut, dynamic tolerance)
- `FRepEditTool._hit_test_corners` (corner list, dynamic tolerance)
- `CurveCreator._hit_test` (point list, radius from `_compute_handle_radius`)
All share the same pattern: `v = pt - ray_p; proj = v.dot(ray_d); perp = (ray_p + ray_d*proj - pt).Length`.

**Task:** Add to `DMBase`:
```python
def _hit_test_perp(self, ray_p, ray_d, points, tolerance=None):
    """
    Returns (best_idx, best_perp_dist) for list of FreeCAD.Vector points.
    Uses perpendicular distance (depth-independent, correct for ortho cameras).
    tolerance defaults to _compute_handle_radius() if None.
    """
```
Replace the inline loops in all three tools with calls to `self._hit_test_perp(...)`.
`EditTool._hit_test` still needs to build its flat point list from pts/h_in/h_out before calling.

---

## R-004 — Workplane First-Click Extraction
**Files:** `tools/primitive_tool.py`
**Problem:** The first-click workplane-lock block is copy-pasted in `BoxCreator.on_button1_down`,
`SphereCreator.on_button1_down`, and `CylinderCreator.on_button1_down`:
```python
result = self.projector.get_mouse_plane_pt(event_dict, ...)
if isinstance(result, tuple): pos, wp_hit = result
else: pos, wp_hit = result, None
if wp_hit is not None:
    self.working_plane = wp_hit.getGlobalPlacement() if hasattr(...) else wp_hit.Placement
```

**Task:** Add to `DMBase`:
```python
def _resolve_wp_click(self, event_dict, skip_objects=None):
    """
    Call projector.get_mouse_plane_pt, unpack the (pt, wp_hit) tuple,
    update self.working_plane from wp_hit if not already set.
    Returns pos (FreeCAD.Vector or None).
    """
```
Replace the 10-line boilerplate in all three Creator classes with a single call.
Each class's `_last_working_plane` save logic remains in the subclass after the call.

---

## R-005 — Fix `EditTool._hit_test_edge` Signature Bug
**File:** `tools/edit_tool.py`
**Problem:** `_hit_test_edge(self, ray_p, ray_d)` (line 132) references `event_dict`
internally (line 150: `pos_global = self.get_mouse_world_pos(event_dict, n, o)`) but
`event_dict` is not a parameter — this will `NameError` on call.
Also, `handle_click` calls `self._hit_test_edge(None, None)` with no event_dict, so
the method never had correct inputs.

**Task:** Fix the signature:
```python
def _hit_test_edge(self, event_dict):
```
Remove `ray_p, ray_d` parameters (they are unused in the body). Update the one call
site in `handle_click` to pass `event_dict`:
```python
hit_p = self._hit_test_edge(event_dict)
```
Also import `Part` at the top of the method (currently missing — `Part.Point` is used
but `Part` is not imported in `edit_tool.py`).

---

## R-006 — Fix E-key Dispatch Missing FRep Branch
**File:** `core/input_manager.py`
**Problem:** The E-key handler at line 150 only dispatches `EditTool` when
`ShapeType == "curve"`. When `ShapeType == "frep"`, it silently does nothing.
`FRepEditTool` is only reachable via double-click (line 78) — not via E-key.

**Task:** In `DMInputManager.eventFilter`, inside the E-key block after the
`DMObjectProxy` check, add the frep branch:
```python
elif proxy_name == "DMObjectProxy":
    shape_type = getattr(obj, "ShapeType", "")
    if shape_type == "curve":
        from tools.edit_tool import EditTool
        tool = EditTool(); tool.activate(); return True
    elif shape_type == "frep":
        from tools.edit_tool import FRepEditTool
        tool = FRepEditTool(); tool.activate(); return True
```
This mirrors the logic already in `edit_tool.activate()`.

---

## R-007 — Double-Fire Guard for `on_button3_down`
**File:** `tools/dm_base.py`
**Problem:** Right-click arrives on TWO paths:
1. Qt-level: `DMInputManager.eventFilter` synthesizes a `{"Button": "BUTTON3", ...}` dict
   and calls `tool.on_button3_down(synthetic)` directly (line 98 in input_manager.py).
2. Coin3D-level: `DMBase.event_cb` also fires `on_button3_down` when the `SoMouseButtonEvent`
   reaches the scene graph callback (which it does ~10ms later).
This means `_finish_scheduled` may fire twice (second call is a no-op via the guard, but
`super().on_button3_down` still runs and returns True, consuming the event unnecessarily).

**Task:** Add a timestamp-based dedup guard in `DMBase.on_button3_down`:
```python
import time
_last_btn3_time = 0.0

def on_button3_down(self, event_dict):
    now = time.monotonic()
    if now - self._last_btn3_time < 0.05:   # 50ms dedup window
        return True                           # duplicate fire, consume silently
    self._last_btn3_time = now
    # ... existing logic
```
(Instance variable, not class variable — reset in `__init__`.)

---

## R-008 — Fix `EditTool.handle_move` Drag Path Using Wrong Projector Call
**File:** `tools/edit_tool.py`
**Problem:** `EditTool.handle_move` at line 253 calls:
```python
pt_global = self.get_mouse_world_pos(event_dict, self.drag_plane_n, self.drag_plane_o)
```
This is the `DMBase` wrapper, which uses `place_on_geometry=getattr(self, "place_on_geometry", False)`.
If `DMBase.place_on_geometry` is True, the cursor snaps to geometry instead of the drag plane —
violating the drag standard (see patterns.md: Drag Plane Bypass Pattern).

**Task:** Replace line 253 with the direct projector call:
```python
pt_global = self.projector.get_mouse_world_pos(
    event_dict, self.drag_plane_n, self.drag_plane_o,
    place_on_geometry=False
)
```
This mirrors `_drag_update` (line 199) which already does this correctly.

---

## R-009 — Extract Throttle Update Helper
**Files:** `tools/curve_tool.py`, `tools/primitive_tool.py`, `tools/dm_base.py`
**Problem:** The `_update_pending / QTimer.singleShot(interval_ms, callback)` throttle
pattern is duplicated in `CurveCreator.update_preview()` (line 222) and
`PrimitiveCreatorBase.update_preview()` (line 63):
```python
if not self._update_pending:
    self._update_pending = True
    QtCore.QTimer.singleShot(interval_ms, lambda: self._do_update_preview())
```

**Task:** Add to `DMBase`:
```python
def _schedule_update(self, callback, interval_ms=None):
    """Throttled single-shot update. Drops duplicate calls within the interval."""
    if getattr(self, "_update_pending", False):
        return
    if interval_ms is None:
        from core.dm_object import get_interactive_throttle_interval
        interval_ms = int(get_interactive_throttle_interval() * 1000)
    self._update_pending = True
    QtCore.QTimer.singleShot(interval_ms, callback)
```
Initialize `self._update_pending = False` in `DMBase.__init__`.
Replace the duplicated blocks in `CurveCreator.update_preview` and
`PrimitiveCreatorBase.update_preview` with `self._schedule_update(self._do_update_preview)`.
Remember each callback must set `self._update_pending = False` at its start.

---

## R-010 — Unify `_do_terminate` Cleanup Ordering
**Files:** `tools/edit_tool.py`, `tools/curve_tool.py`
**Problem:** `_do_terminate` override ordering is inconsistent:
- `CurveCreator._do_terminate`: cleans up spheres, removes scenegraph node, THEN calls `super()._do_terminate()`
- `FRepEditTool._do_terminate`: stops timer, restores cursor, THEN calls `super()._do_terminate()`
- `DMBase._do_terminate`: removes event callback, closes dialog, removes unfinished objects

If `super()._do_terminate()` raises (e.g. callback already removed), the subclass cleanup
that ran before `super()` is fine, but cleanup registered in `super()` is lost.
Requires R-001 (DragTimerMixin handles timer stop) and R-002 (DMBase handles cursor restore).

**Task:** After R-001 and R-002 are done, verify `_do_terminate` in each tool only:
1. Cleans up tool-specific Coin3D nodes (undraw dm_points, remove points_root from sg)
2. Calls `super()._do_terminate()` last

Remove redundant timer-stop and cursor-restore calls that are now handled by mixins.
Document the expected `_do_terminate` call chain in a comment in `DMBase._do_terminate`.

---

## R-011 — Consistent Commit Lifecycle Hook
**Files:** `tools/dm_base.py`, `tools/curve_tool.py`, `tools/primitive_tool.py`
**Problem:** Two separate commit/finalize patterns exist:
- `NURBSPrimitiveCreator._do_finish()`: finalizes `_active_obj`, sets label, calls `terminate()`
- `PrimitiveCreatorBase._finalize_object()`: sets `_finished=True`, schedules `__do_commit`, calls `terminate()`

Neither calls a shared hook for post-commit operations (e.g. selecting the new object,
emitting a signal, logging).

**Task:** Add an `_on_committed(obj)` hook to `DMBase`:
```python
def _on_committed(self, obj):
    """Called after a tool successfully commits its object. Override to customize."""
    if obj:
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(obj.Document.Name, obj.Name)
```
Call `self._on_committed(self._active_obj)` at the end of `NURBSPrimitiveCreator._do_finish`.
Call `self._on_committed(obj)` at the end of `PrimitiveCreatorBase.__do_commit` (after recompute).

---

## R-012 — State Constant Definitions
**File:** `tools/dm_base.py`
**Problem:** Tool state integers (0, 1, 2, 3) are used bare throughout the codebase
with different semantic meanings depending on context:
- In `DMBase` placement tools: 0=pre-click, 1=first-click-placed, 2=second-click-placed
- In `EditTool`/`FRepEditTool`: 0=idle, 1=dragging

**Task:** Add named constants at the top of `dm_base.py` (below imports):
```python
# Placement tool states
TOOL_STATE_IDLE     = 0   # No input yet
TOOL_STATE_PLACE1   = 1   # First anchor placed
TOOL_STATE_PLACE2   = 2   # Second anchor placed
TOOL_STATE_DONE     = 3   # Finalized, cleaning up

# Edit tool states (reuse IDLE / PLACE1 as drag states for clarity)
EDIT_STATE_IDLE     = 0   # Hovering, no selection
EDIT_STATE_DRAGGING = 1   # LMB held, dragging selected element
```
Update references in `DMBase.handle_click`, `BoxCreator`, `SphereCreator`, `CylinderCreator`,
`EditTool`, `FRepEditTool` to use the named constants.
This is a mechanical find-and-replace; do NOT change any logic.

---

## Priority Order
1. R-005 (bug fix, standalone — no deps)
2. R-006 (bug fix, standalone — no deps)
3. R-008 (bug fix, standalone — no deps)
4. R-001 (DragTimerMixin — other tasks depend on it)
5. R-002 (CursorMixin — R-010 depends on it)
6. R-003 (hit-test helper — standalone)
7. R-004 (wp first-click — standalone)
8. R-007 (btn3 dedup — standalone)
9. R-009 (throttle helper — standalone)
10. R-010 (terminate ordering — requires R-001, R-002)
11. R-011 (commit hook — standalone)
12. R-012 (state constants — purely mechanical, last)
