# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField, swept_rotation_bounds
import numpy as np

AXES = {"X": 0, "Y": 1, "Z": 2}


class SdfTwistField(SdfField):
    """Twist deformation wrapping a source SDF field.

    Rotates points around the chosen axis by (angle_per_unit * position_along_axis) degrees
    before sampling the source field.  CPU evaluate() and GPU to_glsl() are stubs.
    """

    def __init__(self, source: SdfField, axis: str = "Z", angle_per_unit: float = 1.0):
        super().__init__()
        self.source = source
        self.axis = axis          # "X", "Y", or "Z"
        self.angle_per_unit = angle_per_unit  # degrees per mm along axis

    # ------------------------------------------------------------------
    # SdfField interface
    # ------------------------------------------------------------------

    # Per axis: the two coordinate indices the twist rotates in, and the sign of the
    # solid's rotation relative to +angle_per_unit. evaluate() rotates the *query*
    # point, so the solid turns the other way -- and the Y case builds R(+theta)
    # where X and Z build R(-theta), which flips its sign back again.
    _PLANE = {"X": (1, 2, +1.0), "Y": (0, 2, -1.0), "Z": (0, 1, +1.0)}

    def bounding_box(self):
        """The source box grown to cover the twisted solid.

        The slice at axis coordinate t is the source's slice rotated by
        sign * rate * t, so the angular range is fixed by the source's own extent
        along the axis -- no twist, no growth.
        """
        import math
        bb_min, bb_max = self.source.bounding_box()
        axis_u, axis_v, sign = self._PLANE.get(self.axis, self._PLANE["Z"])
        axis_i = AXES.get(self.axis, 2)

        rate = sign * math.radians(self.angle_per_unit)
        lo_t = (bb_min.x, bb_min.y, bb_min.z)[axis_i]
        hi_t = (bb_max.x, bb_max.y, bb_max.z)[axis_i]
        u_min, u_max, v_min, v_max = swept_rotation_bounds(
            bb_min, bb_max, axis_u, axis_v, 0.0, 0.0, rate * lo_t, rate * hi_t)

        lo = [bb_min.x, bb_min.y, bb_min.z]
        hi = [bb_max.x, bb_max.y, bb_max.z]
        lo[axis_u], hi[axis_u] = u_min, u_max
        lo[axis_v], hi[axis_v] = v_min, v_max
        return FreeCAD.Vector(*lo), FreeCAD.Vector(*hi)

    def lipschitz(self) -> float:
        try:
            bb_min, bb_max = self.source.bounding_box()
        except NotImplementedError:
            return self.source.lipschitz()
        
        import math
        rate = abs(math.radians(self.angle_per_unit))
        corners = [
            [bb_min.x, bb_min.y, bb_min.z],
            [bb_min.x, bb_min.y, bb_max.z],
            [bb_min.x, bb_max.y, bb_min.z],
            [bb_min.x, bb_max.y, bb_max.z],
            [bb_max.x, bb_min.y, bb_min.z],
            [bb_max.x, bb_min.y, bb_max.z],
            [bb_max.x, bb_max.y, bb_min.z],
            [bb_max.x, bb_max.y, bb_max.z],
        ]
        
        r_max = 0.0
        for x, y, z in corners:
            if self.axis == "X":
                r = math.sqrt(y*y + z*z)
            elif self.axis == "Y":
                r = math.sqrt(x*x + z*z)
            else: # "Z"
                r = math.sqrt(x*x + y*y)
            if r > r_max:
                r_max = r
                
        return self.source.lipschitz() * (1.0 + rate * r_max)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        import math
        rate = math.radians(self.angle_per_unit)
        x, y, z = point.x, point.y, point.z
        
        if self.axis == "X":
            theta = x * rate
            c = math.cos(theta)
            s = math.sin(theta)
            p_rot = FreeCAD.Vector(x, c * y + s * z, -s * y + c * z)
        elif self.axis == "Y":
            theta = y * rate
            c = math.cos(theta)
            s = math.sin(theta)
            p_rot = FreeCAD.Vector(c * x - s * z, y, s * x + c * z)
        else: # "Z"
            theta = z * rate
            c = math.cos(theta)
            s = math.sin(theta)
            p_rot = FreeCAD.Vector(c * x + s * y, -s * x + c * y, z)
            
        return self.source.evaluate(p_rot)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        rate = np.radians(self.angle_per_unit)
        x = points[:, 0]
        y = points[:, 1]
        z = points[:, 2]
        
        pts_rot = np.empty_like(points)
        
        if self.axis == "X":
            theta = x * rate
            c = np.cos(theta)
            s = np.sin(theta)
            pts_rot[:, 0] = x
            pts_rot[:, 1] = c * y + s * z
            pts_rot[:, 2] = -s * y + c * z
        elif self.axis == "Y":
            theta = y * rate
            c = np.cos(theta)
            s = np.sin(theta)
            pts_rot[:, 0] = c * x - s * z
            pts_rot[:, 1] = y
            pts_rot[:, 2] = s * x + c * z
        else: # "Z"
            theta = z * rate
            c = np.cos(theta)
            s = np.sin(theta)
            pts_rot[:, 0] = c * x + s * y
            pts_rot[:, 1] = -s * x + c * y
            pts_rot[:, 2] = z
            
        return self.source.evaluate_grid(pts_rot).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        import math
        rate_val = math.radians(self.angle_per_unit)
        rate_u = ctx.uniform("float", rate_val)
        
        if self.axis == "X":
            ctx.add_custom_helper("twist_x", _GLSL_TWIST_X)
            deformed_var = f"twist_x({point_var}, {rate_u})"
        elif self.axis == "Y":
            ctx.add_custom_helper("twist_y", _GLSL_TWIST_Y)
            deformed_var = f"twist_y({point_var}, {rate_u})"
        else: # Z
            ctx.add_custom_helper("twist_z", _GLSL_TWIST_Z)
            deformed_var = f"twist_z({point_var}, {rate_u})"
            
        return self.source.to_glsl(ctx, deformed_var)

    def to_glsl_sample(self, ctx, point_var="p"):
        import math
        rate_val = math.radians(self.angle_per_unit)
        rate_u = ctx.uniform("float", rate_val)
        
        if self.axis == "X":
            ctx.add_custom_helper("twist_x", _GLSL_TWIST_X)
            deformed_var = f"twist_x({point_var}, {rate_u})"
        elif self.axis == "Y":
            ctx.add_custom_helper("twist_y", _GLSL_TWIST_Y)
            deformed_var = f"twist_y({point_var}, {rate_u})"
        else: # Z
            ctx.add_custom_helper("twist_z", _GLSL_TWIST_Z)
            deformed_var = f"twist_z({point_var}, {rate_u})"
            
        return self.source.to_glsl_sample(ctx, deformed_var)


_GLSL_TWIST_X = """
vec3 twist_x(vec3 p, float rate) {
    float theta = p.x * rate;
    float c = cos(theta);
    float s = sin(theta);
    return vec3(p.x, c * p.y + s * p.z, -s * p.y + c * p.z);
}
"""

_GLSL_TWIST_Y = """
vec3 twist_y(vec3 p, float rate) {
    float theta = p.y * rate;
    float c = cos(theta);
    float s = sin(theta);
    return vec3(c * p.x - s * p.z, p.y, s * p.x + c * p.z);
}
"""

_GLSL_TWIST_Z = """
vec3 twist_z(vec3 p, float rate) {
    float theta = p.z * rate;
    float c = cos(theta);
    float s = sin(theta);
    return vec3(c * p.x + s * p.y, -s * p.x + c * p.y, p.z);
}
"""

