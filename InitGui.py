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
        
        from core import dm_logger
        dm_logger.log(f"DM: Loading from {os.path.dirname(inspect.getfile(inspect.currentframe()))}")
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
            import commands.cmd_primitive
            import commands.cmd_curve_sdf
            import commands.cmd_sdf_export
            import commands.cmd_sdf_slice
            import commands.cmd_noise
            
            # Import core modules
            import core as FCDirectModeling
            import tools
            
            self.appendToolbar("DM - Edit", [
                'DM_EditObject',
                'DM_Settings',
            ])
            self.appendToolbar("DM - Constructive", [
                'DM_WorkPlane',
                'DM_CreatePoint',
                'DM_CreateCurve',
                'DM_CreateBox',
                'DM_CreateSphere',
                'DM_CreateCylinder',
                'DM_CreateTorus',
                'DM_FillCurve',
                'DM_ExtrudeCurve',
                'DM_CurvePipe',
                'DM_Curve3DExtrude',
            ])
            self.appendToolbar("DM - Operations", [
                'DM_Translate',
                'DM_Add',
                'DM_Subtract',
                'DM_Intersection',
                'DM_CreateNoiseModifier',
                'DM_SDFSlice',
                'DM_SDFToShape',
                'DM_OpenSketcher',
            ])
            self.appendMenu("Direct Modeling", [
                'DM_EditObject',
                'DM_Settings',
                'Separator',
                'DM_WorkPlane',
                'DM_CreatePoint',
                'DM_CreateCurve',
                'DM_CreateBox',
                'DM_CreateSphere',
                'DM_CreateCylinder',
                'DM_CreateTorus',
                'DM_FillCurve',
                'DM_ExtrudeCurve',
                'DM_CurvePipe',
                'DM_Curve3DExtrude',
                'Separator',
                'DM_Translate',
                'DM_Add',
                'DM_Subtract',
                'DM_Intersection',
                'DM_CreateNoiseModifier',
                'DM_SDFSlice',
                'DM_SDFToShape',
                'DM_OpenSketcher',
            ])
        except Exception as e:
            from core import dm_logger
            dm_logger.error(f"Error importing Direct Modeling commands: {e}")
            import traceback
            traceback.print_exc()

    def Activated(self):
        """This function is executed when the workbench is activated."""
        try:
            from core.input_manager import DMInputManager
            DMInputManager.get_instance().initialize()
        except Exception as e:
            from core import dm_logger
            dm_logger.error(f"DM Activated Error: {e}")

    def Deactivated(self):
        """This function is executed when the workbench is deactivated."""
        try:
            from core.input_manager import DMInputManager
            DMInputManager.get_instance().restore()
        except Exception as e:
            from core import dm_logger
            dm_logger.error(f"DM Deactivated Error: {e}")
        try:
            from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
            DMSceneRayMarchRenderer.destroy()
        except Exception as e:
            from core import dm_logger
            dm_logger.error(f"DM Deactivated SceneRM Error: {e}")

# Add the workbench to FreeCAD's list of available workbenches
FreeCADGui.addWorkbench(DirectModelingWorkbench())