# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Flattening sliced contours into nesting-ready 2D output.

Used by the "Flatten for Nesting" option in Fields_SDFSlice: reprojects each traced
contour into its own cut plane's (u, v) basis (world Z=0, i.e. the cut normal now
points along +Z), anchored to the sliced object's own placement and origin so the
flattened output lands where the object is instead of at an arbitrary world location.
Array copies share the same cut-plane origin along the normal, so they land exactly on
top of one another -- that's expected: overlap-free packing is the Nesting workbench's
job, not this module's; this only needs to hand it flat, non-degenerate input.
"""
import FreeCAD
from freecad.fields.core.sdf.sdf_contour import slice_frame


def _frame_for_layout(normal, placement):
    """The (u, v) basis to flatten into: the sliced object's own local X axis,
    projected onto the cut plane, when there is one to use.

    `slice_frame` alone picks u/v by crossing the normal against a fixed world
    "up" -- for a primitive with no privileged direction (a sphere) that's as
    good as anything, but for a part with a real local frame (a box, an array)
    it means the flattened orientation is arbitrary and can flip between
    recomputes for reasons that have nothing to do with the part. Anchoring to
    the object's own Placement instead makes the orientation the part's own,
    not an artifact of the cross product.

    Falls back to `slice_frame`'s scheme when there's no placement, or the
    object's local X happens to be (near-)parallel to the cut normal, in which
    case its projection onto the plane is degenerate.
    """
    normal = FreeCAD.Vector(normal)
    normal.normalize()

    if placement is not None:
        local_x = placement.Rotation.multVec(FreeCAD.Vector(1, 0, 0))
        u_axis = local_x - normal * local_x.dot(normal)
        if u_axis.Length > 1e-6:
            u_axis.normalize()
            v_axis = normal.cross(u_axis)
            v_axis.normalize()
            return normal, u_axis, v_axis

    return slice_frame(normal)


def _flatten_point(p, plane_origin, u_axis, v_axis):
    rel = p - plane_origin
    return FreeCAD.Vector(rel.dot(u_axis), rel.dot(v_axis), 0.0)


def flatten_slice_members_for_nesting(members, origin, normal, spacing=0.0, placement=None):
    """Mutate `members` (as returned by `build_slice_group_members`) in place.

    Args:
        members: list of (c_idx, [curve_dict, ...]) -- same shape build_slice_group_members
            returns, and the same curve_dict objects do_slice goes on to build FldCurve
            objects from, so this must run *before* those objects are created.
        origin: base plane origin passed to build_slice_group_members (FreeCAD.Vector).
        normal: slice plane normal passed to build_slice_group_members (FreeCAD.Vector).
        spacing: array spacing passed to build_slice_group_members, in mm.
        placement: the sliced object's FreeCAD.Placement, used to anchor the flattened
            orientation to the object's own local X axis instead of an arbitrary one.
            Optional -- pass None to keep the old world-up-relative behaviour.
    """
    _, u_axis, v_axis = _frame_for_layout(normal, placement)

    for c_idx, curves in members:
        if not curves:
            continue

        plane_origin = origin + normal * (c_idx * spacing)

        for curve in curves:
            curve["Points"] = [_flatten_point(p, plane_origin, u_axis, v_axis) for p in curve["Points"]]
            curve["HandleIn"] = [_flatten_point(p, plane_origin, u_axis, v_axis) for p in curve["HandleIn"]]
            curve["HandleOut"] = [_flatten_point(p, plane_origin, u_axis, v_axis) for p in curve["HandleOut"]]
