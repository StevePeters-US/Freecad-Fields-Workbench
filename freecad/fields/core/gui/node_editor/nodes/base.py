# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.base

Socket types, socket definitions, GLSL parameter identifier coercion, and BaseNode.
"""
import re


# Names `SdfNoiseField.to_glsl` already declares in the helper it wraps the
# formula body in -- its arguments and the locals it always emits. A custom
# parameter that took one of these would be a duplicate declaration and the
# whole scene shader would fail to compile, not just this field. `r`, `d` and
# `radial` are deliberately absent: to_glsl only emits those locals when no
# custom param claims the name, so overriding them is a supported thing to do.
_GLSL_TAKEN_NAMES = frozenset((
    "p", "amp", "freq", "u_ax", "w_ax", "dir", "x", "y", "z",
    "to_world_rot", "to_world_base", "to_local_inv",   # ToWorldLocalNode (transform.py)
))

_GLSL_KEYWORDS = frozenset((
    "attribute", "bool", "break", "buffer", "case", "const", "continue", "default",
    "discard", "do", "double", "else", "false", "float", "for", "highp", "if", "in",
    "inout", "int", "invariant", "layout", "lowp", "mat2", "mat3", "mat4", "mediump",
    "out", "precision", "return", "sampler2D", "sampler3D", "struct", "switch",
    "true", "uint", "uniform", "varying", "vec2", "vec3", "vec4", "void", "while",
))


def glsl_param_identifier(name):
    """Coerce a user-typed parameter name into a legal, unclaimed GLSL identifier.

    The name is typed in the node editor and then pasted verbatim into BOTH the
    formula body and its `// @param` comment. Anything that is not a bare
    identifier desynchronises the two: `parse_custom_params` fails to read the
    comment (its `(\w+)` name group stops at the first illegal character, so the
    default field no longer matches a number), no uniform is declared, and the
    body still references the name -- `error C1503: undefined variable`. That
    kills the scan program for the ENTIRE scene volume, so one mistyped name
    blanks every field in the document, not just this one. Sanitising here, at
    the single point where the name becomes GLSL text, is what keeps the body and
    the comment spelling the same thing.
    """
    ident = re.sub(r"[^A-Za-z0-9_]+", "_", str(name).strip()).strip("_")
    if not ident:
        return "param"          # the node's own default; an unnamed param is not an error
    if ident[0].isdigit():
        ident = "p_" + ident
    if ident in _GLSL_KEYWORDS or ident in _GLSL_TAKEN_NAMES:
        ident += "_"
    return ident


class SocketType:
    FLOAT = "float"   # 1D Scalar
    VEC2 = "vec2"     # 2D Vector
    VEC3 = "vec3"     # 3D Vector
    BOOL = "bool"     # Boolean Toggle


SOCKET_TYPE_NAMES = {
    SocketType.FLOAT: "1D Float",
    SocketType.VEC2: "2D Vector",
    SocketType.VEC3: "3D Vector",
    SocketType.BOOL: "Boolean",
}

SOCKET_TYPE_SHORT_LABELS = {
    SocketType.FLOAT: "1D",
    SocketType.VEC2: "2D",
    SocketType.VEC3: "3D",
    SocketType.BOOL: "Bool",
}


class SocketDef:
    def __init__(self, name, socket_type=SocketType.FLOAT, default_value=0.0, doc=""):
        self.name = name
        self.socket_type = socket_type
        self.default_value = default_value
        self.doc = doc

    def to_dict(self):
        return {
            "name": self.name,
            "type": self.socket_type,
            "default": self.default_value,
        }


class BaseNode:
    """Base definition for a node type in the graph editor."""
    node_type = "base"
    title = "Base Node"
    category = "General"
    doc = ""   # shown as the node card's tooltip; keep to a few short lines

    def __init__(self):
        self.inputs = []
        self.outputs = []
        self.default_params = {}

    def generate_code(self, input_exprs, params):
        """Returns dict of {output_socket_name: glsl_expression_str}."""
        return {}

    def get_param_comments(self, params):
        """Returns list of // @param comment strings."""
        return []
