# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/creators/prism_creator.py

Interactive SDF prism primitive creator tool.
"""
import FreeCAD
from freecad.fields.core.sdf.sdf2d.polygon import Sdf2dPolygon
from freecad.fields.core.sdf.sdf_extrusion import SdfExtrusionField
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.tools.primitive_creator_base import (
    PrimitiveCreatorBase, MIN_PRIMITIVE_DIM_TYPED_MM
)
import math


class PrismCreator(PrimitiveCreatorBase):
    """N-sided regular prism via polygon extrusion. Default 6 sides (hexagonal prism).

    3-click creation: anchor (center) → XY circumradius → Z half-height.
    The solid extends ±half-height from the anchor along local Z.
    """

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.DRAG_Z]
    _DEFAULT_SIDES = 6

    def get_command_id(self):
        return "Fields_CreatePrism"

    def get_sdf_type(self):
        return "prism"

    def __init__(self):
        super().__init__()
        self.points = []
        self.n_sides = self._DEFAULT_SIDES
        self._height_drag_base = None

    def edit_object(self, obj):
        super().edit_object(obj)
        # points[3] encodes n_sides in its .x component (stored in local space)
        if len(self.points) >= 4:
            ns = round(self.to_local(self.points[3]).x)
            if 3 <= ns <= 64:
                self.n_sides = ns
        self.state = ToolState.IDLE
        self._draw_point_handles(self.points[:3])

    def _sync_edit_points(self):
        for i in range(min(len(self.fld_points), 3)):
            if i < len(self.points):
                self.points[i] = self.fld_points[i].position

    # ── Creation stage hooks ──────────────────────────────────────────────────

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self._anchor_pt = pos
            self.points = [pos, pos, pos]
            self._field_placement = self._get_placement()
        elif state == ToolState.DRAG_XY:
            self.points[1] = pos
            self._height_drag_base = self.points[0]
            if len(self.points) < 3:
                self.points.append(self.points[0])
        elif state == ToolState.DRAG_Z:
            self.points[2] = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY and len(self.points) >= 2:
            self.points[1] = pos
        elif state == ToolState.DRAG_Z and len(self.points) >= 3:
            self.points[2] = pos

    # ── Field builders ────────────────────────────────────────────────────────

    def _prism_field(self):
        if len(self.points) < 2:
            return None
        loc_center = self.to_local(self.points[0])
        loc_rad = self.to_local(self.points[1])
        dx = loc_rad.x - loc_center.x
        dy = loc_rad.y - loc_center.y
        radius = math.sqrt(dx * dx + dy * dy)
        if radius < 0.1:
            return None

        if len(self.points) >= 3:
            loc_h = self.to_local(self.points[2])
            half_h = abs(loc_h.z - loc_center.z)
        else:
            half_h = radius * 0.5

        if half_h < MIN_PRIMITIVE_DIM_TYPED_MM:
            half_h = MIN_PRIMITIVE_DIM_TYPED_MM

        n = self.n_sides
        # Polygon built about (0, 0) and the frame moved to points[0]:
        # SdfExtrusionField spans +/-height/2 about local z=0, so a profile that
        # carried the XY offset alone would leave the prism's Z untethered from
        # its own origin.
        vertices = [
            (radius * math.cos(2.0 * math.pi * i / n),
             radius * math.sin(2.0 * math.pi * i / n))
            for i in range(n)
        ]
        profile = Sdf2dPolygon(vertices)
        return SdfExtrusionField(profile, height=2.0 * half_h, placement=self._origin_placement())

    def _get_preview_field(self):
        return self._prism_field()

    def _get_edit_preview_field(self):
        return self._prism_field()

    def _get_final_field(self):
        return self._prism_field()

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        pts = [self.to_local(p) for p in self.points[:3]]
        pts.append(FreeCAD.Vector(float(self.n_sides), 0.0, 0.0))
        return pts

    def get_parameters(self):
        if len(self.points) < 3:
            return {}
        loc_center = self.to_local(self.points[0])
        loc_rad = self.to_local(self.points[1])
        loc_h = self.to_local(self.points[2])
        radius = math.sqrt((loc_rad.x - loc_center.x) ** 2 + (loc_rad.y - loc_center.y) ** 2)
        half_h = abs(loc_h.z - loc_center.z)
        return {"Radius": radius, "Height": 2.0 * half_h}

    def _apply_parameters(self, params):
        if len(self.points) < 3:
            return False
        r = max(float(params.get("Radius", 10.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        h = max(float(params.get("Height", 20.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        loc_center = self.to_local(self.points[0])
        self.points[1] = self.to_global(loc_center + FreeCAD.Vector(r, 0, 0))
        self.points[2] = self.to_global(loc_center + FreeCAD.Vector(0, 0, h * 0.5))
        return True


