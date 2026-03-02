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

class DirectModelingWorkbench(Workbench):
    "Direct Modeling workbench object"
    Icon = os.path.join(os.path.dirname(inspect.getfile(inspect.currentframe())), 'Resources', 'icons', 'DirectModeling.svg')
    MenuText = "Direct Modeling"
    ToolTip = "Real-time NURBS and BRep modeling"

    def GetClassName(self):
        return "Gui::PythonWorkbench"

    def Initialize(self):
        """This function is executed when the workbench is activated for the first time."""
        # Add icon path
        resource_path = os.path.join(os.path.dirname(inspect.getfile(inspect.currentframe())), 'Resources', 'icons')
        FreeCADGui.addIconPath(resource_path)
        
        FreeCAD.Console.PrintLog(f"DM: Loading from {os.path.dirname(inspect.getfile(inspect.currentframe()))}\n")
        try:
            # Import commands from the new commands/ directory
            import commands.cmd_point
            import commands.cmd_curve
            import commands.cmd_workplane
            import commands.cmd_boolean
            import commands.cmd_settings
            import commands.cmd_sketcher
            import commands.cmd_translate
            import commands.cmd_fill_curve
            import commands.cmd_edit
            
            # Import core modules
            import core as FCDirectModeling
            import tools
            
            self.appendToolbar("Direct Modeling", [
                'DM_WorkPlane',
                'DM_CreatePoint',
                'DM_CreateCurve',
                'DM_FillCurve',
                'DM_EditObject',
                'DM_Translate',
                'DM_OpenSketcher',
                'DM_Fuse',
                'DM_Cut',
                'DM_Common',
                'DM_Settings',
            ])
            self.appendMenu("Direct Modeling", [
                'DM_WorkPlane',
                'DM_CreatePoint',
                'DM_CreateCurve',
                'DM_FillCurve',
                'DM_EditObject',
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
        try:
            # Global event filter to suppress context menus
            if not hasattr(self, "_event_filter"):
                from PySide import QtCore, QtGui
                class DMEventFilter(QtCore.QObject):
                    def eventFilter(self, obj, event):
                        # Suppress context menu events everywhere in the workbench
                        if event.type() == QtCore.QEvent.ContextMenu:
                            return True
                            
                        # If a tool is active, Right Click finishes it and we consume the event
                        if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                            if event.button() == QtCore.Qt.RightButton:
                                from tools.primitive_base import PrimitiveBase
                                if PrimitiveBase.active_tool:
                                    if event.type() == QtCore.QEvent.MouseButtonPress:
                                        PrimitiveBase.active_tool.finish()
                                    return True # Block FreeCAD's context menu and rotation
                                
                        return False
                self._event_filter = DMEventFilter()
            
            # Install on the qApp to catch all context menus globally
            QtGui.QApplication.instance().installEventFilter(self._event_filter)
        except Exception as e:
            FreeCAD.Console.PrintError(f"DM Activated Error: {e}\n")

    def Deactivated(self):
        """This function is executed when the workbench is deactivated."""
        try:
            if hasattr(self, "_event_filter"):
                from PySide import QtGui
                QtGui.QApplication.instance().removeEventFilter(self._event_filter)
        except Exception as e:
            FreeCAD.Console.PrintError(f"DM Deactivated Error: {e}\n")

# Add the workbench to FreeCAD's list of available workbenches
FreeCADGui.addWorkbench(DirectModelingWorkbench())