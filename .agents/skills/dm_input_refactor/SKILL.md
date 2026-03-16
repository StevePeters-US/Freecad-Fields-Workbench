---
name: DM Input Pipeline Refactor Reference
description: Architecture of the dual Qt/Coin3D event pipeline and the target single-owner design. Required reading before editing input_manager.py, dm_base.py, or dm_tool_manager.py.
---

# DM Input Pipeline Refactor Reference

## Current Architecture (Problematic)

Two independent event pipelines fire for the same user input:

```
User Input
    │
    ├──► Qt Event Filter (DMInputManager.eventFilter)
    │       • Tracks modifier state (_shift_down, _control_down)
    │       • Updates _last_qt_pos continuously
    │       • Handles right-click finish (lines 79-90)     ◄── DUPLICATE
    │       • Intercepts ShortcutOverride for S/D/E
    │       • Suppresses ContextMenu when tool active
    │       • Handles global D/E hotkeys (no tool active)
    │
    └──► Coin3D Callback (DMBase.event_cb)
            • Dispatches SoMouseButtonEvent → on_button1/2/3_down/up
            • Dispatches SoLocation2Event → handle_move
            • Dispatches SoKeyboardEvent → handle_keyboard
            • Right-click in on_button3_down → finish()    ◄── DUPLICATE
```

### Inconsistencies

| Issue | Location | Problem |
|-------|----------|---------|
| **Dual right-click** | `input_manager.py:79-90` + `dm_base.py:208-224` | Both schedule `finish()` via QTimer. The `_finish_scheduled` flag prevents double-fire but adds fragility. |
| **Mixed modifier access** | Various tools | Some read `DMInputManager._shift_down`, others read `event_dict["ShiftDown"]`. No single source of truth. |
| **SDF selection in Qt filter** | `input_manager.py:55-72` | Ray-casting + ViewProjector instantiation happens inside the global Qt filter — expensive per-click. |
| **Right-click consumed at Qt level** | `input_manager.py:90` | Returns `True`, so Coin3D `event_cb` never fires `on_button3_down`. The Qt filter replicates finish logic. |
| **Middle-mouse tracking duplication** | `input_manager.py:51-52` + `dm_base.py:244,252` | Both track `_middle_mouse_down` independently. |
| **Tool lifecycle complexity** | `dm_base.py` | `terminate()` → QTimer → `_do_terminate()`, `finish()` → QTimer → `_do_finish()` → `terminate()`. Three-layer chain. |

## Target Architecture (Single Owner Per Event)

The refactored design assigns **one owner** to each event type:

```
User Input
    │
    ├──► Qt Event Filter (DMInputManager) — THIN LAYER
    │       • Track _last_qt_pos, _shift_down, _control_down, button states
    │       • ShortcutOverride: claim S/D/E keys
    │       • ContextMenu: suppress when tool active
    │       • Global hotkeys (D/E) when no tool active
    │       • Right-click: suppress FreeCAD context menu ONLY (return True)
    │         ◄── NO finish logic, NO tool method calls
    │
    └──► Coin3D Callback (DMBase.event_cb) — ALL TOOL LOGIC
            • on_button3_down: sole owner of right-click → finish()
            • handle_move: sole owner of mouse movement
            • handle_keyboard: sole owner of key events
```

### Key Principle

The Qt filter's job is to **suppress FreeCAD defaults** and **track state**. It never calls tool methods. All tool logic lives in Coin3D callbacks.

### Right-Click Fix

**Problem:** Qt filter consumes right-click (returns True), so Coin3D never sees it.

**Solution:** Qt filter still returns True to block FreeCAD's context menu, but does NOT call `tool.finish()`. Instead, Coin3D must receive the event. Two approaches:

1. **Preferred**: Use `event.accept()` instead of `return True` for RightButton press. This tells Qt the event is handled but still allows Coin3D to process it.
2. **Fallback**: If approach 1 doesn't work (Coin3D may not see accepted-but-not-filtered events), the Qt filter posts a synthetic Coin3D-style event to the tool:
   ```python
   # In eventFilter, right-click block:
   tool.on_button3_down({"Button": "BUTTON3", "State": "DOWN",
                         "QtPosition": (event.pos().x(), event.pos().y()),
                         "ShiftDown": bool(event.modifiers() & Qt.ShiftModifier)})
   return True
   ```
   But this is what we have now — the key change is that `on_button3_down` is the **single** handler, not duplicated logic.

## Files to Read Before Editing

1. `core/input_manager.py` — Qt event filter, coordinate helpers, ray generation
2. `tools/dm_base.py` — Coin3D event_cb, handle_keyboard, on_button*_down/up, tool lifecycle
3. `core/dm_tool_manager.py` — DMToolManager singleton, DMSelectionObserver
4. `core/view_projector.py` — get_mouse_plane_pt, get_sdf_hit (called from input_manager for LMB selection)

## Modifier State Contract

After refactor, all code should read modifiers from ONE source:

```python
# Correct — single source of truth
im = DMInputManager.get_instance()
shift = im._shift_down
ctrl  = im._control_down
lmb   = im._left_mouse_down
mmb   = im._middle_mouse_down

# Deprecated — do NOT use
event_dict.get("ShiftDown")      # Coin3D modifier, may disagree with Qt
self._middle_mouse_down           # per-tool tracking in dm_base.py
```

## Drag Pattern (Unchanged)

The QTimer-based drag pattern is correct and should not be modified:

```python
on_button1_down → self._start_drag_timer()  # QTimer(16ms)
_drag_update    → poll DMInputManager._last_qt_pos
on_button1_up   → self._stop_drag_timer()
```

This works because Coin3D suppresses SoLocation2Event during LMB hold.

## Tool Lifecycle (Simplified Target)

```
finish()          → _do_finish() → self._finished = True; terminate()
terminate()       → _do_terminate() [via QTimer]
_do_terminate()   → unregister event_cb, close dialog, clean up objects
```

The QTimer in `terminate()` exists to defer cleanup until after the current event_cb returns. This is correct and should stay.
