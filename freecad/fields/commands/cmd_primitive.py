# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui

class CommandFldCreatePrimitive:
    _ICONS = {
        "Box": "Fields_CreateBox",
        "Sphere": "Fields_CreateSphere",
        "Cylinder": "Fields_CreateCylinder",
        "Torus": "Fields_CreateTorus",
        "Prism": "Part_Prism",
        "Revolve": "Part_Revolution",
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

    def _resolve_creator_class(self):
        from freecad.fields.tools.creators.box_creator import BoxCreator
        from freecad.fields.tools.creators.sphere_creator import SphereCreator
        from freecad.fields.tools.creators.cylinder_creator import CylinderCreator
        from freecad.fields.tools.creators.torus_creator import TorusCreator
        from freecad.fields.tools.creators.prism_creator import PrismCreator
        from freecad.fields.tools.creators.revolve_creator import RevolveCreator

        creators = {
            "Box": BoxCreator,
            "Sphere": SphereCreator,
            "Cylinder": CylinderCreator,
            "Torus": TorusCreator,
            "Prism": PrismCreator,
            "Revolve": RevolveCreator,
        }
        return creators.get(self.c_type)

    def Activated(self):
        cls = self._resolve_creator_class()
        if cls:
            self.tool = cls()

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        active_tool = FldToolManager.get_instance().get_active_tool()
        if not active_tool:
            return False
        expected = self._resolve_creator_class()
        return isinstance(active_tool, expected) if expected else False

FreeCADGui.addCommand('Fields_CreateBox', CommandFldCreatePrimitive("Box"))
FreeCADGui.addCommand('Fields_CreateSphere', CommandFldCreatePrimitive("Sphere"))
FreeCADGui.addCommand('Fields_CreateCylinder', CommandFldCreatePrimitive("Cylinder"))
FreeCADGui.addCommand('Fields_CreateTorus', CommandFldCreatePrimitive("Torus"))
FreeCADGui.addCommand('Fields_CreatePrism', CommandFldCreatePrimitive("Prism"))
FreeCADGui.addCommand('Fields_CreateRevolve', CommandFldCreatePrimitive("Revolve"))
