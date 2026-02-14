import sys
import os
import datetime
import traceback
import FreeCAD
import FreeCADGui

# --- DEBUG LOGGING SETUP ---
LOG_FILE = "/tmp/dm_debug.log"

def log(msg):
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except:
        print(f"DM LOG FAIL: {msg}")

log("----------------------------------------------------------------")
log("InitGUI.py execution started")

try:
    # --- PATH FIX ---
    # Explicitly add the current directory to sys.path to ensure local imports work
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.append(current_dir)
        log(f"Added {current_dir} to sys.path")
    else:
        log(f"{current_dir} already in sys.path")

    # --- IMPORTS ---
    log("Attempting to import FCDirectModeling...")
    import FCDirectModeling
    log("FCDirectModeling imported successfully")

    # Register the icon path
    wb_path = os.path.dirname(os.path.dirname(FCDirectModeling.__file__))
    icon_path = os.path.join(wb_path, 'Resources', 'icons')
    log(f"Icon path calculated: {icon_path}")
    
    if os.path.exists(icon_path):
        FreeCADGui.addIconPath(icon_path)
    else:
        log(f"ERROR: Icon path does not exist: {icon_path}")

    class DirectModelingWorkbench(FreeCADGui.Workbench):
        """
        Defines the Direct Modeling Workbench.
        """
        MenuText = "Direct Modeling"
        ToolTip = "Direct Modeling workbench"
        Icon = "DirectModeling.svg"

        def GetClassName(self):
            return "Gui::PythonWorkbench"

        def Initialize(self):
            log("Initialize() called")
            try:
                from dm_commands import command_create_box
                from dm_commands import command_open_task_panel
                from dm_commands import command_draw_box
                log("Commands imported")
                
                self.appendToolbar("Direct Modeling", [
                    'DM_DrawBox',
                    'DM_CreateBox',
                    'DM_OpenTaskPanel',
                ])
                self.appendMenu("Direct Modeling", [
                    'DM_DrawBox',
                    'DM_CreateBox',
                    'DM_OpenTaskPanel',
                ])
                log("Menu/Toolbar appended")
            except Exception as e:
                log(f"ERROR in Initialize: {e}\n{traceback.format_exc()}")
                FreeCAD.Console.Error(f"Direct Modeling Init Error: {e}\n")

        def Activated(self):
            log("Activated() called")
            return

        def Deactivated(self):
            log("Deactivated() called")
            return

    # Add the workbench
    FreeCADGui.addWorkbench(DirectModelingWorkbench())
    log("Workbench registered")

except Exception as e:
    log(f"CRITICAL ERROR in InitGUI.py: {e}\n{traceback.format_exc()}")
    FreeCAD.Console.Error(f"Direct Modeling Critical Error: {e}\n")
