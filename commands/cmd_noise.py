import FreeCAD
import FreeCADGui
from core import dm_logger

class CommandDMNoise:
    def GetResources(self):
        return {
            'Pixmap': 'Part_Shape', # Standard FreeCAD part icon for now
            'MenuText': 'Add Noise Modifier',
            'ToolTip': 'Add a procedural noise modifier to the selected SDF object.'
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            dm_logger.error("Please select an SDF object first.")
            return
            
        obj = sel[0]
        st = getattr(obj, "ShapeType", None)
        if st != "sdf":
            dm_logger.error("Selected object must be an SDF object.")
            return

        from core.dm_noise_object import create_noise_modifier
        
        # Hide original
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            obj.ViewObject.Visibility = False
            
        # Create noise modifier
        mod_obj = create_noise_modifier(f"{obj.Name}_Noise", obj)
        
        # Launch tool
        from tools.noise_tool import NoiseTool
        tool = NoiseTool()
        tool.edit_object(mod_obj)

FreeCADGui.addCommand('DM_CreateNoiseModifier', CommandDMNoise())
