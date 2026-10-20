# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import Part
import math
from pivy import coin
from freecad.fields.core import fld_logger

MAJOR_EVERY = 5          # every fifth line is drawn dark
MAX_LINES_PER_AXIS = 400  # past this, the light lines coarsen by MAJOR_EVERY


def grid_lines(length, width, step):
    """Line endpoints for a centred grid: a light line every `step`, dark every fifth.

    The lines through the origin are dark, so the dark lines fall on multiples of
    `MAJOR_EVERY * step` -- the values Ctrl-snapping lands on every fifth step. When a
    small step would draw more than MAX_LINES_PER_AXIS lines across the plane, the
    step is multiplied by MAJOR_EVERY until it fits.

    Returns:
        (light, dark): two lists of ((x, y, 0), (x, y, 0)) segments.
    """
    if step <= 0:
        step = 1.0
    while max(length, width) / step > MAX_LINES_PER_AXIS:
        step *= MAJOR_EVERY
    half_l, half_w = length / 2.0, width / 2.0
    light, dark = [], []
    for half_span, half_other, horizontal in ((half_w, half_l, True), (half_l, half_w, False)):
        n = int(half_span / step + 1e-9)
        for i in range(-n, n + 1):
            c = i * step
            seg = (((-half_other, c, 0), (half_other, c, 0)) if horizontal
                   else ((c, -half_other, 0), (c, half_other, 0)))
            (dark if i % MAJOR_EVERY == 0 else light).append(seg)
    return light, dark


class _SnapStepObserver:
    """Redraws a workplane grid when the snap grid step parameter changes."""

    def __init__(self, vp):
        import weakref
        self._vp = weakref.ref(vp)

    def onChange(self, grp, name):
        if name != "SnapGridStep":
            return
        vp = self._vp()
        if vp is not None:
            try:
                vp.refresh_grid()
            except Exception as e:
                fld_logger.debug(f"workplane grid refresh failed: {e}")


class FldWorkPlane:
    def __init__(self, obj):
        obj.Proxy = self
        self.is_fld_workplane = True
        
        # Add a Placement property specifically for the work plane if we need explicit tracking,
        # otherwise we can just use the standard Placement of the FeaturePython object.
        # We'll rely on the standard obj.Placement for the actual 3D orientation.
        
        # A property to store the size of the grid if we wanted to make it static,
        # but the request is for it to scale to the viewport size.
        if not hasattr(obj, "Shape"):
            obj.addProperty("Part::PropertyPartShape", "Shape", "Fields", "Shape")
        
        if not hasattr(obj, "Length"):
            obj.addProperty("App::PropertyLength", "Length", "Fields", "Length of the grid")
            obj.Length = 100.0
        if not hasattr(obj, "Width"):
            obj.addProperty("App::PropertyLength", "Width", "Fields", "Width of the grid")
            obj.Width = 100.0
            
    def execute(self, obj):
        try:
            l = obj.Length.Value if hasattr(obj.Length, "Value") else float(obj.Length)
            w = obj.Width.Value if hasattr(obj.Width, "Value") else float(obj.Width)
            hl, hw = l / 2.0, w / 2.0
            v1 = FreeCAD.Vector(-hl, -hw, 0)
            v2 = FreeCAD.Vector( hl, -hw, 0)
            v3 = FreeCAD.Vector( hl,  hw, 0)
            v4 = FreeCAD.Vector(-hl,  hw, 0)
            obj.Shape = Part.makePolygon([v1, v2, v3, v4, v1])
        except Exception as e:
            fld_logger.error(f"FldWorkPlane.execute failed for {obj.Label}: {e}")

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        self.is_fld_workplane = True

