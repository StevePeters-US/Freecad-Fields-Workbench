# FreeCAD Direct Modeling Workbench

This is a basic structure for a Direct Modeling workbench for FreeCAD.

## Installation

To install this workbench, you can either:

1.  Copy the `DirectModeling` directory into your FreeCAD Mod directory.
2.  Symlink the `DirectModeling` directory into your FreeCAD Mod directory.

The FreeCAD Mod directory is typically located at:
*   **Windows:** `%APPDATA%\FreeCAD\Mod`
*   **macOS:** `~/Library/Application Support/FreeCAD/Mod`
*   **Linux:** `~/.FreeCAD/Mod`

## Structure

*   `FCDirectModeling/`: The main package directory (renamed from DirectModeling to avoid conflicts).
    *   `Init.py`: Initializes the module.
    *   `InitGUI.py`: Initializes the workbench GUI, menus, and toolbars.
    *   `__init__.py`: Module initializer.
    *   `commands/`: Contains the workbench commands.
        *   `command_create_box.py`: A simple command to create a box.
        *   `command_open_task_panel.py`: A command to open a task panel.
    *   `resources/`: Contains resources like icons.
        *   `icons/`: SVG icons for the workbench and commands.
    *   `task_panel.py`: A simple task panel UI.

## Usage

Once installed, you can activate the "Direct Modeling" workbench from the workbench selector in FreeCAD.

You will find two commands in the "Direct Modeling" menu and toolbar:
*   **Create Box**: Creates a simple box in the active document.
*   **Open Task Panel**: Opens a sample task panel.
