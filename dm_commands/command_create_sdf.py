import FreeCAD
import FreeCADGui
from FCDirectModeling import sdf_renderer, box_creator, box_task_panel

class CreateSDFCommand:
    """
    Create an SDF Object
    """

    def GetResources(self):
        return {
            "Pixmap": "Part_Box_Parametric", # Standard Box Icon
            "MenuText": "Create SDF Box",
            "ToolTip": "Creates a signed distance field box",
        }

    def Activated(self):
        import importlib
        from FCDirectModeling import sdf_renderer, box_creator, box_task_panel, sdf_utils
        importlib.reload(sdf_utils)
        importlib.reload(sdf_renderer)
        importlib.reload(box_creator)
        importlib.reload(box_task_panel)

        # Instantiate the creator. It attaches itself to the view.
        self.creator = box_creator.BoxCreator()
        self.creator.create_sdf_mode = True
        
        # Instantiate and show task panel
        self.panel = box_task_panel.BoxTaskPanel(self.creator)
        self.creator.set_panel(self.panel)
        FreeCADGui.Control.showDialog(self.panel)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand("DM_CreateSDF", CreateSDFCommand())
