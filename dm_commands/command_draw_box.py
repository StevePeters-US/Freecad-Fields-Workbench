import FreeCAD
import FreeCADGui
from FCDirectModeling import box_creator, box_task_panel

class DrawBoxCommand:
    """
    Interactive tool to draw a box.
    """

    def GetResources(self):
        return {
            "Pixmap": "CreateBox.svg", # We can reuse the icon for now or use "Draft_Rectangle" if available defaults
            "MenuText": "Draw Box",
            "ToolTip": "Draw a box by dragging base and height",
        }

    def Activated(self):
        import importlib
        from FCDirectModeling import box_creator, box_task_panel, sdf_utils
        importlib.reload(sdf_utils)
        importlib.reload(box_creator)
        importlib.reload(box_task_panel)
        
        # Instantiate the creator. It attaches itself to the view.
        self.creator = box_creator.BoxCreator()
        
        # Instantiate and show task panel
        self.panel = box_task_panel.BoxTaskPanel(self.creator)
        self.creator.set_panel(self.panel)
        FreeCADGui.Control.showDialog(self.panel)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand("DM_DrawBox", DrawBoxCommand())
