# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_field import SdfField


def _primitive_to_patches(obj):
    """Convert any supported SDF primitive to a patch SDF object in-place."""
    from freecad.fields.core.sdf.sdf.cage import SdfCageField
    from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
    import numpy as np

    proxy = getattr(obj, "Proxy", None)
    if not proxy or not hasattr(proxy, "SdfField"):
        fld_logger.warn("PrimitiveToPatches: selected object has no SdfField")
        return
    field = proxy.SdfField

    cage = field.to_patch_cage()
    if cage is None:
        fld_logger.warn(f"PrimitiveToPatches: unsupported field type {field.__class__.__name__}")
        return

    # Bake placement into world-space vertices/handles (cage_tool expects placement=None)
    pl = cage.placement
    if pl is not None:
        def _to_world(v):
            w = pl.multVec(FreeCAD.Vector(float(v[0]), float(v[1]), float(v[2])))
            return np.array([w.x, w.y, w.z], dtype=np.float64)
        verts_w   = np.array([_to_world(v) for v in cage.vertices])
        handles_w = np.array([_to_world(h) for h in cage.handles])
        fv_flat = [v for face in cage._face_verts for v in face]
        fs      = [len(face) for face in cage._face_verts]
        cage = SdfCageField(verts_w, handles_w, fv_flat, fs,
                            edges=cage._edges, placement=None)

    # Snapshot original primitive properties for cancel reversion
    _CAGE_PROPERTIES = [
        ("SdfType",       "App::PropertyString",       "Sdf", "SDF primitive type"),
        ("FaceVertices",  "App::PropertyIntegerList",  "Sdf", "Flattened face vertex indices"),
        ("FaceSizes",     "App::PropertyIntegerList",  "Sdf", "Vertex count per face"),
        ("EdgeVertices",  "App::PropertyIntegerList",  "Sdf", "Edge endpoint indices [v0, v1, v2, v3, ...]"),
        ("HandleTypes",   "App::PropertyIntegerList",  "Sdf", "Handle type per handle"),
        ("EdgeStraight",  "App::PropertyIntegerList",  "Sdf", "Straight edge flags (0 = curved, 1 = straight)"),
        ("SignMode",      "App::PropertyEnumeration",  "Sdf", "Inside/Outside sign mode"),
    ]
    added_properties = []
    for name, ptype, group, doc in _CAGE_PROPERTIES:
        if not hasattr(obj, name):
            added_properties.append(name)
            obj.addProperty(ptype, name, group, doc)
    if "SignMode" in added_properties:
        obj.SignMode = ["closest", "winding"]
        obj.SignMode = "closest"

    proxy._original_primitive_props = {
        "SdfType": getattr(obj, "SdfType", None),
        "Points": list(getattr(obj, "Points", [])),
        "Placement": FreeCAD.Placement(obj.Placement.Base, obj.Placement.Rotation) if obj.Placement else None,
        "SdfField": field,
        "AddedProperties": added_properties,
    }

    # Store vertices first, then edge handles (cage_tool uses len(pts)-24 for n_verts)
    all_pts = list(cage.vertices) + list(cage.handles)
    obj.Points       = [FreeCAD.Vector(float(p[0]), float(p[1]), float(p[2])) for p in all_pts]
    obj.FaceVertices = [v for face in cage._face_verts for v in face]
    obj.FaceSizes    = [len(face) for face in cage._face_verts]
    obj.EdgeVertices = [v for edge in cage._edges for v in edge]
    obj.HandleTypes  = list(cage._handle_types)
    obj.EdgeStraight = [1 if es else 0 for es in getattr(cage, "_edge_straight", [])]
    obj.SignMode     = "closest"
    obj.SdfType      = "cage"
    obj.Placement    = FreeCAD.Placement()  # cage stores world-space coords

    proxy.SdfField = cage
    label = f"{obj.Document.Name}.{obj.Name}"
    FldSceneVoxelRenderer.get_instance().update_field(label, cage)
    obj.touch()

    # Recompute first, then activate the edit tool.
    def do_recompute_and_edit():
        obj.Document.recompute([obj])
        
        def start_edit():
            from freecad.fields.tools import edit_tool
            # Ensure the object is selected
            FreeCADGui.Selection.addSelection(obj)
            edit_tool.activate()
            
        QtCore.QTimer.singleShot(0, start_edit)

    QtCore.QTimer.singleShot(0, do_recompute_and_edit)


