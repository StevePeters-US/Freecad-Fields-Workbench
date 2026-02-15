
import FreeCAD
import FreeCADGui
from FCDirectModeling import sdf_renderer

class CreateSDFCommand:
    """
    Create an SDF Object
    """

    def GetResources(self):
        return {
            "Pixmap": "CreateBox.svg", # Reuse box icon
            "MenuText": "Create SDF Box",
            "ToolTip": "Creates a signed distance field box",
        }

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if not doc:
            doc = FreeCAD.newDocument()
        
        obj = doc.addObject("Part::FeaturePython", "SDF_Box")
        
        # Initialize Proxy
        sdf_renderer.SDFBoxFeature(obj)
        
        # Add Properties
        obj.addProperty("App::PropertyLength", "Length", "SDF", "Length of the box").Length = 10.0
        obj.addProperty("App::PropertyLength", "Width", "SDF", "Width of the box").Width = 10.0
        obj.addProperty("App::PropertyLength", "Height", "SDF", "Height of the box").Height = 10.0
        obj.addProperty("App::PropertyInteger", "Resolution", "SDF", "Grid resolution").Resolution = 32
        obj.addProperty("App::PropertyFloat", "Margin", "SDF", "Grid margin").Margin = 0.2
        
        # Slicing Properties
        obj.addProperty("App::PropertyEnumeration", "SliceAxis", "SDF", "Axis to slice along")
        obj.SliceAxis = ["X", "Y", "Z"]
        obj.SliceAxis = "Z"
        
        obj.addProperty("App::PropertyInteger", "SliceCount", "SDF", "Number of slices").SliceCount = 10
        
        # Attach ViewProvider
        if FreeCAD.GuiUp:
            sdf_renderer.SDFRenderer(obj.ViewObject)
            
        doc.recompute()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand("DM_CreateSDF", CreateSDFCommand())
