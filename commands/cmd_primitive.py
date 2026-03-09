import FreeCAD
import FreeCADGui
from core import dm_logger
from core.dm_object import get_frep_storage_type

class CommandDMCreation:
    _ICONS = {
        "Box": "CreateBox",
        "Sphere": "CreateSphere",
        "Cylinder": "CreateCylinder"
    }

    def __init__(self, c_type="Box"):
        self.c_type = c_type

    def GetResources(self):
        return {
            'Pixmap': self._ICONS.get(self.c_type, f"Part_{self.c_type}"),
            'MenuText': f"Create {self.c_type}",
            'ToolTip': f"Interactive F-Rep {self.c_type} creation tool."
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        # We want to wait to import the tool to avoid circular dependencies
        from tools.primitive_tool import BoxCreator, SphereCreator, CylinderCreator
        from core.input_manager import DMInputManager
        
        manager = DMInputManager.get_instance()
        
        if self.c_type == "Box":
            self.tool = BoxCreator()
        elif self.c_type == "Sphere":
            self.tool = SphereCreator()
        elif self.c_type == "Cylinder":
            self.tool = CylinderCreator()
        else:
            return

FreeCADGui.addCommand('DM_CreateBox', CommandDMCreation("Box"))
FreeCADGui.addCommand('DM_CreateSphere', CommandDMCreation("Sphere"))
FreeCADGui.addCommand('DM_CreateCylinder', CommandDMCreation("Cylinder"))
