# DirectModeling/InitGui.py

try:
    import shapely
except ImportError:
    from PySide.QtGui import QMessageBox
    from PySide import QtCore

    title = "Direct Modeling Workbench - Missing Dependency"
    message = """
<p>The 'shapely' library is not installed.</p>
<p>This is a required dependency for the Direct Modeling Workbench to function correctly.</p>
<p><b>Instructions:</b></p>
<ol>
<li>Open a terminal (Command Prompt on Windows).</li>
<li>Navigate to the 'bin' directory of your FreeCAD installation (C:\\Program Files\\FreeCAD 1.0\\bin).</li>
<li>If 'pip' is not available, first run: <code>python -m ensurepip</code></li>
<li>Install shapely by running: <code>python -m pip install shapely</code></li>
</ol>
<p>Please restart FreeCAD after the installation is complete.</p>
"""
    msgBox = QMessageBox()
    msgBox.setWindowTitle(title)
    msgBox.setTextFormat(QtCore.Qt.RichText)
    msgBox.setText(message)
    msgBox.setStandardButtons(QMessageBox.Ok)
    msgBox.exec_()


import FreeCAD
import FreeCADGui
import os
import sys
import inspect
import FCDirectModeling

# Ensure local imports work by adding the workbench directory to sys.path
# This is often needed if FreeCAD doesn't add it automatically
try:
    # Use inspect to get the file path since __file__ might not be defined
    wb_root = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
    if wb_root not in sys.path:
        sys.path.append(wb_root)
except Exception as e:
    FreeCAD.Console.Error("DirectModeling: Error setting up sys.path: " + str(e) + "\n")

# Register the icon path at module level so it's available immediately
# Use FCDirectModeling module location to reliably find the workbench root
wb_path = os.path.dirname(os.path.dirname(FCDirectModeling.__file__))
icon_path = os.path.join(wb_path, 'Resources', 'icons')
FreeCADGui.addIconPath(icon_path)

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
        """This function is executed when the workbench is activated."""
        # Import the command modules. This executes the FreeCADGui.addCommand()
        # in each file, making the commands available to FreeCAD.
        try:
            from dm_commands import command_create_curve
            from dm_commands import command_boolean
            from dm_commands import command_dm_settings
            from dm_commands import command_open_sketcher
            from dm_commands import command_create_point
            from dm_commands import command_translate
            
            self.appendToolbar("Direct Modeling", [
                'DM_CreatePoint',
                'DM_CreateCurve',
                'DM_Translate',
                'DM_OpenSketcher',
                'DM_Fuse',
                'DM_Cut',
                'DM_Common',
                'DM_Settings',
            ])
            self.appendMenu("Direct Modeling", [
                'DM_CreatePoint',
                'DM_CreateCurve',
                'DM_Translate',
                'DM_OpenSketcher',
                'DM_Fuse',
                'DM_Cut',
                'DM_Common',
                'DM_Settings',
            ])
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error importing Direct Modeling commands: {e}\n")
            import traceback
            traceback.print_exc()

    def Activated(self):
        """This function is executed when the workbench is activated."""
        return

    def Deactivated(self):
        """This function is executed when the workbench is deactivated."""
        return

# Add the workbench to FreeCAD's list of available workbenches
FreeCADGui.addWorkbench(DirectModelingWorkbench())