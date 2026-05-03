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
