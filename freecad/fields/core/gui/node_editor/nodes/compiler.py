# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.compiler

Graph compilation pipeline: CompileResult and compile_graph.
"""
from .base import SocketType
from .subgraph import _add_comment_dedup, topological_sort


class CompileResult(tuple):
    def __new__(cls, formula, comments, err, result_type=SocketType.VEC3, gates=None):
        obj = super().__new__(cls, (formula, comments, err))
        obj.formula = formula
        obj.comments = comments
        obj.err = err
        obj.result_type = result_type
        obj.gates = dict(gates) if gates else {}  # {"surf_n": param, "front": param}
        return obj



def compile_graph(graph_data):
    """
    Compiles a node graph dictionary into a formula string and custom parameters.

    graph_data:
        {
            "nodes": [
                {"id": "n1", "type": "part", "params": {...}},
                ...
            ],
            "wires": [
                {"from_node": "n1", "from_socket": "p", "to_node": "n2", "to_socket": "A"},
                ...
            ]
        }

    Returns:
        (formula_string, list_of_param_comments, error_message)
        Unpacks as a 3-tuple; also carries .result_type (SocketType).
    """
    from . import NODE_REGISTRY

    nodes = {n["id"]: n for n in graph_data.get("nodes", [])}
    wires = graph_data.get("wires", [])

    # Find the output node
    output_node_id = None
    for nid, n in nodes.items():
        if n.get("type") == "output":
            output_node_id = nid
            break

    if not output_node_id:
        return CompileResult("", [], "No Output node found in graph.")

    # Build adjacency: to_node -> list of (from_node, from_socket, to_socket)
    incoming = {nid: [] for nid in nodes}
    for w in wires:
        fn = w.get("from_node")
        tn = w.get("to_node")
        fs = w.get("from_socket")
        ts = w.get("to_socket")
        if fn in nodes and tn in nodes:
            incoming[tn].append((fn, fs, ts))

    order, has_cycle = topological_sort(output_node_id, incoming)
    if has_cycle:
        return CompileResult("", [], "Graph contains a cyclic dependency.")

    from .bounds import BoundWriter, const_param_names, default_bound, node_bounds
    w = BoundWriter(const_param_names(graph_data))
    bound_cache = {}

    # Node evaluation cache: nid -> {socket_name: expr_str}
    eval_cache = {}
    eval_types = {}
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

        # Collect input expressions
        input_exprs = {}
        # Fill defaults first
        for s in instance.inputs:
            input_exprs[s.name] = str(s.default_value)

        # Wire inputs override defaults
        wired = set()
        for (fn, fs, ts) in incoming.get(nid, []):
            if fn in eval_cache and fs in eval_cache[fn]:
                input_exprs[ts] = eval_cache[fn][fs]
                wired.add(ts)
                if fn in eval_types and fs in eval_types[fn]:
                    input_exprs[f"__{ts}_type__"] = eval_types[fn][fs]

        # Which sockets a wire actually reached. The defaults filled in above are
        # indistinguishable from a wired expression once they are in the dict, so
        # a node carrying a widget that mirrors an optional input had no way to
        # ask "is anything plugged in here?" -- Radial Projection tested
        # `"Radial" not in inputs`, which was never true, so its Radial Mode
        # checkbox was dead and the socket's 1.0 default forced radial on for
        # good (NE-018).
        input_exprs["__wired__"] = wired

        # Generate output expressions
        outputs = instance.generate_code(input_exprs, params)
        err_here = outputs.pop("__error__", None)
        if err_here and node_err is None:
            node_err = err_here
        eval_cache[nid] = outputs

        ins = {s.name: default_bound(s.default_value) for s in instance.inputs}
        for (fn, fs, ts) in incoming.get(nid, []):
            if fn in bound_cache and fs in bound_cache[fn]:
                ins[ts] = bound_cache[fn][fs]
        bound_cache[nid] = node_bounds(instance, ins, input_exprs, params, outputs, w)

        # Store output socket types
        out_types = {}
        out_defs = instance.get_outputs(params) if hasattr(instance, "get_outputs") else instance.outputs
        for s in out_defs:
            out_types[s.name] = s.socket_type
        eval_types[nid] = out_types

        # Collect any custom parameters
        comments = instance.get_param_comments(params)
        for c in comments:
            _add_comment_dedup(param_comments, c)

    # Also collect custom parameters from all custom_param nodes in graph (even if not yet wired to output)
    for nid, node_data in nodes.items():
        if node_data.get("type") == "custom_param":
            cls = NODE_REGISTRY.get("custom_param")
            if cls:
                c_comments = cls().get_param_comments(node_data.get("params", {}))
                for c in c_comments:
                    _add_comment_dedup(param_comments, c)

    output_res = eval_cache.get(output_node_id, {})
    final_expr = output_res.get("result", "vec3(0.0)")
    result_type = output_res.get("result_type", SocketType.VEC3)
    err = output_res.get("error") or node_err
    formula_body = final_expr.strip()

    bound_lines = list(w.lines)
    final = bound_cache.get(output_node_id, {}).get("result")
    if final is not None:
        bound_lines += [f"// @bound peak = {final.peak}", f"// @bound slope = {final.slope}"]
    head = param_comments + bound_lines
    full_formula = ("\n".join(head) + "\n" if head else "") + formula_body

    # If SurfaceNormalNode or FrontFaceNode's Enabled socket is wired from a
    # custom_param node, record that param's identifier in gates (NV-037).
    gates = {}
    for nid, node_data in nodes.items():
        ntype = node_data.get("type")
        target_builtin = None
        if ntype == "surface_normal":
            target_builtin = "surf_n"
        elif ntype == "front_face":
            target_builtin = "front"
        if target_builtin:
            for (fn, fs, ts) in incoming.get(nid, []):
                if ts == "Enabled" and fn in nodes:
                    src_node = nodes[fn]
                    if src_node.get("type") == "custom_param":
                        raw_name = src_node.get("params", {}).get("name", "")
                        from .base import glsl_param_identifier
                        ident = glsl_param_identifier(raw_name)
                        if ident:
                            gates[target_builtin] = ident

    return CompileResult(full_formula, param_comments, err, result_type, gates)

