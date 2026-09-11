# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/objects/fld_voxel_field.py

Document object proxy for the discrete 3D voxel SDF field, plus its factory
and the write-back helper that persists a mutated grid onto the object.
"""
import base64
import numpy as np
from scipy.ndimage import map_coordinates

import FreeCAD
from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_view_provider import FldViewProvider
from freecad.fields.core.objects.fld_modifier_proxy_base import FldModifierProxyBase
from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR, apply_default_view_settings
from freecad.fields.core.objects.sculpt_store import (
    install_observer, load_grid, save_grid, mark_dirty
)
from freecad.fields.core.sdf.sdf.voxel_field import SdfVoxelField, default_voxel_grid


class FldVoxelFieldProxy(FldModifierProxyBase):
    """
    Proxy object for a 3D Voxel Field primitive / modifier.
    Wraps an optional Source SDF solid or operates as a standalone voxel grid.
    """
    SHAPE_TYPE_TOOLTIP = "Type of primitive"
    SOURCE_GROUP = "VoxelField"
    ALWAYS_REBUILD = False

    def __init__(self, obj):
        super().__init__(obj)
        install_observer()
        if not hasattr(obj, "SizeX"):
            obj.addProperty("App::PropertyFloat", "SizeX", "VoxelField", "Bounding box size along X").SizeX = 100.0
        if not hasattr(obj, "SizeY"):
            obj.addProperty("App::PropertyFloat", "SizeY", "VoxelField", "Bounding box size along Y").SizeY = 100.0
        if not hasattr(obj, "SizeZ"):
            obj.addProperty("App::PropertyFloat", "SizeZ", "VoxelField", "Bounding box size along Z").SizeZ = 100.0

        if not hasattr(obj, "ResolutionX"):
            obj.addProperty("App::PropertyInteger", "ResolutionX", "VoxelField", "Voxel grid resolution along X").ResolutionX = 32
        if not hasattr(obj, "ResolutionY"):
            obj.addProperty("App::PropertyInteger", "ResolutionY", "VoxelField", "Voxel grid resolution along Y").ResolutionY = 32
        if not hasattr(obj, "ResolutionZ"):
            obj.addProperty("App::PropertyInteger", "ResolutionZ", "VoxelField", "Voxel grid resolution along Z").ResolutionZ = 32

        if not hasattr(obj, "Interpolation"):
            obj.addProperty("App::PropertyEnumeration", "Interpolation", "VoxelField", "Sampling interpolation mode")
            obj.Interpolation = ["Trilinear", "Nearest"]
            obj.Interpolation = "Trilinear"

        if not hasattr(obj, "Sculpted"):
            obj.addProperty("App::PropertyBool", "Sculpted", "VoxelField", "Whether field contains sculpted/standalone data")
            obj.Sculpted = False

    def onDocumentRestored(self, obj):
        super().onDocumentRestored(obj)
        install_observer()
        if not hasattr(obj, "Sculpted"):
            obj.addProperty("App::PropertyBool", "Sculpted", "VoxelField", "Whether field contains sculpted/standalone data")
            obj.Sculpted = False

        # Migrate legacy VoxelData property to sidecar if present
        if hasattr(obj, "VoxelData") and getattr(obj, "VoxelData", ""):
            try:
                raw_bytes = base64.b64decode(obj.VoxelData.encode('ascii'))
                data_shape = tuple(getattr(obj, "DataShape", (obj.ResolutionX, obj.ResolutionY, obj.ResolutionZ)))
                data = np.frombuffer(raw_bytes, dtype=np.float32).copy().reshape(data_shape)
                interp = str(getattr(obj, "Interpolation", "Trilinear")).lower()
                fld = SdfVoxelField(size=(obj.SizeX, obj.SizeY, obj.SizeZ), resolution=data_shape, data=data,
                                    interpolation=interp, placement=obj.Placement)
                self._grid = fld
                if save_grid(obj.Document, obj.Name, fld):
                    obj.removeProperty("VoxelData")
                    obj.removeProperty("DataShape")
                else:
                    mark_dirty(self, obj)
            except Exception as e:
                fld_logger.error(f"Failed to migrate VoxelData to sidecar for {obj.Name}: {e}")

    def _grid_field(self, fp):
        """The voxel grid, loaded lazily from the sidecar on first use after restore."""
        interp = str(getattr(fp, "Interpolation", "Trilinear")).lower()
        if getattr(self, "_grid", None) is not None:
            self._grid.interpolation = interp
            return self._grid
        size = (getattr(fp, "SizeX", 100.0), getattr(fp, "SizeY", 100.0), getattr(fp, "SizeZ", 100.0))
        res = (getattr(fp, "ResolutionX", 32), getattr(fp, "ResolutionY", 32), getattr(fp, "ResolutionZ", 32))
        placement = getattr(fp, "Placement", None)
        loaded = load_grid(getattr(fp, "Document", None), fp.Name)
        if loaded is not None:
            data, sz, r = loaded
            self._grid = SdfVoxelField(size=sz, resolution=r, data=data,
                                       interpolation=interp, placement=placement)
        else:
            self._grid = SdfVoxelField(size=size, resolution=res,
                                       interpolation=interp, placement=placement)
        return self._grid

    def onChanged(self, fp, prop):
        if prop in ("ResolutionX", "ResolutionY", "ResolutionZ"):
            res = (getattr(fp, "ResolutionX", 32), getattr(fp, "ResolutionY", 32), getattr(fp, "ResolutionZ", 32))
            grid = getattr(self, "_grid", None)
            if grid is not None and grid.resolution != res:
                nx_old, ny_old, nz_old = grid.resolution
                nx_new, ny_new, nz_new = res
                u = np.linspace(0, nx_old - 1, nx_new)
                v = np.linspace(0, ny_old - 1, ny_new)
                w = np.linspace(0, nz_old - 1, nz_new)
                gu, gv, gw = np.meshgrid(u, v, w, indexing="ij")
                coords = np.column_stack((gu.ravel(), gv.ravel(), gw.ravel())).T
                interp = str(getattr(fp, "Interpolation", "Trilinear")).lower()
                order = 0 if interp == "nearest" else 1
                new_data = map_coordinates(grid.data, coords, order=order, mode="nearest").reshape(res).astype(np.float32)
                self._grid = SdfVoxelField(size=grid.size, resolution=res, data=new_data, interpolation=grid.interpolation, placement=grid.placement)
                if getattr(fp, "Document", None) is not None:
                    save_grid(fp.Document, fp.Name, self._grid)
        if prop == "Interpolation":
            interp = str(getattr(fp, "Interpolation", "Trilinear")).lower()
            grid = getattr(self, "_grid", None)
            if grid is not None:
                grid.interpolation = interp
        if prop in ("SizeX", "SizeY", "SizeZ", "ResolutionX", "ResolutionY", "ResolutionZ", "Interpolation", "Source", "Placement"):
            self.SdfField = None

    def _build_field(self, fp):
        base_field = self._resolve_source_field(fp)
        is_sculpted = getattr(fp, "Sculpted", False)
        if base_field is not None and not is_sculpted:
            size = (getattr(fp, "SizeX", 100.0), getattr(fp, "SizeY", 100.0), getattr(fp, "SizeZ", 100.0))
            res = (getattr(fp, "ResolutionX", 32), getattr(fp, "ResolutionY", 32), getattr(fp, "ResolutionZ", 32))
            interp = str(getattr(fp, "Interpolation", "Trilinear")).lower()
            placement = getattr(fp, "Placement", None)
            fld = SdfVoxelField.from_field(
                base_field, size=size, resolution=res,
                interpolation=interp, placement=placement
            )
            self.SdfField = fld
        else:
            self.SdfField = self._grid_field(fp)


def write_back_voxel_data(obj, fld):
    """Writes back live SdfVoxelField data array to the sidecar store."""
    if obj is None or fld is None:
        return
    obj.Sculpted = True
    if hasattr(obj, "Proxy") and obj.Proxy:
        obj.Proxy._grid = fld
        obj.Proxy.SdfField = fld
        mark_dirty(obj.Proxy, obj)
    doc = getattr(obj, "Document", None)
    if doc is not None:
        save_grid(doc, obj.Name, fld)


def create_voxel_field_object(name="VoxelField", source_obj=None,
                              size=None, resolution=(32, 32, 32),
                              interpolation="Trilinear", placement=None):
    """Creates a new FldVoxelField document object."""
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument()

    obj = doc.addObject("Part::FeaturePython", name)
    FldVoxelFieldProxy(obj)

    computed_size = (100.0, 100.0, 100.0) if size is None else size
    computed_placement = placement

    if source_obj is not None:
        obj.Source = source_obj
        obj.Sculpted = False
        if hasattr(source_obj, "Proxy") and hasattr(source_obj.Proxy, "get_sdf_field"):
            fld = source_obj.Proxy.get_sdf_field(source_obj)
            if fld:
                bb_min, bb_max = fld.bounding_box()
                extent = bb_max - bb_min
                margin = max(5.0, extent.Length * 0.05)
                if size is None:
                    computed_size = (
                        max(float(extent.x + 2 * margin), 10.0),
                        max(float(extent.y + 2 * margin), 10.0),
                        max(float(extent.z + 2 * margin), 10.0),
                    )
                if computed_placement is None:
                    center = (bb_min + bb_max) * 0.5
                    computed_placement = FreeCAD.Placement(center, FreeCAD.Rotation())
    else:
        obj.Sculpted = True

    obj.SizeX = float(computed_size[0])
    obj.SizeY = float(computed_size[1])
    obj.SizeZ = float(computed_size[2])
    obj.ResolutionX = int(resolution[0])
    obj.ResolutionY = int(resolution[1])
    obj.ResolutionZ = int(resolution[2])
    obj.Interpolation = str(interpolation)
    if computed_placement is not None:
        obj.Placement = computed_placement

    # If standalone, initialize grid in sidecar. The seed comes from
    # default_voxel_grid -- the same one SdfVoxelField.__init__ uses -- so there is one
    # definition of "a default field" rather than a copy here that can drift from it.
    if source_obj is None:
        init_data = default_voxel_grid(computed_size, resolution)
        init_fld = SdfVoxelField(size=computed_size, resolution=resolution, data=init_data, placement=computed_placement)
        doc_for_save = getattr(obj, "Document", None) or doc
        if doc_for_save is not None:
            save_grid(doc_for_save, obj.Name, init_fld)

    if FreeCAD.GuiUp:
        FldViewProvider(obj.ViewObject)
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            try:
                apply_default_view_settings(obj.ViewObject, DEFAULT_ADDITIVE_COLOR)
            except Exception as e:
                fld_logger.debug(f"create_voxel_field: ViewObject appearance setup failed: {e}")

    obj.touch()
    return obj
