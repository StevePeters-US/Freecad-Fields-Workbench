# Direct Modeling Workbench — Input System Refactor Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The input system currently has several inconsistencies causing intermittent snapping, command swallowing, and flow issues. These issues stem from a problematic dual-pipeline architecture (Qt event filter vs. Coin3D `event_cb`) and stateful "silent fallbacks" that make the tool flow feel unpredictable.

The goal is to move towards a strict "single owner" model for events:
- **No silent fallbacks**: The viewport-aligned plane is the only fallback. The "last working plane" should never be silently reused.
- **Qt Filter**: Only handles state tracking and FreeCAD suppression. It should never call tool methods directly.
- **Coin3D**: Handles all tool logic.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `DMInputManager.eventFilter` | `core/input_manager.py:99` | Intercepts Qt events, tracks modifiers, steals keys |
| `DMBase.event_cb` | `tools/dm_base.py:633` | Primary Coin3D event loop for active tools |
| `PrimitiveCreatorBase._init_working_plane` | `tools/primitive_tool.py:173` | Sets initial workplane |
| `ViewProjector.get_mouse_plane_pt` | `core/view_projector.py:214` | Casts ray to find 3D point from 2D mouse position |

---

## Tier 1 — Core Input Flow (Do First)

Refactor the most problematic areas of the input pipeline: silent fallbacks and right-click event swallowing.

### I-001: Remove Last Working Plane Fallback

**File:** `tools/primitive_tool.py`

**What:** Remove the `_last_working_plane` and `_last_wp_is_fallback` class-level variables from `PrimitiveCreatorBase`. Ensure the fallback is always the viewport-aligned plane instead.

**Implementation:**
1. Delete `_last_working_plane = None` and `_last_wp_is_fallback = True` (lines 30-31).
2. Modify `_init_working_plane()` (line 173):
```python
    def _init_working_plane(self):
        """Pre-load a workplane if one isn't already detected from selection."""
        if not self.working_plane:
            visible_wps = self.get_visible_workplanes()
            if visible_wps:
                wp = visible_wps[0]
                self.working_plane = wp.getGlobalPlacement() if hasattr(wp, "getGlobalPlacement") else wp.Placement
                self._working_plane_is_fallback = False
```
3. Remove assignments to `PrimitiveCreatorBase._last_working_plane` and `PrimitiveCreatorBase._last_wp_is_fallback` in `BoxCreator.on_button1_down` (lines 520-521), and `SphereCreator.on_button1_down` (lines 756-757).

### I-002: Route Right-Click through Coin3D

**File:** `core/input_manager.py` — modify `eventFilter` right-click block (line 152)

**What:** Stop injecting a synthetic `event_dict` into `tool.on_button3_down()`. Instead, accept the event so FreeCAD doesn't open its menu, but allow Coin3D to process it naturally.

**Implementation:**
```python
            # [Event Owner: Qt Event Filter] Right-click suppression: when a tool is active,
            # consume the right mouse button press so FreeCAD's NavigationStyle never opens
            # its context menu.
            if event.type() == QtCore.QEvent.MouseButtonPress:
                if event.button() == QtCore.Qt.RightButton:
                    from core.dm_tool_manager import DMToolManager
                    tool = DMToolManager.get_instance().get_active_tool()
                    if tool and not self._middle_mouse_down:
                        event.accept()
                        return True  # suppress FreeCAD context menu - Coin3D still gets SoMouseButtonEvent
```

**Depends on:** None


### I-003: Unconditional Shortcut Stealing

**File:** `core/input_manager.py` — modify `eventFilter` ShortcutOverride block (line 170)

**What:** Claim `S`, `D`, and `E` keys unconditionally if a DM tool is active, rather than checking if they support specific menus.

**Implementation:**
Replace the `ShortcutOverride` tool check with:
```python
            if event.type() == QtCore.QEvent.ShortcutOverride:
                text = event.text().lower() if hasattr(event, "text") else ""
                from core.dm_tool_manager import DMToolManager
                tool = DMToolManager.get_instance().get_active_tool()
                if tool:
                    if text in ['s', 'd', 'e']:
                        event.accept()
                        return True
                if not tool and text == 'e':
                    # Claim 'E' when any editable DM object is selected
                    sel = FreeCADGui.Selection.getSelection()
                    if any(hasattr(o, "Proxy") and getattr(o.Proxy, "__class__", None).__name__ in ("DMWorkPlane", "DMObjectProxy") for o in sel):
                        event.accept()
                        return True
```

**Depends on:** None

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_input_refactor` | Architecture of Qt/Coin3D dual event pipeline |
| `dm_event_pipeline` | Complete flow of events from FreeCAD to DM tools |
| `dm_viewport_fallback` | Enforces the "No Silent Fallbacks" rule for workplanes |
