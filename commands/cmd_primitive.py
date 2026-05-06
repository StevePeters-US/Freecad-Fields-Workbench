import FreeCAD
import FreeCADGui

class CommandDMCreation:
    _ICONS = {
        "Box": "CreateBox",
        "Sphere": "CreateSphere",
        "Cylinder": "CreateCylinder",
        "Torus": "CreateTorus",
        "Prism": "Part_Prism",
        "Revolve": "Part_Revolution",
    }
    _CREATORS = {
        "Box": "BoxCreator",
        "Sphere": "SphereCreator",
        "Cylinder": "CylinderCreator",
        "Torus": "TorusCreator",
        "Prism": "PrismCreator",
        "Revolve": "RevolveCreator",
    }

    def __init__(self, c_type="Box"):
        self.c_type = c_type

    def GetResources(self):
        return {
            'Pixmap': self._ICONS.get(self.c_type, f"Part_{self.c_type}"),
            'MenuText': f"Create {self.c_type}",
            'ToolTip': f"Interactive SDF {self.c_type} creation tool."
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        from tools.primitive_tool import (
            BoxCreator, SphereCreator, CylinderCreator, TorusCreator,
            PrismCreator, RevolveCreator,
        )
        from core.input_manager import DMInputManager

        DMInputManager.get_instance()

        creators = {
            "Box": BoxCreator,
            "Sphere": SphereCreator,
            "Cylinder": CylinderCreator,
            "Torus": TorusCreator,
            "Prism": PrismCreator,
            "Revolve": RevolveCreator,
        }
        cls = creators.get(self.c_type)
        if cls:
            self.tool = cls()

    def getIsChecked(self):
        from core.dm_tool_manager import DMToolManager
        active_tool = DMToolManager.get_instance().get_active_tool()
        if not active_tool:
            return False
        expected = self._CREATORS.get(self.c_type)
        return type(active_tool).__name__ == expected if expected else False

FreeCADGui.addCommand('DM_CreateBox', CommandDMCreation("Box"))
FreeCADGui.addCommand('DM_CreateSphere', CommandDMCreation("Sphere"))
FreeCADGui.addCommand('DM_CreateCylinder', CommandDMCreation("Cylinder"))
FreeCADGui.addCommand('DM_CreateTorus', CommandDMCreation("Torus"))
FreeCADGui.addCommand('DM_CreatePrism', CommandDMCreation("Prism"))
FreeCADGui.addCommand('DM_CreateRevolve', CommandDMCreation("Revolve"))
