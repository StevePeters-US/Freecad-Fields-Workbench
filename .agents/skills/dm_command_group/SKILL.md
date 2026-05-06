---
name: DM FreeCAD Command Group (Dropdown Toolbar Buttons)
description: How to create dropdown/flyout toolbar buttons in FreeCAD using command groups. Required reading before adding grouped commands to InitGui.py.
---

# DM FreeCAD Command Group (Dropdown Toolbar Buttons)

FreeCAD toolbars can show dropdown/flyout buttons that expand to reveal related commands.
This is done by registering a "group command" that returns a list of sub-commands.

---

## Group Command Pattern

```python
class DMPrimitiveGroup:
    """Dropdown group for multiple related creation commands."""
    def GetCommands(self):
        """Return tuple of command IDs that appear in the dropdown."""
        return (
            'DM_CreateBox',
            'DM_CreateSphere',
            'DM_CreateCylinder',
        )

    def GetDefaultCommand(self):
        """Index into GetCommands() for the default (top-level) button."""
        return 0

    def GetResources(self):
        return {
            'MenuText': 'Create Primitive',
            'ToolTip':  'Create an SDF primitive shape',
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('DM_PrimitiveGroup', DMPrimitiveGroup())
```

---

## Registration in InitGui.py

Add only the **group command ID** to the toolbar — NOT the individual sub-commands:

```python
self.appendToolbar("DM - Constructive", [
    'DM_WorkPlane',
    'DM_PrimitiveGroup',   # ← dropdown appears here
    'DM_FillCurve',
])
```

Individual sub-commands must still be registered with `FreeCADGui.addCommand()` — the group
just bundles them visually.

---

## Menu Registration

For menus, list the individual commands (not the group) so each gets its own menu entry:

```python
self.appendMenu("Direct Modeling", [
    'DM_CreateBox',
    'DM_CreateSphere',
    'DM_CreateCylinder',
])
```

---

## Key Rules

1. `GetCommands()` must return a **tuple** of strings (not a list)
2. Every command ID in the tuple must already be registered via `addCommand()`
3. `GetDefaultCommand()` returns the **index** (int), not the command ID
4. The group command class does NOT need `Activated()` — clicking a sub-command calls that sub-command's `Activated()`
5. The group's `GetResources()` `Pixmap` is used only as fallback — the default sub-command's icon is normally shown

---

## Files to Read Before Editing

1. `InitGui.py` — toolbar registration
2. `commands/cmd_primitive.py` — existing flat command pattern
