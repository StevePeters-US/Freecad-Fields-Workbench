
import FreeCAD
import FreeCADGui
from FCDirectModeling import sdf_renderer

class ConvertToSDFCommand:
    """
    Convert a selected Part object to an SDF Object
    """

    def GetResources(self):
        return {
            "Pixmap": "Part_Box", # Use standard Part Box icon for now or similar
            "MenuText": "Convert to SDF",
            "ToolTip": "Converts selected Part object to a Signed Distance Field",
        }

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            FreeCAD.Console.PrintWarning("Please select an object to convert.\n")
            return

        doc = FreeCAD.activeDocument()
        doc.openTransaction("Convert to SDF")

        try:
            for obj in sel:
                if self.is_box(obj):
                    self.convert_box(doc, obj)
                else:
                    FreeCAD.Console.PrintWarning(f"Object {obj.Name} is not a supported shape type for SDF conversion yet.\n")
            
            doc.commitTransaction()
            doc.recompute()
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error converting to SDF: {e}\n")
            doc.abortTransaction()

    def is_box(self, obj):
        # Check if object is a primitive Box or has Box-like shape
        # For simplicity, we check if it has Length, Width, Height properties
        # or if it is a Part::Box
        if hasattr(obj, "Proxy") and isinstance(obj.Proxy, FreeCAD.Part.Box):
             return True
        
        # Check standard properties
        has_dims = hasattr(obj, "Length") and hasattr(obj, "Width") and hasattr(obj, "Height")
        if has_dims:
            return True
            
        return False

    def convert_box(self, doc, source_obj):
        # Create new SDF Box
        new_obj = doc.addObject("Part::FeaturePython", f"SDF_{source_obj.Name}")
        
        # Initialize Proxy
        sdf_renderer.SDFBoxFeature(new_obj)
        
        # Add Properties
        new_obj.addProperty("App::PropertyLength", "Length", "SDF", "Length of the box").Length = source_obj.Length
        new_obj.addProperty("App::PropertyLength", "Width", "SDF", "Width of the box").Width = source_obj.Width
        new_obj.addProperty("App::PropertyLength", "Height", "SDF", "Height of the box").Height = source_obj.Height
        new_obj.addProperty("App::PropertyInteger", "Resolution", "SDF", "Grid resolution").Resolution = 32
        new_obj.addProperty("App::PropertyFloat", "Margin", "SDF", "Grid margin").Margin = 0.2
        
        # Copy Placement
        new_obj.Placement = source_obj.Placement
        
        # Attach ViewProvider
        if FreeCAD.GuiUp:
            sdf_renderer.SDFRenderer(new_obj.ViewObject)
            
        # Hide original
        if hasattr(source_obj, "ViewObject"):
            source_obj.ViewObject.Visibility = False
            
        FreeCAD.Console.PrintMessage(f"Converted {source_obj.Name} to SDF.\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None and len(FreeCADGui.Selection.getSelection()) > 0

FreeCADGui.addCommand("DM_ConvertToSDF", ConvertToSDFCommand())
