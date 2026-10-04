# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip


class CommandFldCurveExtrude:
    def GetResources(self):
        return {
            'Pixmap':   'SDF_Extrude',
            'MenuText': 'Extrude SDF Face',
            'ToolTip':  rich_tooltip("Fields", QT_TRANSLATE_NOOP("Fields", "Extrudes the selected SDF face into a solid; drag in the view to set the depth.\nSelect one SDF face.")),
        }

    def IsActive(self):
        if FreeCAD.activeDocument() is None:
            return False
        return any(
            getattr(o, "ShapeType", None) == "surface"
            for o in FreeCADGui.Selection.getSelection()
        )

    def Activated(self):
        from freecad.fields.tools.creators.extrusion_creator import SdfCurveFillExtrudeCreator
        from freecad.fields.core.input.input_manager import FldInputManager
        FldInputManager.get_instance()
        self.tool = SdfCurveFillExtrudeCreator()

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        from freecad.fields.tools.creators.extrusion_creator import SdfCurveFillExtrudeCreator
        tool = FldToolManager.get_instance().get_active_tool()
        return isinstance(tool, SdfCurveFillExtrudeCreator)


FreeCADGui.addCommand('Fields_ExtrudeCurve', CommandFldCurveExtrude())


class CommandFldCurvePipe:
    def GetResources(self):
        return {
            'Pixmap':   'SDF_RuledSurface',
            'MenuText': 'Pipe (3D Curve)',
            'ToolTip':  rich_tooltip("Fields", QT_TRANSLATE_NOOP("Fields", "Creates a round tube along the selected 3D curve.\nSelect one curve.")),
        }

    def IsActive(self):
        if FreeCAD.activeDocument() is None:
            return False
        return any(
            getattr(o, "ShapeType", None) == "curve"
            for o in FreeCADGui.Selection.getSelection()
        )

    def Activated(self):
        from freecad.fields.tools.creators.extrusion_creator import CurveExtrude3DCreator
        from freecad.fields.core.input.input_manager import FldInputManager
        FldInputManager.get_instance()
        self.tool = CurveExtrude3DCreator()

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        from freecad.fields.tools.creators.extrusion_creator import CurveExtrude3DCreator
        tool = FldToolManager.get_instance().get_active_tool()
        return isinstance(tool, CurveExtrude3DCreator)


FreeCADGui.addCommand('Fields_CurvePipe', CommandFldCurvePipe())



