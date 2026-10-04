# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.inputs

Input node definitions: 3D/2D coordinates, noise inputs, constants, and custom parameters.
"""
from .base import BaseNode, SocketDef, SocketType, glsl_param_identifier


class PositionNode(BaseNode):
    node_type = "position"
    title = "Position"
    category = "Inputs"
    doc = (
        "The sample point in object-local Cartesian coordinates: the world point\n"
        "mapped through the inverse of the noise object's Placement.\n"
        "  p       vec3, the local position\n"
        "  p.x/y/z its components, along the object's own axes\n"
        "  len(p)  distance from the object's origin\n"
        "  r       sqrt(x*x + y*y), the in-plane radius about the local Z axis\n"
        "  front   1 in front of the base surface, fading to 0 behind it"
    )

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [
            SocketDef("p", SocketType.VEC3, doc="Local position"),
            SocketDef("p.x", SocketType.FLOAT, doc="Local X"),
            SocketDef("p.y", SocketType.FLOAT, doc="Local Y"),
            SocketDef("p.z", SocketType.FLOAT, doc="Local Z"),
            SocketDef("len(p)", SocketType.FLOAT, doc="Distance from local origin"),
            SocketDef("r", SocketType.FLOAT, doc="sqrt(x*x + y*y) about local Z"),
            SocketDef("front", SocketType.FLOAT,
                      doc="1 in front of the base surface along the mask axis, "
                          "fading to 0 behind it"),
        ]

    def generate_code(self, input_exprs, params):
        return {
            "p": "p", "p.x": "p.x", "p.y": "p.y", "p.z": "p.z",
            "len(p)": "length(p)", "r": "r", "front": "front",
        }


class NoiseInputsNode(BaseNode):
    node_type = "noise_inputs"
    title = "Noise Inputs"
    category = "Inputs"

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [
            SocketDef("amp", SocketType.FLOAT, 1.0),
            SocketDef("freq", SocketType.FLOAT, 0.1),
        ]

    def generate_code(self, input_exprs, params):
        return {
            "amp": "amp",
            "freq": "freq",
        }


class ConstantNode(BaseNode):
    node_type = "constant"
    title = "Constant"
    category = "Inputs"

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [SocketDef("Val", SocketType.FLOAT, 1.0)]
        self.default_params = {"val": 1.0}

    def generate_code(self, input_exprs, params):
        val = float(params.get("val", 1.0))
        return {"Val": f"{val:.4g}"}


class CustomParamNode(BaseNode):
    node_type = "custom_param"
    title = "Custom Parameter"
    category = "Inputs"

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [SocketDef("Val", SocketType.FLOAT, 1.0)]
        self.default_params = {
            "name": "param",
            "ptype": "float",
            "default": 1.0,
            "min": 0.0,
            "max": 5.0,
            "step": 0.1,
        }

    def get_outputs(self, params):
        ptype = params.get("ptype", "float")
        if ptype == "vec3":
            return [SocketDef("Val", SocketType.VEC3, (0.0, 0.0, 0.0))]
        elif ptype == "vec2":
            return [SocketDef("Val", SocketType.VEC2, (0.0, 0.0))]
        return [SocketDef("Val", SocketType.FLOAT, 1.0)]

    def generate_code(self, input_exprs, params):
        return {"Val": glsl_param_identifier(params.get("name", "param"))}

    def get_param_comments(self, params):
        name = glsl_param_identifier(params.get("name", "param"))
        ptype = params.get("ptype", "float")

        def _fmt(val):
            try:
                v = float(val)
                if v.is_integer():
                    return str(int(v))
                return f"{v:.4f}".rstrip('0').rstrip('.')
            except Exception:
                return str(val)

        def_str = _fmt(params.get("default", 1.0))
        min_str = _fmt(params.get("min", 0.0))
        max_str = _fmt(params.get("max", 5.0))
        step_str = _fmt(params.get("step", 0.1))

        if ptype == "bool":
            def_v = 1 if params.get("default", 1) in (1, 1.0, True, "1", "true", "True") else 0
            return [f"// @param bool {name} {def_v}"]
        elif ptype == "int":
            return [f"// @param int {name} {def_str} {min_str} {max_str}"]
        elif ptype == "vec2":
            def_val = params.get("default", [0.0, 0.0])
            if isinstance(def_val, (list, tuple)):
                v_str = f"{_fmt(def_val[0])},{_fmt(def_val[1])}"
            else:
                v_str = f"{_fmt(def_val)},0"
            return [f"// @param vec2 {name} {v_str}"]
        elif ptype == "vec3":
            def_val = params.get("default", [0.0, 0.0, 0.0])
            if isinstance(def_val, (list, tuple)):
                v_str = f"{_fmt(def_val[0])},{_fmt(def_val[1])},{_fmt(def_val[2])}"
            else:
                v_str = f"{_fmt(def_val)},0,0"
            return [f"// @param vec3 {name} {v_str}"]
        return [f"// @param {ptype} {name} {def_str} {min_str} {max_str} {step_str}"]
