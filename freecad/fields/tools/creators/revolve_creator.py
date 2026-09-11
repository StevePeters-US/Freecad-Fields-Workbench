# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/creators/revolve_creator.py

Interactive SDF revolve primitive creator tool.
"""
import FreeCAD
from freecad.fields.core.sdf.sdf2d.circle import Sdf2dCircle
from freecad.fields.core.sdf.sdf_revolution import SdfRevolutionField
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.tools.primitive_creator_base import (
    PrimitiveCreatorBase, MIN_PRIMITIVE_DIM_MM, MIN_PRIMITIVE_DIM_TYPED_MM
)
import math


class RevolveCreator(PrimitiveCreatorBase):
    """Revolves a circle profile around the local Z axis.

    3-click creation: anchor (center) → ring offset point (XY) → tube radius point.
    offset=0 collapses to a sphere; offset>0 gives a toroidal ring.
    """

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.CUSTOM_1]

    def get_command_id(self):
        return "Fields_CreateRevolve"

    def get_sdf_type(self):
        return "revolve"

    def __init__(self):
        super().__init__()
        self.points = []

    def edit_object(self, obj):
        super().edit_object(obj)
        if not self.points:
            field = getattr(obj.Proxy, "SdfField", None)
            if field and hasattr(field, "offset") and hasattr(field, "profile"):
                offset = field.offset
                tube_r = getattr(field.profile, "radius", 5.0)
                loc_c = FreeCAD.Vector(0.0, 0.0, 0.0)
                self.points = [
                    self.to_global(loc_c),
                    self.to_global(loc_c + FreeCAD.Vector(max(offset, tube_r), 0, 0)),
                    self.to_global(loc_c + FreeCAD.Vector(offset + tube_r, 0, 0)),
                ]
        self.state = ToolState.IDLE
        self._draw_point_handles()

    def _sync_edit_points(self):
        for i in range(len(self.fld_points)):
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
            self.points[2] = pos
        elif state == ToolState.CUSTOM_1:
            self.points[2] = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY and len(self.points) >= 2:
            self.points[1] = pos
            self.points[2] = pos
        elif state == ToolState.CUSTOM_1 and len(self.points) >= 3:
            self.points[2] = pos

    # ── Field builders ────────────────────────────────────────────────────────

    def _ring_and_tube(self):
        """Return (ring_offset, tube_radius) in local units, or None."""
        if len(self.points) < 2:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        ring_d = math.sqrt((loc_r.x - loc_c.x) ** 2 + (loc_r.y - loc_c.y) ** 2)

        if len(self.points) >= 3 and self.points[1] != self.points[2]:
            loc_t = self.to_local(self.points[2])
            dt = math.sqrt((loc_t.x - loc_c.x) ** 2 + (loc_t.y - loc_c.y) ** 2)
            tube_r = max(abs(dt - ring_d), MIN_PRIMITIVE_DIM_MM)
        else:
            tube_r = self.preview_minor_radius(ring_d)

        return ring_d, tube_r

    def _revolve_field(self):
        result = self._ring_and_tube()
        if result is None:
            return None
        ring_d, tube_r = result
        if ring_d < 0.1 and tube_r < 0.1:
            return None
        profile = Sdf2dCircle(tube_r)
        # SdfRevolutionField spins about local Z through the local origin, so the
        # frame is what carries the position: points[0] is the revolve's origin.
        return SdfRevolutionField(profile, offset=ring_d, placement=self._origin_placement())

    def _get_preview_field(self):
        return self._revolve_field()

    def _get_edit_preview_field(self):
        return self._revolve_field()

    def _get_final_field(self):
        return self._revolve_field()

    def get_parameters(self):
        result = self._ring_and_tube()
        if result is None:
            return {}
        ring_d, tube_r = result
        return {"Ring Offset": ring_d, "Tube Radius": tube_r}

    def _apply_parameters(self, params):
        if len(self.points) < 3:
            return False
        offset = max(float(params.get("Ring Offset", 10.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        tube_r = max(float(params.get("Tube Radius", 2.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        loc_c = self.to_local(self.points[0])
        self.points[1] = self.to_global(loc_c + FreeCAD.Vector(offset, 0, 0))
        self.points[2] = self.to_global(loc_c + FreeCAD.Vector(offset + tube_r, 0, 0))
        return True


