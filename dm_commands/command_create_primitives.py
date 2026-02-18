
import FreeCAD
import FreeCADGui
import FCDirectModeling.sdf_renderer as sdf_renderer
import FCDirectModeling.primitives as primitive_creators


class CreateSDFSphere:
    def GetResources(self):
        return {'Pixmap': 'Part_Sphere_Parametric', 'MenuText': 'SDF Sphere', 'ToolTip': 'Create an SDF Sphere'}

    def Activated(self):
        import FCDirectModeling.sdf_lib as sdf_lib
        import FCDirectModeling.sdf_renderer as sdf_renderer
        import FCDirectModeling.primitives as primitive_creators
        import FCDirectModeling.sdf_utils as sdf_utils
        import FCDirectModeling.primitives.base as _base
        import FCDirectModeling.primitives.sphere_creator as _sphere

        FreeCAD.Console.PrintMessage("CreateSDFSphere: Activated!\n")
        try:
            self.creator = primitive_creators.SphereCreator()
        except Exception as e:
            FreeCAD.Console.PrintError(f"CreateSDFSphere: Error: {e}\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


class CreateSDFCone:
    def GetResources(self):
        return {'Pixmap': 'Part_Cone_Parametric', 'MenuText': 'SDF Cone', 'ToolTip': 'Create an SDF Cone'}

    def Activated(self):
        import FCDirectModeling.sdf_lib as sdf_lib
        import FCDirectModeling.sdf_renderer as sdf_renderer
        import FCDirectModeling.primitives as primitive_creators
        import FCDirectModeling.sdf_utils as sdf_utils
        import FCDirectModeling.primitives.base as _base
        import FCDirectModeling.primitives.cone_creator as _cone

        try:
            self.creator = primitive_creators.ConeCreator()
        except Exception as e:
            FreeCAD.Console.PrintError(f"CreateSDFCone: Error: {e}\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


class CreateSDFTorus:
    def GetResources(self):
        return {'Pixmap': 'Part_Torus_Parametric', 'MenuText': 'SDF Torus', 'ToolTip': 'Create an SDF Torus'}

    def Activated(self):
        import FCDirectModeling.sdf_lib as sdf_lib
        import FCDirectModeling.sdf_renderer as sdf_renderer
        import FCDirectModeling.primitives as primitive_creators
        import FCDirectModeling.sdf_utils as sdf_utils
        import FCDirectModeling.primitives.base as _base
        import FCDirectModeling.primitives.torus_creator as _torus

        try:
            self.creator = primitive_creators.TorusCreator()
        except Exception as e:
            FreeCAD.Console.PrintError(f"CreateSDFTorus: Error: {e}\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


# Register
FreeCADGui.addCommand('SDF_Sphere', CreateSDFSphere())
FreeCADGui.addCommand('SDF_Cone', CreateSDFCone())
FreeCADGui.addCommand('SDF_Torus', CreateSDFTorus())
