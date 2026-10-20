# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.vector

Vector node definitions: combine, separate, and 2D rotation.
"""
from .base import BaseNode, SocketDef, SocketType
from .bounds import INF, badd, bmul


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

    def bound(self, ins, input_exprs, params, w):
        # Sum of components (>= the norm).
        return {"Out": (badd(*(ins[k].peak for k in "XY")), badd(*(ins[k].slope for k in "XY")))}


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

    def bound(self, ins, input_exprs, params, w):
        v = ins["In"]       # each component is bounded by the vector
        return {k: (v.peak, v.slope) for k in "XY"}


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

    def bound(self, ins, input_exprs, params, w):
        return {"Out": (badd(*(ins[k].peak for k in "XYZ")), badd(*(ins[k].slope for k in "XYZ")))}


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

    def bound(self, ins, input_exprs, params, w):
        v = ins["In"]
        return {k: (v.peak, v.slope) for k in "XYZ"}


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

    def bound(self, ins, input_exprs, params, w):
        from .subgraph import compile_subgraph
        sg = params.get("subgraph_data")
        if not sg or not sg.get("nodes"):
            sg = get_default_rotate_2d_subgraph()
        res, _ = compile_subgraph(sg, input_exprs, ins, w)
        u, v = ins["U"], ins["V"]
        fallback = (badd(u.peak, v.peak), badd(u.slope, v.slope))
        b = res.get("__bounds__", {})
        return {"U'": b.get("U'", fallback), "V'": b.get("V'", fallback)}

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

    def bound(self, ins, input_exprs, params, w):
        x, y, cx, cy = ins["X"], ins["Y"], ins["CenterX"], ins["CenterY"]
        dx = (badd(x.peak, cx.peak), badd(x.slope, cx.slope))
        dy = (badd(y.peak, cy.peak), badd(y.slope, cy.slope))
        both = (badd(dx[0], dy[0]), badd(dx[1], dy[1]))
        return {"Dist": both, "DX": dx, "DY": dy, "Offset": both}


class VectorMathNode(BaseNode):
    """Vector mathematics operations on vec3 vectors: Dot, Cross, Add, Subtract, Scale, Length, Normalize."""
    node_type = "vector_math"
    title = "Vector Math"
    category = "Vector"

    OPS = {
        "Dot": "Dot",
        "Cross": "Cross",
        "Add": "Add",
        "Subtract": "Subtract",
        "Scale": "Scale",
        "Length": "Length",
        "Normalize": "Normalize",
    }

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("A", SocketType.VEC3, (0.0, 0.0, 0.0), doc="First vector"),
            SocketDef("B", SocketType.VEC3, (0.0, 0.0, 0.0), doc="Second vector"),
            SocketDef("S", SocketType.FLOAT, 1.0, doc="Scalar multiplier"),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]
        self.default_params = {"op": "Dot"}

    def get_outputs(self, params):
        op = params.get("op", "Dot")
        if op in ("Dot", "Length"):
            return [SocketDef("Out", SocketType.FLOAT, doc=f"Scalar result of {op}")]
        return [SocketDef("Out", SocketType.VEC3, doc=f"Vector result of {op}")]

    def generate_code(self, input_exprs, params):
        a = input_exprs.get("A", "vec3(0.0)")
        b = input_exprs.get("B", "vec3(0.0)")
        s = input_exprs.get("S", "1.0")
        op = params.get("op", "Dot")
        if op == "Dot":
            return {"Out": f"dot({a}, {b})"}
        elif op == "Cross":
            return {"Out": f"cross({a}, {b})"}
        elif op == "Add":
            return {"Out": f"({a} + {b})"}
        elif op == "Subtract":
            return {"Out": f"({a} - {b})"}
        elif op == "Scale":
            return {"Out": f"({a} * {s})"}
        elif op == "Length":
            return {"Out": f"length({a})"}
        elif op == "Normalize":
            return {"Out": f"mix(vec3(0.0), normalize(({a}) + vec3(1e-9, 0.0, 0.0)), step(1e-6, length({a})))"}
        return {"Out": f"dot({a}, {b})"}

    def bound(self, ins, input_exprs, params, w):
        a, b, s = ins["A"], ins["B"], ins["S"]
        op = params.get("op", "Dot")
        if op in ("Dot", "Cross"):
            r = (bmul(a.peak, b.peak), badd(bmul(a.slope, b.peak), bmul(b.slope, a.peak)))
        elif op in ("Add", "Subtract"):
            r = (badd(a.peak, b.peak), badd(a.slope, b.slope))
        elif op == "Scale":
            r = (bmul(a.peak, s.peak), badd(bmul(a.slope, s.peak), bmul(s.slope, a.peak)))
        elif op == "Length":
            r = (a.peak, a.slope)
        elif op == "Normalize":
            r = ("1.0", INF)                         # unbounded near the zero vector
        else:
            r = (INF, INF)
        return {"Out": r}


class PolarNode(BaseNode):
    """Converts a 3D vector to cylindrical/polar coordinates: radius R, angle Theta, and depth Z."""
    node_type = "polar"
    title = "Polar"
    category = "Vector"
    doc = (
        "Converts a 3D vector V into in-plane radius R, polar angle Theta, and depth Z.\n\n"
        "R = length(vec2(V.x, V.y))\n"
        "Theta = atan(V.y, V.x) in radians (-pi..pi)\n"
        "Z = V.z\n\n"
        "The angle is measured about whatever axis V's z is, so the normal wiring is "
        "Direction Transform's Local into V.\n"
        "Theta jumps at +-pi, so a pattern built on Theta must repeat a whole number of times "
        "around, or it leaves a seam."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("V", SocketType.VEC3, (0.0, 0.0, 0.0), doc="Input vector"),
        ]
        self.outputs = [
            SocketDef("R", SocketType.FLOAT, doc="In-plane radius length(vec2(V.x, V.y))"),
            SocketDef("Theta", SocketType.FLOAT, doc="Polar angle atan(V.y, V.x) in radians (-pi..pi)"),
            SocketDef("Z", SocketType.FLOAT, doc="Height / depth V.z"),
        ]

    def generate_code(self, input_exprs, params):
        v = input_exprs.get("V", "vec3(0.0)")
        return {
            "R": f"length(vec2(({v}).x, ({v}).y))",
            "Theta": f"atan(({v}).y, ({v}).x)",
            "Z": f"({v}).z",
        }

    def bound(self, ins, input_exprs, params, w):
        v = ins["V"]
        # Theta jumps at +-pi, so its slope is unbounded.
        return {"R": (v.peak, v.slope), "Theta": ("3.1415927", INF), "Z": (v.peak, v.slope)}
