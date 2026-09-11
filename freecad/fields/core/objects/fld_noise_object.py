# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.objects.fld_view_provider import FldViewProvider
from freecad.fields.core.objects.fld_modifier_proxy_base import FldModifierProxyBase
from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR, apply_default_view_settings
from freecad.fields.core.objects.fld_noise_shared import (
    setup_amplitude_frequency_properties, resolve_custom_params,
)

class FldNoiseProxy(FldModifierProxyBase):
    """
    Proxy object for a parametric Noise modifier.
    Wraps a Source SDF object and applies SdfNoiseField.
    """
    SHAPE_TYPE_TOOLTIP = "Type of primitive"
    SOURCE_GROUP = "Noise"
    # No onChanged: the recompute is the only invalidation signal this proxy gets.
    ALWAYS_REBUILD = True

    def __init__(self, obj):
        super().__init__(obj)
        from freecad.fields.core.sdf.sdf.noise import SdfNoiseField
        setup_amplitude_frequency_properties(obj, SdfNoiseField, "Perlin")
        if not hasattr(obj, "NodeGraphJson"):
            try:
                import json
                from freecad.fields.core.gui.node_editor.node_definitions import get_template_graph
                default_graph = json.dumps(get_template_graph("3D: Simple Sine", is_2d=False))
            except Exception:
                default_graph = ""
            obj.addProperty("App::PropertyString", "NodeGraphJson", "Noise", "Serialized node graph").NodeGraphJson = default_graph

    def _build_field(self, fp):
        from freecad.fields.core.sdf.sdf.noise import SdfNoiseField
        base_field = self._resolve_source_field(fp)
        if base_field:
            noise_type = getattr(fp, "NoiseType", "Perlin")
            # Pass the stored Formula for ANY non-complex preset, not just
            # "Custom". Documents saved under the removed Sine / Ripple /
            # Diagonal Waves / Turbulence presets keep their formula string in
            # this property, and passing it is what preserves their shape --
            # without it they would silently fall back to DEFAULT_FORMULA.
            # Matches FldNoise2DProxy._build_field, which already did this.
            is_complex = noise_type in SdfNoiseField.COMPLEX_PRESETS
            custom_formula = getattr(fp, "Formula", None) if not is_complex else None
            custom_params = resolve_custom_params(fp, custom_formula)

            self.SdfField = SdfNoiseField(
                base_field, fp.Amplitude, fp.Frequency,
                formula=custom_formula, normalization=getattr(fp, "Normalization", 0.0),
                preset_name=noise_type, custom_params=custom_params
            )


def create_noise_modifier(name, source_obj):
    doc = getattr(source_obj, "Document", None) or FreeCAD.activeDocument()
    obj = doc.addObject("Part::FeaturePython", name)
    FldNoiseProxy(obj)
    obj.Source = source_obj
    
    if FreeCAD.GuiUp:
        FldViewProvider(obj.ViewObject)
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            try:
                apply_default_view_settings(obj.ViewObject, DEFAULT_ADDITIVE_COLOR)  # default orange
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"create_noise_modifier: ViewObject appearance setup failed: {e}")
                
    obj.touch()   # recompute belongs to the caller -- see CommandFldModifierBase (IF-016)
    return obj
