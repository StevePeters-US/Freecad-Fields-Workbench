import FreeCAD
import numpy as np
import math
from core.sdf.sdf_field import SdfField

class SdfNoiseField(SdfField):
    """
    Applies procedural sine-wave noise (displacement) to a base SDF field.
    """
    def __init__(self, base_field: SdfField, amplitude: float = 1.0, frequency: float = 1.0):
        self.base_field = base_field
        self.amplitude = amplitude
        self.frequency = frequency

    def evaluate(self, point: FreeCAD.Vector) -> float:
        d = self.base_field.evaluate(point)
        # Sine-based procedural noise
        noise = math.sin(point.x * self.frequency) * \
                math.sin(point.y * self.frequency) * \
                math.sin(point.z * self.frequency) * self.amplitude
        return d + noise

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        d = self.base_field.evaluate_grid(points)
        noise = np.sin(points[:, 0] * self.frequency) * \
                np.sin(points[:, 1] * self.frequency) * \
                np.sin(points[:, 2] * self.frequency) * self.amplitude
        return (d + noise).astype(np.float32)

    def bounding_box(self):
        bb_min, bb_max = self.base_field.bounding_box()
        # Expand bounding box by the noise amplitude
        offset = FreeCAD.Vector(self.amplitude, self.amplitude, self.amplitude)
        return (bb_min - offset, bb_max + offset)

    def to_glsl(self, ctx, point_var="p"):
        base_glsl = self.base_field.to_glsl(ctx, point_var)
        amp_u = ctx.uniform("float", self.amplitude)
        freq_u = ctx.uniform("float", self.frequency)

        ctx.add_custom_helper("sine_noise",
            "float sine_noise(vec3 p, float freq, float amp) {\n"
            "    return sin(p.x * freq) * sin(p.y * freq) * sin(p.z * freq) * amp;\n"
            "}"
        )
        return f"({base_glsl} + sine_noise({point_var}, {freq_u}, {amp_u}))"
