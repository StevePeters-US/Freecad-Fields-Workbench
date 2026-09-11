# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""SDF boolean tree composition: Fuse/Cut/Common field-building and dependency tracking.

Split out of commands/cmd_boolean.py (CR-064) -- the composition math has no UI or
command-dispatch dependency, and was already being reached from core/ and tools/ code
(fld_object_proxy.py, sdf_csg_convert.py, fld_sdf_tool_base.py, primitive_creator_base.py)
through a commands/ import, which is backwards layering for a `core/` package to depend
on. Moving it here makes those imports core->core / tools->core instead of ->commands.
"""
from freecad.fields.core import fld_logger


def _fold_union(fields, k=0.0):
    """Left-associative union fold. Uses SmoothUnionField when k > 0."""
    from freecad.fields.core.sdf.sdf_composer import UnionField, SmoothUnionField
    if not fields:
        return None
    result = fields[0]
    for f in fields[1:]:
        result = SmoothUnionField(result, f, k) if k > 0.0 else UnionField(result, f)
    return result


def _round_cutters(fields, k, operation):
    """Round each subtractive cutter's own convex edges to k mm.

    A difference never creates a concave edge where the two surfaces meet: the
    material there is the intersection of two half-spaces, which is convex from
    every direction. So every internal edge of a cavity belongs to the cutter,
    and filleting the inside of a cut means rounding the cutter -- not blending
    the operation, which would only round the rim off on the outside.
    """
    from freecad.fields.core.sdf.sdf_composer import round_convex_edges
    if k <= 0.0:
        return list(fields)
    rounded = []
    for f in fields:
        rf = round_convex_edges(f, k)
        if rf is None:
            fld_logger.error(f"Fld_{operation}: {type(f).__name__} has no exact rounded "
                            "form, so that cutter's edges stay sharp.")
            rf = f
        rounded.append(rf)
    return rounded


def build_boolean_field(operation, orange_fields, blue_fields, k, bevels=None):
    """Compose the result field for `operation` from the two colour groups at
    radius k (mm). Returns None (and logs) when the groups cannot supply it.

    k rounds internal edges only. For Add that is the union's own crease, which
    SmoothUnionField fillets. For Subtract it is the cutter radius: the cavity
    gets k mm internal corners and the cut itself stays sharp, so the rim where
    it breaks the surface keeps its edge.
    """
    from freecad.fields.core.sdf.sdf_composer import (
        SubtractionField, IntersectionField, SmoothIntersectionField)

    tree = None
    if operation == "Add":
        all_fields = orange_fields + blue_fields
        if not all_fields:
            fld_logger.error("Fields_Add: no fields selected.")
            return None
        tree = _fold_union(all_fields, k)

    elif operation not in ("Subtract", "Intersection"):
        return None

    elif not orange_fields:
        fld_logger.error(f"Fld_{operation}: no orange (additive) fields selected.")
        return None
    elif not blue_fields:
        fld_logger.error(f"Fld_{operation}: no blue (subtractive) fields selected.")
        return None
    else:
        orange = _fold_union(orange_fields, k)

        if operation == "Subtract":
            blue = _fold_union(_round_cutters(blue_fields, k, operation), k)
            tree = SubtractionField(orange, blue)
        else:
            blue = _fold_union(blue_fields, k)
            tree = SmoothIntersectionField(orange, blue, k) if k > 0.0 else IntersectionField(orange, blue)

    if tree is not None and bevels:
        from freecad.fields.core.sdf.sdf_rewriter import apply_bevels
        tree = apply_bevels(tree, bevels)

    return tree


def _recompose_boolean(fp):
    """Re-compose the SDF tree from fp.BooleanInputs + fp.SmoothK + fp.Bevel*.

    Called from FldObjectProxy.execute() when BooleanInputs are present.
    Returns the composed SdfField, or None on failure.
    """
    op     = getattr(fp, "BooleanOp",    None)
    inputs = getattr(fp, "BooleanInputs", [])
    if not inputs or not op:
        return None

    k = float(getattr(fp, "SmoothK", 0.0) or 0.0)

    orange_fields, blue_fields = [], []
    for child in inputs:
        if child is None:
            continue
        proxy = getattr(child, "Proxy", None)
        if proxy is None:
            continue
        field = (proxy.get_sdf_field(child) if hasattr(proxy, "get_sdf_field")
                 else getattr(proxy, "SdfField", None))
        if field is None:
            continue
        if getattr(child, "Group", "Additive") == "Subtractive":
            blue_fields.append(field)
        else:
            orange_fields.append(field)

    from freecad.fields.core.sdf.sdf_rewriter import Bevel
    bevel_ida = getattr(fp, "BevelIdA", []) or []
    bevel_idb = getattr(fp, "BevelIdB", []) or []
    bevel_rad = getattr(fp, "BevelRadius", []) or []
    bevel_chm = getattr(fp, "BevelChamfer", []) or []
    bevels = []
    for i in range(min(len(bevel_ida), len(bevel_idb), len(bevel_rad))):
        chm = bevel_chm[i] if i < len(bevel_chm) else 0.0
        bevels.append(Bevel(id_a=int(bevel_ida[i]), id_b=int(bevel_idb[i]), radius=float(bevel_rad[i]), chamfer=float(chm)))

    return build_boolean_field(op, orange_fields, blue_fields, k, bevels=bevels)


def get_boolean_parents(child_obj):
    """Return a list of all boolean parent objects that depend on child_obj (directly or indirectly)."""
    parents = []
    if not child_obj or not child_obj.Document:
        return parents

    to_check = [child_obj]
    checked = set()

    while to_check:
        current = to_check.pop(0)
        if current in checked:
            continue
        checked.add(current)

        for obj in child_obj.Document.Objects:
            if getattr(obj, "ShapeType", None) != "sdf":
                continue
            inputs = getattr(obj, "BooleanInputs", None) or []
            if current in inputs and obj not in checked:
                parents.append(obj)
                to_check.append(obj)
    return parents


def update_boolean_parents(child_obj):
    """Recompose and update all boolean parent fields in the renderer."""
    parents = get_boolean_parents(child_obj)
    if not parents:
        return
    try:
        from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
        doc = child_obj.Document
        renderer = FldSceneVoxelRenderer.get_instance()
        renderer.begin_update_batch()
        try:
            for parent in parents:
                new_field = _recompose_boolean(parent)
                if new_field is not None:
                    if hasattr(parent, "Proxy"):
                        parent.Proxy.SdfField = new_field
                    label = f"{doc.Name}.{parent.Name}"
                    renderer.update_field(label, new_field)
        finally:
            renderer.end_update_batch()
    except Exception as e:
        from freecad.fields.core import fld_logger
        fld_logger.debug(f"update_boolean_parents error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Live-preview task panel
# ──────────────────────────────────────────────────────────────────────────────

