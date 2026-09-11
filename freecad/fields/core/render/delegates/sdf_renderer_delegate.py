# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/delegates/sdf_renderer_delegate.py

Rendering delegate for SDF primitive bounding-box and corner-handle
overlays. Owns the debug-visibility switch and the 8 corner spheres used
as box-field drag handles. Receives the owning FldRenderer as `renderer`
to reach shared state (vis_switch, vobj, _corner_sphere_radius()).
"""
import FreeCAD
try:
    from pivy import coin
except ImportError:
    coin = None

from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR
from freecad.fields.core.render.delegates import add_to_scene


class SdfRendererDelegate:
    def __init__(self):
        self._sdf_debug_switch = None
        self._sdf_wide_switch = None
        self._sdf_wire_sep = None
        self._sdf_wire_style = None

        self._sdf_handle_coords = None
        self._sdf_handle_lines = None
        self._sdf_corner_xfs = []      # 8 SoTransform nodes for corner sphere positions
        self._sdf_corner_spheres = []  # 8 SoSphere nodes

    def on_prefs_changed(self, renderer, show_wire, lw):
        """Apply line-width/debug-visibility prefs to the SDF bbox/corner overlay.

        CR-054: `show_wire` is SDF-specific (this delegate's own bbox/corner
        wireframe toggle) -- `CurveRendererDelegate`/`CageSurfaceRendererDelegate`
        take just `(renderer, lw)`, not because they were left out of an update,
        but because neither has an equivalent overlay to gate. `fld_renderer.py`
        calls each delegate by name with its own matching arity, never
        polymorphically, so the mismatch is intentional -- documented rather
        than forced into one shared signature.
        """
        if self._sdf_wide_switch:
            self._sdf_wide_switch.whichChild = 0 if show_wire else -1
        if self._sdf_wire_style:
            self._sdf_wire_style.lineWidth = lw
        if self._sdf_debug_switch:
            from freecad.fields.core.fld_settings import get_render_debug_mode
            self._sdf_debug_switch.whichChild = 0 if get_render_debug_mode() else -1

    def setup_sdf_mesh_nodes(self, renderer):
        """Setup nodes for SDF bounding box and corner spheres (no mesh)."""
        if not coin: return
        try:
            sep = coin.SoSeparator()

            # ── Corner sphere handles (8 corners) ──
            sphere_root = coin.SoSeparator()
            s_mat = coin.SoMaterial()
            s_mat.diffuseColor.setValue(*DEFAULT_ADDITIVE_COLOR)
            s_mat.specularColor.setValue(0.8, 0.8, 0.8)
            s_mat.shininess.setValue(0.7)
            sphere_root.addChild(s_mat)

            self._sdf_corner_xfs = []
            self._sdf_corner_spheres = []
            for _ in range(8):
                s_sep = coin.SoSeparator()
                xf = coin.SoTransform()
                sphere = coin.SoSphere()
                sphere.radius = 5.0
                s_sep.addChild(xf)
                s_sep.addChild(sphere)
                sphere_root.addChild(s_sep)
                self._sdf_corner_xfs.append(xf)
                self._sdf_corner_spheres.append(sphere)
            sep.addChild(sphere_root)

            # ── Bounding-box edge lines ──
            corner_sep = coin.SoSeparator()

            h_mat = coin.SoMaterial()
            h_mat.diffuseColor.setValue(1.0, 0.6, 0.2)
            corner_sep.addChild(h_mat)

            h_style = coin.SoDrawStyle()
            h_style.lineWidth = 1
            h_style.linePattern = 0x0F0F
            corner_sep.addChild(h_style)

            self._sdf_handle_coords = coin.SoCoordinate3()
            corner_sep.addChild(self._sdf_handle_coords)
            self._sdf_handle_lines = coin.SoLineSet()
            corner_sep.addChild(self._sdf_handle_lines)

            sep.addChild(corner_sep)

            # Wrapper switch for debug visuals (bbox + corners)
            from freecad.fields.core.fld_settings import get_render_debug_mode
            self._sdf_debug_switch = coin.SoSwitch()
            self._sdf_debug_switch.whichChild = 0 if get_render_debug_mode() else -1
            self._sdf_debug_switch.addChild(sep)

            add_to_scene(renderer, self._sdf_debug_switch)

        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.render_debug(f"SdfRendererDelegate.setup_sdf_mesh_nodes failed: {e}")

    def update_sdf_corners(self, renderer, field):
        if not coin or not self._sdf_handle_coords:
            return
        try:
            placement = getattr(field, "placement", None)
            center = getattr(field, "center", None)
            half_size = getattr(field, "half_size", None)

            obj = renderer.vobj.Object
            inv = obj.Placement.inverse() if (obj and hasattr(obj, "Placement")) else None

            if center is not None and half_size is not None:
                c = center
                h = half_size
                local_corners = [
                    FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z - h.z),
                    FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z - h.z),
                    FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z - h.z),
                    FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z - h.z),
                    FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z + h.z),
                    FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z + h.z),
                    FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z + h.z),
                    FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z + h.z),
                ]
                if placement:
                    # placement is the field's internal placement (usually same as obj.Placement)
                    # We want to show these in the object's local space.
                    # If placement == obj.Placement, then lc is exactly what we want.
                    # But to be safe, we compute world then go back to local.
                    world_corners = [placement.multVec(lc) for lc in local_corners]
                else:
                    world_corners = local_corners

                if inv:
                    corners = [(v.x, v.y, v.z) for v in [inv.multVec(wc) for wc in world_corners]]
                else:
                    corners = [(v.x, v.y, v.z) for v in world_corners]
            elif hasattr(field, "bounding_box"):
                mn, mx = field.bounding_box()
                world_corners = [
                    FreeCAD.Vector(mn.x, mn.y, mn.z), FreeCAD.Vector(mx.x, mn.y, mn.z),
                    FreeCAD.Vector(mx.x, mx.y, mn.z), FreeCAD.Vector(mn.x, mx.y, mn.z),
                    FreeCAD.Vector(mn.x, mn.y, mx.z), FreeCAD.Vector(mx.x, mn.y, mx.z),
                    FreeCAD.Vector(mx.x, mx.y, mx.z), FreeCAD.Vector(mn.x, mx.y, mx.z),
                ]
                if inv:
                    corners = [(v.x, v.y, v.z) for v in [inv.multVec(wc) for wc in world_corners]]
                else:
                    corners = [(v.x, v.y, v.z) for v in world_corners]
            else:
                return

            lines = [
                (0,1), (1,2), (2,3), (3,0),
                (4,5), (5,6), (6,7), (7,4),
                (0,4), (1,5), (2,6), (3,7)
            ]
            all_pts = []
            for i, j in lines:
                all_pts.extend([corners[i], corners[j]])

            self._sdf_handle_coords.point.setValues(all_pts)
            self._sdf_handle_lines.numVertices.setValues([2] * len(lines))

            # Corner spheres are control handles only for box-type fields (center + half_size).
            # For other SDF types (sphere, cylinder, torus, etc.) hide them to avoid
            # confusing non-interactive visual duplicates.
            if self._sdf_corner_xfs:
                if center is not None and half_size is not None:
                    r = renderer._corner_sphere_radius()
                    for i, (x, y, z) in enumerate(corners):
                        self._sdf_corner_xfs[i].translation.setValue(x, y, z)
                        self._sdf_corner_spheres[i].radius = r
                else:
                    for sphere in self._sdf_corner_spheres:
                        sphere.radius = 0.001
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.render_debug(f"SdfRendererDelegate.update_sdf_corners failed: {e}")

    def set_sdf_display_mode(self, renderer, mode):
        """Toggle between shaded and wireframe rendering."""
        # TODO: Implement actual display mode switching for SDF objects.
        pass
