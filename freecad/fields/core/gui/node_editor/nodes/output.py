# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.output

Output node definition for the visual noise formula node editor.
"""
from .base import BaseNode, SocketDef, SocketType


class OutputNode(BaseNode):
    node_type = "output"
    title = "Noise Output"
    category = "Output"

    def __init__(self):
        super().__init__()
        # vec3 only, in object-local coordinates. A scalar pattern reaches here
        # through a Displace node, which is what makes the displacement axis a
        # visible wire instead of hidden field state.
        self.inputs = [SocketDef("Displacement", SocketType.VEC3, (0.0, 0.0, 0.0))]
        self.outputs = []

    def generate_code(self, input_exprs, params):
        expr = input_exprs.get("Displacement", "vec3(0.0)")
        src = input_exprs.get("__Displacement_type__", SocketType.VEC3)
        if src == SocketType.FLOAT:
            return {"result": "vec3(0.0)", "result_type": SocketType.VEC3,
                    "error": "Output takes a vec3 -- insert a Displace node."}
        if src == SocketType.VEC2:
            return {"result": f"vec3({expr}, 0.0)", "result_type": SocketType.VEC3}
        return {"result": expr, "result_type": SocketType.VEC3}
