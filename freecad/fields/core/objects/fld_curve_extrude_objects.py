# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.objects.fld_modifier_proxy_base import FldModifierProxyBase
from freecad.fields.core.objects.fld_view_provider import FldViewProvider
from freecad.fields.core.sdf.sdf_curve_sweep import SdfCurveSweepField

CAP_TYPES = SdfCurveSweepField.CAP_TYPES


def section_profile_of(source_obj):
    """The 2D cross-section of a source solid, or None if it cannot supply one.

    Sections through the object's own placement frame -- see Sdf2dSectionOfField
    for why the previous world-origin slice could not work.
    """
    if source_obj is None or not hasattr(source_obj, "Proxy"):
        return None
    from freecad.fields.core.sdf.sdf2d.section_of_field import Sdf2dSectionOfField
    try:
        field = source_obj.Proxy.get_sdf_field(source_obj)
    except Exception:
        return None
    if field is None or not hasattr(field, "evaluate_grid"):
        return None
    if hasattr(field, "evaluate_2d_grid"):
        return field  # already a 2D profile
    return Sdf2dSectionOfField.from_object(source_obj, field)


def build_path_extrude_field(fp):
    """Build the swept field for a path-extrude feature, or None if it has no path.

    The single place that reads path-extrude properties. FldPathExtrudeProxy
    calls it on recompute and FldObjectProxy._reconstruct_field calls it on
    document load, so a property added here reaches both without the two
    drifting apart.
    """
    from freecad.fields.core.sdf.curve_sampler import extract_bezier_segments_3d
    from freecad.fields.core.sdf.sdf2d.circle import Sdf2dCircle
    from freecad.fields.core.sdf.sdf2d.box import Sdf2dBox

    path_obj = getattr(fp, "Path", None) or getattr(fp, "SourceCurveLink", None)
    if not path_obj:
        return None

    segs = extract_bezier_segments_3d(path_obj)
    if not segs:
        return None

    ptype = getattr(fp, "ProfileType", "Rectangle")
    if ptype == "Circle":
        profile = Sdf2dCircle(radius=float(getattr(fp, "Radius", 5.0)))
    elif ptype == "Rectangle":
        profile = Sdf2dBox(size=(float(getattr(fp, "Width", 10.0)),
                                 float(getattr(fp, "Height", 6.0))))
    else:
        profile = section_profile_of(getattr(fp, "Source", None))
        if profile is None:
            # Nothing usable to section: fall back to the circle the Radius
            # property already describes, rather than to a silent hardcoded one.
            profile = Sdf2dCircle(radius=float(getattr(fp, "Radius", 5.0)))

    return SdfCurveSweepField(
        profile=profile,
        segments_3d=segs,
        start_scale=getattr(fp, "StartScale", 1.0),
        end_scale=getattr(fp, "EndScale", 1.0),
        twist_degrees=getattr(fp, "Twist", 0.0),
        cap_type=getattr(fp, "CapType", "Flat"),
        is_closed=getattr(path_obj, "Closed", False),
    )


class FldPathExtrudeProxy(FldModifierProxyBase):
    """Document object proxy for Extrude Along Path solids and modifiers."""

    SOURCE_GROUP = "Path Extrude"

    def __init__(self, obj):
        super().__init__(obj)
        if not hasattr(obj, "SdfType"):
            obj.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
        obj.SdfType = "path_extrude"

        if not hasattr(obj, "Path"):
            obj.addProperty("App::PropertyLink", "Path", "Path Extrude", "Guide path curve")
        if not hasattr(obj, "ProfileType"):
            obj.addProperty("App::PropertyEnumeration", "ProfileType", "Path Extrude", "Profile shape type")
            obj.ProfileType = ["Circle", "Rectangle", "Source"]
            obj.ProfileType = "Rectangle"
        if not hasattr(obj, "Radius"):
            obj.addProperty("App::PropertyFloat", "Radius", "Path Extrude", "Profile radius (Circle)").Radius = 5.0
        if not hasattr(obj, "Width"):
            obj.addProperty("App::PropertyFloat", "Width", "Path Extrude", "Profile width (Rectangle)").Width = 10.0
        if not hasattr(obj, "Height"):
            obj.addProperty("App::PropertyFloat", "Height", "Path Extrude", "Profile height (Rectangle)").Height = 6.0
        if not hasattr(obj, "StartScale"):
            obj.addProperty("App::PropertyFloat", "StartScale", "Path Extrude", "Start scale factor").StartScale = 1.0
        if not hasattr(obj, "EndScale"):
            obj.addProperty("App::PropertyFloat", "EndScale", "Path Extrude", "End scale factor").EndScale = 1.0
        if not hasattr(obj, "Twist"):
            obj.addProperty("App::PropertyFloat", "Twist", "Path Extrude", "Total twist in degrees").Twist = 0.0
        if not hasattr(obj, "CapType"):
            obj.addProperty("App::PropertyEnumeration", "CapType", "Path Extrude", "End cap type")
            obj.CapType = list(CAP_TYPES)
            obj.CapType = "Flat"

    def _build_field(self, fp):
        self.SdfField = build_path_extrude_field(fp)

    def onChanged(self, fp, prop):
        if prop in ("Path", "Source", "ProfileType", "Radius", "Width", "Height",
                    "StartScale", "EndScale", "Twist", "CapType", "Enabled"):
            self.SdfField = None


def create_path_extrude_object(name: str, path_obj, source_obj=None) -> object:
    doc = FreeCAD.activeDocument()
    obj = doc.addObject("Part::FeaturePython", name)
    FldPathExtrudeProxy(obj)
    FldViewProvider(obj.ViewObject)
    obj.Path = path_obj
    if source_obj:
        obj.Source = source_obj
        obj.ProfileType = "Source"
    obj.touch()
    return obj
