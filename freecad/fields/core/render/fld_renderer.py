# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import math
try:
    from pivy import coin
except ImportError:
    coin = None

from freecad.fields.core.objects.fld_object import get_show_wireframe, get_line_width, get_point_size, get_show_cage_curves
from freecad.fields.core.render.delegates.sdf_renderer_delegate import SdfRendererDelegate
from freecad.fields.core.render.delegates.cage_surface_renderer_delegate import CageSurfaceRendererDelegate
from freecad.fields.core.render.delegates.point_renderer_delegate import PointRendererDelegate
from freecad.fields.core.render.delegates.curve_renderer_delegate import CurveRendererDelegate
from freecad.fields.core.render.delegates.surface_renderer_delegate import SurfaceRendererDelegate
from freecad.fields.core import fld_logger

class FldRenderer:
    """
    Handles all Coin3D custom visual rendering for Fields objects.
    This replaces FreeCAD's slow BRep rendering with direct mesh/overlay rendering.

    Owns the shared vis_switch/camera-sensor plumbing and delegates each
    visualization pipeline (SDF bbox/corners, cage curves, point markers,
    curve control cages, surface extension overlays) to a dedicated
    Strategy Delegate (see core/render/delegates/).
    """
    def __init__(self, vobj):
        self.vobj = vobj

        # Base visibility switch for all custom nodes
        self.vis_switch = None
        if coin:
            self.vis_switch = coin.SoSwitch()
            # SO_SWITCH_ALL (-3) shows all children; -1 hides all.
            self.vis_switch.whichChild = -3 if vobj.Visibility else -1
            vobj.RootNode.addChild(self.vis_switch)

        self._cam_sensor = None

        # One delegate per visualization pipeline.
        self.sdf_delegate = SdfRendererDelegate()
        self.cage_delegate = CageSurfaceRendererDelegate()
        self.point_delegate = PointRendererDelegate()
        self.curve_delegate = CurveRendererDelegate()
        self.surface_delegate = SurfaceRendererDelegate()

    def update_visibility(self, is_visible):
        if self.vis_switch:
            self.vis_switch.whichChild = -3 if is_visible else -1

    def on_prefs_changed(self, obj):
        if not coin: return

        show_wire = getattr(obj, "ShowWireframe", get_show_wireframe())
        lw = get_line_width()

        self.sdf_delegate.on_prefs_changed(self, show_wire, lw)
        self.curve_delegate.on_prefs_changed(self, lw)
        self.cage_delegate.on_prefs_changed(self, lw)

        try:
            if self.vobj:
                self.vobj.LineWidth = lw
                self.vobj.PointSize = get_point_size()
        except Exception as e:
            fld_logger.render_debug(f"on_prefs_changed view object updates failed: {e}")

        if getattr(obj, "SdfType", None) == "cage":
            self.rebuild_cage_curves(obj)

    # -------------------------------------------------------------------------
    # SDF Mesh Rendering — delegated to SdfRendererDelegate
    # -------------------------------------------------------------------------

    def setup_sdf_mesh_nodes(self):
        return self.sdf_delegate.setup_sdf_mesh_nodes(self)

    def update_sdf_corners(self, field):
        return self.sdf_delegate.update_sdf_corners(self, field)

    def set_sdf_display_mode(self, mode):
        """Toggle between shaded and wireframe rendering."""
        return self.sdf_delegate.set_sdf_display_mode(self, mode)

    # -------------------------------------------------------------------------
    # Cage Surface Rendering — delegated to CageSurfaceRendererDelegate
    # -------------------------------------------------------------------------

    def setup_cage_curves(self):
        return self.cage_delegate.setup_cage_curves(self)

    def rebuild_cage_curves(self, fp):
        return self.cage_delegate.rebuild_cage_curves(self, fp)

    # -------------------------------------------------------------------------
    # Point Rendering — delegated to PointRendererDelegate
    # -------------------------------------------------------------------------

    def setup_point_marker_nodes(self):
        return self.point_delegate.setup_point_marker_nodes(self)

    def update_point_marker(self, fp):
        return self.point_delegate.update_point_marker(self, fp)

    # -------------------------------------------------------------------------
    # Curve / Control Cage Rendering — delegated to CurveRendererDelegate
    # -------------------------------------------------------------------------

    def setup_coin_overlay(self):
        return self.curve_delegate.setup_coin_overlay(self)

    def rebuild_control_cage(self, fp):
        return self.curve_delegate.rebuild_control_cage(self, fp)

    # -------------------------------------------------------------------------
    # Surface Extension Overlay — delegated to SurfaceRendererDelegate
    # -------------------------------------------------------------------------

    def setup_extension_overlay(self):
        return self.surface_delegate.setup_extension_overlay(self)

    def update_extension_overlay(self, fp):
        return self.surface_delegate.update_extension_overlay(self, fp)

    # -------------------------------------------------------------------------
    # Shared: screen-space handle sizing + camera-change sensor
    # -------------------------------------------------------------------------

    def _corner_sphere_radius(self):
        """Compute sphere radius to appear ~8px on screen, matching FldBase._compute_handle_radius."""
        try:
            import FreeCADGui
            view = FreeCADGui.activeView()
            if not view:
                active_doc = FreeCADGui.ActiveDocument
                view = getattr(active_doc, "ActiveView", None) if active_doc else None

            if not view:
                return 5.0

            cam = view.getCameraNode()
            viewer = view.getViewer()
            vp_h = 800.0
            try:
                if hasattr(viewer, "getGlxSize"):
                    vp_h = float(viewer.getGlxSize()[1])
                elif hasattr(viewer, "getSize"):
                    sz = viewer.getSize()
                    vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
            except Exception as e:
                fld_logger.render_debug(f"Failed to get viewport height: {e}")
            if hasattr(cam, "height"):
                half_world_h = cam.height.getValue() / 2.0
            elif hasattr(cam, "heightAngle"):
                cam_vals = cam.position.getValue()
                cam_pos_v = FreeCAD.Vector(cam_vals[0], cam_vals[1], cam_vals[2])
                ref = FreeCAD.Vector(0, 0, 0)
                if self.vobj and hasattr(self.vobj, "Object") and self.vobj.Object:
                    ref = self.vobj.Object.Placement.Base
                depth = (ref - cam_pos_v).Length
                half_world_h = depth * math.tan(cam.heightAngle.getValue() / 2.0)
            else:
                half_world_h = 100.0

            px_per_world = (vp_h / 2.0) / max(half_world_h, 1e-6)
            pt_sz = get_point_size()
            return max(0.01, pt_sz / px_per_world)
        except Exception as e:
            fld_logger.render_debug(f"_corner_sphere_radius fallback used: {e}")
            return 3.0

    def _setup_camera_sensor(self):
        if not coin: return
        try:
            import FreeCADGui
            view = FreeCADGui.activeView()
            if not view:
                active_doc = FreeCADGui.ActiveDocument
                view = getattr(active_doc, "ActiveView", None) if active_doc else None
            if view:
                cam = view.getCameraNode()
                if cam:
                    self._cam_sensor = coin.SoNodeSensor(self._on_camera_changed, None)
                    self._cam_sensor.attach(cam)
        except Exception as e:
            fld_logger.render_debug(f"FldRenderer._setup_camera_sensor failed: {e}")

    def _cleanup_camera_sensor(self):
        if hasattr(self, "_cam_sensor") and self._cam_sensor is not None:
            try:
                self._cam_sensor.detach()
            except Exception as e:
                fld_logger.render_debug(f"Sensor detach failed: {e}")
            self._cam_sensor = None

    def _on_camera_changed(self, data, sensor):
        try:
            # Recalculate and update the radii of all active spheres
            sdf_spheres = self.sdf_delegate._sdf_corner_spheres
            if sdf_spheres and sdf_spheres[0].radius.getValue() > 0.01:
                r = self._corner_sphere_radius()
                for sphere in sdf_spheres:
                    sphere.radius = r

            # Point marker
            if self.point_delegate._point_marker:
                self.point_delegate._point_marker.update_draw(radius=self._corner_sphere_radius())

            # Control cage points
            if self.curve_delegate._dm_point_spheres:
                r_knot = self._corner_sphere_radius()
                r_handle = r_knot * 0.75
                for fld_pt in self.curve_delegate._dm_point_spheres:
                    r = r_handle if getattr(fld_pt, "is_handle", False) else r_knot
                    fld_pt.update_draw(radius=r)
        except Exception as e:
            fld_logger.render_debug(f"_on_camera_changed update failed: {e}")


