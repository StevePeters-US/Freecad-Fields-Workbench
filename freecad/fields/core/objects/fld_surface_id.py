# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Monotonic per-document surface-id allocation, stamping, and lookup.

Split out of fld_object_proxy.py (CR-061) -- already treated as a logically separate
module by its own callers (`sdf_field_registry.py`, `fld_modifier_proxy_base.py`,
`fld_tool_manager.py`, `sculpt_brush_tool.py` all import these three functions directly
rather than going through `FldObjectProxy`), so this was the lowest-risk piece to
extract first.
"""
from freecad.fields.core.sdf.sdf_constants import SURFACE_ID_UNSET


def allocate_surface_ids(doc, count: int = 1) -> int:
    """Allocate a contiguous block of `count` monotonic, persistent surface IDs for `doc`.

    Surface IDs are stored and persisted in `doc.Meta["Fld_NextSurfaceId"]` so they survive
    saving and reloading without reusing IDs when objects are deleted.
    Range is checked against 65534 (65535 is the reserved sentinel).
    """
    if doc is None:
        return SURFACE_ID_UNSET

    # `App.Document.Meta` hands back a COPY of the metadata dict. Mutating what the
    # getter returned does nothing -- the counter has to be read, incremented, and
    # assigned back through the setter, or every allocation re-derives the same
    # number from the object scan and ids get REUSED after a delete. Reuse is the
    # one thing this allocator exists to prevent (trap 6: a bevel that referenced
    # a deleted surface would silently reattach to whatever inherited its id).
    try:
        meta = dict(getattr(doc, "Meta", None) or {})
    except Exception:
        return SURFACE_ID_UNSET

    curr_str = meta.get("Fld_NextSurfaceId", None)
    if curr_str is not None:
        try:
            curr = int(curr_str)
        except (ValueError, TypeError):
            curr = 0
    else:
        # First allocation in this document -- scan existing objects to avoid collisions
        max_existing = -1
        for obj in getattr(doc, "Objects", []):
            sid = getattr(obj, "SurfaceIdBase", None)
            if isinstance(sid, int) and sid < SURFACE_ID_UNSET:
                max_existing = max(max_existing, sid)
        curr = max_existing + 1 if max_existing >= 0 else 0

    if curr + count > SURFACE_ID_UNSET - 1:
        from freecad.fields.core import fld_logger
        fld_logger.error(
            f"allocate_surface_ids: Exceeded max surface ID {SURFACE_ID_UNSET - 1} (requested {curr} + {count})"
        )
        return min(curr, SURFACE_ID_UNSET - 1)

    meta["Fld_NextSurfaceId"] = str(curr + count)
    try:
        doc.Meta = meta
    except Exception as e:
        from freecad.fields.core import fld_logger
        fld_logger.error(f"allocate_surface_ids: could not persist counter on the document: {e}")
        return SURFACE_ID_UNSET
    return curr


def stamp_surface_id(obj, field):
    """Give `field` the root surface id that `obj` owns. Returns `field`.

    The id volume holds exactly ONE id per field -- the root's, written by
    scene_bake as `u_root_surface_id` -- and everything the march does with a
    field is keyed by it: the FieldMeta row that carries the body colour, the
    subtractive flag that draws the hatch, the selected flag and the outline
    colour, and `pick_surface_at` for hit testing. A field that reaches the
    renderer unstamped is 65535 in every one of its voxels, which is the miss
    sentinel, so it draws in the global base colour and has no row at all.

    This is the only writer of `SdfField.surface_id`. It was previously an inline
    block at the tail of `FldObjectProxy.get_sdf_field`, which stamped only the
    fields handed out by that method -- but `execute` pushes booleans, cages and
    reconstructed primitives straight at the renderer, and so does every tool's
    drag preview. Those arrived with no id. What hid it was a pair of
    `min(field_index, 65534)` fallbacks that keyed the id volume AND the FieldMeta
    table by field index instead: wrong in the same direction, so they agreed with
    each other and the picture looked right until the fallbacks came out.
    """
    if obj is None or field is None:
        return field
    sid = getattr(obj, "SurfaceIdBase", None)
    if sid is None or sid == SURFACE_ID_UNSET:
        return field
    field.surface_id = int(sid)
    return field


def find_object_by_surface_id(doc, surface_id: int):
    """Map a surface ID to the owning FreeCAD document object."""
    if doc is None or surface_id is None or surface_id >= SURFACE_ID_UNSET or surface_id < 0:
        return None
    for obj in getattr(doc, "Objects", []):
        sid_base = getattr(obj, "SurfaceIdBase", None)
        if sid_base is not None and sid_base != SURFACE_ID_UNSET:
            proxy = getattr(obj, "Proxy", None)
            fld = getattr(proxy, "SdfField", None) if proxy is not None else None
            count = fld.surface_count() if (fld is not None and hasattr(fld, "surface_count")) else 1
            if sid_base <= surface_id < sid_base + count:
                return obj
    return None


