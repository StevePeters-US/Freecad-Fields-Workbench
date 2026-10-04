# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.sdf.sdf.noise_nodes

The noise kernel registry. To add a node: write `noise_nodes/<thing>.py` with one
`SdfNoiseNode` subclass, then add it to the import and to `NOISE_NODES` below. Order
here is the order of the NoiseType combo box.

Value noise was removed from both of the old registries (2D first, 3D on 2026-08-03) as
not useful in practice; its scalar, numpy and GLSL implementations and the
fld_vhash2/fld_vhash3 helpers that served only it went with it. Do not resurrect one
half -- a preset is all four pieces or none (`design_3d_noise_presets`).
"""
from freecad.fields.core.sdf.sdf.noise_nodes.perlin_2d import Perlin2DNode
from freecad.fields.core.sdf.sdf.noise_nodes.voronoi_2d import Voronoi2DNode
from freecad.fields.core.sdf.sdf.noise_nodes.perlin_3d import Perlin3DNode
from freecad.fields.core.sdf.sdf.noise_nodes.voronoi_3d import Voronoi3DNode

_ALL = (Perlin2DNode, Voronoi2DNode, Perlin3DNode, Voronoi3DNode)

for _node in _ALL:
    _node.validate()

#: Display name -> node CLASS (never an instance). `SdfNoiseField.COMPLEX_PRESETS`.
NOISE_NODES = {node.name: node for node in _ALL}

