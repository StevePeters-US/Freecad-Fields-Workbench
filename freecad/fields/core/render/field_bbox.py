# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/field_bbox.py

Bounding-box arithmetic the renderer does around a field before it is baked:
clamping an oversized box to the render size limit, padding it by a cell, and
the sanity probe that warns when a field's own box is too tight for it.

Pure CPU work with no GL and no renderer state, so it is unit-testable on its
own -- `test_sdf_size_limit.py` drives the clamp through
`FldSceneVoxelRenderer._compute_grid_params`, which now forwards here.
"""
import numpy as np
import FreeCAD

from freecad.fields.core import fld_logger

FACE_NAMES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

#: How far outside each face the margin probe samples, in mm.
MARGIN_PROBE_OFFSET = 1.0


def compute_grid_params(field, cell_size: float) -> dict:
    """The clamped and padded bounding box for one field.

    Returns {"bbox_min": Vector, "bbox_max": Vector}. Used by the size limit
    tests and by volume baking.
    """
    from freecad.fields.core.fld_settings import get_max_sdf_render_size
    mn, mx = field.bounding_box()

    limit = get_max_sdf_render_size()

    # Clamp dimensions to limit around their midpoints.
    # Copies of the vectors, so the field's internal state is not mutated.
    bmin = FreeCAD.Vector(mn)
    bmax = FreeCAD.Vector(mx)

    for attr in ('x', 'y', 'z'):
        mn_val = getattr(bmin, attr)
        mx_val = getattr(bmax, attr)
        if mx_val - mn_val > limit:
            mid = (mn_val + mx_val) * 0.5
            setattr(bmin, attr, mid - limit * 0.5)
            setattr(bmax, attr, mid + limit * 0.5)

    # Apply padding
    bmin.x -= cell_size
    bmax.x += cell_size
    bmin.y -= cell_size
    bmax.y += cell_size
    bmin.z -= cell_size
    bmax.z += cell_size

    return {"bbox_min": bmin, "bbox_max": bmax}


def check_bbox_margin_safety(field, label, bmin, bmax):
    """Sample the field just outside each face of its box and warn on a hit.

    A negative value there means solid extends past the box the renderer was
    told to bake, which shows up as the model being sliced flat at that face.
    Reports the first offending face only -- once a box is too tight the rest
    of the faces carry no extra information.
    """
    c = (bmin + bmax) * 0.5
    h = (bmax - bmin) * 0.5
    offset = MARGIN_PROBE_OFFSET

    pts = np.array([
        [c.x + h.x + offset, c.y, c.z],
        [c.x - h.x - offset, c.y, c.z],
        [c.x, c.y + h.y + offset, c.z],
        [c.x, c.y - h.y - offset, c.z],
        [c.x, c.y, c.z + h.z + offset],
        [c.x, c.y, c.z - h.z - offset],
    ], dtype=np.float32)

    try:
        vals = field.evaluate_grid(pts)
        for i, val in enumerate(vals):
            if val < 0.0:
                fld_logger.warn(
                    f"SDF field '{label}' ({type(field).__name__}) bounding box might be too tight. "
                    f"Point just outside {FACE_NAMES[i]} face has negative SDF value: {val:.4f} mm (inside solid)."
                )
                break
    except Exception as e:
        fld_logger.render_debug(f"check_bbox_margin_safety failed for '{label}': {e}")


def union_dirty_region(dirty_labels, old_data_map, new_data_map):
    """The box enclosing everything that changed this rebuild, or None.

    None means 'nothing narrowed enough to be worth a region' and the caller
    should fall back to a full bake.

    A field's bounding box says where it IS, not where it CHANGED, and for a
    one-field cage those are wildly different: the box is the whole object on
    every tick, so a union of boxes degenerates to a full bake. A field that
    can name the part of space it actually rewrote (an extrusion stack knows
    only the dragged lobe moved) says so through `changed_region(old_field)`;
    anything that cannot falls through to the old/new box union, which is
    always safe and always coarse.
    """
    rmin_x = rmin_y = rmin_z = float('inf')
    rmax_x = rmax_y = rmax_z = float('-inf')

    for lbl in dirty_labels:
        nd_f = (new_data_map.get(lbl) or {}).get("field")
        od_f = (old_data_map.get(lbl) or {}).get("field")
        narrowed = None
        if nd_f is not None and od_f is not None and nd_f is not od_f:
            try:
                fn = getattr(nd_f, "changed_region", None)
                narrowed = fn(od_f) if fn is not None else None
            except Exception as e:
                fld_logger.render_debug(
                    f"SceneVoxel: changed_region failed for '{lbl}': {e}")
                narrowed = None
        if narrowed is not None:
            nmin, nmax = narrowed
            rmin_x, rmin_y, rmin_z = min(rmin_x, nmin.x), min(rmin_y, nmin.y), min(rmin_z, nmin.z)
            rmax_x, rmax_y, rmax_z = max(rmax_x, nmax.x), max(rmax_y, nmax.y), max(rmax_z, nmax.z)
            continue
        for data_map in (old_data_map, new_data_map):
            d = data_map.get(lbl)
            if d is None:
                continue
            rmin_x, rmin_y, rmin_z = (min(rmin_x, d["bbox_min"].x),
                                      min(rmin_y, d["bbox_min"].y),
                                      min(rmin_z, d["bbox_min"].z))
            rmax_x, rmax_y, rmax_z = (max(rmax_x, d["bbox_max"].x),
                                      max(rmax_y, d["bbox_max"].y),
                                      max(rmax_z, d["bbox_max"].z))

    if rmin_x >= rmax_x:
        return None
    return (FreeCAD.Vector(rmin_x, rmin_y, rmin_z),
            FreeCAD.Vector(rmax_x, rmax_y, rmax_z))
