# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.patterns

Graph nodes wrapping the SdfNoiseNode kernels. A kernel's GLSL entry point is
already `float f(vec3 q, float amp, float freq)`, so the adapter is pure plumbing:
no kernel maths lives here, and a new kernel needs no new node class.
"""
from .base import BaseNode, SocketDef, SocketType


class PatternNode(BaseNode):
    """Base for kernel-backed pattern nodes. Subclasses set `kernel`."""
    category = "Patterns"
    kernel = None          # an SdfNoiseNode subclass

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Position", SocketType.VEC3, (0.0, 0.0, 0.0),
                      doc="Sample position, object-local"),
            SocketDef("Amp", SocketType.FLOAT, 1.0, doc="Peak output"),
            SocketDef("Freq", SocketType.FLOAT, 0.1, doc="Cycles per mm"),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT, doc="Scalar pattern value")]

    def generate_code(self, input_exprs, params):
        # An unwired VEC3/FLOAT socket's pre-filled "default" is a Python
        # tuple/float repr, not GLSL text (`(0.0, 0.0, 0.0)`, missing the
        # `vec3` constructor) -- `input_exprs.get(name, fallback)` can never
        # reach `fallback` since compile_graph always pre-fills the key
        # (bug_node_socket_default_hides_wiredness). Falling back to the raw
        # `p`/`amp`/`freq` locals -- always in scope in the emitted helper --
        # only when the socket is actually unwired is what keeps an unwired
        # Position from emitting a malformed call.
        wired = input_exprs.get("__wired__", set())
        q = input_exprs["Position"] if "Position" in wired else "p"
        amp = input_exprs["Amp"] if "Amp" in wired else "amp"
        freq = input_exprs["Freq"] if "Freq" in wired else "freq"
        return {"Out": f"{self.kernel.main_fn_name}({q}, {amp}, {freq})"}

    @classmethod
    def glsl_helpers(cls):
        """(name, code) pairs this node's kernel needs registered. Consumed by
        SdfNoiseField.to_glsl."""
        return tuple(cls.kernel.dep_helpers) + (
            (cls.kernel.main_fn_name, cls.kernel.main_fn_code),
        )


from freecad.fields.core.sdf.sdf.noise_nodes import NOISE_NODES

PATTERN_NODES = []
for _name, _kernel in NOISE_NODES.items():
    _cls = type(
        f"{_kernel.__name__}PatternNode",
        (PatternNode,),
        {"node_type": f"pattern_{_kernel.main_fn_name}",
         "title": _name,
         "doc": _kernel.display,
         "kernel": _kernel},
    )
    PATTERN_NODES.append(_cls)
