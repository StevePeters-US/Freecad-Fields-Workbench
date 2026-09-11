# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/noise_3d_tool.py

Edit tool and task panel for the 3D procedural noise SDF modifier.
"""
from freecad.fields.tools.noise_base_tool import BaseNoiseTaskPanel, BaseNoiseTool

_HINT_3D = (
    "Variables: <b>p</b> (vec3), <b>amp</b> (float), <b>freq</b> (float)<br>"
    "Functions: sin cos tan sqrt abs pow exp log min max<br>"
    "fract floor ceil clamp mix mod step smoothstep length<br>"
    "Custom params: <b>// @param slider speed 1.5 0.0 5.0 0.1</b>"
)


class Noise3DTaskPanel(BaseNoiseTaskPanel):
    def __init__(self, tool):
        from freecad.fields.core.sdf.sdf.noise import SdfNoiseField
        super().__init__(tool, "3D Noise Parameters", _HINT_3D)
        self.type_combo.blockSignals(True)
        self.type_combo.addItems(SdfNoiseField.PRESET_NAMES)
        self.type_combo.blockSignals(False)
        self.update_ui()

    def _get_presets_and_names(self):
        from freecad.fields.core.sdf.sdf.noise import SdfNoiseField
        return SdfNoiseField.PRESETS, SdfNoiseField.PRESET_NAMES

    def _get_preset_complex_presets(self):
        from freecad.fields.core.sdf.sdf.noise import SdfNoiseField
        return SdfNoiseField.COMPLEX_PRESETS


class Noise3DTool(BaseNoiseTool):
    def get_handled_types(self):
        return ["FldNoiseProxy"]

    def _make_panel(self):
        return Noise3DTaskPanel(self)


# Backward-compat alias
NoiseTool = Noise3DTool
