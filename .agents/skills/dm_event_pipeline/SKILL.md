---
name: DM Event Pipeline Reference
description: How input events flow through the Direct Modeling workbench. Required reading before touching input handling, hotkeys, or tool event_cb / handle_keyboard methods.
---

# DM Event Pipeline Reference

There is **one primary event channel** — Coin3D — plus a thin Qt layer for key-stealing only.

---

## Channel 1 — Qt Level (DMInputManager) — Key Stealing Only

**File**: `core/input_manager.py`

```
QApplication event
    → DMInputManager.eventFilter(obj, event)
        → ShortcutOverride 'S'/'D': accept if DMBase.active_tool supports them
        → KeyPress 'D' (no active tool): show default DM context menu
        → ContextMenu event: suppress if menu already open
        → Modifier tracking: _shift_down, _control_down, _middle_mouse_down
```

- **Does not broadcast events to tools** — no signal, no `_on_input_event`
- Its only job is to prevent FreeCAD menus from consuming 'S' and 'D' before Coin3D gets them
- Accepting a ShortcutOverride routes the subsequent KeyPress to the focused widget (the Coin3D viewer), which then generates a `SoKeyboardEvent` that fires `event_cb`

---

## Channel 2 — Coin3D Level (DMBase.event_cb) — All Tool Logic

**File**: `tools/dm_base.py`

```
Coin3D SoEvent (3D viewport only)
    → DMBase.event_cb(event_dict)
        → SoMouseButtonEvent → on_button1/2/3_down/up()
        → SoLocation2Event   → handle_move(event_dict)
        → SoKeyboardEvent    → handle_keyboard(event_dict)
```

- Registered in `DMBase.__init__()` via `view.addEventCallback("SoEvent", self.event_cb)`
- Fires **only** for events inside the 3D viewport
- `event_dict` contains `"Position"` (Coin3D coordinates, **Y=bottom**)

### handle_keyboard key map (dm_base.py)

| Key | Action |
|-----|--------|
| ESC | `terminate()` |
| ENTER | `finish()` |
| C | `toggle_cutter_mode()` |
| X / Y / Z | `toggle_axis()` |
| R | `reset_state()` |
| SHIFT | `on_tool_option_0()` |
| CTRL | `on_tool_option_1()` → in NURBSPrimitiveCreator: `toggle_snapping()` |
| S | `trigger_dynamic_menu(get_snapping_menu())` |
| D | `trigger_dynamic_menu(get_context_menu())` or `on_tool_menu()` |

---

## The Y-Flip Problem

| Source | Y origin | Key in event_dict |
|--------|----------|-------------------|
| Qt     | **top**  | `"QtPosition"`    |
| Coin3D | **bottom** | `"Position"`   |

`DMInputManager.get_mouse_pos(event_dict)` handles the flip. Always pass the raw `event_dict` from `event_cb` — do not manually reconstruct it.

---

## Adding a New Hotkey — Correct Pattern

Example: add 'F' to focus camera to the curve plane.

1. **Claim it in `eventFilter`** so FreeCAD menus don't eat it (only needed if the key has a FreeCAD menu binding):
   ```python
   # in core/input_manager.py eventFilter, ShortcutOverride block:
   if text == 'f' and tool:
       event.accept()
       return True
   ```

2. **Handle it in `handle_keyboard`** (in the tool subclass or `dm_base.py`):
   ```python
   if key == "F":
       self._focus_camera_to_plane()
       return True
   ```

That's it — one place, Coin3D path only.

---

## Known Issues

- `ViewProjector.get_geometry_info()` **does not exist** — `work_plane_tool.py:148` calls it, which will crash.
