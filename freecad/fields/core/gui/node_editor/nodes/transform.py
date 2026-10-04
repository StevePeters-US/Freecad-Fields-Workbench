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


class AlignToDirectionNode(BaseNode):
    node_type = "align_to_direction"
    title = "Align to Direction"
    category = "Transform"
    doc = ("Re-expresses Vector on the (u, w, Direction) basis built from\n"
           "Direction + Roll -- the node form of the projection frame this\n"
           "workbench used to hide inside the field. u is Direction's in-plane\n"
           "'horizontal', w its 'vertical', matching the old top view.")

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("Vector", SocketType.VEC3, (0.0, 0.0, 0.0),
                                  doc="Vector to reorient")]
        self.outputs = [SocketDef("Out", SocketType.VEC3,
                                   doc="Vector.x*u + Vector.y*w + Vector.z*Direction")]
        self.default_params = {"direction": [0.0, 0.0, -1.0], "roll": 0.0}

    def generate_code(self, input_exprs, params):
        import FreeCAD
        from freecad.fields.core.sdf.sdf.noise import SdfNoiseField

        wired = input_exprs.get("__wired__", set())
        v = input_exprs["Vector"] if "Vector" in wired else "vec3(0.0)"
        d = params.get("direction", [0.0, 0.0, -1.0])
        roll = float(params.get("roll", 0.0))
        direction = FreeCAD.Vector(float(d[0]), float(d[1]), float(d[2]))
        if direction.Length < 1e-9:
            direction = FreeCAD.Vector(0, 0, -1)
        else:
            direction.normalize()
        # Same left-handed (u, w, d) triad _make_basis always builds -- see its
        # own docstring (noise.py) for why u x d, not d x u.
        u, w = SdfNoiseField._make_basis(direction, roll)
        u_lit = f"vec3({u.x:.8g}, {u.y:.8g}, {u.z:.8g})"
        w_lit = f"vec3({w.x:.8g}, {w.y:.8g}, {w.z:.8g})"
        d_lit = f"vec3({direction.x:.8g}, {direction.y:.8g}, {direction.z:.8g})"
        expr = f"(({v}).x * {u_lit} + ({v}).y * {w_lit} + ({v}).z * {d_lit})"
        return {"Out": expr}


class ToWorldLocalNode(BaseNode):
    node_type = "to_world_local"
    title = "To World / To Local"
    category = "Transform"
    doc = ("Crosses between the object's local frame and world space, using\n"
           "the object's own Placement -- so a pattern can be pinned to world\n"
           "space (stays put when the object moves) instead of riding it.\n"
           "'To World' treats Vector as a point (adds the object's origin);\n"
           "'To Local' is its inverse.")

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("Vector", SocketType.VEC3, (0.0, 0.0, 0.0))]
        self.outputs = [SocketDef("Out", SocketType.VEC3)]
        self.default_params = {"space": "To World"}

    def generate_code(self, input_exprs, params):
        wired = input_exprs.get("__wired__", set())
        v = input_exprs["Vector"] if "Vector" in wired else "vec3(0.0)"
        space = params.get("space", "To World")
        # `to_world_rot`/`to_world_base`/`to_local_inv` are well-known
        # identifiers, not uniform names: SdfNoiseField.to_glsl (noise.py)
        # substring-scans the compiled formula for them and, only then, adds
        # the matching mat4/vec3 as an extra formula-helper parameter bound to
        # THIS field's own placement uniforms -- the same mechanism `front`
        # already uses for the ignore-back-face mask. A literal-baked matrix
        # would be wrong the moment two fields sharing this formula text have
        # different placements; a fixed global uniform name would collide the
        # same way. `_eval_formula`/`_eval_formula_grid_np` bind the same three
        # names to closures over `self.placement`, so the identical text means
        # the identical thing in all three backends.
        if space == "To Local":
            return {"Out": f"apply_inv_mat(to_local_inv, ({v}))"}
        return {"Out": f"(apply_rot_mat(to_world_rot, ({v})) + to_world_base)"}
