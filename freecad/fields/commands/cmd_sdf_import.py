# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""commands/cmd_sdf_import.py

Convert an arbitrary FreeCAD CSG or B-Rep shape to a Fields SDF object.
"""
import FreeCAD
import FreeCADGui
from PySide import QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.objects import fld_object
from freecad.fields.core.objects.fld_object_proxy import persist_brep_cage_field
from freecad.fields.core.sdf.sdf_csg_convert import convert_csg_to_sdf


class CommandFldConvertShapeToSdf:
    """Convert a Part / PartDesign / CSG shape into a Fields SDF object."""

    def GetResources(self):
        from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip
        return {
            'Pixmap': 'SDF_Cage',
            'MenuText': 'Convert B-Rep / CSG to SDF',
            'ToolTip': rich_tooltip("Fields", QT_TRANSLATE_NOOP("Fields", "Converts each selected Part, PartDesign or boolean shape into an SDF object.\nSelect one or more shapes.")),
        }

    def IsActive(self):
        if FreeCAD.ActiveDocument is None:
            return False
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return False
        return any(hasattr(obj, 'Shape') or hasattr(obj, 'Base') or hasattr(obj, 'Shapes') for obj in sel)

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            fld_logger.error('Convert to SDF: Please select at least one Part or CSG object.')
            return

        doc = FreeCAD.ActiveDocument
        if not doc:
            return

        def action():
            try:
                doc.openTransaction('Convert Shape to SDF')
                converted_count = 0
                transient_count = 0
                for obj in sel:
                    field = convert_csg_to_sdf(obj)
                    if field is None:
                        fld_logger.warn(f"Convert to SDF: Could not convert '{getattr(obj, 'Label', obj.Name)}'.")
                        continue

                    name = f"Fld_{getattr(obj, 'Label', 'SDF')}"
                    fld_obj = fld_object.create_fld_object(name=name, shape_type='sdf')
                    fld_obj.Proxy.SdfField = field

                    if hasattr(obj, 'Placement'):
                        fld_obj.Placement = obj.Placement

                    persisted = persist_brep_cage_field(fld_obj, field)
                    if not persisted:
                        transient_count += 1

                    if hasattr(obj, 'ViewObject') and obj.ViewObject:
                        obj.ViewObject.Visibility = False

                    fld_object.finalize_new_object(fld_obj)

                    try:
                        from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                        renderer = FldSceneVoxelRenderer.get_instance()
                        label = f"{doc.Name}.{fld_obj.Name}"
                        renderer.update_field(label, field)
                    except Exception as e:
                        fld_logger.debug(f"Convert to SDF: renderer update notice: {e}")

                    converted_count += 1

                if converted_count > 0:
                    doc.commitTransaction()
                    doc.recompute()
                    if FreeCADGui.activeView():
                        FreeCADGui.activeView().redraw()
                    msg = f'Convert to SDF: Successfully converted {converted_count} object(s).'
                    if transient_count:
                        msg += (f' {transient_count} will NOT survive a document reload - '
                                f'the field tree could not be described (see log).')
                    fld_logger.info(msg)
                else:
                    doc.abortTransaction()
            except Exception as e:
                doc.abortTransaction()
                fld_logger.error(f'Convert to SDF failed: {e}')

        QtCore.QTimer.singleShot(0, action)


FreeCADGui.addCommand('Fields_ConvertShapeToSdf', CommandFldConvertShapeToSdf())
