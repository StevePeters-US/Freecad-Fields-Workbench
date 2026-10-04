# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip


class CommandFldExtrudeAlongPath:
    def GetResources(self):
        return {
            'Pixmap': 'SDF_Sweep',
            'MenuText': 'Extrude Along Path',
            'ToolTip': rich_tooltip("Fields", QT_TRANSLATE_NOOP("Fields", "Sweeps an SDF profile or solid along a 3D curve.\nSelect the profile and the path curve.")),
        }

    def IsActive(self):
        if FreeCAD.activeDocument() is None:
            return False
        return any(
            getattr(o, "ShapeType", None) == "curve"
            for o in FreeCADGui.Selection.getSelection()
        )

    def Activated(self):
        from freecad.fields.tools.creators.path_extrude_creator import PathExtrudeCreator
        from freecad.fields.core.input.input_manager import FldInputManager
        FldInputManager.get_instance()
        # The tool shows its own task panel from _post_init, like every other
        # creator. Building one here as well stacked two dialogs in the task view.
        self.tool = PathExtrudeCreator()

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        from freecad.fields.tools.creators.path_extrude_creator import PathExtrudeCreator
        tool = FldToolManager.get_instance().get_active_tool()
        return isinstance(tool, PathExtrudeCreator)


FreeCADGui.addCommand('Fields_ExtrudeAlongPath', CommandFldExtrudeAlongPath())
