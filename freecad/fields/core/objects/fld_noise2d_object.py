# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.objects.fld_view_provider import FldViewProvider
from freecad.fields.core.objects.fld_modifier_proxy_base import FldModifierProxyBase
from freecad.fields.core.objects.fld_noise_shared import (
    setup_amplitude_frequency_properties, resolve_custom_params,
)
from freecad.fields.core.render.field_appearance import apply_default_view_settings


class FldNoise2DProxy(FldModifierProxyBase):
    """
    Proxy object for a parametric 2D Noise modifier.
    Applies SdfNoise2DField: noise evaluated in the plane perpendicular to Direction,
    so the pattern is constant along the direction axis (like wood grain).
    """
    SHAPE_TYPE_TOOLTIP = "Type of primitive"
    SOURCE_GROUP = "Noise"
    # No onChanged: the recompute is the only invalidation signal this proxy gets.
    ALWAYS_REBUILD = True

    def __init__(self, obj):
        super().__init__(obj)
        from freecad.fields.core.sdf.sdf.noise import SdfNoise2DField
        setup_amplitude_frequency_properties(obj, SdfNoise2DField, "Waves")
        if not hasattr(obj, "DirectionX"):
            obj.addProperty("App::PropertyFloat", "DirectionX", "Noise", "Direction X").DirectionX = 0.0
        if not hasattr(obj, "DirectionY"):
            obj.addProperty("App::PropertyFloat", "DirectionY", "Noise", "Direction Y").DirectionY = 0.0
        if not hasattr(obj, "DirectionZ"):
            obj.addProperty("App::PropertyFloat", "DirectionZ", "Noise", "Direction Z").DirectionZ = -1.0
        if not hasattr(obj, "Roll"):
            obj.addProperty("App::PropertyFloat", "Roll", "Noise",
                            "Spin of the pattern about Direction, in degrees").Roll = 0.0
        if not hasattr(obj, "IgnoreBackFace"):
            obj.addProperty("App::PropertyBool", "IgnoreBackFace", "Noise",
                            "Only perturb the front-facing surface (first hit along direction)")
            obj.IgnoreBackFace = False
        if not hasattr(obj, "CenterX"):
            obj.addProperty("App::PropertyFloat", "CenterX", "Noise", "Pattern center X").CenterX = 0.0
        if not hasattr(obj, "CenterY"):
            obj.addProperty("App::PropertyFloat", "CenterY", "Noise", "Pattern center Y").CenterY = 0.0
        if not hasattr(obj, "CenterZ"):
            obj.addProperty("App::PropertyFloat", "CenterZ", "Noise", "Pattern center Z").CenterZ = 0.0
        if not hasattr(obj, "Bias"):
            self._add_bias_property(obj)
        if not hasattr(obj, "Radial"):
            obj.addProperty("App::PropertyBool", "Radial", "Noise",
                            "Project the pattern radially from Center instead of along one axis")
        if not hasattr(obj, "NodeGraphJson"):
            try:
                import json
                from freecad.fields.core.gui.node_editor.node_definitions import get_template_graph
                default_graph = json.dumps(get_template_graph("2D: Dual Waves", is_2d=True))
            except Exception:
                default_graph = ""
            obj.addProperty("App::PropertyString", "NodeGraphJson", "Noise", "Serialized node graph").NodeGraphJson = default_graph

    @staticmethod
    def _add_bias_property(obj):
        from freecad.fields.core.sdf.sdf.noise import SdfNoise2DField
        obj.addProperty("App::PropertyEnumeration", "Bias", "Noise",
                        "Where the original surface sits on the waveform: Middle "
                        "displaces both ways (the stock grows by one amplitude), "
                        "Top hangs the wave inside the material (removal only), "
                        "Bottom puts it outside (material only added). Only the "
                        "face the direction arrow points at is anchored; the far "
                        "face keeps the plain wave running through it unless "
                        "IgnoreBackFace holds it still.")
        obj.Bias = SdfNoise2DField.BIAS_MODES
        obj.Bias = SdfNoise2DField.DEFAULT_BIAS

    def _build_field(self, fp):
        from freecad.fields.core.sdf.sdf.noise import SdfNoise2DField
        base_field = self._resolve_source_field(fp)
        if base_field:
            direction = FreeCAD.Vector(fp.DirectionX, fp.DirectionY, fp.DirectionZ)
            noise_type = getattr(fp, "NoiseType", "Sine")
            is_complex = noise_type in SdfNoise2DField.COMPLEX_PRESETS
            custom_formula = getattr(fp, "Formula", None) if not is_complex else None
            custom_params = resolve_custom_params(fp, custom_formula)

            center = FreeCAD.Vector(
                getattr(fp, "CenterX", 0.0), getattr(fp, "CenterY", 0.0), getattr(fp, "CenterZ", 0.0)
            )
            self.SdfField = SdfNoise2DField(
                base_field, fp.Amplitude, fp.Frequency, direction,
                formula=custom_formula, normalization=getattr(fp, "Normalization", 0.0),
                preset_name=noise_type, custom_params=custom_params,
                ignore_back_face=getattr(fp, "IgnoreBackFace", False),
                center=center, radial=getattr(fp, "Custom_radial", getattr(fp, "Radial", False)),
                roll=getattr(fp, "Roll", 0.0),
                bias=getattr(fp, "Bias", None),
            )

    def onDocumentRestored(self, obj):
        super().onDocumentRestored(obj)
        # Roll postdates the first released documents; without this a restored
        # 2D noise has no Roll property and the rotation gizmo has nowhere to
        # write the spin it computes.
        if not hasattr(obj, "Roll"):
            obj.addProperty("App::PropertyFloat", "Roll", "Noise",
                            "Spin of the pattern about Direction, in degrees").Roll = 0.0
        # Same story as Roll: documents saved before Bias existed have no such
        # property, and the panel's combo would have nowhere to write to.
        if not hasattr(obj, "Bias"):
            self._add_bias_property(obj)


def create_noise2d_modifier(name, source_obj):
    doc = getattr(source_obj, "Document", None) or FreeCAD.activeDocument()
    obj = doc.addObject("Part::FeaturePython", name)
    FldNoise2DProxy(obj)
    obj.Source = source_obj

    try:
        field = source_obj.Proxy.get_sdf_field(source_obj)
        if field is not None:
            direction = FreeCAD.Vector(obj.DirectionX, obj.DirectionY, obj.DirectionZ)
            if direction.Length > 1e-8:
                direction = direction / direction.Length
                bb_min, bb_max = field.bounding_box()
                diag = (bb_max - bb_min).Length
                bbox_center = (bb_min + bb_max) * 0.5
                margin = max(diag, 1.0) * 1.5
                ray_origin = bbox_center - direction * margin
                hit = field.ray_march(ray_origin, direction)
                if hit is not None:
                    hit_point, _ = hit
                    obj.CenterX = hit_point.x
                    obj.CenterY = hit_point.y
                    obj.CenterZ = hit_point.z
    except Exception as e:
        from freecad.fields.core import fld_logger
        fld_logger.debug(f"create_noise2d_modifier: default Center ray-march failed: {e}")

    if FreeCAD.GuiUp:
        FldViewProvider(obj.ViewObject)
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            try:
                apply_default_view_settings(obj.ViewObject, (0.2, 0.8, 0.4))
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"create_noise2d_modifier: ViewObject appearance setup failed: {e}")

    obj.touch()   # recompute belongs to the caller -- see CommandFldModifierBase (IF-016)
    return obj
