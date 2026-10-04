# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.projection

Projection node definitions: RadialProjectionNode and its default subgraph.
"""
from .base import BaseNode, SocketDef, SocketType
from .subgraph import compile_subgraph


def get_default_radial_projection_subgraph():
    """Default internal node graph for RadialProjectionNode."""
    return {
        "nodes": [
            {
                "id": "sg_in",
                "type": "subgraph_inputs",
                "pos": [40, 100],
                "params": {
                    "outputs": [
                        {"name": "X", "type": "float", "default": 0.0},
                        {"name": "Y", "type": "float", "default": 0.0},
                        {"name": "Z", "type": "float", "default": 0.0},
                        {"name": "Radial", "type": "bool", "default": 1.0},
                    ]
                },
            },
            {"id": "mul_x", "type": "math", "pos": [260, 40], "params": {"op": "Multiply (*)"}},
            {"id": "mul_y", "type": "math", "pos": [260, 160], "params": {"op": "Multiply (*)"}},
            {"id": "add_xy", "type": "math", "pos": [460, 100], "params": {"op": "Add (+)"}},
            {"id": "sqrt_r", "type": "math", "pos": [660, 100], "params": {"op": "Sqrt (sqrt)"}},
            {"id": "mix_u", "type": "mix", "pos": [860, 40], "params": {}},
            {"id": "mix_v", "type": "mix", "pos": [860, 180], "params": {}},
            {"id": "comb_uv", "type": "combine_2d", "pos": [1080, 240], "params": {}},
            {
                "id": "sg_out",
                "type": "subgraph_outputs",
                "pos": [1280, 100],
                "params": {
                    "inputs": [
                        {"name": "U", "type": "float", "default": 0.0},
                        {"name": "V", "type": "float", "default": 0.0},
                        {"name": "UV", "type": "vec2", "default": 0.0},
                    ]
                },
            },
        ],
        "wires": [
            {"from_node": "sg_in", "from_socket": "X", "to_node": "mul_x", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "X", "to_node": "mul_x", "to_socket": "B"},
            {"from_node": "sg_in", "from_socket": "Y", "to_node": "mul_y", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "Y", "to_node": "mul_y", "to_socket": "B"},
            {"from_node": "mul_x", "from_socket": "Out", "to_node": "add_xy", "to_socket": "A"},
            {"from_node": "mul_y", "from_socket": "Out", "to_node": "add_xy", "to_socket": "B"},
            {"from_node": "add_xy", "from_socket": "Out", "to_node": "sqrt_r", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "X", "to_node": "mix_u", "to_socket": "A"},
            {"from_node": "sqrt_r", "from_socket": "Out", "to_node": "mix_u", "to_socket": "B"},
            {"from_node": "sg_in", "from_socket": "Radial", "to_node": "mix_u", "to_socket": "T"},
            {"from_node": "sg_in", "from_socket": "Y", "to_node": "mix_v", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "Z", "to_node": "mix_v", "to_socket": "B"},
            {"from_node": "sg_in", "from_socket": "Radial", "to_node": "mix_v", "to_socket": "T"},
            {"from_node": "mix_u", "from_socket": "Out", "to_node": "comb_uv", "to_socket": "X"},
            {"from_node": "mix_v", "from_socket": "Out", "to_node": "comb_uv", "to_socket": "Y"},
            {"from_node": "mix_u", "from_socket": "Out", "to_node": "sg_out", "to_socket": "U"},
            {"from_node": "mix_v", "from_socket": "Out", "to_node": "sg_out", "to_socket": "V"},
            {"from_node": "comb_uv", "from_socket": "Out", "to_node": "sg_out", "to_socket": "UV"},
        ]
    }


class RadialProjectionNode(BaseNode):
    """Converts cartesian (X, Y, Z) to cylindrical (U = sqrt(x²+y²), V = z) toggled by Radial bool."""
    node_type = "radial_projection"
    title = "Radial Projection"
    category = "Subgraph"
    is_subgraph = True

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("X", SocketType.FLOAT, 0.0),
            SocketDef("Y", SocketType.FLOAT, 0.0),
            SocketDef("Z", SocketType.FLOAT, 0.0),
            SocketDef("Radial", SocketType.BOOL, 1.0),
        ]
        self.outputs = [
            SocketDef("U", SocketType.FLOAT),
            SocketDef("V", SocketType.FLOAT),
            SocketDef("UV", SocketType.VEC2),
        ]
        self.default_params = {
            "radial": True,
            "subgraph_data": get_default_radial_projection_subgraph()
        }

    def generate_code(self, input_exprs, params):
        sg_data = params.get("subgraph_data")
        if not sg_data or not sg_data.get("nodes"):
            sg_data = get_default_radial_projection_subgraph()

        inputs = dict(input_exprs)
        # A wire into Radial wins; with nothing plugged in, the node's own
        # "Radial Mode" checkbox decides. This used to test `"Radial" not in
        # inputs` alone, but the compiler pre-fills every input with its socket
        # default, so that never fired: the checkbox did nothing and the 1.0
        # default pinned radial on. Toggling `radial` in the task panel looked
        # broken for the same reason -- an unwired Radial socket emits the
        # literal 1.0, so the formula never mentioned the parameter at all and
        # there was nothing for the property to move (NE-018).
        #
        # `__wired__` missing means the caller never told us (a direct
        # generate_code call in a test, say), so the dict is taken at face value.
        wired = input_exprs.get("__wired__")
        if "Radial" not in inputs or (wired is not None and "Radial" not in wired):
            is_rad = params.get("radial", True)
            inputs["Radial"] = "1.0" if is_rad else "0.0"
        inputs.pop("__wired__", None)

        res, comments = compile_subgraph(sg_data, inputs)
        x = inputs.get("X", "0.0")
        y = inputs.get("Y", "0.0")
        z = inputs.get("Z", "0.0")
        rad = inputs.get("Radial", "1.0")
        if "U" not in res:
            res["U"] = f"mix({x}, sqrt(({x}) * ({x}) + ({y}) * ({y})), {rad})"
        if "V" not in res:
            res["V"] = f"mix({y}, {z}, {rad})"
        if "UV" not in res:
            res["UV"] = f"vec2({res['U']}, {res['V']})"
        return res

    def get_param_comments(self, params):
        sg_data = params.get("subgraph_data")
        if not sg_data or not sg_data.get("nodes"):
            sg_data = get_default_radial_projection_subgraph()
        _, comments = compile_subgraph(sg_data, {})
        return comments


class ProjectionControlNode(BaseNode):
    """Controls planar projection and displacement direction.
    Projects 3D Position onto a 2D plane perpendicular to Direction,
    producing in-plane coordinates (U, V, UV), Depth along Direction,
    and the Direction vector itself.
    """
    node_type = "projection_control"
    title = "Projection Control"
    category = "Projection"
    doc = (
        "Controls planar projection and displacement direction.\n"
        "Projects 3D Position onto a 2D plane perpendicular to Direction,\n"
        "producing in-plane coordinates (U, V, UV), Depth along Direction,\n"
        "and the Direction vector itself."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Position", SocketType.VEC3, (0.0, 0.0, 0.0),
                      doc="Sample point to project (typically Position.p)"),
            SocketDef("Direction", SocketType.VEC3, (0.0, 0.0, 1.0),
                      doc="Projection / displacement axis (unwired: uses node param)"),
            SocketDef("Roll", SocketType.FLOAT, 0.0,
                      doc="Spin about projection axis in degrees (unwired: uses node param)"),
        ]
        self.outputs = [
            SocketDef("U", SocketType.FLOAT, doc="In-plane horizontal coordinate"),
            SocketDef("V", SocketType.FLOAT, doc="In-plane vertical coordinate"),
            SocketDef("R", SocketType.FLOAT, doc="In-plane radial distance sqrt(U²+V²)"),
            SocketDef("UV", SocketType.VEC2, doc="In-plane 2D coordinate vec2(U, V)"),
            SocketDef("Depth", SocketType.FLOAT, doc="Distance along projection axis"),
            SocketDef("Direction", SocketType.VEC3, doc="Normalized projection direction"),
        ]
        self.default_params = {
            "direction": [0.0, 0.0, 1.0],
            "roll": 0.0,
        }

    def generate_code(self, input_exprs, params):
        import FreeCAD
        from freecad.fields.core.sdf.sdf.noise import SdfNoiseField

        wired = input_exprs.get("__wired__", set()) or set()
        p_expr = input_exprs["Position"] if "Position" in wired else "p"

        if "Direction" not in wired and "Roll" not in wired:
            d_param = params.get("direction", [0.0, 0.0, 1.0])
            roll_param = float(params.get("roll", 0.0))
            d_vec = FreeCAD.Vector(float(d_param[0]), float(d_param[1]), float(d_param[2]))
            if d_vec.Length < 1e-9:
                d_vec = FreeCAD.Vector(0.0, 0.0, 1.0)
            else:
                d_vec.normalize()

            ref = FreeCAD.Vector(0, 1, 0) if abs(d_vec.x) > 0.9 else FreeCAD.Vector(1, 0, 0)
            u_vec = ref - d_vec * ref.dot(d_vec)
            u_vec = u_vec / u_vec.Length if u_vec.Length > 1e-8 else FreeCAD.Vector(1, 0, 0)
            # Right-handed (u, v, d) frame: for d = (0, 0, 1), u = (1, 0, 0) (+X) and v = (0, 1, 0) (+Y)
            v_vec = d_vec.cross(u_vec)
            v_vec = v_vec / v_vec.Length if v_vec.Length > 1e-8 else FreeCAD.Vector(0, 1, 0)
            if abs(roll_param) > 1e-9:
                rot = FreeCAD.Rotation(d_vec, roll_param)
                u_vec = rot.multVec(u_vec)
                v_vec = rot.multVec(v_vec)

            u_lit = f"vec3({u_vec.x:.8g}, {u_vec.y:.8g}, {u_vec.z:.8g})"
            v_lit = f"vec3({v_vec.x:.8g}, {v_vec.y:.8g}, {v_vec.z:.8g})"
            d_lit = f"vec3({d_vec.x:.8g}, {d_vec.y:.8g}, {d_vec.z:.8g})"

            return {
                "U": f"dot({p_expr}, {u_lit})",
                "V": f"dot({p_expr}, {v_lit})",
                "R": f"length(vec2(dot({p_expr}, {u_lit}), dot({p_expr}, {v_lit})))",
                "UV": f"vec2(dot({p_expr}, {u_lit}), dot({p_expr}, {v_lit}))",
                "Depth": f"dot({p_expr}, {d_lit})",
                "Direction": d_lit,
            }
        else:
            if "Direction" in wired:
                d_raw = input_exprs["Direction"]
                d_expr = f"mix(vec3(0.0, 0.0, 1.0), normalize(({d_raw}) + vec3(1e-9, 0.0, 0.0)), step(1e-6, length({d_raw})))"
            else:
                d_param = params.get("direction", [0.0, 0.0, 1.0])
                d_vec = FreeCAD.Vector(float(d_param[0]), float(d_param[1]), float(d_param[2]))
                if d_vec.Length < 1e-9:
                    d_vec = FreeCAD.Vector(0.0, 0.0, 1.0)
                else:
                    d_vec.normalize()
                d_expr = f"vec3({d_vec.x:.8g}, {d_vec.y:.8g}, {d_vec.z:.8g})"

            ref_expr = f"mix(vec3(1.0, 0.0, 0.0), vec3(0.0, 1.0, 0.0), step(0.9, abs(({d_expr}).x)))"
            u_raw = f"normalize({ref_expr} - ({d_expr}) * dot({ref_expr}, {d_expr}))"
            v_raw = f"cross({d_expr}, {u_raw})"

            if "Roll" in wired or abs(float(params.get("roll", 0.0))) > 1e-9:
                roll_expr = input_exprs["Roll"] if "Roll" in wired else str(float(params.get("roll", 0.0)))
                rad = f"radians({roll_expr})"
                cos_t = f"cos({rad})"
                sin_t = f"sin({rad})"
                u_rot = f"(({u_raw}) * {cos_t} + cross({d_expr}, {u_raw}) * {sin_t})"
                v_rot = f"(({v_raw}) * {cos_t} + cross({d_expr}, {v_raw}) * {sin_t})"
            else:
                u_rot = u_raw
                v_rot = v_raw

            return {
                "U": f"dot({p_expr}, {u_rot})",
                "V": f"dot({p_expr}, {v_rot})",
                "R": f"length(vec2(dot({p_expr}, {u_rot}), dot({p_expr}, {v_rot})))",
                "UV": f"vec2(dot({p_expr}, {u_rot}), dot({p_expr}, {v_rot}))",
                "Depth": f"dot({p_expr}, {d_expr})",
                "Direction": d_expr,
            }
