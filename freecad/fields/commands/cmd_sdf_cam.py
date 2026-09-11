# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
commands/cmd_sdf_cam.py

Command registration for generating Z-level toolpaths from SDF fields.
"""

import FreeCAD
import FreeCADGui
from freecad.fields.core import fld_logger


class CommandFldSdfCam:
    """FreeCAD Command class for the SDF CAM toolpath generation."""

    def GetResources(self):
        return {
            'Pixmap': 'SDFCamProfile',
            'MenuText': 'SDF CNC Toolpath',
            'ToolTip': 'Generate a CNC toolpath directly from the analytical SDF field.',
        }

    def Activated(self):
        try:
            sel = FreeCADGui.Selection.getSelection()
            if not sel:
                fld_logger.warn("SDFCam: No object selected")
                return
            from freecad.fields.core.objects.fld_modifier_stack import stack_target
            obj = stack_target(sel[0]) or sel[0]
            proxy = getattr(obj, "Proxy", None)
            field = (proxy.get_sdf_field(obj) if (proxy and hasattr(proxy, "get_sdf_field"))
                     else getattr(proxy, "SdfField", None)) if proxy else None
            if field is None:
                fld_logger.warn("SDFCam: Selected object has no SdfField")
                return

            from freecad.fields.tools.sdf_cam_tool import SdfCamTool
            tool = SdfCamTool()
            tool.start_cam_panel(obj, field)

        except Exception as e:
            fld_logger.exception(f"SDFCam: Error: {e}")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('Fields_SDFCamProfile', CommandFldSdfCam())
