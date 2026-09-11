# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/objects/sculpt_store.py

Sidecar persistence for sculpt-layer voxel grids. Grids are far too large for
document properties, so they live in `<doc>.dmsculpt/<ObjectName>.npz` and are
flushed on document save.
"""
import os
import shutil
import FreeCAD
import numpy as np
from freecad.fields.core import fld_logger

SIDECAR_SUFFIX = ".dmsculpt"

# Proxies whose grid changed since the last flush: {(doc_name, obj_name): proxy}
_dirty = {}

# Where each document's sidecars lived before its FileName last changed:
# {doc_name: directory}. FreeCAD points `doc.FileName` at the destination BEFORE
# it emits `slotStartSaveDocument`, so by the time the save observer runs a Save
# As is indistinguishable from a Save -- the old directory has to be captured
# while the FileName change is still pending (VOX-020).
_previous_dirs = {}


def sidecar_dir(doc, filename=None):
    """Directory holding one document's sculpt grids, or None if never saved."""
    path = filename or getattr(doc, "FileName", "") or ""
    if not path:
        return None
    return os.path.splitext(path)[0] + SIDECAR_SUFFIX


def mark_dirty(proxy, fp):
    doc = getattr(fp, "Document", None)
    if doc is not None:
        _dirty[(doc.Name, fp.Name)] = proxy


def save_grid(doc, obj_name, grid, filename=None):
    """Write one grid. Returns True on success."""
    d = sidecar_dir(doc, filename)
    if d is None:
        return False        # unsaved document -- the grid stays in memory
    try:
        os.makedirs(d, exist_ok=True)
        np.savez_compressed(
            os.path.join(d, obj_name + ".npz"),
            data=grid.data,
            size=grid.size,
            resolution=np.array(grid.resolution, dtype=np.int32),
        )
        return True
    except Exception as e:
        fld_logger.error(f"sculpt_store: failed to save grid for {obj_name}: {e}")
        return False


def load_grid(doc, obj_name):
    """Return (data, size, resolution) or None. Logs an error when a sidecar that
    should exist does not -- callers must NOT treat that as an empty sculpt."""
    d = sidecar_dir(doc)
    if d is None:
        return None
    path = os.path.join(d, obj_name + ".npz")
    if not os.path.exists(path):
        fld_logger.error(
            f"sculpt_store: no sidecar at {path}. The sculpt data for {obj_name} is "
            f"missing -- was the .FCStd moved without its {SIDECAR_SUFFIX} directory?")
        return None
    try:
        with np.load(path) as z:
            return (z["data"].astype(np.float32), tuple(z["size"]), tuple(int(r) for r in z["resolution"]))
    except Exception as e:
        fld_logger.error(f"sculpt_store: failed to load {path}: {e}")
        return None


def _carry_sidecars(previous, target):
    """Copy across the sidecars `target` does not already have.

    A grid that was never loaded into memory exists only as a file at
    `previous`, so a Save As has to bring the file itself; a grid that IS in
    memory is written by `_grids_to_flush` instead and needs no copy.
    """
    if not os.path.isdir(previous):
        return
    try:
        os.makedirs(target, exist_ok=True)
        for name in os.listdir(previous):
            if name.endswith(".npz") and not os.path.exists(os.path.join(target, name)):
                shutil.copy2(os.path.join(previous, name), os.path.join(target, name))
    except Exception as e:
        fld_logger.error(f"sculpt_store: could not carry sidecars to {target}: {e}")


def _grids_to_flush(doc, target):
    """{obj_name: grid} -- every grid this document must write at `target`.

    A dirty grid is always written. A clean one is written only when `target`
    has no sidecar for it, which is what makes Save As work: the destination is
    new, so "clean" says nothing about what is on disk there.
    """
    out = {}
    for (doc_name, obj_name), proxy in _dirty.items():
        if doc_name != doc.Name:
            continue
        grid = getattr(proxy, "_grid", None)
        if grid is not None:
            out[obj_name] = grid
    for obj in getattr(doc, "Objects", []):
        grid = getattr(getattr(obj, "Proxy", None), "_grid", None)
        if grid is None or obj.Name in out:
            continue
        if not os.path.exists(os.path.join(target, obj.Name + ".npz")):
            out[obj.Name] = grid
    return out


def _drop_migrated_properties(doc, obj_name):
    """A legacy migration that could not write its sidecar keeps `VoxelData`
    until one lands (VOX-021). It has now landed, so the properties can go."""
    obj = doc.getObject(obj_name) if hasattr(doc, "getObject") else None
    if obj is None or not hasattr(obj, "VoxelData"):
        return
    try:
        obj.removeProperty("VoxelData")
        obj.removeProperty("DataShape")
    except Exception as e:
        fld_logger.error(f"sculpt_store: could not drop legacy properties on {obj_name}: {e}")


class _SculptSidecarObserver:
    """Flushes sculpt grids when a document is saved.

    Separate from `_FldDocumentObserver` (fld_scene_voxel_renderer.py:384), which owns
    renderer field release -- a different concern, not a second copy of one.
    """

    def slotBeforeChangeDocument(self, doc, prop):
        # The only chance to read the outgoing path: `Changed` already carries
        # the new one, and so does the save that follows.
        if prop == "FileName":
            _previous_dirs[doc.Name] = sidecar_dir(doc)

    def slotStartSaveDocument(self, doc, filename):
        target = sidecar_dir(doc, filename)
        if target is None:
            return                          # unsaved document -- grids stay in memory
        previous = _previous_dirs.get(doc.Name)
        if previous and os.path.abspath(previous) != os.path.abspath(target):
            _carry_sidecars(previous, target)
        for obj_name, grid in _grids_to_flush(doc, target).items():
            if save_grid(doc, obj_name, grid, filename):
                _dirty.pop((doc.Name, obj_name), None)
                _drop_migrated_properties(doc, obj_name)

    def slotDeletedDocument(self, doc):
        # Document names are reused -- close A and open A again and a stale entry
        # would copy one document's sidecars into another's.
        _previous_dirs.pop(doc.Name, None)
        for key in [k for k in _dirty if k[0] == doc.Name]:
            _dirty.pop(key, None)


_observer = None


def install_observer():
    """Idempotent; called from the sculpt layer proxy's __init__."""
    global _observer
    if _observer is None:
        _observer = _SculptSidecarObserver()
        add = getattr(FreeCAD, "addDocumentObserver", None)
        if add is not None:
            add(_observer)
