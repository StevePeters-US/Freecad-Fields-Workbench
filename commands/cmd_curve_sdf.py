import FreeCAD
import FreeCADGui


class CommandDMCurveExtrude:
    def GetResources(self):
        return {
            'Pixmap':   'Part_Extrude',
            'MenuText': 'Extrude Curve',
            'ToolTip':  'Extrude a selected closed curve into an SDF solid.',
        }

    def IsActive(self):
        if FreeCAD.activeDocument() is None:
            return False
        return any(
            getattr(o, "ShapeType", None) == "curve" and getattr(o, "Closed", False)
            for o in FreeCADGui.Selection.getSelection()
        )

    def Activated(self):
        from tools.primitive_tool import CurveExtrudeCreator
        from core.input_manager import DMInputManager
        DMInputManager.get_instance()
        self.tool = CurveExtrudeCreator()

    def getIsChecked(self):
        from core.dm_tool_manager import DMToolManager
        from tools.primitive_tool import CurveExtrudeCreator
        tool = DMToolManager.get_instance().get_active_tool()
        return isinstance(tool, CurveExtrudeCreator)


FreeCADGui.addCommand('DM_ExtrudeCurve', CommandDMCurveExtrude())


class CommandDMCurvePipe:
    def GetResources(self):
        return {
            'Pixmap':   'Part_RuledSurface',
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
        from tools.primitive_tool import CurveExtrude3DCreator
        from core.input_manager import DMInputManager
        DMInputManager.get_instance()
        self.tool = CurveExtrude3DCreator()

    def getIsChecked(self):
        from core.dm_tool_manager import DMToolManager
        from tools.primitive_tool import CurveExtrude3DCreator
        tool = DMToolManager.get_instance().get_active_tool()
        return isinstance(tool, CurveExtrude3DCreator)


FreeCADGui.addCommand('DM_CurvePipe', CommandDMCurvePipe())
