# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes

The node graph definition and compiler registry.
"""
from .base import (
    glsl_param_identifier,
    SocketType,
    SOCKET_TYPE_NAMES,
    SOCKET_TYPE_SHORT_LABELS,
    SocketDef,
    BaseNode,
)
from .inputs import (
    PositionNode,
    NoiseInputsNode,
    ConstantNode,
    CustomParamNode,
)
from .math_nodes import (
    MathNode,
    TrigNode,
    MixNode,
    ClampNode,
    MinMaxNode,
    SmoothstepNode,
    IgnoreBackFaceNode,
)
from .vector import (
    Combine2DNode,
    Separate2DNode,
    Rotate2DNode,
    get_default_rotate_2d_subgraph,
    Combine3DNode,
    Separate3DNode,
    Distance2DNode,
    DomainWarp2DNode,
)
from .projection import (
    RadialProjectionNode,
    get_default_radial_projection_subgraph,
    ProjectionControlNode,
)
from .generators import (
    WaveGeneratorNode,
    get_default_wave_gen_subgraph,
    SquareWaveNode,
    TriangleWaveNode,
    RadialWaveNode,
)
from .subgraph import (
    SubgraphInputsNode,
    SubgraphOutputsNode,
    compile_subgraph,
    _add_comment_dedup,
)
from .displace import DisplaceNode
from .patterns import PatternNode, PATTERN_NODES
from .transform import OrientNode, AlignToDirectionNode, ToWorldLocalNode
from .output import OutputNode
from .compiler import (
    CompileResult,
    compile_graph,
)
from .templates import get_template_graph

NODE_CLASSES = [
    PositionNode,
    Combine2DNode,
    Separate2DNode,
    Rotate2DNode,
    Distance2DNode,
    DomainWarp2DNode,
    Combine3DNode,
    Separate3DNode,
    NoiseInputsNode,
    ConstantNode,
    CustomParamNode,
    MathNode,
    TrigNode,
    MixNode,
    ClampNode,
    MinMaxNode,
    SmoothstepNode,
    IgnoreBackFaceNode,
    RadialProjectionNode,
    ProjectionControlNode,
    WaveGeneratorNode,
    SquareWaveNode,
    TriangleWaveNode,
    RadialWaveNode,
    SubgraphInputsNode,
    SubgraphOutputsNode,
    OrientNode,
    AlignToDirectionNode,
    ToWorldLocalNode,
    DisplaceNode,
    OutputNode,
] + PATTERN_NODES

NODE_REGISTRY = {cls.node_type: cls for cls in NODE_CLASSES}


def get_available_node_classes(in_subgraph=False):
    """Returns list of node classes applicable to the given context."""
    return [cls for cls in NODE_CLASSES
            if (in_subgraph or not getattr(cls, "is_subgraph_only", False))
            and not getattr(cls, "is_deprecated", False)]
