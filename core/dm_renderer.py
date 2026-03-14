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
            self.vis_switch.whichChild = 0 if vobj.Visibility else -1
            vobj.RootNode.addChild(self.vis_switch)

        # FRep specific nodes
        self._frep_sep = None
        self._frep_draw_style = None
        self._frep_coords = None
        self._frep_faces = None
        
        self._frep_wide_switch = None
        self._frep_wire_sep = None
        self._frep_wire_style = None
        self._frep_wire_faces = None
        
        self._frep_handle_coords = None
        self._frep_handle_lines = None
        self._frep_corner_xfs = []      # 8 SoTransform nodes for corner sphere positions
        self._frep_corner_spheres = []  # 8 SoSphere nodes

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
            self.vis_switch.whichChild = 0 if is_visible else -1

    def on_prefs_changed(self, obj):
        if not coin: return
        
        show_wire = getattr(obj, "ShowWireframe", get_show_wireframe())
        lw = get_line_width()
        
        if self._frep_wide_switch:
            self._frep_wide_switch.whichChild = 0 if show_wire else -1
        if self._frep_wire_style:
            self._frep_wire_style.lineWidth = lw
            
        if self._style:
            self._style.lineWidth = lw
            
        try:
            if self.vobj:
                self.vobj.LineWidth = lw
                self.vobj.PointSize = get_point_size()
        except:
            pass

    # -------------------------------------------------------------------------
    # F-Rep Mesh Rendering
    # -------------------------------------------------------------------------

    def setup_frep_mesh_nodes(self):
        if not coin: return
        try:
            sep = coin.SoSeparator()

            # ── Mesh nodes ──
            mesh_sep = coin.SoSeparator()
            mat = coin.SoMaterial()
            mat.diffuseColor.setValue(1.0, 0.5, 0.0)
            mat.specularColor.setValue(0.3, 0.3, 0.3)
            mat.shininess.setValue(0.3)
            mesh_sep.addChild(mat)

            hints = coin.SoShapeHints()
            try:
                hints.vertexOrdering = coin.SoShapeHints.COUNTER_CLOCKWISE
            except AttributeError:
                hints.vertexOrdering = 2
            try:
                hints.shapeType = coin.SoShapeHints.SOLID
            except AttributeError:
                hints.shapeType = 1
            hints.creaseAngle = 0.5
            mesh_sep.addChild(hints)

            self._frep_draw_style = coin.SoDrawStyle()
            try:
                self._frep_draw_style.style = coin.SoDrawStyle.FILLED
            except AttributeError:
                self._frep_draw_style.style = 1 # FILLED
            mesh_sep.addChild(self._frep_draw_style)

            self._frep_coords = coin.SoCoordinate3()
            mesh_sep.addChild(self._frep_coords)

            self._frep_faces = coin.SoIndexedFaceSet()
            mesh_sep.addChild(self._frep_faces)
            sep.addChild(mesh_sep)

            # ── Wireframe overlay nodes ──
            self._frep_wide_switch = coin.SoSwitch()
            self._frep_wire_sep = coin.SoSeparator()
            self._frep_wide_switch.addChild(self._frep_wire_sep)

            wire_mat = coin.SoMaterial()
            wire_mat.diffuseColor.setValue(0.0, 0.0, 0.0)
            self._frep_wire_sep.addChild(wire_mat)

            self._frep_wire_style = coin.SoDrawStyle()
            self._frep_wire_style.style = coin.SoDrawStyle.LINES
            self._frep_wire_style.lineWidth = get_line_width()
            self._frep_wire_sep.addChild(self._frep_wire_style)

            self._frep_wire_sep.addChild(self._frep_coords)

            self._frep_wire_faces = coin.SoIndexedFaceSet()
            self._frep_wire_sep.addChild(self._frep_wire_faces)
            
            sep.addChild(self._frep_wide_switch)
            self._frep_wide_switch.whichChild = 0 if get_show_wireframe() else -1

            # ── Corner sphere handles (8 corners) ──
            sphere_root = coin.SoSeparator()
            s_mat = coin.SoMaterial()
            s_mat.diffuseColor.setValue(1.0, 0.5, 0.0)
            s_mat.specularColor.setValue(0.8, 0.8, 0.8)
            s_mat.shininess.setValue(0.7)
            sphere_root.addChild(s_mat)

            self._frep_corner_xfs = []
            self._frep_corner_spheres = []
            for _ in range(8):
                s_sep = coin.SoSeparator()
                xf = coin.SoTransform()
                sphere = coin.SoSphere()
                sphere.radius = 5.0
                s_sep.addChild(xf)
                s_sep.addChild(sphere)
                sphere_root.addChild(s_sep)
                self._frep_corner_xfs.append(xf)
                self._frep_corner_spheres.append(sphere)
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

            self._frep_handle_coords = coin.SoCoordinate3()
            corner_sep.addChild(self._frep_handle_coords)
            self._frep_handle_lines = coin.SoLineSet()
            corner_sep.addChild(self._frep_handle_lines)

            sep.addChild(corner_sep)

            self._frep_sep = sep
            if self.vis_switch:
                self.vis_switch.addChild(sep)
            else:
                self.vobj.RootNode.addChild(sep)
                
        except Exception as e:
            from core import dm_logger
            dm_logger.debug(f"DMRenderer.setup_frep_mesh_nodes failed: {e}")

    def update_frep_mesh(self, verts, flat_idx):
        from core import dm_logger
        if not coin or not self._frep_coords:
            return
        try:
            # Always clear first to prevent stale geometry accumulating on redraw
            self._frep_coords.point.setNum(0)
            self._frep_faces.coordIndex.setNum(0)
            if self._frep_wire_faces:
                self._frep_wire_faces.coordIndex.setNum(0)

            if verts is None or flat_idx is None or len(verts) == 0:
                return

            self._frep_coords.point.setValues(verts)
            self._frep_faces.coordIndex.setValues(flat_idx)
            if self._frep_wire_faces:
                self._frep_wire_faces.coordIndex.setValues(flat_idx)
        except Exception as e:
            dm_logger.info(f"DMRenderer.update_frep_mesh failed ({type(e).__name__}): {e}")

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

    def update_frep_corners(self, field):
        if not coin or not self._frep_handle_coords:
            return
        try:
            placement = getattr(field, "placement", None)
            center = getattr(field, "center", None)
            half_size = getattr(field, "half_size", None)

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
                    world_corners = [placement.multVec(lc) for lc in local_corners]
                else:
                    world_corners = local_corners
                corners = [(v.x, v.y, v.z) for v in world_corners]
            else:
                min_b, max_b = field.bounding_box()
                corners = [
                    (min_b.x, min_b.y, min_b.z), (max_b.x, min_b.y, min_b.z),
                    (max_b.x, max_b.y, min_b.z), (min_b.x, max_b.y, min_b.z),
                    (min_b.x, min_b.y, max_b.z), (max_b.x, min_b.y, max_b.z),
                    (max_b.x, max_b.y, max_b.z), (min_b.x, max_b.y, max_b.z),
                ]

            lines = [
                (0,1), (1,2), (2,3), (3,0),
                (4,5), (5,6), (6,7), (7,4),
                (0,4), (1,5), (2,6), (3,7)
            ]
            all_pts = []
            for i, j in lines:
                all_pts.extend([corners[i], corners[j]])

            self._frep_handle_coords.point.setValues(all_pts)
            self._frep_handle_lines.numVertices.setValues([2] * len(lines))

            # Update corner sphere positions and radius
            if self._frep_corner_xfs:
                r = self._corner_sphere_radius()
                for i, (x, y, z) in enumerate(corners):
                    self._frep_corner_xfs[i].translation.setValue(x, y, z)
                    self._frep_corner_spheres[i].radius = r
        except Exception as e:
            from core import dm_logger
            dm_logger.debug(f"DMRenderer.update_frep_corners failed: {e}")

    def set_frep_display_mode(self, mode):
        if not self._frep_draw_style: return
        if mode == "Wireframe":
            try:
                self._frep_draw_style.style = coin.SoDrawStyle.LINES
            except AttributeError:
                self._frep_draw_style.style = 2
        else:
            try:
                self._frep_draw_style.style = coin.SoDrawStyle.FILLED
            except AttributeError:
                self._frep_draw_style.style = 1

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

        self.vobj.addDisplayMode(self._ctrl_cage_sep, "ControlCage")

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
            # Control point sphere — always visible
            dm_pt = DMPoint(p)
            dm_pt.draw_point(self._spheres_sep, radius=r_knot, color=(1.0, 0.5, 0.0))
            self._dm_point_spheres.append(dm_pt)

            if edit_mode:
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
        self._ctrl_lines.startIndex.setValue(0)
