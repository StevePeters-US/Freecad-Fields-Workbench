---
name: FreeCAD UI Overriding (PieMenu)
description: Reference for how the PieMenu add-on replaces FreeCAD's native UI elements by shadowing commands, clearing toolbars, and using Qt event filters.
---

# FreeCAD UI Overriding (PieMenu)

This skill describes the mechanisms used by the PieMenu add-on to provide a complete UI replacement while preserving underlying functionality.

## Core Mechanisms

| Step | What the add-on does | How it overrides the UI |
|------|----------------------|--------------------------|
| 1️⃣ **Command registration** | Defines Python classes that implement FreeCAD command methods (`GetResources`, `Activated`, …) and registers them with `FreeCADGui.addCommand`. | If a new command uses the same name as an existing one, it **shadows** the original, so calls to that name invoke the PieMenu version. |
| 2️⃣ **Toolbar/menu clearing** | Retrieves the main window (`FreeCADGui.getMainWindow()`), iterates over `QToolBar` objects, and calls `clear()` on each. | Removes all default actions, eliminating the built-in toolbar entries. |
| 3️⃣ **Injecting pie-menu triggers** | Creates `QAction` objects (icon + text) that are connected to a `showPieMenu` slot, then adds them to the main window’s toolbar (`addToolBar`). | The only visible UI elements become the pie-menu buttons; clicking them opens the radial menu. |
| 4️⃣ **Qt event filter** | Subclasses `QtCore.QObject` and implements `eventFilter`. It intercepts `QEvent.ContextMenu` events, calls `showPieMenu`, and returns `True`. | Prevents the default right-click context menu from appearing and substitutes the custom pie menu. |
| 5️⃣ **Dynamic pie construction** | When a pie is requested, the add-on calls `FreeCADGui.listCommands()` to get all registered commands, filters them based on the current workbench/selection, and creates a slice for each relevant command. | The pie menu shows the same functionality as the original UI but arranged radially. |
| 6️⃣ **Persistence** | Writes user preferences (which commands belong to which pie) to `~/.FreeCAD/PieMenu.cfg` and reads this file on startup. | Ensures the overridden UI is re-applied every time FreeCAD launches. |

## Flow Overview (pseudocode)

```python
import FreeCADGui
from PySide import QtGui, QtCore

# 1. Register custom commands (shadow originals)
class PieCutCommand:
    def GetResources(self):
        return {'MenuText': 'Cut', 'ToolTip': 'Pie Cut'}
    def Activated(self):
        print("Pie Cut Activated")

FreeCADGui.addCommand('Cut', PieCutCommand())

# 2. On startup, clear default UI
mw = FreeCADGui.getMainWindow()
for tb in mw.findChildren(QtGui.QToolBar):
    tb.clear()

# 3. Add pie-menu actions
def showPieMenu(pos=None):
    if pos is None:
        pos = QtGui.QCursor.pos()
    cmds = FreeCADGui.listCommands()
    # Build and exec pie menu at pos
    print(f"Showing Pie Menu at {pos}")

action = QtGui.QAction('Pie Menu', mw)
action.triggered.connect(showPieMenu)
mw.addToolBar(action)

# 4. Install event filter to replace context menus
class PieEventFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.ContextMenu:
            showPieMenu(event.globalPos())
            return True
        return False

filter = PieEventFilter(mw)
mw.installEventFilter(filter)
```

## Key Takeaway

The add-on **removes** FreeCAD’s default toolbar actions, **injects** its own actions that launch radial menus, and **intercepts** context-menu events via a Qt event filter. By registering commands that shadow the originals and dynamically populating the pies, it provides a complete UI replacement while preserving underlying functionality.
