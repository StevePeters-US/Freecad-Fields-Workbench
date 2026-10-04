# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.vector

Vector node definitions: combine, separate, and 2D rotation.
"""
from .base import BaseNode, SocketDef, SocketType


class Combine2DNode(BaseNode):
    node_type = "combine_2d"
    title = "Combine 2D"
    category = "Vector"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("X", SocketType.FLOAT, 0.0),
            SocketDef("Y", SocketType.FLOAT, 0.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.VEC2)]

    def generate_code(self, input_exprs, params):
        x = input_exprs.get("X", "0.0")
        y = input_exprs.get("Y", "0.0")
        return {"Out": f"vec2({x}, {y})"}


class Separate2DNode(BaseNode):
    node_type = "separate_2d"
    title = "Separate 2D"
    category = "Vector"

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("In", SocketType.VEC2)]
        self.outputs = [
            SocketDef("X", SocketType.FLOAT),
            SocketDef("Y", SocketType.FLOAT),
        ]

    def generate_code(self, input_exprs, params):
        v = input_exprs.get("In", "vec2(0.0)")
        return {
            "X": f"({v}).x",
            "Y": f"({v}).y",
        }


class Combine3DNode(BaseNode):
    node_type = "combine_3d"
    title = "Combine 3D"
    category = "Vector"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("X", SocketType.FLOAT, 0.0),
            SocketDef("Y", SocketType.FLOAT, 0.0),
            SocketDef("Z", SocketType.FLOAT, 0.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.VEC3)]

    def generate_code(self, input_exprs, params):
        x = input_exprs.get("X", "0.0")
        y = input_exprs.get("Y", "0.0")
        z = input_exprs.get("Z", "0.0")
        return {"Out": f"vec3({x}, {y}, {z})"}


class Separate3DNode(BaseNode):
    node_type = "separate_3d"
    title = "Separate 3D"
    category = "Vector"

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("In", SocketType.VEC3)]
        self.outputs = [
            SocketDef("X", SocketType.FLOAT),
            SocketDef("Y", SocketType.FLOAT),
            SocketDef("Z", SocketType.FLOAT),
        ]

    def generate_code(self, input_exprs, params):
        v = input_exprs.get("In", "vec3(0.0)")
        return {
            "X": f"({v}).x",
            "Y": f"({v}).y",
            "Z": f"({v}).z",
        }


def get_default_rotate_2d_subgraph():
    """Default internal graph for Rotate2DNode: the 2x2 rotation matrix, spelled out.

    U' = U*cos(t) - V*sin(t)
    V' = U*sin(t) + V*cos(t)     with t = radians(Angle)
    """
    return {
        "nodes": [
            {"id": "sg_in", "type": "subgraph_inputs", "pos": [40, 160],
             "params": {"outputs": [
                 {"name": "U", "type": "float", "default": 0.0},
                 {"name": "V", "type": "float", "default": 0.0},
                 {"name": "Angle", "type": "float", "default": 0.0},
             ]}},
            {"id": "rad", "type": "trig", "pos": [280, 300], "params": {"fn": "radians"}},
            {"id": "cos", "type": "trig", "pos": [480, 250], "params": {"fn": "cos"}},
            {"id": "sin", "type": "trig", "pos": [480, 360], "params": {"fn": "sin"}},
            {"id": "u_cos", "type": "math", "pos": [700, 40], "params": {"op": "Multiply (*)"}},
            {"id": "v_sin", "type": "math", "pos": [700, 150], "params": {"op": "Multiply (*)"}},
            {"id": "u_sin", "type": "math", "pos": [700, 430], "params": {"op": "Multiply (*)"}},
            {"id": "v_cos", "type": "math", "pos": [700, 540], "params": {"op": "Multiply (*)"}},
            {"id": "sub_u", "type": "math", "pos": [920, 95], "params": {"op": "Subtract (-)"}},
            {"id": "add_v", "type": "math", "pos": [920, 485], "params": {"op": "Add (+)"}},
            {"id": "sg_out", "type": "subgraph_outputs", "pos": [1140, 280],
             "params": {"inputs": [
                 {"name": "U'", "type": "float", "default": 0.0},
                 {"name": "V'", "type": "float", "default": 0.0},
             ]}},
        ],
        "wires": [
            {"from_node": "sg_in", "from_socket": "Angle", "to_node": "rad", "to_socket": "In"},
            {"from_node": "rad", "from_socket": "Out", "to_node": "cos", "to_socket": "In"},
            {"from_node": "rad", "from_socket": "Out", "to_node": "sin", "to_socket": "In"},
            {"from_node": "sg_in", "from_socket": "U", "to_node": "u_cos", "to_socket": "A"},
            {"from_node": "cos", "from_socket": "Out", "to_node": "u_cos", "to_socket": "B"},
            {"from_node": "sg_in", "from_socket": "V", "to_node": "v_sin", "to_socket": "A"},
            {"from_node": "sin", "from_socket": "Out", "to_node": "v_sin", "to_socket": "B"},
            {"from_node": "sg_in", "from_socket": "U", "to_node": "u_sin", "to_socket": "A"},
            {"from_node": "sin", "from_socket": "Out", "to_node": "u_sin", "to_socket": "B"},
            {"from_node": "sg_in", "from_socket": "V", "to_node": "v_cos", "to_socket": "A"},
            {"from_node": "cos", "from_socket": "Out", "to_node": "v_cos", "to_socket": "B"},
            {"from_node": "u_cos", "from_socket": "Out", "to_node": "sub_u", "to_socket": "A"},
            {"from_node": "v_sin", "from_socket": "Out", "to_node": "sub_u", "to_socket": "B"},
            {"from_node": "u_sin", "from_socket": "Out", "to_node": "add_v", "to_socket": "A"},
            {"from_node": "v_cos", "from_socket": "Out", "to_node": "add_v", "to_socket": "B"},
            {"from_node": "sub_u", "from_socket": "Out", "to_node": "sg_out", "to_socket": "U'"},
            {"from_node": "add_v", "from_socket": "Out", "to_node": "sg_out", "to_socket": "V'"},
        ]
    }


class Rotate2DNode(BaseNode):
    node_type = "rotate_2d"
    title = "Rotate 2D"
    category = "Vector"
    is_subgraph = True
    doc = (
        "Rotates a 2D coordinate pair about the origin by Angle degrees, "
        "counter-clockwise.\n\n"
        "U and V are just the two components of that pair. Feed them whatever 2D "
        "pair you want rotated: x and y from Plane Coordinates for a flat pattern, "
        "or U and V from Radial Projection to rotate in the projected (radius, "
        "height) plane instead.\n\n"
        "Usually only one output is needed: take U' and ignore V' to get a wave "
        "travelling at Angle degrees across the plane."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("U", SocketType.FLOAT, 0.0,
                      doc="First component of the pair to rotate"),
            SocketDef("V", SocketType.FLOAT, 0.0,
                      doc="Second component of the pair to rotate"),
            SocketDef("Angle", SocketType.FLOAT, 0.0,
                      doc="Rotation in DEGREES, counter-clockwise. Converted with radians()."),
        ]
        self.outputs = [
            SocketDef("U'", SocketType.FLOAT, doc="U*cos(Angle) - V*sin(Angle)"),
            SocketDef("V'", SocketType.FLOAT, doc="U*sin(Angle) + V*cos(Angle)"),
        ]
        self.default_params = {"subgraph_data": get_default_rotate_2d_subgraph()}

    def generate_code(self, input_exprs, params):
        from .subgraph import compile_subgraph
        sg = params.get("subgraph_data")
        if not sg or not sg.get("nodes"):
            sg = get_default_rotate_2d_subgraph()
        res, _comments = compile_subgraph(sg, input_exprs)
        u = input_exprs.get("U", "0.0")
        v = input_exprs.get("V", "0.0")
        a = input_exprs.get("Angle", "0.0")
        res.setdefault("U'", f"({u} * cos(radians({a})) - {v} * sin(radians({a})))")
        res.setdefault("V'", f"({u} * sin(radians({a})) + {v} * cos(radians({a})))")
        return res

    def get_param_comments(self, params):
        from .subgraph import compile_subgraph
        sg = params.get("subgraph_data") or get_default_rotate_2d_subgraph()
        _res, comments = compile_subgraph(sg, {})
        return comments


class Distance2DNode(BaseNode):
    node_type = "distance_2d"
    title = "Distance 2D"
    category = "Vector"
    doc = (
        "Calculates the Euclidean distance between a 2D point (X, Y) and a center origin (CenterX, CenterY).\n\n"
        "Dist = length(vec2(X - CenterX, Y - CenterY))\n"
        "DX = X - CenterX\n"
        "DY = Y - CenterY\n\n"
        "Used for radial waves, ripples, and circular falloffs."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("X", SocketType.FLOAT, 0.0, doc="Sample X coordinate"),
            SocketDef("Y", SocketType.FLOAT, 0.0, doc="Sample Y coordinate"),
            SocketDef("CenterX", SocketType.FLOAT, 0.0, doc="Center X coordinate"),
            SocketDef("CenterY", SocketType.FLOAT, 0.0, doc="Center Y coordinate"),
        ]
        self.outputs = [
            SocketDef("Dist", SocketType.FLOAT, doc="Euclidean distance from center"),
            SocketDef("DX", SocketType.FLOAT, doc="Offset along X: X - CenterX"),
            SocketDef("DY", SocketType.FLOAT, doc="Offset along Y: Y - CenterY"),
            SocketDef("Offset", SocketType.VEC2, doc="Offset vector: vec2(DX, DY)"),
        ]

    def generate_code(self, input_exprs, params):
        x = input_exprs.get("X", "0.0")
        y = input_exprs.get("Y", "0.0")
        cx = input_exprs.get("CenterX", "0.0")
        cy = input_exprs.get("CenterY", "0.0")
        dx = f"(({x}) - ({cx}))"
        dy = f"(({y}) - ({cy}))"
        dist = f"length(vec2({dx}, {dy}))"
        return {
            "Dist": dist,
            "DX": dx,
            "DY": dy,
            "Offset": f"vec2({dx}, {dy})",
        }


class DomainWarp2DNode(BaseNode):
    node_type = "domain_warp_2d"
    title = "Domain Warp 2D"
    category = "Vector"
    doc = (
        "Perturbs 2D coordinates using a scalar noise factor.\n\n"
        "X' = X * (1.0 + Noise * Strength)\n"
        "Y' = Y * (1.0 + Noise * Strength)\n\n"
        "Breaks geometric symmetry to turn concentric waves into natural organic patterns."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("X", SocketType.FLOAT, 0.0, doc="Input X coordinate"),
            SocketDef("Y", SocketType.FLOAT, 0.0, doc="Input Y coordinate"),
            SocketDef("Noise", SocketType.FLOAT, 0.0, doc="Noise factor (e.g. from Perlin 2D)"),
            SocketDef("Strength", SocketType.FLOAT, 0.1, doc="Warp distortion strength"),
        ]
        self.outputs = [
            SocketDef("X'", SocketType.FLOAT, doc="Warped X: X * (1 + Noise * Strength)"),
            SocketDef("Y'", SocketType.FLOAT, doc="Warped Y: Y * (1 + Noise * Strength)"),
            SocketDef("UV'", SocketType.VEC2, doc="Warped 2D vector vec2(X', Y')"),
        ]

    def generate_code(self, input_exprs, params):
        x = input_exprs.get("X", "0.0")
        y = input_exprs.get("Y", "0.0")
        noise = input_exprs.get("Noise", "0.0")
        strength = input_exprs.get("Strength", "0.1")
        factor = f"(1.0 + ({noise}) * ({strength}))"
        wx = f"(({x}) * {factor})"
        wy = f"(({y}) * {factor})"
        return {
            "X'": wx,
            "Y'": wy,
            "UV'": f"vec2({wx}, {wy})",
        }

