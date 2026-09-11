# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/objects/fld_sculpt_layer.py

FldSculptLayerProxy -- a modifier holding a mutable voxel grid of sculpted delta,
composed onto its Source by Group. The grid persists to a sidecar (sculpt_store).
"""
import collections
import FreeCAD
import numpy as np
from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_view_provider import FldViewProvider
from freecad.fields.core.objects.fld_modifier_proxy_base import FldModifierProxyBase
from freecad.fields.core.objects.sculpt_store import (
    install_observer, load_grid, mark_dirty)
from freecad.fields.core.sdf.sdf.voxel_field import SdfVoxelField, VOXEL_FAR
from freecad.fields.core.sdf.sdf_composer import (
    UnionField, SmoothUnionField, SubtractionField, SmoothSubtractionField)


class FldSculptLayerProxy(FldModifierProxyBase):
    """A sculpt layer: Source composed with a voxel grid of sculpted material."""

    SHAPE_TYPE_TOOLTIP = "Type of modifier"
    SOURCE_GROUP = "Sculpt"
    # False: onChanged clears SdfField for every property read below, so an
    # unrelated recompute must NOT re-read the grid -- that is what discards edits.
    ALWAYS_REBUILD = False

    _WATCHED = ("Group", "SculptMode", "SmoothK", "SizeX", "SizeY", "SizeZ",
                "ResolutionX", "ResolutionY", "ResolutionZ")

    def __init__(self, obj):
        super().__init__(obj)
        install_observer()
        if not hasattr(obj, "SculptMode"):
            obj.addProperty("App::PropertyEnumeration", "SculptMode", "Sculpt",
                            "Sculpt layer mode: Additive (deposit) or Subtractive (carve)")
            obj.SculptMode = ["Additive", "Subtractive"]
            obj.SculptMode = "Additive"
        if not hasattr(obj, "SmoothK"):
            obj.addProperty("App::PropertyFloat", "SmoothK", "Sculpt",
                            "Blend radius where the layer meets the source (mm, 0 = sharp)").SmoothK = 0.0
        if not hasattr(obj, "StrokeCount"):
            obj.addProperty("App::PropertyInteger", "StrokeCount", "Sculpt",
                            "Number of sculpt strokes committed").StrokeCount = 0
        for axis, default in (("X", 100.0), ("Y", 100.0), ("Z", 100.0)):
            if not hasattr(obj, "Size" + axis):
                obj.addProperty("App::PropertyFloat", "Size" + axis, "Sculpt",
                                f"Grid extent along {axis} (mm)")
                setattr(obj, "Size" + axis, default)
            if not hasattr(obj, "Resolution" + axis):
                obj.addProperty("App::PropertyInteger", "Resolution" + axis, "Sculpt",
                                f"Grid resolution along {axis}")
                setattr(obj, "Resolution" + axis, 64)
        self._grid = None
        self._undo_journal = collections.deque(maxlen=32)
        self._stroke_dabs = []
        self._last_stroke_count = getattr(obj, "StrokeCount", 0)

    def onChanged(self, fp, prop):
        if prop == "StrokeCount":
            count = getattr(fp, "StrokeCount", 0) or 0
            last = getattr(self, "_last_stroke_count", None)
            if last is not None and count < last:
                journal = getattr(self, "_undo_journal", None)
                if journal and len(journal) > 0:
                    last_stroke = journal.pop()
                    grid = self._grid_field(fp)
                    if isinstance(last_stroke, tuple):
                        i0, i1, before = last_stroke
                        grid.data[i0[0]:i1[0], i0[1]:i1[1], i0[2]:i1[2]] = before
                    else:
                        for i0, i1, before in reversed(last_stroke):
                            grid.data[i0[0]:i1[0], i0[1]:i1[1], i0[2]:i1[2]] = before
                    self.touch_grid(fp)
                    self.SdfField = None
                else:
                    fld_logger.warn("sculpt_layer: stroke undone past journal capacity")
            self._last_stroke_count = count
        elif prop in self._WATCHED:
            self.SdfField = None

    def onDocumentRestored(self, obj):
        # __init__ does NOT run on restore, so without this a document opened in a
        # fresh session has no save observer and silently never flushes its grids.
        super().onDocumentRestored(obj)
        install_observer()
        if not hasattr(obj, "SculptMode"):
            obj.addProperty("App::PropertyEnumeration", "SculptMode", "Sculpt",
                            "Sculpt layer mode: Additive (deposit) or Subtractive (carve)")
            obj.SculptMode = ["Additive", "Subtractive"]
            obj.SculptMode = getattr(obj, "Group", "Additive")
        if not hasattr(obj, "StrokeCount"):
            obj.addProperty("App::PropertyInteger", "StrokeCount", "Sculpt",
                            "Number of sculpt strokes committed").StrokeCount = 0
        self._undo_journal = collections.deque(maxlen=32)
        self._stroke_dabs = []
        self._last_stroke_count = getattr(obj, "StrokeCount", 0)

    # -- grid & strokes --------------------------------------------------------

    def _grid_field(self, fp):
        """The layer's own voxel grid, loaded lazily from the sidecar on first use
        and cached on `self._grid`."""
        if getattr(self, "_grid", None) is not None:
            return self._grid
        size = (fp.SizeX, fp.SizeY, fp.SizeZ)
        res = (fp.ResolutionX, fp.ResolutionY, fp.ResolutionZ)
        loaded = load_grid(getattr(fp, "Document", None), fp.Name)
        if loaded is not None:
            data, size, res = loaded
            self._grid = SdfVoxelField(size=size, resolution=res, data=data,
                                       placement=fp.Placement)
        else:
            seed = np.full(res, VOXEL_FAR, dtype=np.float32)
            self._grid = SdfVoxelField(size=size, resolution=res, data=seed, placement=fp.Placement)
        return self._grid

    def touch_grid(self, fp):
        """Call after mutating the grid: bumps the version and queues a sidecar flush."""
        if getattr(self, "_grid", None) is not None:
            self._grid.geometry_version += 1
            mark_dirty(self, fp)

    def begin_stroke(self, fp):
        """Start recording dabs for a single undoable stroke."""
        grid = self._grid_field(fp)
        self._pre_stroke_data = grid.data.copy()
        self._stroke_dabs = []

    def record_dab(self, fp, field):
        """Snapshot the sub-box before a dab writes to it."""
        grid = self._grid_field(fp)
        box = grid._sub_index_box(*field.bounding_box())
        if box is not None:
            i0, i1 = box
            before = grid.data[i0[0]:i1[0], i0[1]:i1[1], i0[2]:i1[2]].copy()
            if not hasattr(self, "_stroke_dabs") or self._stroke_dabs is None:
                self._stroke_dabs = []
            self._stroke_dabs.append((i0, i1, before))

    def end_stroke(self, fp):
        """Commit the stroke: store undo entry, redistance modified sub-box, touch grid, and bump StrokeCount."""
        if getattr(self, "_stroke_dabs", None):
            i0_min = np.min([dab[0] for dab in self._stroke_dabs], axis=0)
            i1_max = np.max([dab[1] for dab in self._stroke_dabs], axis=0)
            grid = self._grid_field(fp)
            pad = 4
            i0_min_pad = np.maximum(i0_min - pad, 0)
            i1_max_pad = np.minimum(i1_max + pad, np.array(grid.resolution))

            pre_data = getattr(self, "_pre_stroke_data", None)
            if pre_data is not None:
                before_box = pre_data[i0_min_pad[0]:i1_max_pad[0], i0_min_pad[1]:i1_max_pad[1], i0_min_pad[2]:i1_max_pad[2]].copy()
            else:
                before_box = None

            if hasattr(grid, "redistance"):
                grid.redistance((i0_min, i1_max))

            if not hasattr(self, "_undo_journal") or self._undo_journal is None:
                self._undo_journal = collections.deque(maxlen=32)
            if before_box is not None:
                self._undo_journal.append((i0_min_pad, i1_max_pad, before_box))
            else:
                self._undo_journal.append(list(self._stroke_dabs))
            self._stroke_dabs = []
            self._pre_stroke_data = None
            self.touch_grid(fp)
            fp.StrokeCount = getattr(fp, "StrokeCount", 0) + 1

    def ensure_covers(self, fp, world_min, world_max):
        """Grow the grid if world_min..world_max falls outside its current extent."""
        grid = self._grid_field(fp)
        lo_local = grid._to_local_point(world_min)
        hi_local = grid._to_local_point(world_max)
        lo_req = np.minimum([lo_local.x, lo_local.y, lo_local.z], [hi_local.x, hi_local.y, hi_local.z])
        hi_req = np.maximum([lo_local.x, lo_local.y, lo_local.z], [hi_local.x, hi_local.y, hi_local.z])

        h = grid.size * 0.5
        if np.all(lo_req >= -h) and np.all(hi_req <= h):
            return

        spacing = grid.size / np.maximum(np.array(grid.resolution) - 1, 1)

        lo_new = np.minimum(-h, lo_req - spacing)
        hi_new = np.maximum(h, hi_req + spacing)

        vox_lo = np.maximum(np.ceil((-h - lo_new) / spacing).astype(int), 0)
        vox_hi = np.maximum(np.ceil((hi_new - h) / spacing).astype(int), 0)

        new_res = np.array(grid.resolution) + vox_lo + vox_hi
        if np.prod(new_res) > (256 ** 3):
            fld_logger.warn(f"sculpt_layer: grid expansion to {tuple(new_res)} exceeds 256^3 cap; clipping stroke")
            return

        new_size = (new_res - 1) * spacing
        local_shift = (vox_hi - vox_lo) * 0.5 * spacing

        if grid.placement is not None:
            rot = grid.placement.Rotation
            world_offset = rot.multVec(FreeCAD.Vector(*local_shift))
            new_placement = FreeCAD.Placement(grid.placement.Base + world_offset, rot)
        else:
            new_placement = FreeCAD.Placement(FreeCAD.Vector(*local_shift), FreeCAD.Rotation())

        new_data = np.full(tuple(new_res), VOXEL_FAR, dtype=np.float32)
        old_shape = grid.resolution
        new_data[
            vox_lo[0]:vox_lo[0] + old_shape[0],
            vox_lo[1]:vox_lo[1] + old_shape[1],
            vox_lo[2]:vox_lo[2] + old_shape[2]
        ] = grid.data

        grid.data = new_data
        grid.size = new_size
        grid.resolution = tuple(int(r) for r in new_res)
        grid.placement = new_placement
        grid.geometry_version += 1

        fp.SizeX = float(new_size[0])
        fp.SizeY = float(new_size[1])
        fp.SizeZ = float(new_size[2])
        fp.ResolutionX = int(new_res[0])
        fp.ResolutionY = int(new_res[1])
        fp.ResolutionZ = int(new_res[2])
        fp.Placement = new_placement
        self.touch_grid(fp)
        self.SdfField = None

    # -- composition -----------------------------------------------------------

    def _build_field(self, fp):
        source = self._resolve_source_field(fp)
        grid = self._grid_field(fp)
        if source is None:
            self.SdfField = grid
            return
        k = float(getattr(fp, "SmoothK", 0.0))
        mode = getattr(fp, "SculptMode", "Additive")
        if mode == "Subtractive":
            if k > 0.0:
                self.SdfField = SmoothSubtractionField(source, grid, k)
            else:
                self.SdfField = SubtractionField(source, grid)
        elif k > 0.0:
            self.SdfField = SmoothUnionField(source, grid, k)
        else:
            self.SdfField = UnionField(source, grid)


def create_sculpt_layer(source_obj, subtractive=False, name=None, smooth_k=0.0):
    """Create a sculpt layer above `source_obj`, sized to its bounding box."""
    doc = getattr(source_obj, "Document", None) or FreeCAD.activeDocument() or FreeCAD.newDocument()
    obj = doc.addObject("Part::FeaturePython",
                        name or (source_obj.Name + ("_Cut" if subtractive else "_Sculpt")))
    FldSculptLayerProxy(obj)
    obj.Source = source_obj
    obj.SculptMode = "Subtractive" if subtractive else "Additive"
    obj.SmoothK = float(smooth_k)

    # Inherit scene rendering Group from the source/base object
    from freecad.fields.core.objects.fld_modifier_stack import get_chain
    base, _ = get_chain(source_obj)
    base_group = getattr(base or source_obj, "Group", "Additive")
    obj.Group = base_group

    proxy = getattr(source_obj, "Proxy", None)
    fld = proxy.get_sdf_field(source_obj) if hasattr(proxy, "get_sdf_field") else None
    if fld is not None:
        bb_min, bb_max = fld.bounding_box()
        extent = bb_max - bb_min
        margin = max(5.0, extent.Length * 0.10)
        obj.SizeX = max(float(extent.x + 2 * margin), 10.0)
        obj.SizeY = max(float(extent.y + 2 * margin), 10.0)
        obj.SizeZ = max(float(extent.z + 2 * margin), 10.0)
        obj.Placement = FreeCAD.Placement((bb_min + bb_max) * 0.5, FreeCAD.Rotation())

    if FreeCAD.GuiUp:
        FldViewProvider(obj.ViewObject)
        if hasattr(source_obj, "ViewObject") and hasattr(source_obj.ViewObject, "ShapeColor"):
            obj.ViewObject.ShapeColor = source_obj.ViewObject.ShapeColor
        source_obj.ViewObject.Visibility = False
    obj.touch()
    return obj


def find_or_create_layer(target_obj, subtractive=False, smooth_k=0.0):
    """Find the topmost matching sculpt layer above target_obj, or create one."""
    from freecad.fields.core.objects.fld_modifier_stack import get_chain
    base, modifiers = get_chain(target_obj)
    if base is None:
        target_tail = target_obj
    else:
        target_tail = modifiers[-1] if modifiers else base

    target_mode = "Subtractive" if subtractive else "Additive"
    all_objs = ([base] + modifiers) if base is not None else [target_obj]
    for obj in reversed(all_objs):
        if isinstance(getattr(obj, "Proxy", None), FldSculptLayerProxy):
            if getattr(obj, "SculptMode", getattr(obj, "Group", "Additive")) == target_mode:
                return obj

    return create_sculpt_layer(target_tail, subtractive=subtractive, smooth_k=smooth_k)