class FldWorkPlaneViewProvider:
    """ViewProvider for the workplane.

    `vobj.Proxy = self` below means FreeCAD persists this class's module path
    + name into the saved document, so renaming the class breaks documents
    saved under the old name. That is accepted: this codebase deletes replaced
    code and writes no migrations.
    """

    def __init__(self, vobj):
        vobj.Proxy = self
        
    def attach(self, vobj):
        self.Object = vobj.Object
        
        self.root_node = coin.SoSeparator()
        self.root_node.setName("Fld_WorkPlane_Feature_Root")
        
        # Make visuals pickable so the user can select the workplane in the 3D view
        pick_style = coin.SoPickStyle()
        pick_style.style.setValue(coin.SoPickStyle.SHAPE)
        self.root_node.addChild(pick_style)
        
        # Grid visual
        # Add a bright center marker to visualize the exactly (0,0,0) point of the grid
        self.center_sep = coin.SoSeparator()
        self.center_mat = coin.SoMaterial()
        self.center_mat.diffuseColor.setValue(1.0, 0.0, 0.0) # Red
        
        self.center_coords = coin.SoCoordinate3()
        self.center_coords.point.setValues(0, 4, [(-10.0, 0, 0), (10.0, 0, 0), (0, -10.0, 0), (0, 10.0, 0)])
        self.center_lines = coin.SoLineSet()
        self.center_lines.numVertices.setValues(0, 2, [2, 2]) # Two lines, each with 2 vertices
        self.center_sep.addChild(self.center_mat)
        self.center_sep.addChild(self.center_coords)
        self.center_sep.addChild(self.center_lines)
        self.root_node.addChild(self.center_sep)

        self.grid_sep = coin.SoSeparator()
        
        # Ensure transparent workplane geometry depth-tests against opaque SDF renders.
        # test=True: discard fragments behind opaque surfaces (e.g. SDF quad).
        # write=False: don't write depth — transparent objects must not occlude each other.
        try:
            wp_depth = coin.SoDepthBuffer()
            wp_depth.test.setValue(True)
            wp_depth.write.setValue(False)
            self.grid_sep.addChild(wp_depth)
        except AttributeError as e:
            fld_logger.debug(
                f"FldWorkPlaneViewProvider.attach: SoDepthBuffer unavailable in "
                f"this Coin3D/pivy build ({e}); workplane will write depth."
            )
        
        # Light lines at every snap step, dark lines every fifth.
        self.light_coords, self.light_lines = self._add_line_set(0.2, 0.6, 0.9, 0.85)
        self.dark_coords, self.dark_lines = self._add_line_set(0.1, 0.35, 0.6, 0.3)

        # Semi-transparent rectangle
        self.plane_mat = coin.SoMaterial()
        self.plane_mat.diffuseColor.setValue(0.2, 0.6, 0.9)
        self.plane_mat.transparency.setValue(0.7)
        self.grid_sep.addChild(self.plane_mat)
        self.face_coords = coin.SoCoordinate3()
        self.face_set = coin.SoFaceSet()
        self.grid_sep.addChild(self.face_coords)
        self.grid_sep.addChild(self.face_set)
        
        self.refresh_grid()
        from freecad.fields.core.fld_prefs import PREFS_PATH
        # FreeCAD drops an observer when the group handle it was attached to is
        # garbage-collected, so the handle must live as long as this view provider.
        self._snap_observer = _SnapStepObserver(self)
        self._prefs_grp = FreeCAD.ParamGet(PREFS_PATH)
        self._prefs_grp.Attach(self._snap_observer)
        
        self.transform = coin.SoTransform()
        self.root_node.insertChild(self.transform, 0)
        self.root_node.addChild(self.grid_sep)
        
        vobj.addDisplayMode(self.root_node, "Standard")
        
        # No dynamic scale event callback needed anymore.

    def _add_line_set(self, r, g, b, transparency):
        sep = coin.SoSeparator()
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(r, g, b)
        mat.transparency.setValue(transparency)
        coords, lines = coin.SoCoordinate3(), coin.SoLineSet()
        for node in (mat, coords, lines):
            sep.addChild(node)
        self.grid_sep.addChild(sep)
        return coords, lines

    @staticmethod
    def _fill(coords, lines, segments):
        pts = [p for seg in segments for p in seg]
        coords.point.setNum(len(pts))
        if pts:
            coords.point.setValues(0, len(pts), pts)
        lines.numVertices.setNum(len(segments))
        if segments:
            lines.numVertices.setValues(0, len(segments), [2] * len(segments))

    def refresh_grid(self):
        """Redraw from the object's size and the current snap grid step."""
        from freecad.fields.core.fld_settings import get_snap_grid_step
        length = float(getattr(self.Object.Length, "Value", self.Object.Length))
        width = float(getattr(self.Object.Width, "Value", self.Object.Width))
        light, dark = grid_lines(length, width, get_snap_grid_step())
        self._fill(self.light_coords, self.light_lines, light)
        self._fill(self.dark_coords, self.dark_lines, dark)
        half_l, half_w = length / 2.0, width / 2.0
        self.face_coords.point.setValues(0, 4, [(-half_l, -half_w, 0), (half_l, -half_w, 0),
                                                (half_l, half_w, 0), (-half_l, half_w, 0)])
        self.face_set.numVertices.setValue(4)

    def updateData(self, fp, prop):
        if prop == "Placement":
            # Nothing to do for translation/rotation as Coin3D parent handles it
            pass
        elif prop in ["Length", "Width"]:
            self.refresh_grid()

    def getDisplayModes(self, obj):
        return ["Standard"]

    def getDefaultDisplayMode(self):
        return "Standard"

    def setDisplayMode(self, mode):
        return mode

    def __del__(self):
        pass

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        pass

    def doubleClicked(self, vobj):
        import FreeCADGui
        from freecad.fields.core import fld_logger
        
        obj = vobj.Object
        
        # Ensure the object is selected
        sel = FreeCADGui.Selection.getSelection()
        if not sel or sel[0] != obj:
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(obj)
            
        fld_logger.info(f"FldWorkPlaneViewProvider: double-clicked {obj.Label}")
        from freecad.fields.tools.work_plane_tool import WorkPlaneCreator
        tool = WorkPlaneCreator()
        if not getattr(tool, "_is_editing", False):
            tool.edit_object(obj)
        
        return True

    def setEdit(self, vobj, mode=0):
        self.doubleClicked(vobj)
        return True

    def unsetEdit(self, vobj, mode=0):
        return True


def create_fld_workplane(name="Fields_WorkPlane", placement=None):
    doc = FreeCAD.activeDocument()
    if not doc:
        return None
        
    obj = doc.addObject("Part::FeaturePython", name)
    FldWorkPlane(obj)
    FldWorkPlaneViewProvider(obj.ViewObject)
    
    if placement:
        obj.Placement = placement
        
    return obj
