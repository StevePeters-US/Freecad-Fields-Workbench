# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.transform

Orientation nodes: rotate a vec3 by a fixed axis-angle (Orient), build the
Direction+Roll frame the old projection used to hide (Align to Direction), and
cross between the object's local frame and world space (To World / To Local).
"""
import math
from .base import BaseNode, SocketDef, SocketType


def _rodrigues_expr(v_expr, kx, ky, kz, cos_t, sin_t):
    """Rotate `v_expr` (GLSL/Python text) by angle `t` about unit axis (kx,ky,kz).

    `k`, `cos_t` and `sin_t` are node PARAMS, known at compile time, so they are
    formatted into the text as literals -- no uniform plumbing needed, and the
    same text parses as Python (CPU eval), NumPy (grid eval) and GLSL, since it
    is built entirely from `cross`/`dot`/vec3 `*`/`+` -- all defined for
    GlslVec3 in formula_eval.py.
    """
    k = f"vec3({kx:.8g}, {ky:.8g}, {kz:.8g})"
    return (f"(({v_expr}) * {cos_t:.8g} + cross({k}, ({v_expr})) * {sin_t:.8g} "
            f"+ {k} * (dot({k}, ({v_expr})) * {(1.0 - cos_t):.8g}))")


class OrientNode(BaseNode):
    node_type = "orient"
    title = "Orient"
    category = "Transform"
    doc = ("Rotates Vector by Angle degrees about Axis (object-local).\n"
           "Axis and Angle are node parameters, not sockets -- use this to spin\n"
           "a pattern or aim a displacement without rotating the object itself.")

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("Vector", SocketType.VEC3, (0.0, 0.0, 0.0),
                                  doc="Vector to rotate")]
        self.outputs = [SocketDef("Out", SocketType.VEC3, doc="Rotated vector")]
        self.default_params = {"axis": [0.0, 0.0, 1.0], "angle": 0.0}

    def generate_code(self, input_exprs, params):
        wired = input_exprs.get("__wired__", set())
        v = input_exprs["Vector"] if "Vector" in wired else "vec3(0.0)"
        axis = params.get("axis", [0.0, 0.0, 1.0])
        angle = float(params.get("angle", 0.0))
        ax, ay, az = float(axis[0]), float(axis[1]), float(axis[2])
        n = max((ax * ax + ay * ay + az * az) ** 0.5, 1e-9)
        t = math.radians(angle)
        expr = _rodrigues_expr(v, ax / n, ay / n, az / n, math.cos(t), math.sin(t))
        return {"Out": expr}

    def bound(self, ins, input_exprs, params, w):
        # A rotation keeps the norm and the slope.
        if "Vector" not in input_exprs.get("__wired__", set()):
            return {"Out": ("0.0", "0.0")}
        v = ins["Vector"]
        return {"Out": (v.peak, v.slope)}


class DirectionTransformNode(BaseNode):
    node_type = "direction_transform"
    title = "Direction Transform"
    category = "Transform"
    doc = (
        "Expresses points and directions in the noise modifier's local Placement.\n"
        "In: world-space sample point (defaults to p).\n"
        "Local: point in object-local frame (apply_inv_mat(to_local_inv, In)).\n"
        "Direction: world vector along local +Z (the Direction arrow, apply_rot_mat(to_world_rot, (0,0,1))).\n"
        "U: world vector along local +X (apply_rot_mat(to_world_rot, (1,0,0))).\n"
        "V: world vector along local +Y (apply_rot_mat(to_world_rot, (0,1,0)))."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("In", SocketType.VEC3, (0.0, 0.0, 0.0), doc="World-space vector or point (defaults to p)")
        ]
        self.outputs = [
            SocketDef("Local", SocketType.VEC3, doc="Object-local point"),
            SocketDef("Direction", SocketType.VEC3, doc="World direction of local +Z"),
            SocketDef("U", SocketType.VEC3, doc="World direction of local +X"),
            SocketDef("V", SocketType.VEC3, doc="World direction of local +Y"),
        ]
        self.default_params = {}

    def generate_code(self, input_exprs, params):
        wired = input_exprs.get("__wired__", set())
        in_pt = input_exprs["In"] if "In" in wired else "p"
        return {
            "Local": f"apply_inv_mat(to_local_inv, ({in_pt}))",
            "Direction": "apply_rot_mat(to_world_rot, vec3(0.0, 0.0, 1.0))",
            "U": "apply_rot_mat(to_world_rot, vec3(1.0, 0.0, 0.0))",
            "V": "apply_rot_mat(to_world_rot, vec3(0.0, 1.0, 0.0))",
        }

    def bound(self, ins, input_exprs, params, w):
        # Local is an isometry of In (p when unwired).
        if "In" in input_exprs.get("__wired__", set()):
            local = (ins["In"].peak, ins["In"].slope)
        else:
            local = ("inf", "1.0")
        unit = ("1.0", "0.0")
        return {"Local": local, "Direction": unit, "U": unit, "V": unit}
