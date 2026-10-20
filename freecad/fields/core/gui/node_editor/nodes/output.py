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
        # Displacement (VEC3) and additive Offset (FLOAT, default 0.0) (NV-038).
        # A scalar pattern reaches Displacement through a Displace node, or Offset directly.
        self.inputs = [
            SocketDef("Displacement", SocketType.VEC3, (0.0, 0.0, 0.0)),
            SocketDef("Offset", SocketType.FLOAT, 0.0),
        ]
        self.outputs = []

    def generate_code(self, input_exprs, params):
        wired = input_exprs.get("__wired__", set())
        src = input_exprs.get("__Displacement_type__", SocketType.VEC3)
        disp_expr = input_exprs.get("Displacement", "vec3(0.0)")
        if "Displacement" not in wired:
            disp_expr = "vec3(0.0)"
        elif src == SocketType.FLOAT:
            return {"result": "vec3(0.0)", "result_type": SocketType.VEC3,
                    "error": "Output takes a vec3 -- insert a Displace node."}
        elif src == SocketType.VEC2:
            disp_expr = f"vec3({disp_expr}, 0.0)"

        if "Offset" in wired:
            offset_expr = input_exprs.get("Offset", "0.0")
            return {"result": f"vec4({disp_expr}, {offset_expr})", "result_type": SocketType.VEC3}

        return {"result": disp_expr, "result_type": SocketType.VEC3}

    def bound(self, ins, input_exprs, params, w):
        from .bounds import badd
        wired = input_exprs.get("__wired__", set())
        src = input_exprs.get("__Displacement_type__", SocketType.VEC3)
        if "Displacement" in wired and src != SocketType.FLOAT:
            peak, slope = ins["Displacement"].peak, ins["Displacement"].slope
        else:
            peak, slope = "0.0", "0.0"
        if "Offset" in wired:
            peak, slope = badd(peak, ins["Offset"].peak), badd(slope, ins["Offset"].slope)
        return {"result": (peak, slope)}
