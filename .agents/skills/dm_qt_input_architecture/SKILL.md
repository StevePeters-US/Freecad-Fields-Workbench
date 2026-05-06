---
name: DM Qt-Native Input Architecture
description: Architecture of the single-owner Qt-native input pipeline. Required reading before implementing the R-* tasks in todo_inputrefactor.md.
---

# DM Qt-Native Input Architecture

## Background

The previous input system relied on a dual-pipeline: `DMInputManager` (a Qt event filter) handled state tracking and attempted to suppress FreeCAD UI, while Coin3D `SoEvent` callbacks (`event_cb` in `DMBase`) handled actual tool logic. This split ownership led to unresolvable conflicts, such as the impossibility of suppressing a FreeCAD context menu while simultaneously allowing Coin3D to see the right-click `MouseButtonPress` event naturally.

To fix this, Direct Modeling now uses a **Single-Owner Qt-Native architecture**, heavily inspired by complete UI-replacement add-ons like *PieMenu*.

## Core Design Principles

1. **Total Suppression via Qt:** 
   When a DM tool is active, `DMInputManager` intercepts *all* relevant Qt events (`MouseButtonPress`, `MouseButtonRelease`, `MouseMove`, `KeyPress`, `KeyRelease`, `ContextMenu`, `ShortcutOverride`) and returns `True` to prevent them from propagating to FreeCAD or Coin3D.
   
2. **Direct Dispatch:**
   Instead of waiting for a Coin3D `event_cb`, `DMInputManager` calls standard input methods on the active tool directly.

3. **Standardized Coordinates:**
   All mouse positions are passed as native Qt Top-Left `(x, y)` coordinates. The old Coin3D Y-inversion hacks are removed. FreeCAD's native `view.getRay(x, y)` natively understands Qt coordinates.

## Event Pipeline Flow

```
User Input (Qt Event)
    │
    ▼
DMInputManager.eventFilter
    │
    ├─► Unrelated Event? ──► return False (Let FreeCAD handle it)
    │
    ├─► Update Global State (_last_qt_pos, modifiers, button states)
    │
    ├─► Active Tool Exists?
    │       │
    │       ├─► Construct strict Qt event_dict:
    │       │     { "Button": event.button(),
    │       │       "Modifiers": event.modifiers(),
    │       │       "Position": (event.pos().x(), event.pos().y()),
    │       │       "Key": event.key() } (if keyboard)
    │       │
    │       ├─► Dispatch to Tool:
    │       │     tool.on_mouse_press(event_dict)
    │       │     tool.on_mouse_move(event_dict)
    │       │     tool.on_key_press(event_dict) ... etc.
    │       │
    │       └─► return True (Swallows event completely, blocking native UI/Coin3D)
    │
    └─► No Active Tool?
            └─► Handle global hotkeys (D, E) / Selection rays
            └─► return False (Let FreeCAD operate freely)
```

## Implementation Rules (for R-001 through R-005)

### `DMBase` Interface
`DMBase` drops the `self.view.addEventCallback(...)` logic. It now implements:
```python
def on_mouse_press(self, event_dict): pass
def on_mouse_release(self, event_dict): pass
def on_mouse_move(self, event_dict): pass
def on_key_press(self, event_dict): pass
def on_key_release(self, event_dict): pass
def on_context_menu(self, event_dict): pass
```

### Right Clicks
Right clicks generate `MouseButtonPress` (Qt.RightButton) and subsequently `ContextMenu` events in Qt. `DMInputManager` intercepts both. The tool should typically execute its `finish()` logic on `on_mouse_press` when the button is `Qt.RightButton`. `ContextMenu` is purely intercepted and suppressed to avoid FreeCAD menus.

### Modifier State
Always reliably tracked inside `DMInputManager` from `KeyPress` and `KeyRelease` events, accessible via `DMInputManager.get_instance().is_shift_down()`, etc. Do not trust instantaneous modifiers heavily.

### Drag Pattern
Retains the `QTimer` polling pattern (`DragTimerMixin`), reading `_last_qt_pos` on update ticks, because real-time 3D ray-marching/rendering on every `MouseMove` event would stall the UI thread.
