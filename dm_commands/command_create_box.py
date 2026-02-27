import FreeCAD
import FreeCADGui
from FCDirectModeling import dm_logger

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
        dm_logger.debug("DEBUG: Box Command Activated")
        from FCDirectModeling.primitives import box_creator, box_task_panel
        dm_logger.debug("DEBUG: Imported modules")

        # Instantiate the creator. It attaches itself to the view.
        dm_logger.debug("DEBUG: Instantiating BoxCreator...")
        self.creator = box_creator.BoxCreator()
        dm_logger.debug("DEBUG: BoxCreator instantiated")
        
        # Instantiate and show task panel
        dm_logger.debug("DEBUG: Instantiating BoxTaskPanel...")
        self.panel = box_task_panel.BoxTaskPanel(self.creator)
        dm_logger.debug("DEBUG: BoxTaskPanel instantiated")

        self.creator.set_panel(self.panel)
        dm_logger.debug("DEBUG: Showing dialog...")
        FreeCADGui.Control.showDialog(self.panel)
        dm_logger.debug("DEBUG: Dialog shown")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand("DM_CreateBox", CreateBoxCommand())
