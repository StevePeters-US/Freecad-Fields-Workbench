# SPDX-License-Identifier: CC-BY-NC-SA-4.0
try:
    import shapely
except ImportError:
    from PySide.QtGui import QMessageBox
    from PySide import QtCore

    title = "Fields Workbench - Missing Dependency"
    message = """
<p>The 'shapely' library is not installed.</p>
<p>This is a required dependency for the Fields Workbench to function correctly.</p>
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

from freecad.fields import ADDON_DIR, ICONS_DIR


class FieldsWorkbench(FreeCADGui.Workbench):
    "Fields workbench object"
    Icon = os.path.join(ICONS_DIR, 'Fields.svg')
    MenuText = "Fields"
    ToolTip = "Real-time NURBS and BRep modeling"

    def GetClassName(self):
        return "Gui::PythonWorkbench"

    def Initialize(self):
        """This function is executed when the workbench is activated for the first time."""
        FreeCADGui.addIconPath(ICONS_DIR)

        from freecad.fields.core import fld_logger
        fld_logger.log(f"Fields: Loading from {ADDON_DIR}")
        try:
            import freecad.fields.commands.cmd_point
            import freecad.fields.commands.cmd_curve
            import freecad.fields.commands.cmd_workplane
            import freecad.fields.commands.cmd_boolean
            import freecad.fields.commands.cmd_settings
            import freecad.fields.commands.cmd_sketcher
            import freecad.fields.commands.cmd_translate
            import freecad.fields.commands.cmd_rotate
            import freecad.fields.commands.cmd_scale
            import freecad.fields.commands.cmd_fill_curve
            import freecad.fields.commands.cmd_edit
            import freecad.fields.commands.cmd_primitive
            import freecad.fields.commands.cmd_curve_sdf
            import freecad.fields.commands.cmd_sdf_export
            import freecad.fields.commands.cmd_sdf_import
            import freecad.fields.commands.cmd_sdf_slice
            import freecad.fields.commands.cmd_sdf_cam
            import freecad.fields.commands.cmd_noise
            import freecad.fields.commands.cmd_heightmap
            import freecad.fields.commands.cmd_deform
            import freecad.fields.commands.cmd_cage
            import freecad.fields.commands.cmd_cage_from_surfaces
            import freecad.fields.commands.cmd_modifier_stack
            import freecad.fields.commands.cmd_convert_to_voxel_field
            import freecad.fields.commands.cmd_convert_to_brush
            import freecad.fields.commands.cmd_sculpt

            import freecad.fields.core as FCFields
            import freecad.fields.tools

            self.appendToolbar("Fields - Edit", [
                'Fields_EditObject',
                'Fields_ModifierStack',
                'Fields_Settings',
            ])
            self.appendToolbar("Fields - Constructive", [
                'Fields_WorkPlane',
                'Fields_CreatePoint',
                'Fields_CreateCurve',
                'Fields_CreateBox',
                'Fields_CreateSphere',
                'Fields_CreateCylinder',
                'Fields_CreateTorus',
                'Fields_FillCurve',
                'Fields_ExtrudeCurve',
                'Fields_CurvePipe',
            ])
            self.appendToolbar("Fields - Cage", [
                'Fields_DeformCageFromPrimitive',
                'Fields_CageFromSurfaces',
                'Fields_EditCage',
            ])
            self.appendToolbar("Fields - Voxel", [
                'Fields_CreateVoxelField',
                'Fields_ConvertToVoxelField',
                'Fields_ConvertToBrush',
                'Fields_SculptBrush',
            ])
            self.appendToolbar("Fields - Operations", [
                'Fields_Translate',
                'Fields_Rotate',
                'Fields_Scale',
                'Fields_Add',
                'Fields_Subtract',
                'Fields_Intersection',
                'Fields_CreateNoiseModifier',
                'Fields_Create2DNoiseModifier',
                'Fields_CreateHeightmapModifier',
                'Fields_Twist',
                'Fields_Bend',
                'Fields_Lattice',
                'Fields_Array',
                'Fields_SDFSlice',
                'Fields_SDFCamProfile',
                'Fields_SDFToShape',
                'Fields_ConvertShapeToSdf',
                'Fields_OpenSketcher',
            ])
            self.appendMenu("Fields", [
                'Fields_EditObject',
                'Fields_ModifierStack',
                'Fields_Settings',
                'Separator',
                'Fields_WorkPlane',
                'Fields_CreatePoint',
                'Fields_CreateCurve',
                'Fields_CreateBox',
                'Fields_CreateSphere',
                'Fields_CreateCylinder',
                'Fields_CreateTorus',
                'Fields_FillCurve',
                'Fields_ExtrudeCurve',
                'Fields_CurvePipe',
                'Separator',
                'Fields_DeformCageFromPrimitive',
                'Fields_CageFromSurfaces',
                'Fields_EditCage',
                'Separator',
                'Fields_CreateVoxelField',
                'Fields_ConvertToVoxelField',
                'Fields_ConvertToBrush',
                'Fields_SculptBrush',
                'Separator',
                'Fields_Translate',
                'Fields_Rotate',
                'Fields_Scale',
                'Fields_Add',
                'Fields_Subtract',
                'Fields_Intersection',
                'Fields_CreateNoiseModifier',
                'Fields_Create2DNoiseModifier',
                'Fields_CreateHeightmapModifier',
                'Fields_Twist',
                'Fields_Bend',
                'Fields_Lattice',
                'Fields_Array',
                'Fields_SDFSlice',
                'Fields_SDFCamProfile',
                'Fields_SDFToShape',
                'Fields_ConvertShapeToSdf',
                'Fields_OpenSketcher',
            ])
        except Exception as e:
            from freecad.fields.core import fld_logger
            import traceback
            fld_logger.error(
                f"Error importing Fields commands: {e}\n"
                f"{traceback.format_exc()}")

    def Activated(self):
        """This function is executed when the workbench is activated."""
        try:
            from freecad.fields.core.input.input_manager import FldInputManager
            FldInputManager.get_instance().initialize()
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.error(f"Fields Activated Error: {e}")

    def Deactivated(self):
        """This function is executed when the workbench is deactivated."""
        try:
            from freecad.fields.core.input.input_manager import FldInputManager
            FldInputManager.get_instance().restore()
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.error(f"Fields Deactivated Error: {e}")
        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            FldSceneVoxelRenderer.destroy()
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.error(f"Fields Deactivated SceneRM Error: {e}")


FreeCADGui.addWorkbench(FieldsWorkbench())
