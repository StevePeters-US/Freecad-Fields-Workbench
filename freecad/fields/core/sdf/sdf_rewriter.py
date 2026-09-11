# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf_rewriter.py

Distributivity-based tree rewriter for edge-level bevels (fillets and chamfers).

A bevel is a geometric property of two intersecting surfaces, not of a boolean
operation. When two surfaces A and B are not siblings in the boolean tree
(for example, in `((A ∪ B) ∪ C)` where the user wants to bevel the A/C edge),
distributivity rewrites the tree so that A and C become sibling operands of a
combinator:
    `((A ∪ B) ∪ C)  ->  ((A ∪ C) ∪ (B ∪ C))`
The radius is then applied locally at the `(A ∪ C)` combinator node.

Distributivity holds for lattice operations (union `min` and intersection `max`),
which distribute over each other. For SubtractionField, internal cavity edges
belong to the cutter and are rounded by `round_convex_edges` (Trap 8), so
subtraction nodes are not distributed across.
"""

from dataclasses import dataclass, field
import copy
from typing import List, Optional, Set, Tuple

import FreeCAD
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_constants import SURFACE_ID_UNSET
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf_composer import (
    ComposerField,
    UnionField,
    IntersectionField,
    SubtractionField,
    SmoothUnionField,
    SmoothIntersectionField,
    SmoothSubtractionField,
)


@dataclass
class Bevel:
    """Specification of an edge bevel between two surface IDs."""
    id_a: int
    id_b: int
    radius: float
    chamfer: float = 0.0
    blend_id: int = SURFACE_ID_UNSET


@dataclass
class _Node:
    """Intermediate tree node for SDF rewriting.

    Attributes:
        kind: "combinator" or "leaf"
        field: The SdfField instance (for combinators, child references are ignored)
        left: Left child node (for combinators)
        right: Right child node (for combinators)
        modifiers: Outermost-first list of modifier field wrappers to re-apply
    """
    kind: str
    field: SdfField
    left: Optional["_Node"] = None
    right: Optional["_Node"] = None
    modifiers: list = field(default_factory=list)


def _is_modifier(f: SdfField) -> bool:
    """Return True if f wraps an inner source field."""
    return hasattr(f, "source") and not isinstance(f, ComposerField)


def _apply_modifier(mod: SdfField, child: SdfField) -> SdfField:
    """Create a copy of modifier field wrapping child."""
    new_mod = copy.copy(mod)
    if hasattr(new_mod, "source"):
        new_mod.source = child
    elif hasattr(new_mod, "field"):
        new_mod.field = child
    elif hasattr(new_mod, "child"):
        new_mod.child = child
    return new_mod


def from_field(f: SdfField, modifiers: tuple = ()) -> _Node:
    """Convert an SdfField tree into an intermediate _Node tree."""
    if _is_modifier(f):
        return from_field(f.source, modifiers=(*modifiers, f))

    if isinstance(f, ComposerField):
        left_node = from_field(f.a, modifiers=())
        right_node = from_field(f.b, modifiers=())
        return _Node(
            kind="combinator",
            field=f,
            left=left_node,
            right=right_node,
            modifiers=list(modifiers),
        )

    return _Node(
        kind="leaf",
        field=f,
        left=None,
        right=None,
        modifiers=list(modifiers),
    )


def to_field(node: _Node) -> SdfField:
    """Rebuild an SdfField tree from an intermediate _Node tree."""
    if node.kind == "combinator":
        left_f = to_field(node.left)
        right_f = to_field(node.right)
        comb = copy.copy(node.field)
        comb.a = left_f
        comb.b = right_f
        res = comb
    else:
        res = node.field

    for mod in reversed(node.modifiers):
        res = _apply_modifier(mod, res)
    return res


def ids_under(node: Optional[_Node]) -> Set[int]:
    """Return all surface IDs present in the subtree under node."""
    if node is None:
        return set()

    result = set()
    if node.kind == "leaf":
        sid = getattr(node.field, "surface_id", SURFACE_ID_UNSET)
        if sid < SURFACE_ID_UNSET:
            count = node.field.surface_count() if hasattr(node.field, "surface_count") else 1
            result.update(range(sid, sid + count))
    else:
        result.update(ids_under(node.left))
        result.update(ids_under(node.right))

    for mod in node.modifiers:
        sid = getattr(mod, "surface_id", SURFACE_ID_UNSET)
        if sid < SURFACE_ID_UNSET:
            count = mod.surface_count() if hasattr(mod, "surface_count") else 1
            result.update(range(sid, sid + count))

    return result


def count_nodes(node: Optional[_Node]) -> int:
    """Count total number of nodes in the subtree."""
    if node is None:
        return 0
    return 1 + count_nodes(node.left) + count_nodes(node.right)


def _find_id_depth(node: Optional[_Node], target_id: int, current_depth: int = 0) -> Optional[int]:
    """Find minimum depth of a surface ID under node."""
    if node is None:
        return None
    if target_id not in ids_under(node):
        return None
    if node.kind == "leaf":
        return current_depth

    d_l = _find_id_depth(node.left, target_id, current_depth + 1)
    d_r = _find_id_depth(node.right, target_id, current_depth + 1)
    if d_l is not None and d_r is not None:
        return min(d_l, d_r)
    return d_l if d_l is not None else d_r


def pair_distance(node: Optional[_Node], id_a: int, id_b: int) -> int:
    """Compute how many levels apart id_a and id_b are under node.

    Returns 1 if they are immediate left/right siblings under node.
    Returns 999999 if either ID is missing.
    """
    if node is None or node.kind != "combinator":
        return 999999

    ids_l = ids_under(node.left)
    ids_r = ids_under(node.right)

    both_in_l = (id_a in ids_l) and (id_b in ids_l)
    both_in_r = (id_a in ids_r) and (id_b in ids_r)

    if both_in_l:
        return pair_distance(node.left, id_a, id_b)
    if both_in_r:
        return pair_distance(node.right, id_a, id_b)

    # Split across left and right
    has_a_l = id_a in ids_l
    has_b_r = id_b in ids_r
    has_b_l = id_b in ids_l
    has_a_r = id_a in ids_r

    if (has_a_l and has_b_r) or (has_b_l and has_a_r):
        d_a = _find_id_depth(node.left if has_a_l else node.right, id_a, 0) or 0
        d_b = _find_id_depth(node.right if has_a_l else node.left, id_b, 0) or 0
        return d_a + d_b + 1

    return 999999


def is_sibling_pair(node: _Node, id_a: int, id_b: int) -> bool:
    """Return True if node is a combinator whose direct children are leaves supplying id_a and id_b."""
    if node.kind != "combinator" or node.left is None or node.right is None:
        return False
    if node.left.kind != "leaf" or node.right.kind != "leaf":
        return False
    ids_l = ids_under(node.left)
    ids_r = ids_under(node.right)
    return ((id_a in ids_l and id_b in ids_r) or (id_b in ids_l and id_a in ids_r))


def find_sibling_node(node: Optional[_Node], id_a: int, id_b: int) -> Optional[_Node]:
    """Find a combinator node where id_a and id_b are direct sibling operands."""
    if node is None or node.kind != "combinator":
        return None
    if is_sibling_pair(node, id_a, id_b):
        return node
    found = find_sibling_node(node.left, id_a, id_b)
    if found is not None:
        return found
    return find_sibling_node(node.right, id_a, id_b)


def rewrite_for_pair(node: _Node, id_a: int, id_b: int) -> bool:
    """Distribute combinators so that id_a and id_b become direct siblings.

    Returns True if the tree was modified or is already satisfied.
    """
    if node.kind != "combinator" or node.left is None or node.right is None:
        return False

    ids_l = ids_under(node.left)
    ids_r = ids_under(node.right)
    all_ids = ids_l | ids_r

    if id_a not in all_ids or id_b not in all_ids:
        return False

    # If both IDs are in both subtrees (from prior distributions), pick closer one
    if (id_a in ids_l and id_b in ids_l) and (id_a in ids_r and id_b in ids_r):
        if pair_distance(node.left, id_a, id_b) <= pair_distance(node.right, id_a, id_b):
            return rewrite_for_pair(node.left, id_a, id_b)
        else:
            return rewrite_for_pair(node.right, id_a, id_b)

    if id_a in ids_l and id_b in ids_l:
        return rewrite_for_pair(node.left, id_a, id_b)

    if id_a in ids_r and id_b in ids_r:
        return rewrite_for_pair(node.right, id_a, id_b)

    # Subtraction boundary: do not distribute across SubtractionField
    if isinstance(node.field, SubtractionField):
        return False

    # The pair is split: one in left, one in right
    # If both children are single leaves with these IDs, they are already siblings
    if node.left.kind == "leaf" and node.right.kind == "leaf":
        return True

    # Case A: left child is a combinator: (X o Y) o Z -> (X o Z) o (Y o Z)
    if node.left.kind == "combinator" and node.left.left is not None and node.left.right is not None:
        orig_left = node.left
        orig_right = node.right

        new_ll = orig_left.left
        new_lr = copy.deepcopy(orig_right)
        new_left = _Node(
            kind="combinator",
            field=copy.copy(node.field),
            left=new_ll,
            right=new_lr,
            modifiers=list(orig_left.modifiers),
        )

        new_rl = orig_left.right
        new_rr = copy.deepcopy(orig_right)
        new_right = _Node(
            kind="combinator",
            field=copy.copy(node.field),
            left=new_rl,
            right=new_rr,
            modifiers=list(orig_left.modifiers),
        )

        node.field = copy.copy(orig_left.field)
        node.left = new_left
        node.right = new_right
        return True

    # Case B: right child is a combinator: Z o (X o Y) -> (Z o X) o (Z o Y)
    if node.right.kind == "combinator" and node.right.left is not None and node.right.right is not None:
        orig_left = node.left
        orig_right = node.right

        new_ll = copy.deepcopy(orig_left)
        new_lr = orig_right.left
        new_left = _Node(
            kind="combinator",
            field=copy.copy(node.field),
            left=new_ll,
            right=new_lr,
            modifiers=list(orig_right.modifiers),
        )

        new_rl = copy.deepcopy(orig_left)
        new_rr = orig_right.right
        new_right = _Node(
            kind="combinator",
            field=copy.copy(node.field),
            left=new_rl,
            right=new_rr,
            modifiers=list(orig_right.modifiers),
        )

        node.field = copy.copy(orig_right.field)
        node.left = new_left
        node.right = new_right
        return True

    return False


def _apply_radius_to_sibling(node: _Node, bevel: Bevel) -> bool:
    """Apply the bevel radius to a sibling combinator node."""
    if node.kind != "combinator" or node.left is None or node.right is None:
        return False

    orig = node.field
    r = float(bevel.radius)
    bid = int(bevel.blend_id)

    if isinstance(orig, (UnionField, SmoothUnionField)):
        if r > 0.0:
            node.field = SmoothUnionField(orig.a, orig.b, k=r, blend_surface_id=bid)
        else:
            node.field = UnionField(orig.a, orig.b)
        return True

    if isinstance(orig, (IntersectionField, SmoothIntersectionField)):
        if r > 0.0:
            node.field = SmoothIntersectionField(orig.a, orig.b, k=r, blend_surface_id=bid)
        else:
            node.field = IntersectionField(orig.a, orig.b)
        return True

    return False


def apply_bevels(field_tree: SdfField, bevels: List[Bevel], max_passes: int = 10) -> SdfField:
    """Apply a list of edge bevels to an SdfField boolean tree via tree rewriting.

    Args:
        field_tree: Root SdfField tree
        bevels: List of Bevel specifications
        max_passes: Maximum rewriting iterations (default 10)

    Returns:
        Rewritten SdfField with smooth combinators at sibling pairs.
    """
    if not bevels or field_tree is None:
        return field_tree

    root = from_field(field_tree)
    available_ids = ids_under(root)

    # 1. Filter valid bevel pairs
    valid_bevels = [
        b for b in bevels
        if b.id_a in available_ids and b.id_b in available_ids and b.id_a != b.id_b
    ]
    if not valid_bevels:
        return field_tree

    initial_count = count_nodes(root)
    max_nodes = max(64, initial_count * 8)

    # 2. Iterative rewrite loop
    for pass_idx in range(max_passes):
        all_satisfied = True

        for b in valid_bevels:
            sib_node = find_sibling_node(root, b.id_a, b.id_b)
            if sib_node is None:
                all_satisfied = False
                rewrite_for_pair(root, b.id_a, b.id_b)

                if count_nodes(root) > max_nodes:
                    fld_logger.warn(
                        f"apply_bevels: Tree size limit exceeded ({count_nodes(root)} > {max_nodes}); aborting rewrite."
                    )
                    return to_field(root)

        if all_satisfied:
            break
    else:
        fld_logger.warn(
            f"apply_bevels: Reached iteration cap ({max_passes} passes); some bevels may be unsatisifed."
        )

    # 3. Apply radii at sibling nodes
    for b in valid_bevels:
        sib_node = find_sibling_node(root, b.id_a, b.id_b)
        if sib_node is not None:
            _apply_radius_to_sibling(sib_node, b)

    return to_field(root)
