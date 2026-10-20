# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/delegates/point_renderer_delegate.py

Rendering delegate for standalone Fields Point objects (ShapeType == "point").
"""
import FreeCAD
from freecad.fields.core.render.delegates import add_to_scene
try:
    from pivy import coin
except ImportError:
    coin = None


class PointRendererDelegate:
    def __init__(self):
        self._point_sep = None
        self._point_marker = None     # FldPoint instance for ShapeType == "point"

    def setup_point_marker_nodes(self, renderer):
        if not coin: return
        from freecad.fields.core.objects.fld_point import FldPoint

        # We use a separator for the point marker
        self._point_sep = coin.SoSeparator()
        add_to_scene(renderer, self._point_sep)

        # Initial FldPoint. Position will be set in update_point_marker
        self._point_marker = FldPoint(FreeCAD.Vector(0,0,0))
        self._point_marker.draw_point(self._point_sep, radius=renderer._corner_sphere_radius())

    def update_point_marker(self, renderer, fp):
        if not self._point_marker:
            self.setup_point_marker_nodes(renderer)
        if not self._point_marker:
            return

        # The marker lives under the object's own root, after FreeCAD's SoFCTransform,
        # which already applies Placement (kept equal to Coordinates by the proxy).
        # So it sits at the local origin. Placing it at Coordinates drew it at twice
        # the point's position: a second, "mirrored" marker away from the real point.
        self._point_marker.position = FreeCAD.Vector(0, 0, 0)
        # Use the same 8px logic as corners/handles for consistency
        self._point_marker.update_draw(radius=renderer._corner_sphere_radius())
