
import FreeCAD
import FreeCADGui
import FCDirectModeling.sdf_renderer as sdf_renderer

class SDFBooleanBase:
    def GetResources(self):
        return {'Pixmap': 'Part_Boolean', 'MenuText': 'SDF Boolean', 'ToolTip': 'SDF Boolean Operation'}

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) < 2:
            FreeCAD.Console.PrintError("Select at least two SDF objects.\n")
            return

        base = sel[0]
        tool = sel[1]
        
        doc = FreeCAD.activeDocument()
        doc.openTransaction("SDF Boolean")
        
        try:
            # Create Object
            obj = doc.addObject("Part::FeaturePython", f"SDF_{self.OPERATION}")
            sdf_renderer.SDFBooleanFeature(obj)
            
            # Properties
            obj.addProperty("App::PropertyEnumeration", "Operation", "SDF", "Boolean Operation")
            obj.Operation = ["Union", "Difference", "Intersection"]
            obj.Operation = self.OPERATION
            
            obj.addProperty("App::PropertyLink", "Base", "SDF", "Base Object").Base = base
            obj.addProperty("App::PropertyLink", "Tool", "SDF", "Tool Object").Tool = tool
            
            # Copy render settings from Base
            if hasattr(base, "Resolution"):
                obj.addProperty("App::PropertyInteger", "Resolution", "SDF", "Grid resolution").Resolution = base.Resolution
            else:
                 obj.addProperty("App::PropertyInteger", "Resolution", "SDF", "Grid resolution").Resolution = 32
                 
            if hasattr(base, "Margin"):
                obj.addProperty("App::PropertyFloat", "Margin", "SDF", "Grid margin").Margin = base.Margin
            else:
                obj.addProperty("App::PropertyFloat", "Margin", "SDF", "Grid margin").Margin = 0.2
            
            # Wireframe Props (Copy from Base)
            if hasattr(base, "WireframeColor"):
                 obj.addProperty("App::PropertyColor", "WireframeColor", "SDF", "Wireframe Color").WireframeColor = base.WireframeColor
            else:
                 obj.addProperty("App::PropertyColor", "WireframeColor", "SDF", "Wireframe Color").WireframeColor = (1.0, 1.0, 0.0)
                 
            if hasattr(base, "WireframeWidth"):
                 obj.addProperty("App::PropertyFloat", "WireframeWidth", "SDF", "Wireframe Width").WireframeWidth = base.WireframeWidth
            else:
                 obj.addProperty("App::PropertyFloat", "WireframeWidth", "SDF", "Wireframe Width").WireframeWidth = 2.0
            
            if hasattr(base, "ShowVertices"):
                 obj.addProperty("App::PropertyBool", "ShowVertices", "SDF", "Show Vertices").ShowVertices = base.ShowVertices
            else:
                 obj.addProperty("App::PropertyBool", "ShowVertices", "SDF", "Show Vertices").ShowVertices = True
                 
            if hasattr(base, "VertexSize"):
                 obj.addProperty("App::PropertyFloat", "VertexSize", "SDF", "Vertex Size").VertexSize = base.VertexSize
            else:
                 obj.addProperty("App::PropertyFloat", "VertexSize", "SDF", "Vertex Size").VertexSize = 5.0
            
            # ViewProvider
            if FreeCAD.GuiUp:
                sdf_renderer.SDFRenderer(obj.ViewObject)
                # Hide originals
                base.ViewObject.Visibility = False
                tool.ViewObject.Visibility = False
            
            doc.recompute()
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"SDF Boolean Failed: {e}\n")
            
        doc.commitTransaction()

    def IsActive(self):
        return len(FreeCADGui.Selection.getSelection()) >= 2

class SDFUnionCommand(SDFBooleanBase):
    OPERATION = "Union"
    def GetResources(self):
        return {'Pixmap': 'Part_Union', 'MenuText': 'SDF Union', 'ToolTip': 'Union of two SDF objects'}

class SDFDifferenceCommand(SDFBooleanBase):
    OPERATION = "Difference"
    def GetResources(self):
        return {'Pixmap': 'Part_Cut', 'MenuText': 'SDF Difference', 'ToolTip': 'Difference (Base - Tool) of two SDF objects'}

class SDFIntersectionCommand(SDFBooleanBase):
    OPERATION = "Intersection"
    def GetResources(self):
        return {'Pixmap': 'Part_Common', 'MenuText': 'SDF Intersection', 'ToolTip': 'Intersection of two SDF objects'}

FreeCADGui.addCommand('SDF_Union', SDFUnionCommand())
FreeCADGui.addCommand('SDF_Difference', SDFDifferenceCommand())
FreeCADGui.addCommand('SDF_Intersection', SDFIntersectionCommand())
