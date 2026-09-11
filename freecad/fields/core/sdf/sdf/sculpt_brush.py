# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf/sculpt_brush.py

A sculpt brush: a voxel grid normalized to a unit box, plus its parameters.
Any SdfField can become one (`SculptBrush.from_field`).
"""
import numpy as np
import FreeCAD
from freecad.fields.core.sdf.sdf.voxel_field import SdfVoxelField


class SculptBrush:
    """Normalized brush stamp. `grid` spans [-0.5, 0.5]^3 in brush space."""

    def __init__(self, name, grid, strength=1.0, falloff=1.0, spacing=0.35):
        self.name = str(name)
        self.grid = grid                # SdfVoxelField, size == (1,1,1)
        self.strength = float(strength) # 0..1, scales the stamped depth
        self.falloff = float(falloff)   # exponent applied to the normalized profile
        self.spacing = float(spacing)   # dab spacing as a fraction of radius

    @classmethod
    def from_field(cls, field, name, resolution=32):
        """Discretize any SdfField into a unit-box brush.

        The field's own bounding box is mapped onto the unit box, and distances are
        divided by the box's longest side so the stamp scales linearly with Radius.
        """
        bb_min, bb_max = field.bounding_box()
        centre = (bb_min + bb_max) * 0.5
        extent = bb_max - bb_min
        longest = max(extent.x, extent.y, extent.z, 1e-6)
        pl = FreeCAD.Placement(centre, FreeCAD.Rotation())
        raw = SdfVoxelField.from_field(
            field, size=(longest, longest, longest),
            resolution=(resolution,) * 3, placement=pl)
        unit = SdfVoxelField(size=(1.0, 1.0, 1.0), resolution=raw.resolution,
                             data=(raw.data / longest).astype(np.float32))
        return cls(name, unit)

    def stamp_field(self, centre, radius, rotation=None):
        """This brush placed in the world: an SdfVoxelField ready to compose."""
        out = SdfVoxelField(
            size=(radius * 2.0,) * 3,
            resolution=self.grid.resolution,
            data=(self.grid.data * (radius * 2.0)).astype(np.float32),
            interpolation=self.grid.interpolation,
            placement=FreeCAD.Placement(centre, rotation or FreeCAD.Rotation()))
        return out
