
import FreeCAD
import FreeCADGui
import FCDirectModeling.sdf_renderer as sdf_renderer

import FCDirectModeling.primitive_creators as primitive_creators

class CreateSDFSphere:
    def GetResources(self):
        return {'Pixmap': 'Part_Sphere_Parametric', 'MenuText': 'SDF Sphere', 'ToolTip': 'Create an SDF Sphere'}
        
    def Activated(self):
        # Hot Reload for Development
        import importlib
        import FCDirectModeling.sdf_lib as sdf_lib
        import FCDirectModeling.marching_cubes as marching_cubes
        import FCDirectModeling.sdf_renderer as sdf_renderer
        import FCDirectModeling.primitive_creators as primitive_creators
        import FCDirectModeling.sdf_utils as sdf_utils
        
        importlib.reload(sdf_lib)
        importlib.reload(marching_cubes)
        importlib.reload(sdf_renderer)
        importlib.reload(primitive_creators)
        importlib.reload(sdf_utils)
        
        FreeCAD.Console.PrintMessage("CreateSDFSphere: Activated! (Reloaded modules v16)\n")
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
        import importlib
        import FCDirectModeling.sdf_lib as sdf_lib
        import FCDirectModeling.sdf_renderer as sdf_renderer
        import FCDirectModeling.primitive_creators as primitive_creators
        importlib.reload(sdf_lib)
        importlib.reload(sdf_renderer)
        importlib.reload(primitive_creators)
        
        self.creator = primitive_creators.ConeCreator()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

class CreateSDFTorus:
    def GetResources(self):
        return {'Pixmap': 'Part_Torus_Parametric', 'MenuText': 'SDF Torus', 'ToolTip': 'Create an SDF Torus'}
        
    def Activated(self):
        import importlib
        import FCDirectModeling.sdf_lib as sdf_lib
        import FCDirectModeling.sdf_renderer as sdf_renderer
        import FCDirectModeling.primitive_creators as primitive_creators
        importlib.reload(sdf_lib)
        importlib.reload(sdf_renderer)
        importlib.reload(primitive_creators)
        
        self.creator = primitive_creators.TorusCreator()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

# Register
FreeCADGui.addCommand('SDF_Sphere', CreateSDFSphere())
FreeCADGui.addCommand('SDF_Cone', CreateSDFCone())
FreeCADGui.addCommand('SDF_Torus', CreateSDFTorus())
