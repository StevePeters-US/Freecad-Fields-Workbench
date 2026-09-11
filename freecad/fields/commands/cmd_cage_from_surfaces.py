# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.objects import fld_object
from freecad.fields.core.sdf.sdf.cage import SdfCageField
from freecad.fields.core.objects.fld_curve_fill_geometry import _surface_to_patch_spec, _find_curve_loops
import numpy as np

def add_to_assembly(cage_obj, objects):
    """Safely adds member patches or curves to an existing assembled cage."""
    doc = cage_obj.Document
    if not doc:
        return

    def action():
        try:
            doc.openTransaction("Add to Assembly")
            current_members = list(cage_obj.Members)
            for obj in objects:
                if obj not in current_members:
                    current_members.append(obj)
            cage_obj.Members = current_members
            
            # Hide the newly added objects
            for obj in objects:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = False
                    
            doc.commitTransaction()
            doc.recompute()
        except Exception as e:
            doc.abortTransaction()
            fld_logger.error(f"Add to Assembly failed: {e}")
            
    QtCore.QTimer.singleShot(0, action)

def remove_from_assembly(cage_obj, objects):
    """Safely removes member patches or curves from an existing assembled cage."""
    doc = cage_obj.Document
    if not doc:
        return

    def action():
        try:
            doc.openTransaction("Remove from Assembly")
            current_members = list(cage_obj.Members)
            for obj in objects:
                if obj in current_members:
                    current_members.remove(obj)
            cage_obj.Members = current_members
            
            # Show the removed objects again
            for obj in objects:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = True
                    
            doc.commitTransaction()
            doc.recompute()
        except Exception as e:
            doc.abortTransaction()
            fld_logger.error(f"Remove from Assembly failed: {e}")
            
    QtCore.QTimer.singleShot(0, action)

class CommandFldCageFromSurfaces:
    """Command to build a cage from selected surface patches and curve loops."""

    def GetResources(self):
        return {
            'Pixmap': 'SDF_Cage', # Reuse the cage icon
            'MenuText': 'Cage from Surfaces',
            'ToolTip': 'Build an editable cage from the selected surface patches and curve loops',
        }

    def IsActive(self):
        if FreeCAD.ActiveDocument is None:
            return False
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return False
        # Active if all selected objects are surface or curve shape type
        return all(getattr(obj, "ShapeType", None) in ("surface", "curve") for obj in sel)

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return

        def action():
            doc = FreeCAD.activeDocument()
            if not doc:
                return
            try:
                # 1. Build initial field from members
                patch_specs = []
                surfaces = [m for m in sel if getattr(m, "ShapeType", None) == "surface"]
                curves = [m for m in sel if getattr(m, "ShapeType", None) == "curve"]
                
                for surf in surfaces:
                    spec = _surface_to_patch_spec(surf)
                    if spec:
                        patch_specs.append(spec)
                        
                loops = _find_curve_loops(curves)
                for chained in loops:
                    corners = []
                    patch_handles = []
                    for curve, is_reversed in chained:
                        c_start = curve.value(1.0) if is_reversed else curve.value(0.0)
                        h0 = curve.value(2.0/3.0) if is_reversed else curve.value(1.0/3.0)
                        h1 = curve.value(1.0/3.0) if is_reversed else curve.value(2.0/3.0)
                        pl = curve.Placement
                        if pl:
                            c_start = pl.multVec(c_start)
                            h0 = pl.multVec(h0)
                            h1 = pl.multVec(h1)
                        corners.append([c_start.x, c_start.y, c_start.z])
                        patch_handles.append([h0.x, h0.y, h0.z])
                        patch_handles.append([h1.x, h1.y, h1.z])
                    spec = {
                        "corners": np.array(corners, dtype=np.float64),
                        "handles": np.array(patch_handles, dtype=np.float64)
                    }
                    patch_specs.append(spec)
                    
                if not patch_specs:
                    fld_logger.error("Assemble Solid: No valid patches or curve loops could be extracted.")
                    return

                # Build welded cage field
                cage = SdfCageField.from_patches(patch_specs, sign_mode="winding")
                
                doc.openTransaction("Cage from Surfaces")
                
                # 2. Create the cage Fields object
                obj = fld_object.create_fld_object(name="FldCage", shape_type="sdf")
                
                # Add cage properties
                if not hasattr(obj, "SdfType"):
                    obj.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
                obj.SdfType = "cage"
                
                if not hasattr(obj, "FaceVertices"):
                    obj.addProperty("App::PropertyIntegerList", "FaceVertices", "Sdf", "Flattened face vertex indices")
                if not hasattr(obj, "FaceSizes"):
                    obj.addProperty("App::PropertyIntegerList", "FaceSizes", "Sdf", "Vertex count per face")
                if not hasattr(obj, "EdgeVertices"):
                    obj.addProperty("App::PropertyIntegerList", "EdgeVertices", "Sdf", "Edge endpoint indices")
                if not hasattr(obj, "HandleTypes"):
                    obj.addProperty("App::PropertyIntegerList", "HandleTypes", "Sdf", "Handle type per handle")
                if not hasattr(obj, "EdgeStraight"):
                    obj.addProperty("App::PropertyIntegerList", "EdgeStraight", "Sdf", "Straight edge flags")
                if not hasattr(obj, "SignMode"):
                    obj.addProperty("App::PropertyEnumeration", "SignMode", "Sdf", "Inside/Outside sign mode")
                    obj.SignMode = ["closest", "winding"]
                    obj.SignMode = "winding"
                if not hasattr(obj, "Members"):
                    obj.addProperty("App::PropertyLinkList", "Members", "Sdf", "Assembly member patches/curves")
                
                # Hide original member objects
                for m in sel:
                    if hasattr(m, "ViewObject") and m.ViewObject:
                        m.ViewObject.Visibility = False
                
                obj.Members = list(sel)
                
                # Store vertices and handles
                all_pts = list(cage.vertices) + list(cage.handles)
                obj.Points = [FreeCAD.Vector(float(p[0]), float(p[1]), float(p[2])) for p in all_pts]
                obj.FaceVertices = [v for face in cage._face_verts for v in face]
                obj.FaceSizes = [len(face) for face in cage._face_verts]
                obj.EdgeVertices = [v for edge in cage._edges for v in edge]
                obj.HandleTypes = list(cage._handle_types)
                obj.EdgeStraight = [1 if es else 0 for es in getattr(cage, "_edge_straight", [])]
                obj.Placement = FreeCAD.Placement() # world space coordinates
                
                obj.Proxy.SdfField = cage
                
                doc.commitTransaction()
                
                # Recompute and open the edit tool
                label = f"{obj.Document.Name}.{obj.Name}"
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                FldSceneVoxelRenderer.get_instance().update_field(label, cage)
                obj.touch()
                
                def start_edit():
                    obj.Document.recompute([obj])
                    fld_object.apply_shaded_display_mode(obj)
                    from freecad.fields.tools import edit_tool
                    FreeCADGui.Selection.clearSelection()
                    FreeCADGui.Selection.addSelection(obj)
                    edit_tool.activate()
                
                QtCore.QTimer.singleShot(0, start_edit)
                
            except Exception as e:
                doc.abortTransaction()
                fld_logger.error(f"Assemble Solid error: {e}")
                
        QtCore.QTimer.singleShot(0, action)

FreeCADGui.addCommand('Fields_CageFromSurfaces', CommandFldCageFromSurfaces())
