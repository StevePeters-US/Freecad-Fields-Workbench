# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.subgraph

Subgraph and group node definitions and subgraph compilation.
"""
import re
from .base import BaseNode, SocketDef, SocketType


class SubgraphInputsNode(BaseNode):
    node_type = "subgraph_inputs"
    title = "Group Inputs"
    category = "Subgraph"
    is_subgraph_only = True

    def __init__(self, output_defs=None):
        super().__init__()
        self.inputs = []
        if output_defs:
            self.outputs = list(output_defs)
        else:
            self.outputs = [
                SocketDef("X", SocketType.FLOAT, 0.0),
                SocketDef("Y", SocketType.FLOAT, 0.0),
                SocketDef("Z", SocketType.FLOAT, 0.0),
                SocketDef("Radial", SocketType.BOOL, 1.0),
                SocketDef("Freq", SocketType.FLOAT, 1.0),
                SocketDef("Amp", SocketType.FLOAT, 1.0),
            ]

    def generate_code(self, input_exprs, params):
        res = {}
        for s in self.outputs:
            res[s.name] = str(s.name)
        return res

    def get_outputs(self, params):
        # Group Inputs' true socket set is whatever the subgraph's owner
        # (e.g. Radial Projection's U/V/UV template) declared in params --
        # `self.outputs` is only the generic fallback used when a node is
        # instantiated bare (`cls()`, with no params), which is how both the
        # canvas (node_items.py) and the compiler build every node. Without
        # this, the card always draws the generic default sockets and the
        # ones actually declared in params have nowhere to attach a wire.
        return _socket_defs_from_params(params, "outputs", self.outputs)


class SubgraphOutputsNode(BaseNode):
    node_type = "subgraph_outputs"
    title = "Group Outputs"
    category = "Subgraph"
    is_subgraph_only = True

    def __init__(self, input_defs=None):
        super().__init__()
        if input_defs:
            self.inputs = list(input_defs)
        else:
            self.inputs = [SocketDef("Out", SocketType.FLOAT, 0.0)]
        self.outputs = []

    def get_inputs(self, params):
        # See SubgraphInputsNode.get_outputs: the card and the compiler both
        # instantiate this class with `cls()`, so `self.inputs` never reflects
        # the U/V/UV (etc.) sockets a template declared in params -- only this
        # method sees those. Before this existed, a socket named anything but
        # the generic "Out" had no visual socket to wire to, so it could never
        # be marked connected; RadialProjectionNode/Rotate2DNode's `res.setdefault`
        # fallback then silently produced a plausible-looking formula from the
        # raw inputs instead, masking the fact nothing reached Group Outputs.
        return _socket_defs_from_params(params, "inputs", self.inputs)

    def generate_code(self, input_exprs, params):
        # Everything reaching this node is a group output, so the compiler's
        # `__`-prefixed side-channel keys have to be dropped here rather than
        # published under those names.
        res = {k: v for k, v in input_exprs.items() if not k.startswith("__")}
        for s in self.get_inputs(params):
            if s.name not in res:
                res[s.name] = str(s.default_value)
        return res

    def bound(self, ins, input_exprs, params, w):
        # A group output is its input, unchanged.
        return {s.name: ins[s.name] for s in self.get_inputs(params) if s.name in ins}


def _socket_defs_from_params(params, key, fallback):
    """Builds a SocketDef list from a params[key] socket-spec list, if present."""
    specs = params.get(key) if params else None
    if not specs:
        return list(fallback)
    defs = []
    for s in specs:
        if isinstance(s, dict):
            defs.append(SocketDef(s.get("name", "Out"), s.get("type", SocketType.FLOAT), s.get("default", 0.0)))
        else:
            defs.append(s)
    return defs


def _add_comment_dedup(clist, c):
    """Adds comment c to clist, deduplicating/updating by param name."""
    m = re.match(r'^\s*(?://|#)\s*@param\s+\w+\s+(\w+)', c)
    if m:
        pname = m.group(1)
        # Replace existing comment with same param name if already present
        for idx, existing in enumerate(clist):
            em = re.match(r'^\s*(?://|#)\s*@param\s+\w+\s+(\w+)', existing)
            if em and em.group(1) == pname:
                clist[idx] = c
                return
    if c not in clist:
        clist.append(c)


def topological_sort(start_node_id, incoming):
    """Traverse backwards from start_node_id along incoming edges.

    Returns:
        (order, has_cycle)
    """
    visited = set()
    visiting = set()
    order = []
    has_cycle = False

    def dfs(nid):
        nonlocal has_cycle
        if nid in visiting:
            has_cycle = True
            return
        if nid in visited:
            return
        visiting.add(nid)
        for (fn, _, _) in incoming.get(nid, []):
            dfs(fn)
        visiting.remove(nid)
        visited.add(nid)
        order.append(nid)

    dfs(start_node_id)
    return order, has_cycle


def compile_subgraph(subgraph_data, outer_input_exprs, outer_bounds=None, w=None):
    """
    Compiles an inner subgraph using outer input expressions.

    When `w` (a bounds.BoundWriter) is given, the subgraph also carries bounds,
    seeded from `outer_bounds` ({socket: Bound}), and the result dict gains
    "__bounds__": {output socket: Bound} (NV-043). Without it, nothing changes.
    Returns:
        (dict of {output_socket_name: expr_str}, list_of_param_comments)
    """
    from . import NODE_REGISTRY

    nodes = {n["id"]: n for n in subgraph_data.get("nodes", [])}
    wires = subgraph_data.get("wires", [])

    output_node_id = None
    for nid, n in nodes.items():
        if n.get("type") in ("subgraph_outputs", "output"):
            output_node_id = nid
            break

    if not output_node_id:
        return _trivial_subgraph(w), []

    incoming = {nid: [] for nid in nodes}
    for wire in wires:
        fn = wire.get("from_node")
        tn = wire.get("to_node")
        fs = wire.get("from_socket")
        ts = wire.get("to_socket")
        if fn in nodes and tn in nodes:
            incoming[tn].append((fn, fs, ts))

    order, has_cycle = topological_sort(output_node_id, incoming)
    if has_cycle:
        return _trivial_subgraph(w), []

    from .bounds import Bound, default_bound, node_bounds

    eval_cache = {}
    bound_cache = {}
    param_comments = []
    node_err = None

    for nid in order:
        node_data = nodes[nid]
        ntype = node_data.get("type")
        cls = NODE_REGISTRY.get(ntype)
        if not cls:
            continue
        instance = cls()
        params = node_data.get("params", {})

        if ntype == "subgraph_inputs":
            out_dict = {}
            for s in instance.get_outputs(params):
                out_dict[s.name] = outer_input_exprs.get(s.name, str(s.default_value))
            for k, v in outer_input_exprs.items():
                # `__`-prefixed keys are compiler side-channels (`__wired__`,
                # `__<socket>_type__`), not sockets -- passing them through would
                # publish them as group outputs.
                if k not in out_dict and not k.startswith("__"):
                    out_dict[k] = v
            eval_cache[nid] = out_dict
            if w is not None:
                ob = outer_bounds or {}
                bound_cache[nid] = {
                    s.name: ob.get(s.name) or default_bound(s.default_value)
                    for s in instance.get_outputs(params)}
                for k in out_dict:
                    if k not in bound_cache[nid] and k in ob:
                        bound_cache[nid][k] = ob[k]
            continue

        node_inputs = instance.get_inputs(params) if hasattr(instance, "get_inputs") else instance.inputs
        input_exprs = {}
        for s in node_inputs:
            input_exprs[s.name] = str(s.default_value)

        wired = set()
        for (fn, fs, ts) in incoming.get(nid, []):
            if fn in eval_cache and fs in eval_cache[fn]:
                input_exprs[ts] = eval_cache[fn][fs]
                wired.add(ts)

        # See compiler.compile_graph: nodes whose widget mirrors an optional
        # input need to tell a default apart from a wire (NE-018).
        input_exprs["__wired__"] = wired

        outputs = instance.generate_code(input_exprs, params)
        err_here = outputs.pop("__error__", None)
        if err_here and node_err is None:
            node_err = err_here
        if w is not None:
            ins = {s.name: default_bound(s.default_value) for s in node_inputs}
            for (fn, fs, ts) in incoming.get(nid, []):
                if fn in bound_cache and fs in bound_cache[fn]:
                    ins[ts] = bound_cache[fn][fs]
            nb = node_bounds(instance, ins, input_exprs, params, outputs, w)
            for k, b in ins.items():      # mirror the code passthrough below
                if k not in nb and k not in outputs:
                    nb[k] = b
            bound_cache[nid] = nb
        for k, v in input_exprs.items():
            if k not in outputs and not k.startswith("__"):
                outputs[k] = v
        eval_cache[nid] = outputs

        comments = instance.get_param_comments(params)
        for c in comments:
            _add_comment_dedup(param_comments, c)

    for nid, node_data in nodes.items():
        if node_data.get("type") == "custom_param":
            cls = NODE_REGISTRY.get("custom_param")
            if cls:
                c_comments = cls().get_param_comments(node_data.get("params", {}))
                for c in c_comments:
                    _add_comment_dedup(param_comments, c)

    out_node_outputs = eval_cache.get(output_node_id, {})
    res = dict(out_node_outputs) if out_node_outputs else {"Out": "0.0"}
    if node_err:
        res["__error__"] = node_err
    if w is not None:
        res["__bounds__"] = dict(bound_cache.get(output_node_id, {}))
    return res, param_comments


def _trivial_subgraph(w):
    """The early-return result: Out is 0.0 (with its zero bound when `w` is given)."""
    res = {"Out": "0.0"}
    if w is not None:
        from .bounds import Bound
        res["__bounds__"] = {"Out": Bound("0.0", "0.0", "0.0")}
    return res

