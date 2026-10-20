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
                      doc="World axis to move along; typically Direction Transform Direction/U/V or Surface Normal"),
        ]
        self.outputs = [SocketDef("Out", SocketType.VEC3, doc="vec3 displacement")]
        self.default_params = {}

    def generate_code(self, input_exprs, params):
        import re
        amount = input_exprs.get("Amount", "0.0")
        if "Direction" not in input_exprs.get("__wired__", set()):
            return {"Out": "vec3(0.0)", "__error__": "Displace needs a Direction wire"}

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
        # Python (the CPU eval path), NumPy (the grid path) and GLSL.
        expr = (f"mix(vec3(0.0), normalize(({d}) + vec3(1e-9, 0.0, 0.0)), "
                f"step(1e-6, length({d})))")
        return {"Out": f"(({amount}) * {expr})"}

    def bound(self, ins, input_exprs, params, w):
        # |Amount * normalize(Direction)|: the unit vector's slope is L_d / |d|. Only
        # surf_n is known to be unit (charged 2 * L_d, the old surf_n factor); any other
        # varying Direction is unbounded (NV-068). A slope-0 direction pays nothing:
        # bmul(0, inf) is 0.
        from .bounds import badd, bmul
        if "Direction" not in input_exprs.get("__wired__", set()):
            return {"Out": ("0.0", "0.0")}
        a, d = ins["Amount"], ins["Direction"]
        factor = "2.0" if input_exprs.get("Direction", "").strip() == "surf_n" else "inf"
        return {"Out": (a.peak, badd(a.slope, bmul(a.peak, bmul(factor, d.slope))))}
