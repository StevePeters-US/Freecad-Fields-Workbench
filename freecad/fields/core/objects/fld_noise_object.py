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
        setup_amplitude_frequency_properties(obj)
        from freecad.fields.core.gui.node_editor.nodes import get_template_graph
        from freecad.fields.core.objects.fld_noise_shared import apply_graph
        from freecad.fields.core import fld_logger
        err = apply_graph(obj, get_template_graph("Waves"), "Waves")
        if err:
            fld_logger.error(f"FldNoiseProxy.__init__: failed to apply Waves graph: {err}")

    def _build_field(self, fp):
        from freecad.fields.core.sdf.sdf.noise import SdfNoiseField
        base_field = self._resolve_source_field(fp)
        if base_field:
            wl = getattr(fp, "Wavelength", 0.0)
            if wl <= 0.0:
                label = getattr(fp, "Label", "") or "Noise"
                raise ValueError(f"{label}: Wavelength must be > 0")
            formula = getattr(fp, "Formula", "")
            custom_params = resolve_custom_params(fp, formula)

            gates = self._graph_gates(fp)
            self.SdfField = SdfNoiseField(
                base_field, fp.Amplitude, 1.0 / wl,
                placement=fp.Placement,
                formula=formula, normalization=getattr(fp, "Normalization", 0.0),
                custom_params=custom_params,
                gates=gates,
                label=getattr(fp, "Label", ""),
            )

    @staticmethod
    def _graph_gates(fp):
        """Extracts the builtin gates from fp.NodeGraphJson."""
        json_str = getattr(fp, "NodeGraphJson", "")
        if not json_str:
            return {}
        try:
            import json
            from freecad.fields.core.gui.node_editor.nodes import compile_graph
            res = compile_graph(json.loads(json_str))
            return res.gates
        except Exception:
            return {}



def create_noise_modifier(name, source_obj):
    doc = getattr(source_obj, "Document", None) or FreeCAD.activeDocument()
    obj = doc.addObject("Part::FeaturePython", name)
    FldNoiseProxy(obj)
    obj.Source = source_obj
    

    try:
        field = source_obj.Proxy.get_sdf_field(source_obj)
        if field is not None:
            # Local +Z in world space -- see SdfNoiseField.direction.
            direction = obj.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
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
                    obj.Placement = FreeCAD.Placement(hit_point, obj.Placement.Rotation)
    except Exception as e:
        from freecad.fields.core import fld_logger
        fld_logger.debug(f"create_noise_modifier: default Center ray-march failed: {e}")

    from freecad.fields.core.objects.fld_noise_types import apply_noise_type
    err = apply_noise_type(obj, "Waves")
    if err:
        from freecad.fields.core import fld_logger
        fld_logger.error(f"create_noise_modifier: apply_noise_type('Waves') failed: {err}")

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
