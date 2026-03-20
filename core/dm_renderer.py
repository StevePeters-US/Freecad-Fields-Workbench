import FreeCAD
try:
    from pivy import coin
except ImportError:
    coin = None

from core.dm_object import get_show_wireframe, get_line_width, get_point_size

class DMRenderer:
    """
    Handles all Coin3D custom visual rendering for Direct Modeling objects.
    This replaces FreeCAD's slow BRep rendering with direct mesh/overlay rendering.
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

        self._sdf_debug_switch = None
        self._sdf_wide_switch = None
        self._sdf_wire_sep = None
        self._sdf_wire_style = None

        self._sdf_handle_coords = None
        self._sdf_handle_lines = None
        self._sdf_corner_xfs = []      # 8 SoTransform nodes for corner sphere positions
        self._sdf_corner_spheres = []  # 8 SoSphere nodes

        # Curve/Control cage specific nodes
        self._ctrl_cage_sep = None
        self._style = None
        self._ctrl_coords = None
        self._ctrl_lines = None
        self._spheres_sep = None
        self._dm_point_spheres = []  # DMPoint instances for control points and handles
        
        # Standalone Point specific
        self._point_marker = None     # DMPoint instance for ShapeType == "point"

    def update_visibility(self, is_visible):
        if self.vis_switch:
            self.vis_switch.whichChild = -3 if is_visible else -1

    def on_prefs_changed(self, obj):
        if not coin: return
        
        show_wire = getattr(obj, "ShowWireframe", get_show_wireframe())
        lw = get_line_width()
        
        if self._sdf_wide_switch:
            self._sdf_wide_switch.whichChild = 0 if show_wire else -1
        if self._sdf_wire_style:
            self._sdf_wire_style.lineWidth = lw
            
        if self._style:
            self._style.lineWidth = lw
            
        if self._sdf_debug_switch:
            from core.dm_object import get_render_debug_mode
            self._sdf_debug_switch.whichChild = 0 if get_render_debug_mode() else -1
            
        try:
            if self.vobj:
                self.vobj.LineWidth = lw
                self.vobj.PointSize = get_point_size()
        except:
            pass

    # -------------------------------------------------------------------------
    # SDF Mesh Rendering
    # -------------------------------------------------------------------------

    def setup_sdf_mesh_nodes(self):
        """Setup nodes for SDF bounding box and corner spheres (no mesh)."""
        if not coin: return
        try:
            sep = coin.SoSeparator()

            # ── Corner sphere handles (8 corners) ──
            sphere_root = coin.SoSeparator()
            s_mat = coin.SoMaterial()
            s_mat.diffuseColor.setValue(1.0, 0.5, 0.0)
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
            from core.dm_object import get_render_debug_mode
            self._sdf_debug_switch = coin.SoSwitch()
            self._sdf_debug_switch.whichChild = 0 if get_render_debug_mode() else -1
            self._sdf_debug_switch.addChild(sep)

            if self.vis_switch:
                self.vis_switch.addChild(self._sdf_debug_switch)
            else:
                self.vobj.RootNode.addChild(self._sdf_debug_switch)
                
        except Exception as e:
            from core import dm_logger
            dm_logger.debug(f"DMRenderer.setup_sdf_mesh_nodes failed: {e}")

    def _corner_sphere_radius(self):
        """Compute sphere radius to appear ~8px on screen, matching DMBase._compute_handle_radius."""
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
            except Exception:
                pass
            half_world_h = cam.height.getValue() / 2.0 if hasattr(cam, "height") else 100.0
            px_per_world = (vp_h / 2.0) / max(half_world_h, 1e-6)
            return max(2.0, 8.0 / px_per_world)
        except Exception:
            return 5.0

    def update_sdf_corners(self, field):
        if not coin or not self._sdf_handle_coords:
            return
        try:
            placement = getattr(field, "placement", None)
            center = getattr(field, "center", None)
            half_size = getattr(field, "half_size", None)

            obj = self.vp.Object
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

            # Update corner sphere positions and radius
            if self._sdf_corner_xfs:
                r = self._corner_sphere_radius()
                for i, (x, y, z) in enumerate(corners):
                    self._sdf_corner_xfs[i].translation.setValue(x, y, z)
                    self._sdf_corner_spheres[i].radius = r
        except Exception as e:
            from core import dm_logger
            dm_logger.debug(f"DMRenderer.update_sdf_corners failed: {e}")


    # -------------------------------------------------------------------------
    # Point Rendering
    # -------------------------------------------------------------------------

    def setup_point_marker_nodes(self):
        if not coin: return
        from core.dm_point import DMPoint
        
        # We use a separator for the point marker
        self._point_sep = coin.SoSeparator()
        if self.vis_switch:
            self.vis_switch.addChild(self._point_sep)
        else:
            self.vobj.RootNode.addChild(self._point_sep)
            
        # Initial DMPoint. Position will be set in update_point_marker
        self._point_marker = DMPoint(FreeCAD.Vector(0,0,0))
        self._point_marker.draw_point(self._point_sep, radius=self._corner_sphere_radius())

    def update_point_marker(self, fp):
        if not self._point_marker:
            self.setup_point_marker_nodes()
        if not self._point_marker:
            return
            
        pos = getattr(fp, "Position", FreeCAD.Vector(0,0,0))
        self._point_marker.position = pos
        # Use the same 8px logic as corners/handles for consistency
        self._point_marker.update_draw(radius=self._corner_sphere_radius())

    # -------------------------------------------------------------------------
    # Curve / Control Cage Rendering
    # -------------------------------------------------------------------------

    def setup_coin_overlay(self):
        if not coin: return

        self._ctrl_cage_sep = coin.SoSeparator()
        if self.vis_switch:
            # For curves, this is the first (and only) child of vis_switch,
            # so whichChild=0 keeps it visible.
            self.vis_switch.addChild(self._ctrl_cage_sep)
        else:
            self.vobj.RootNode.addChild(self._ctrl_cage_sep)

        # Dashed lines for handle arms
        self._style = coin.SoDrawStyle()
        self._style.linePattern = 0x0F0F
        self._style.lineWidth = 1
        self._ctrl_cage_sep.addChild(self._style)

        self._ctrl_coords = coin.SoCoordinate3()
        self._ctrl_cage_sep.addChild(self._ctrl_coords)

        self._ctrl_lines = coin.SoLineSet()
        self._ctrl_cage_sep.addChild(self._ctrl_lines)

        # SoSphere nodes for control points and handles (via DMPoint)
        self._spheres_sep = coin.SoSeparator()
        self._ctrl_cage_sep.addChild(self._spheres_sep)

        # Note: do NOT call addDisplayMode here — it would add _ctrl_cage_sep
        # to FreeCAD's internal display-mode switch, which may hide it unless
        # the user explicitly selects "ControlCage" as the display mode.
        # The control cage is always managed by vis_switch + edit_mode gating.

    def rebuild_control_cage(self, fp):
        if not coin: return

        if not self._ctrl_coords:
            if hasattr(fp, "ShapeType") and fp.ShapeType == "curve":
                self.setup_coin_overlay()
        if not self._ctrl_coords:
            return

        # Clear previous DMPoint spheres
        from core.dm_point import DMPoint
        for dm_pt in self._dm_point_spheres:
            dm_pt.undraw()
        self._dm_point_spheres.clear()

        if not hasattr(fp, "Points") or not fp.Points:
            self._ctrl_coords.point.setNum(0)
            self._ctrl_lines.numVertices.setNum(0)
            return

        pts = list(fp.Points)
        h_in = list(fp.HandleIn) if hasattr(fp, "HandleIn") else []
        h_out = list(fp.HandleOut) if hasattr(fp, "HandleOut") else []
        edit_mode = getattr(fp, "EditMode", False)



        # Sphere radius: scale with bounding box span so spheres look consistent
        xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1.0)
        r_knot = max(3.0, span * 0.02)
        r_handle = max(2.0, span * 0.015)

        line_coords = []
        num_vertices = []

        for i, p in enumerate(pts):
            if not edit_mode:
                continue

            # Control point sphere
            dm_pt = DMPoint(p)
            dm_pt.draw_point(self._spheres_sep, radius=r_knot, color=(1.0, 0.5, 0.0))
            self._dm_point_spheres.append(dm_pt)

            # Handle arm lines + handle spheres
            if i < len(h_in) and h_in[i] is not None and (h_in[i] - p).Length > 1e-4:
                line_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                line_coords.append(coin.SbVec3f(h_in[i].x, h_in[i].y, h_in[i].z))
                num_vertices.append(2)
                dm_h = DMPoint(h_in[i])
                dm_h.draw_point(self._spheres_sep, radius=r_handle, color=(0.2, 0.7, 1.0))
                self._dm_point_spheres.append(dm_h)

            if i < len(h_out) and h_out[i] is not None and (h_out[i] - p).Length > 1e-4:
                line_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                line_coords.append(coin.SbVec3f(h_out[i].x, h_out[i].y, h_out[i].z))
                num_vertices.append(2)
                dm_h = DMPoint(h_out[i])
                dm_h.draw_point(self._spheres_sep, radius=r_handle, color=(0.2, 0.7, 1.0))
                self._dm_point_spheres.append(dm_h)

        self._ctrl_coords.point.setNum(len(line_coords))
        if line_coords:
            self._ctrl_coords.point.setValues(0, line_coords)
        self._ctrl_lines.numVertices.setNum(len(num_vertices))
        if num_vertices:
            self._ctrl_lines.numVertices.setValues(0, num_vertices)
    def set_sdf_display_mode(self, mode):
        """Toggle between shaded and wireframe rendering."""
        # TODO: Implement actual display mode switching for SDF objects.
        pass

class DMRendererStrategy:
    def setup(self, renderer, vobj):
        pass
    def update(self, renderer, fp, prop):
        pass
    def set_display_mode(self, renderer, mode):
        pass

class NURBSRendererStrategy(DMRendererStrategy):
    def setup(self, renderer, vobj):
        st = getattr(vobj.Object, "ShapeType", None)
        if st == "curve":
            renderer.setup_coin_overlay()
            renderer.rebuild_control_cage(vobj.Object)
        elif st == "point":
            renderer.setup_point_marker_nodes()
            renderer.update_point_marker(vobj.Object)

    def update(self, renderer, fp, prop):
        st = getattr(fp, "ShapeType", None)
        if st == "curve":
            if not prop or prop in ["Points", "HandleIn", "HandleOut", "Closed", "EditMode"]:
                renderer.rebuild_control_cage(fp)
        elif st == "point":
            if prop == "Position" or not prop:
                renderer.update_point_marker(fp)
        elif not prop:
             renderer.rebuild_control_cage(fp)


class SdfRendererStrategy(DMRendererStrategy):
    def __init__(self):
        self.label = None

    def setup(self, renderer, vobj):
        obj = vobj.Object
        self.label = f"{obj.Document.Name}.{obj.Name}"

    def update(self, renderer, fp, prop):
        if prop == "Shape" or not prop:
            proxy = getattr(fp, "Proxy", None)
            # Support both names during transition (SdfField for old documents)
            field = getattr(proxy, "SdfField", None) or getattr(proxy, "SdfField", None)
            if self.label and field is not None:
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                sr = DMSceneRayMarchRenderer.get_instance()
                sr.update_field(self.label, field)
                import FreeCADGui
                if FreeCADGui.activeView():
                    FreeCADGui.activeView().redraw()

    def set_display_mode(self, renderer, mode):
        renderer.set_sdf_display_mode(mode)


