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
        
        self._frep_corner_coords = None
        self._frep_corner_pts = None
        self._frep_handle_coords = None
        self._frep_handle_lines = None

        # Curve/Control cage specific nodes
        self._ctrl_cage_sep = None
        self._style = None
        self._ctrl_coords = None
        self._ctrl_lines = None
        self._ctrl_points = None
        self._ctrl_handle_points = None
        self._knot_points = None

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

            # ── Corner point + handle nodes ──
            corner_sep = coin.SoSeparator()

            c_mat = coin.SoMaterial()
            c_mat.diffuseColor.setValue(1.0, 1.0, 1.0)
            corner_sep.addChild(c_mat)

            c_style = coin.SoDrawStyle()
            c_style.pointSize.setValue(8)
            corner_sep.addChild(c_style)

            self._frep_corner_coords = coin.SoCoordinate3()
            corner_sep.addChild(self._frep_corner_coords)
            self._frep_corner_pts = coin.SoPointSet()
            corner_sep.addChild(self._frep_corner_pts)

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
            if verts is None or flat_idx is None or len(verts) == 0:
                self._frep_coords.point.setNum(0)
                self._frep_faces.coordIndex.setNum(0)
                if self._frep_wire_faces:
                    self._frep_wire_faces.coordIndex.setNum(0)
                return
            self._frep_coords.point.setValues(verts)
            self._frep_faces.coordIndex.setValues(flat_idx)
            if self._frep_wire_faces:
                self._frep_wire_faces.coordIndex.setValues(flat_idx)
        except Exception as e:
            dm_logger.info(f"DMRenderer.update_frep_mesh failed ({type(e).__name__}): {e}")

    def update_frep_corners(self, field):
        if not coin or not self._frep_corner_coords:
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

            corner_pts = list(corners)
            all_pts = []
            
            lines = [
                (0,1), (1,2), (2,3), (3,0),
                (4,5), (5,6), (6,7), (7,4),
                (0,4), (1,5), (2,6), (3,7)
            ]
            for i, j in lines:
                all_pts.extend([corners[i], corners[j]])

            self._frep_corner_coords.point.setValues(corner_pts)
            self._frep_corner_pts.numPoints.setValue(len(corner_pts))
            self._frep_handle_coords.point.setValues(all_pts)
            self._frep_handle_lines.numVertices.setValues([2] * len(lines))
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
    # Curve / Control Cage Rendering
    # -------------------------------------------------------------------------

    def setup_coin_overlay(self):
        if not coin: return
        
        self._ctrl_cage_sep = coin.SoSeparator()
        if self.vis_switch:
            self.vis_switch.addChild(self._ctrl_cage_sep)
        else:
            self.vobj.RootNode.addChild(self._ctrl_cage_sep)

        self._style = coin.SoDrawStyle()
        self._style.linePattern = 0x0F0F
        self._style.lineWidth = 1
        self._ctrl_cage_sep.addChild(self._style)
        
        self._ctrl_coords = coin.SoCoordinate3()
        self._ctrl_cage_sep.addChild(self._ctrl_coords)
        
        self._ctrl_lines = coin.SoLineSet()
        self._ctrl_cage_sep.addChild(self._ctrl_lines)
        
        pts_sep = coin.SoSeparator()
        self._ctrl_cage_sep.addChild(pts_sep)

        pts_mat = coin.SoMaterial()
        pts_mat.diffuseColor = coin.SbColor(1.0, 0.5, 0.0)
        pts_sep.addChild(pts_mat)
        
        pt_style = coin.SoDrawStyle()
        pt_style.pointSize.setValue(8) 
        pts_sep.addChild(pt_style)
        
        self._ctrl_points = coin.SoPointSet()
        pts_sep.addChild(self._ctrl_points)

        h_pts_sep = coin.SoSeparator()
        self._ctrl_cage_sep.addChild(h_pts_sep)
        
        h_pts_mat = coin.SoMaterial()
        h_pts_mat.diffuseColor = coin.SbColor(0.2, 0.7, 1.0)
        h_pts_sep.addChild(h_pts_mat)
        
        h_pt_style = coin.SoDrawStyle()
        h_pt_style.pointSize.setValue(5)
        h_pts_sep.addChild(h_pt_style)
        
        self._ctrl_handle_points = coin.SoPointSet()
        h_pts_sep.addChild(self._ctrl_handle_points)
        
        k_pts_sep = coin.SoSeparator()
        self._ctrl_cage_sep.addChild(k_pts_sep)
        
        k_pts_mat = coin.SoMaterial()
        k_pts_mat.diffuseColor = coin.SbColor(1.0, 1.0, 1.0)
        k_pts_sep.addChild(k_pts_mat)
        
        k_pt_style = coin.SoDrawStyle()
        k_pt_style.pointSize.setValue(3)
        k_pts_sep.addChild(k_pt_style)
        
        self._knot_points = coin.SoPointSet()
        k_pts_sep.addChild(self._knot_points)
        
        self.vobj.addDisplayMode(self._ctrl_cage_sep, "ControlCage")
        self.vobj.RootNode.addChild(self._ctrl_cage_sep)

    def rebuild_control_cage(self, fp):
        if not coin: return
        
        if not self._ctrl_coords:
            if hasattr(fp, "ShapeType") and fp.ShapeType == "curve":
                self.setup_coin_overlay()
        
        if not self._ctrl_coords:
            return

        if not hasattr(fp, "Points") or not fp.Points:
            self._ctrl_coords.point.setNum(0)
            return

        pts = list(fp.Points)
        h_in = list(fp.HandleIn) if hasattr(fp, "HandleIn") else []
        h_out = list(fp.HandleOut) if hasattr(fp, "HandleOut") else []
        edit_mode = getattr(fp, "EditMode", False)
        
        line_coords = []
        marker_coords = []
        handle_marker_coords = []
        num_vertices = []
        knot_coords = []
        
        for i, p in enumerate(pts):
            knot_coords.append(coin.SbVec3f(p.x, p.y, p.z))
            
            if edit_mode:
                marker_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                
                if i < len(h_in) and h_in[i] is not None and (h_in[i] - p).Length > 1e-4:
                    line_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                    line_coords.append(coin.SbVec3f(h_in[i].x, h_in[i].y, h_in[i].z))
                    handle_marker_coords.append(coin.SbVec3f(h_in[i].x, h_in[i].y, h_in[i].z))
                    num_vertices.append(2)
                
                if i < len(h_out) and h_out[i] is not None and (h_out[i] - p).Length > 1e-4:
                    line_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                    line_coords.append(coin.SbVec3f(h_out[i].x, h_out[i].y, h_out[i].z))
                    handle_marker_coords.append(coin.SbVec3f(h_out[i].x, h_out[i].y, h_out[i].z))
                    num_vertices.append(2)

        final_coords = knot_coords + marker_coords + handle_marker_coords + line_coords
        self._ctrl_coords.point.setNum(len(final_coords))
        self._ctrl_coords.point.setValues(0, final_coords)
        
        self._knot_points.numPoints.setValue(len(knot_coords))
        self._knot_points.startIndex.setValue(0)
        
        self._ctrl_points.numPoints.setValue(len(marker_coords))
        self._ctrl_points.startIndex.setValue(len(knot_coords))
        
        self._ctrl_handle_points.numPoints.setValue(len(handle_marker_coords))
        self._ctrl_handle_points.startIndex.setValue(len(knot_coords) + len(marker_coords))
        
        self._ctrl_lines.numVertices.setNum(len(num_vertices))
        self._ctrl_lines.numVertices.setValues(0, num_vertices)
        self._ctrl_lines.startIndex.setValue(len(knot_coords) + len(marker_coords) + len(handle_marker_coords))
