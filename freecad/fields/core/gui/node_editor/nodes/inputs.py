# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.inputs

Input node definitions: 3D/2D coordinates, noise inputs, constants, and custom parameters.
"""
from .base import BaseNode, SocketDef, SocketType, glsl_param_identifier


class PartNode(BaseNode):
    node_type = "part"
    title = "Part"
    category = "Inputs"
    doc = (
        "The sample point p in world space. Direction Transform gives the noise\n"
        "object's local frame, Separate 3D splits it into components, and Origin\n"
        "and Polar give distance and radius."
    )

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [
            SocketDef("p", SocketType.VEC3, doc="Sample point in world space"),
        ]

    def generate_code(self, input_exprs, params):
        return {"p": "p"}

    def bound(self, ins, input_exprs, params, w):
        return {"p": ("inf", "1.0")}


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

    def bound(self, ins, input_exprs, params, w):
        return {"amp": ("abs(amp)", "0.0"), "freq": ("abs(freq)", "0.0")}


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

    def bound(self, ins, input_exprs, params, w):
        return {"Val": (f"abs({float(params.get('val', 1.0))!r})", "0.0")}


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

    def bound(self, ins, input_exprs, params, w):
        if params.get("ptype", "float") in ("vec2", "vec3"):
            return {"Val": ("inf", "0.0")}
        return {"Val": (f"abs({glsl_param_identifier(params.get('name', 'param'))})", "0.0")}

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
        return [f"// @param {ptype} {name} {def_str} {min_str} {max_str} {step_str}"]


class OriginNode(BaseNode):
    """Outputs the noise object's placement center/origin in world space (to_world_base)."""
    node_type = "origin"
    title = "Origin"
    category = "Inputs"
    doc = (
        "The noise object's center / origin point in world space (to_world_base).\n"
        "Driven by Placement.Base / gizmo Center.\n"
        "Compute distances from it using length(p - origin)."
    )

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [
            SocketDef("Origin", SocketType.VEC3, (0.0, 0.0, 0.0), doc="Placement center in world space"),
        ]

    def generate_code(self, input_exprs, params):
        return {"Origin": "to_world_base"}

    def bound(self, ins, input_exprs, params, w):
        return {"Origin": ("inf", "0.0")}


class SurfaceNormalNode(BaseNode):
    """Outputs the surface normal of the underlying base object in world space (surf_n)."""
    node_type = "surface_normal"
    title = "Surface Normal"
    category = "Inputs"
    doc = (
        "The surface normal vector (N) of the base shape in world space.\n"
        "Measured over a step h to bound slope across sharp edges."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Enabled", SocketType.BOOL, 1.0, doc="1.0 to compute normal, 0.0 to gate off"),
        ]
        self.outputs = [
            SocketDef("N", SocketType.VEC3, (0.0, 0.0, 1.0), doc="Surface normal in world space"),
        ]

    def generate_code(self, input_exprs, params):
        return {"N": "surf_n"}

    def bound(self, ins, input_exprs, params, w):
        return {"N": ("1.0", "surf_n_slope")}


class FrontFaceNode(BaseNode):
    """Outputs the front-face weight mask (front) in range [0, 1]."""
    node_type = "front_face"
    title = "Front Face"
    category = "Inputs"
    doc = (
        "The front-face mask weight (front) in [0, 1].\n"
        "Anchors the pattern to the front-facing surface along the projection direction."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Enabled", SocketType.BOOL, 1.0, doc="1.0 to compute mask, 0.0 to gate off"),
        ]
        self.outputs = [
            SocketDef("Front", SocketType.FLOAT, 1.0, doc="Front-face weight in [0, 1]"),
        ]

    def generate_code(self, input_exprs, params):
        return {"Front": "front"}

    def bound(self, ins, input_exprs, params, w):
        return {"Front": ("1.0", "front_slope")}
