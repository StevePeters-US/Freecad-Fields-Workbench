# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Modifier-chain discovery and editing helpers.

A modifier chain is a linked list of document objects connected by their
`Source` App::PropertyLink, ending at a base object (primitive/cage/curve).
The *tail* is the most-downstream object; only the tail is visible, because
CommandFldModifierBase.Activated hides each source as it wraps it.
"""
from freecad.fields.core import fld_logger

_CAGE_REFUSAL = ("fld_modifier_stack: a deform cage cannot be reordered or removed "
                 "from the stack -- its extrusion state is bound to its source")


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
        # a plain Part::Box, a mesh, a curve. Handing it back as the base made
        # ModifierStackPanel.refresh's `if base is None` branch unreachable (obj was
        # already known non-None two lines above it), so the panel showed
        # "Base: PlainBox" with `+ Add Modifier` live, and Twist built PlainBox_Twist
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


def is_deform_cage(obj):
    """True when obj is a deform cage, which may not take part in link surgery."""
    return type(getattr(obj, "Proxy", None)).__name__ == "FldDeformCageProxy"


def _refuse_cage(*objs):
    """True (and logs why) when any of objs is a deform cage.

    Pass every object whose `Source` the caller is about to rewrite, not just the
    two being swapped. Each op rebinds a third link -- move-up re-points the
    consumer of `mod`, move-down the consumer of `B`, remove the consumer of
    `mod` -- and a cage sitting on that link has its own extrusion state
    (`ExtrudeRingSizes`/`ExtrudeBaseRings`/...) silently re-pointed at geometry it
    was never built against.
    """
    for obj in objs:
        if is_deform_cage(obj):
            fld_logger.warn(_CAGE_REFUSAL)
            return True
    return False


def _set_visible(obj, visible):
    """Set obj's viewport visibility, tolerating a headless or view-less object."""
    if obj is None:
        return
    vo = getattr(obj, "ViewObject", None)
    if vo is not None:
        vo.Visibility = visible


def _retail(old_tail, new_tail):
    """Move the 'only the tail is visible' invariant from old_tail to new_tail.

    Reordering the last modifier changes which object the chain ends on, and
    FldViewProvider.onChanged gates each field on ViewObject.Visibility -- so
    without this the new tail stays hidden and the demoted modifier stays drawn,
    leaving the viewport showing the intermediate result and nothing else.
    """
    if old_tail is new_tail:
        return
    _set_visible(new_tail, True)
    _set_visible(old_tail, False)


def _do_move_up(mod):
    """Move modifier up one slot (toward base). Returns True on success, False otherwise."""
    if _refuse_cage(mod):
        return False
    A = getattr(mod, "Source", None)
    if A is None or not is_modifier(A):
        return False
    consumer = _consumer_of(mod)
    if _refuse_cage(A, consumer):
        return False

    mod.Source = getattr(A, "Source", None)
    A.Source = mod
    if consumer is not None:
        consumer.Source = A
    else:
        # mod was the tail; A is now.
        _retail(mod, A)
    return True


def _do_move_down(mod):
    """Move modifier down one slot (toward tail). Returns True on success, False otherwise."""
    if _refuse_cage(mod):
        return False
    B = _consumer_of(mod)
    if B is None or not is_modifier(B):
        return False
    consumer_of_B = _consumer_of(B)
    if _refuse_cage(B, consumer_of_B):
        return False

    B.Source = getattr(mod, "Source", None)
    mod.Source = B
    if consumer_of_B is not None:
        consumer_of_B.Source = mod
    else:
        # B was the tail; mod is now.
        _retail(B, mod)
    return True


def _do_remove(mod):
    """Remove modifier from stack, splicing surrounding links. Returns True on success."""
    if _refuse_cage(mod):
        return False
    consumer = _consumer_of(mod)
    if _refuse_cage(consumer):
        return False
    source = getattr(mod, "Source", None)
    if consumer is not None:
        consumer.Source = source
    else:
        # Removing the tail: un-hide source
        _set_visible(source, True)

    doc = getattr(mod, "Document", None)
    if doc is not None and hasattr(doc, "removeObject"):
        doc.removeObject(mod.Name)
    return True


def _deferred(mod, title, op):
    """Run op(mod) inside an undo transaction, off the current event callback."""
    from PySide import QtCore

    doc = getattr(mod, "Document", None)
    if doc is None:
        return

    def _apply():
        if hasattr(doc, "openTransaction"):
            doc.openTransaction(title)
        try:
            op(mod)
            if hasattr(doc, "commitTransaction"):
                doc.commitTransaction()
        except Exception as e:
            if hasattr(doc, "abortTransaction"):
                doc.abortTransaction()
            fld_logger.error(f"fld_modifier_stack op failed: {e}")
        if hasattr(doc, "recompute"):
            doc.recompute()

    QtCore.QTimer.singleShot(0, _apply)


def move_up(mod):
    """Transaction-wrapped, QTimer-deferred move up."""
    _deferred(mod, "Fields modifier reorder", _do_move_up)


def move_down(mod):
    """Transaction-wrapped, QTimer-deferred move down."""
    _deferred(mod, "Fields modifier reorder", _do_move_down)


def remove(mod):
    """Transaction-wrapped, QTimer-deferred remove/splice."""
    _deferred(mod, "Fields modifier remove", _do_remove)
