# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Modifier-chain discovery helpers.

A modifier chain is a linked list of document objects connected by their
`Source` App::PropertyLink, ending at a base object (primitive/cage/curve).
The *tail* is the most-downstream object; only the tail is visible, because
CommandFldModifierBase.Activated hides each source as it wraps it.
"""
from freecad.fields.core import fld_logger


def is_modifier(obj):
    """True when obj is a Fields modifier (has a Source link and a proxy _build_field)."""
    return (hasattr(obj, "Source")
            and hasattr(getattr(obj, "Proxy", None), "_build_field"))


def is_sdf_object(obj):
    """True when obj can carry a modifier stack.

    One spelling, reused by cmd_modifier_base.require_sdf_selection. Primitives are
    FldObjectProxy and modifiers are FldModifierProxyBase -- two unrelated classes with
    no common ancestor -- but every row of a stack carries ShapeType "sdf", so the
    property is the test and the proxy class is not. See
    memory/bug_modifier_edit_gate_four_spellings.md, where this gate was found spelled
    four different ways at four sites.
    """
    return getattr(obj, "ShapeType", None) == "sdf"


def _key(obj):
    """Loop-guard identity for obj: its Name when it has one, else its id().

    Keying on Name alone let an unnamed object through the guard entirely, which
    on a malformed chain is an infinite loop rather than a warning.
    """
    return getattr(obj, "Name", None) or id(obj)


def get_chain(obj):
    """Return (base_obj, [modifier objs base->tail]) for the chain containing obj.

    Walks Source links upstream from obj, then consumer links downstream
    (document scan for objects whose Source is the current tail).
    Cycle-guarded; on a broken link returns what was reachable.
    """
    if obj is None:
        return None, []
    upstream_seen = set()
    cur = obj
    while is_modifier(cur) and getattr(cur, "Source", None) is not None:
        key = _key(cur)
        if key in upstream_seen:
            fld_logger.warn(f"fld_modifier_stack: Source cycle at {getattr(cur, 'Name', cur)}")
            break
        upstream_seen.add(key)
        cur = cur.Source
    base = cur
    if not is_sdf_object(base):
        # MS-011: the upstream walk ended on something that cannot carry a stack --
        # a plain Part::Box, a mesh, a curve. Handing it back as the base let a
        # caller's `if base is None` early-out go unreached (obj was already known
        # non-None two lines above it), so a Twist built on it as PlainBox_Twist
        # -- a modifier whose get_sdf_field() returns None, with no error anywhere.
        return None, []
    chain = []
    tail = base
    downstream_seen = {_key(base)}
    while True:
        nxt = _consumer_of(tail)
        if nxt is None or _key(nxt) in downstream_seen:
            break
        chain.append(nxt)
        downstream_seen.add(_key(nxt))
        tail = nxt
    return base, chain


def stack_target(obj):
    """The object a *new* modifier should wrap: the tail of obj's chain.

    A modifier goes on top of the stack, whichever row of the chain was selected.
    Without this, selecting the wrong row silently builds a *branch* instead of a
    taller stack: `claimChildren` returns `OutList`, so the tree nests every source
    under its consumer and the base of an existing chain is the easy thing to click
    by accident. Wrapping it gives two modifiers with the same `Source`, both of them
    chain tails, both visible, and the renderer unions them -- so the new modifier
    looks like it did nothing.

    Returns obj itself for a bare object with no modifiers on it.
    """
    if obj is None:
        return None
    base, chain = get_chain(obj)
    return chain[-1] if chain else base


def _consumer_of(obj):
    """The unique modifier whose Source is obj, or None (warns when several)."""
    if obj is None:
        return None
    doc = getattr(obj, "Document", None)
    if doc is None or not hasattr(doc, "Objects"):
        return None
    hits = [o for o in doc.Objects if is_modifier(o) and getattr(o, "Source", None) is obj]
    if len(hits) > 1:
        name = getattr(obj, "Name", str(obj))
        fld_logger.warn(f"fld_modifier_stack: {name} has {len(hits)} consumers; using first")
    return hits[0] if hits else None
