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
        if not hasattr(obj, "NodeGraphJson"):
            try:
                import json
                from freecad.fields.core.gui.node_editor.nodes import get_template_graph
                tmpl = get_template_graph("Waves")
                default_graph = json.dumps(tmpl) if tmpl else ""
            except Exception:
                default_graph = ""
            obj.addProperty("App::PropertyString", "NodeGraphJson", "Noise",
                            "Serialized node graph").NodeGraphJson = default_graph

    def _build_field(self, fp):
        from freecad.fields.core.sdf.sdf.noise import SdfNoiseField
        base_field = self._resolve_source_field(fp)
        if base_field:
            noise_type = getattr(fp, "NoiseType", "Waves")
            # The stored Formula is passed for ANY non-complex preset, not just
            # "Custom": a preset's formula text is user-editable and lives in that
            # property, so reading it only for "Custom" throws away every edit.
            is_complex = noise_type in SdfNoiseField.COMPLEX_PRESETS
            custom_formula = getattr(fp, "Formula", None) if not is_complex else None
            custom_params = resolve_custom_params(fp, custom_formula)

            self.SdfField = SdfNoiseField(
                base_field, fp.Amplitude, fp.Frequency,
                placement=fp.Placement,
                formula=custom_formula, normalization=getattr(fp, "Normalization", 0.0),
                preset_name=noise_type, custom_params=custom_params,
                radial=getattr(fp, "Custom_radial", False),
                disp_axis=self._graph_disp_axis(fp) if not is_complex else None,
            )

    @staticmethod
    def _graph_disp_axis(fp):
        """A literal (unwired) Direction on the graph's terminal Displace node
        means the displacement is pinned to one fixed axis, which lets _lip()
        (noise.py) skip the sqrt(3) three-component Lipschitz bound. Best-effort:
        a hand-typed formula with no graph, or a malformed NodeGraphJson, just
        means no axis information -- not an error."""
        json_str = getattr(fp, "NodeGraphJson", "")
        if not json_str:
            return None
        try:
            import json
            from freecad.fields.core.gui.node_editor.nodes import compile_graph
            return compile_graph(json.loads(json_str)).disp_axis
        except Exception:
            return None


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
