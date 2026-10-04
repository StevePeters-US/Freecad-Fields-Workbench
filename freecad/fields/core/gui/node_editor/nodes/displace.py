# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.displace

The terminal node: scalar pattern + direction -> vec3 displacement.
"""
from .base import BaseNode, SocketDef, SocketType


class DisplaceNode(BaseNode):
    node_type = "displace"
    title = "Displace"
    category = "Output"
    doc = ("Moves the surface by Amount along Direction.\n"
           "Direction is in object-local coordinates and is normalized;\n"
           "a zero-length Direction displaces nothing.")

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Amount", SocketType.FLOAT, 0.0, doc="Signed distance, in mm"),
            SocketDef("Direction", SocketType.VEC3, (0.0, 0.0, 1.0),
                      doc="Local axis to move along; normalized before use"),
        ]
        self.outputs = [SocketDef("Out", SocketType.VEC3, doc="vec3 displacement")]
        self.default_params = {"dir": [0.0, 0.0, 1.0]}

    def generate_code(self, input_exprs, params):
        import re
        amount = input_exprs.get("Amount", "0.0")
        if "Direction" in input_exprs.get("__wired__", set()):
            d = input_exprs["Direction"].strip()
            # If Direction is a constant literal vec3(c0, c1, c2), normalize it at compile time
            m = re.match(
                r"^vec3\(\s*([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*,\s*"
                r"([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*,\s*"
                r"([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*\)$",
                d,
            )
            if m:
                c0, c1, c2 = float(m.group(1)), float(m.group(2)), float(m.group(3))
                n = (c0**2 + c1**2 + c2**2) ** 0.5
                if n > 1e-6:
                    return {"Out": f"({amount}) * vec3({c0/n:.6g}, {c1/n:.6g}, {c2/n:.6g})"}
            # A zero Direction must not produce NaN: GLSL's normalize() of a zero
            # vector is undefined and poisons the whole marched value, not just
            # this field. Branch-free so the same text parses identically as
            # Python (the CPU eval path), NumPy (the grid path) and GLSL --
            # a `cond ? a : b` ternary is valid GLSL but a SyntaxError in Python.
            expr = (f"mix(vec3(0.0), normalize(({d}) + vec3(1e-9, 0.0, 0.0)), "
                    f"step(1e-6, length({d})))")
            expr = f"(({amount}) * {expr})"
        else:
            v = params.get("dir", [0.0, 0.0, 1.0])
            n = max((float(v[0])**2 + float(v[1])**2 + float(v[2])**2) ** 0.5, 1e-6)
            expr = (f"({amount}) * vec3({float(v[0])/n:.6g}, "
                    f"{float(v[1])/n:.6g}, {float(v[2])/n:.6g})")
        return {"Out": expr}
