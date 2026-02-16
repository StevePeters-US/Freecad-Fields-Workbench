
import FreeCAD
import FreeCADGui
import FCDirectModeling.sdf_renderer as sdf_renderer

import FCDirectModeling.primitive_creators as primitive_creators

class CreateSDFSphere:
    def GetResources(self):
        return {'Pixmap': 'Part_Sphere_Parametric', 'MenuText': 'SDF Sphere', 'ToolTip': 'Create an SDF Sphere'}
        
    def Activated(self):
        FreeCAD.Console.PrintMessage("CreateSDFSphere: Activated!\n")
        try:
            self.creator = primitive_creators.SphereCreator()
        except Exception as e:
            FreeCAD.Console.PrintError(f"CreateSDFSphere: Error creating creator: {e}\n")
        # We don't have a task panel for these yet, just visual interaction
        
    def IsActive(self):
        return FreeCAD.activeDocument() is not None

class CreateSDFCone:
    def GetResources(self):
        return {'Pixmap': 'Part_Cone_Parametric', 'MenuText': 'SDF Cone', 'ToolTip': 'Create an SDF Cone'}
        
    def Activated(self):
        self.creator = primitive_creators.ConeCreator()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

class CreateSDFTorus:
    def GetResources(self):
        return {'Pixmap': 'Part_Torus_Parametric', 'MenuText': 'SDF Torus', 'ToolTip': 'Create an SDF Torus'}
        
    def Activated(self):
        self.creator = primitive_creators.TorusCreator()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

# Register
FreeCADGui.addCommand('SDF_Sphere', CreateSDFSphere())
FreeCADGui.addCommand('SDF_Cone', CreateSDFCone())
FreeCADGui.addCommand('SDF_Torus', CreateSDFTorus())
