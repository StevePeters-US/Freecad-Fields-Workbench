# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""SdfEdgeFilletField -- exact constant-radius fillets/chamfers on B-Rep-derived fields.

Split out of sdf_brep_converter.py (CR-060) into sdf/, alongside this directory's other
primitive SdfField subclasses, per that directory's one-class-per-file convention. Fully
self-contained -- no dependency on the B-Rep decomposition or primitive-detection halves
of the former converter module; only `convert_csg_to_sdf` (in `sdf_csg_convert.py`)
constructs it.
"""
import numpy as np
import FreeCAD

from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_field import SdfField


class SdfEdgeFilletField(SdfField):
    """Exact constant-radius fillets on convex edges where two planar faces meet.

    The rounded-corner term is exact for a planar/planar convex edge: with
    ``u = dA + r`` and ``v = dB + r`` measured as *outward* distances from the two
    adjacent faces, ``sqrt(u*u + v*v) - r`` is the rolling-ball surface, and the
    ``u > 0 && v > 0`` guard is continuous because the term degenerates to ``dB``
    (resp. ``dA``) on each boundary, which ``max`` already dominates.

    Adjacency is read off the topology -- the two faces that share an edge with the
    fillet cylinder -- not guessed from edge lengths. Normals are oriented outward by
    probing the solid. A cylinder whose two neighbours are not both planar is
    **skipped with a warning** rather than approximated, so an unsupported fillet is
    visibly absent instead of silently the wrong shape.
    """

    def __init__(self, base_field: SdfField, shape=None, fillet_faces=None,
                 chamfer_faces=None, placement=None):
        super().__init__()
        self.base_field = base_field
        self.placement = placement or getattr(base_field, "placement", None)
        self.fillet_data = []
        self.chamfer_data = []
        self.skipped = 0

        if shape is not None and fillet_faces:
            for f in fillet_faces:
                rec = self._build_fillet(shape, f)
                if rec is None:
                    self.skipped += 1
                else:
                    self.fillet_data.append(rec)

        if shape is not None and chamfer_faces:
            for f in chamfer_faces:
                rec = self._build_chamfer(shape, f)
                if rec is None:
                    self.skipped += 1
                else:
                    self.chamfer_data.append(rec)

        if self.skipped:
            fld_logger.warn(
                "SdfEdgeFilletField: %d edge blend(s) skipped -- adjacent faces are not "
                "both planar. Those edges stay sharp." % self.skipped)

    # -- construction helpers -------------------------------------------------

    @staticmethod
    def _adjacent_faces(shape, face):
        """Faces sharing at least one edge with `face`, by exact topological identity."""
        out = []
        for other in shape.Faces:
            if other.isSame(face):
                continue
            if any(e1.isSame(e2) for e1 in face.Edges for e2 in other.Edges):
                out.append(other)
        return out

    @staticmethod
    def _outward_plane(shape, face):
        """(normal, point) for a planar face, with the normal oriented out of the solid."""
        n = face.normalAt(0.0, 0.0)
        q = face.CenterOfMass
        n = FreeCAD.Vector(n.x, n.y, n.z)
        ln = n.Length
        if ln < 1e-9:
            return None
        n = FreeCAD.Vector(n.x / ln, n.y / ln, n.z / ln)
        probe = FreeCAD.Vector(q.x + n.x * 1e-3, q.y + n.y * 1e-3, q.z + n.z * 1e-3)
        try:
            if shape.isInside(probe, 1e-7, True):
                n = FreeCAD.Vector(-n.x, -n.y, -n.z)
        except Exception:
            return None
        return (np.array([n.x, n.y, n.z], dtype=np.float64),
                np.array([q.x, q.y, q.z], dtype=np.float64))

    def _build_fillet(self, shape, face):
        surf = getattr(face, "Surface", None)
        if surf is None or "Cylinder" not in type(surf).__name__:
            return None
        r = float(surf.Radius)
        c = surf.Center
        ax = surf.Axis
        axis = np.array([ax.x, ax.y, ax.z], dtype=np.float64)
        la = np.linalg.norm(axis)
        if la < 1e-9:
            return None
        axis /= la
        centre = np.array([c.x, c.y, c.z], dtype=np.float64)

        # A blend is tangent to its two neighbours: the plane runs parallel to the
        # cylinder axis and stands exactly r away from it. The other faces a fillet
        # touches (the ones it merely abuts at each end) fail both tests.
        tangent = []
        for a in self._adjacent_faces(shape, face):
            if "Plane" not in type(getattr(a, "Surface", None)).__name__:
                continue
            pl = self._outward_plane(shape, a)
            if pl is None:
                continue
            n, q = pl
            if abs(float(np.dot(n, axis))) > 1e-6:
                continue
            if abs(abs(float(np.dot(centre - q, n))) - r) > 1e-6:
                continue
            tangent.append(pl)

        if len(tangent) != 2:
            return None
        (n1, p1), (n2, p2) = tangent
        return {"r": r, "n1": n1, "p1": p1, "n2": n2, "p2": p2}

    def _build_chamfer(self, shape, face):
        surf = getattr(face, "Surface", None)
        if surf is None or "Plane" not in type(surf).__name__:
            return None
        pl = self._outward_plane(shape, face)
        if pl is None:
            return None
        return {"n": pl[0], "p": pl[1]}

    # -- evaluation -----------------------------------------------------------

    @staticmethod
    def _corner(dA, dB, r):
        """Exact rounded-corner distance; equals max(dA, dB) outside the blend region."""
        u = dA + r
        v = dB + r
        blend = np.sqrt(np.maximum(u, 0.0) ** 2 + np.maximum(v, 0.0) ** 2) - r
        return np.where((u > 0.0) & (v > 0.0), blend, np.maximum(dA, dB))

    def _apply(self, pts, d):
        for fil in self.fillet_data:
            dA = pts @ fil["n1"] - float(fil["p1"] @ fil["n1"])
            dB = pts @ fil["n2"] - float(fil["p2"] @ fil["n2"])
            d = np.maximum(d, self._corner(dA, dB, fil["r"]))
        for ch in self.chamfer_data:
            d = np.maximum(d, pts @ ch["n"] - float(ch["p"] @ ch["n"]))
        return d

    def evaluate(self, point) -> float:
        d = self.base_field.evaluate(point)
        p = np.array([[point.x, point.y, point.z]], dtype=np.float64)
        return float(self._apply(p, np.array([float(d)]))[0])

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return self._apply(np.asarray(points, dtype=np.float64),
                           self.base_field.evaluate_grid(points))

    def lipschitz(self) -> float:
        return max(1.0, float(self.base_field.lipschitz()))

    def bounding_box(self):
        return self.base_field.bounding_box()

    # -- GPU ------------------------------------------------------------------

    def _glsl_parts(self, ctx, point_var="q", dist_var="d"):
        """Statements folding every blend into `dist_var`, reading `point_var`.

        Shared by to_glsl and to_glsl_sample so the plain-float and the FldSample
        contract cannot drift apart -- the drift is invisible headlessly and costs
        the whole fillet in the viewport.
        """
        parts = []
        for fil in self.fillet_data:
            n1 = ctx.uniform("vec3", list(fil["n1"]))
            n2 = ctx.uniform("vec3", list(fil["n2"]))
            o1 = ctx.uniform("float", float(fil["p1"] @ fil["n1"]))
            o2 = ctx.uniform("float", float(fil["p2"] @ fil["n2"]))
            r = ctx.uniform("float", float(fil["r"]))
            parts.append(f"""
            {{
                float dA = dot({point_var}, {n1}) - {o1};
                float dB = dot({point_var}, {n2}) - {o2};
                float u = dA + {r};
                float v = dB + {r};
                float c = (u > 0.0 && v > 0.0)
                        ? sqrt(u * u + v * v) - {r}
                        : max(dA, dB);
                {dist_var} = max({dist_var}, c);
            }}""")
        for ch in self.chamfer_data:
            n = ctx.uniform("vec3", list(ch["n"]))
            o = ctx.uniform("float", float(ch["p"] @ ch["n"]))
            parts.append(f"""
            {{ {dist_var} = max({dist_var}, dot({point_var}, {n}) - {o}); }}""")
        return "".join(parts)

    def to_glsl(self, ctx, point_var="p") -> str:
        base_expr = self.base_field.to_glsl(ctx, "q")
        func_name = ctx.get_unique_name("sd_edge_fillet")
        ctx.add_custom_helper(func_name, f"""
        float {func_name}(vec3 q) {{
            float d = {base_expr};
            {self._glsl_parts(ctx)}
            return d;
        }}
        """)
        return f"{func_name}({point_var})"

    def to_glsl_sample(self, ctx, point_var="p"):
        """A fillet re-shapes existing faces, so the base sample's surface id rides
        through unchanged -- but the DISTANCE has to carry the blend. This is the
        method `compile_field_to_glsl` calls; delegating it wholesale to the base
        field draws the unfilleted solid on the GPU while every CPU test passes.
        """
        base_expr = self.base_field.to_glsl_sample(ctx, "q")
        func_name = ctx.get_unique_name("sd_edge_fillet_sample")
        ctx.add_custom_helper(func_name, f"""
        FldSample {func_name}(vec3 q) {{
            FldSample s = {base_expr};
            float d = s.d;
            {self._glsl_parts(ctx)}
            s.d = d;
            return s;
        }}
        """)
        return f"{func_name}({point_var})"


