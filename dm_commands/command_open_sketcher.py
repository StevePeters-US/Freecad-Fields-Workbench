"""
dm_commands/command_open_sketcher.py

Provides a toolbar button that switches to the Sketcher workbench, optionally
creating a new sketch on the active face or on the XY plane if nothing is selected.
"""

import FreeCAD
import FreeCADGui
from FCDirectModeling import sdf_logger


class DMOpenSketcherCommand:
    """Switch to the Sketcher workbench and optionally start a new sketch."""

    def GetResources(self):
        return {
            "Pixmap":  "OpenSketcher.svg",
            "MenuText": "Open Sketcher",
            "ToolTip":  "Switch to Sketcher workbench to draw profiles for SDF extrusions",
            "Accel":    "S, K",
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        sdf_logger.info("DM_OpenSketcher: Switching to Sketcher workbench...")
        try:
            FreeCADGui.activateWorkbench("SketcherWorkbench")
            sdf_logger.info("DM_OpenSketcher: Sketcher workbench activated.")
        except Exception as e:
            FreeCAD.Console.PrintError(f"DM_OpenSketcher: Failed to open Sketcher: {e}\n")


FreeCADGui.addCommand("DM_OpenSketcher", DMOpenSketcherCommand())
