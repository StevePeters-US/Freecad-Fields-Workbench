import FreeCAD
import FreeCADGui
from FCDirectModeling.primitives import box_creator, box_task_panel

class CreateBoxCommand:
    """
    Create a BRep Box Object
    """

    def GetResources(self):
        return {
            "Pixmap": "Part_Box_Parametric", # Standard Box Icon
            "MenuText": "Create Box",
            "ToolTip": "Creates a Native BRep box",
        }

    def Activated(self):
        from FCDirectModeling.primitives import box_creator, box_task_panel

        # Instantiate the creator. It attaches itself to the view.
        self.creator = box_creator.BoxCreator()
        
        # Instantiate and show task panel
        self.panel = box_task_panel.BoxTaskPanel(self.creator)
        self.creator.set_panel(self.panel)
        FreeCADGui.Control.showDialog(self.panel)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand("DM_CreateBox", CreateBoxCommand())
