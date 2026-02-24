import FreeCAD
import FreeCADGui
from FCDirectModeling import primitives as primitive_creators

class CreateSphere:
    def GetResources(self):
        return {'Pixmap': 'Part_Sphere_Parametric', 'MenuText': 'Create Sphere', 'ToolTip': 'Create a Native BRep Sphere'}

    def Activated(self):
        try:
            self.creator = primitive_creators.SphereCreator()
        except Exception as e:
            FreeCAD.Console.PrintError(f"CreateSphere: Error: {e}\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

class CreateCone:
    def GetResources(self):
        return {'Pixmap': 'Part_Cone_Parametric', 'MenuText': 'Create Cone', 'ToolTip': 'Create a Native BRep Cone'}

    def Activated(self):
        try:
            self.creator = primitive_creators.ConeCreator()
        except Exception as e:
            FreeCAD.Console.PrintError(f"CreateCone: Error: {e}\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

class CreateTorus:
    def GetResources(self):
        return {'Pixmap': 'Part_Torus_Parametric', 'MenuText': 'Create Torus', 'ToolTip': 'Create a Native BRep Torus'}

    def Activated(self):
        try:
            self.creator = primitive_creators.TorusCreator()
        except Exception as e:
            FreeCAD.Console.PrintError(f"CreateTorus: Error: {e}\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

# Register
FreeCADGui.addCommand('DM_CreateSphere', CreateSphere())
FreeCADGui.addCommand('DM_CreateCone', CreateCone())
FreeCADGui.addCommand('DM_CreateTorus', CreateTorus())
