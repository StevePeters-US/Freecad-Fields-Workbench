# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui


class CommandFldCurveExtrude:
    def GetResources(self):
        return {
            'Pixmap':   'SDF_Extrude',
            'MenuText': 'Extrude SDF Face',
            'ToolTip':  'Extrude a selected SDF face into a solid. Drag to set depth.',
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
            'ToolTip':  'Create a round tube swept along a selected 3D curve path.',
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



