# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Single builder for compiled scene shaders and scene metadata.

Extracted from FldSceneVoxelRenderer (RS-012). Replaces seven parallel list
attributes with a single CompiledScene object.
"""
from dataclasses import dataclass, field
from typing import List, Tuple, Any, Dict
import FreeCAD


@dataclass
class CompiledField:
    label: str
    field_obj: Any
    bbox_min: Any
    bbox_max: Any
    is_subtractive: bool
    vis_alpha: float
    shape_color: Tuple[float, float, float]
    selection_color: Tuple[float, float, float]
    specular: Tuple[float, float, float, float]  # (sr, sg, sb, exponent)


@dataclass
class CompiledScene:
    fields: List[CompiledField] = field(default_factory=list)
    uniform_dict: Dict[str, Any] = field(default_factory=dict)


class SceneShaderBuilder:
    @staticmethod
    def build(analytical_data, visible_compiled) -> CompiledScene:
        """Construct a CompiledScene from analytical_data and visible_compiled."""
        if not analytical_data:
            return CompiledScene()

        compiled_fields = []
        for (label, f_obj), d in zip(visible_compiled, analytical_data):
            cf = CompiledField(
                label=label,
                field_obj=f_obj,
                bbox_min=d["bbox_min"],
                bbox_max=d["bbox_max"],
                is_subtractive=d["is_subtractive"],
                vis_alpha=d["vis_alpha"],
                shape_color=d["shape_color"],
                selection_color=d["selection_color"],
                specular=d["specular"],
            )
            compiled_fields.append(cf)

        return CompiledScene(
            fields=compiled_fields,
            uniform_dict={"n_fields": len(analytical_data)}
        )