class FldRendererStrategy:
    def setup(self, renderer, vobj):
        pass
    def update(self, renderer, fp, prop):
        pass
    def set_display_mode(self, renderer, mode):
        pass

class NURBSRendererStrategy(FldRendererStrategy):
    def __init__(self):
        self.label = None

    def setup(self, renderer, vobj):
        obj = vobj.Object
        self.label = f"{obj.Document.Name}.{obj.Name}"

        st = getattr(obj, "ShapeType", None)
        if st == "curve":
            renderer.setup_coin_overlay()
            renderer.rebuild_control_cage(obj)
        elif st == "point":
            renderer.setup_point_marker_nodes()
            renderer.update_point_marker(obj)
        elif st == "surface":
            renderer.setup_extension_overlay()
            renderer.update_extension_overlay(obj)

    def update(self, renderer, fp, prop):
        st = getattr(fp, "ShapeType", None)
        if st == "curve":
            if not prop or prop in ["Points", "HandleIn", "HandleOut", "Closed", "EditMode", "HandleTypes"]:
                renderer.rebuild_control_cage(fp)
        elif st == "point":
            if prop in ("Position", "Coordinates") or not prop:
                renderer.update_point_marker(fp)
        elif st == "surface":
            if not prop or prop in ["Shape", "ControlGrid", "SourceCurve", "Iterations", "CenterOffset", "Tension", "ShowExtension"]:
                renderer.update_extension_overlay(fp)
        elif not prop:
             renderer.rebuild_control_cage(fp)


class SdfRendererStrategy(FldRendererStrategy):
    def __init__(self):
        self.label = None

    def setup(self, renderer, vobj):
        obj = vobj.Object
        self.label = f"{obj.Document.Name}.{obj.Name}"
        if getattr(obj, "SdfType", None) == "cage":
            renderer.setup_cage_curves()
            renderer.rebuild_cage_curves(obj)
        self.update(renderer, obj, None)

    def update(self, renderer, fp, prop):
        if prop == "Shape" or not prop or prop in ["Points", "ControlGrid", "Position"]:
            if getattr(fp, "SdfType", None) == "cage":
                renderer.rebuild_cage_curves(fp)
            proxy = getattr(fp, "Proxy", None)
            if proxy and hasattr(proxy, "get_sdf_field"):
                field = proxy.get_sdf_field(fp)
                if self.label and field is not None:
                    from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                    sr = FldSceneVoxelRenderer.get_instance()
                    sr.update_field(self.label, field)
                    import FreeCADGui
                    if FreeCADGui.activeView():
                        FreeCADGui.activeView().redraw()

    def set_display_mode(self, renderer, mode):
        renderer.set_sdf_display_mode(mode)