class CommandFldCageFromPrimitive:
    """FreeCAD command: convert selected SDF primitive to an editable cage."""

    def GetResources(self):
        return {
            "Pixmap":   "SDF_Cage",
            "MenuText": "Cage from Primitive",
            "ToolTip":  "Convert the selected SDF primitive into an editable cage",
        }

    def IsActive(self):
        if not FreeCADGui.ActiveDocument:
            return False
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return False
        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        if not proxy or not hasattr(proxy, "SdfField"):
            return False
        field = getattr(proxy, "SdfField", None)
        if field is None:
            return False
        return type(field).to_patch_cage is not SdfField.to_patch_cage

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return
        QtCore.QTimer.singleShot(0, lambda: _primitive_to_patches(sel[0]))


class CommandFldEditCage:
    """FreeCAD command: edit the selected cage with the interactive edit tool."""

    def GetResources(self):
        return {
            "Pixmap":   "Fields_EditTool",
            "MenuText": "Edit Cage",
            "ToolTip":  ("Edit the control net of the selected cage"
                        "\n\nIn edit mode: G move, R rotate, S scale."
                        "\nX/Y/Z lock an axis (Shift for the perpendicular plane); "
                        "type a number for an exact value."
                        "\nEnter or left-click applies, Esc or right-click cancels."),
        }

    def IsActive(self):
        if not FreeCADGui.ActiveDocument:
            return False
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return False
        obj = sel[0]
        return (getattr(obj, "ShapeType", None) == "sdf"
                and getattr(obj, "SdfType", None) == "cage")

    def Activated(self):
        from freecad.fields.tools import edit_tool
        QtCore.QTimer.singleShot(0, edit_tool.activate)

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        active_tool = FldToolManager.get_instance().get_active_tool()
        return active_tool is not None and active_tool.get_command_id() == "Fields_EditObject"


# DM_CageFromPrimitive (patch-cage-from-primitive) retired: the patch cage's
# analytic GLSL was removed (RM-001) in favor of the FFD deform cage, so a
# patch cage created from a primitive can no longer render. Use
# Fields_DeformCageFromPrimitive instead. The CommandFldCageFromPrimitive class and
# _primitive_to_patches helper are kept importable for legacy tests only and
# are intentionally not registered as a GUI command.
FreeCADGui.addCommand("Fields_EditCage", CommandFldEditCage())


def _primitive_to_deform_cage(obj):
    """Wrap any supported SDF primitive in a FldDeformCageProxy modifier."""
    from freecad.fields.core.objects.fld_deform_objects import create_deform_cage_modifier

    mod_obj = create_deform_cage_modifier(obj.Document, obj)
    if mod_obj is None:
        return

    # Activate the edit tool on the new modifier object
    def do_edit():
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(mod_obj)
        from freecad.fields.tools import edit_tool
        edit_tool.activate()
        
    QtCore.QTimer.singleShot(0, do_edit)


class CommandFldDeformCageFromPrimitive:
    """FreeCAD command: convert selected SDF primitive to an editable deform cage."""

    def GetResources(self):
        return {
            "Pixmap":   "SDF_Lattice",
            "MenuText": "Convert to Deform Cage",
            "ToolTip":  "Wrap the selected SDF primitive in a deformation lattice cage",
        }

    def IsActive(self):
        if not FreeCADGui.ActiveDocument:
            return False
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return False
        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        if not proxy or not hasattr(proxy, "SdfField"):
            return False
        field = getattr(proxy, "SdfField", None)
        if field is None:
            return False
        from freecad.fields.core.objects.fld_deform_objects import can_build_deform_cage
        return can_build_deform_cage(field)

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return
        QtCore.QTimer.singleShot(0, lambda: _primitive_to_deform_cage(sel[0]))


FreeCADGui.addCommand("Fields_DeformCageFromPrimitive", CommandFldDeformCageFromPrimitive())
