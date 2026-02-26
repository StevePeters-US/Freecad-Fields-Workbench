"""
FCDirectModeling/sdf_object.py

Core SDF document object.  Uses Mesh::FeaturePython so FreeCAD renders it
natively — no Coin3D, no pivy.

Factory
-------
    obj = create_sdf_object("Box", sdf_type="box", params={"bounds_min":[…], "bounds_max":[…]})

The Proxy.execute() reads sdf_type/params, evaluates the SDF, meshes it with the
algorithm chosen in DM Settings, and writes the result to obj.Mesh.
"""

import json
import FreeCAD

# ─────────────────────────────────────────────────────────────────────────────
# DM Settings helpers
# ─────────────────────────────────────────────────────────────────────────────

_PARAM_PATH = "User parameter:FCDirectModeling"

def get_mesh_algorithm():
    """Return the user's chosen meshing algorithm: 'surface_nets' | 'dual_contouring' | 'marching_cubes'."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetString("MeshAlgorithm", "surface_nets")

def get_mesh_resolution():
    """Return the user's chosen voxel resolution (default 32)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("MeshResolution", 32)

def set_mesh_algorithm(algo):
    FreeCAD.ParamGet(_PARAM_PATH).SetString("MeshAlgorithm", algo)

def set_mesh_resolution(res):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("MeshResolution", int(res))

def get_show_wireframe():
    """Return whether to show wireframe for SDF previews/objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("ShowWireframe", False)

def set_show_wireframe(show):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("ShowWireframe", bool(show))


# ─────────────────────────────────────────────────────────────────────────────
# SDF evaluator functions — return numpy-callable SDFs
# ─────────────────────────────────────────────────────────────────────────────

import numpy as np

def _sdf_box(params):
    mn = np.array(params["bounds_min"], dtype=float)
    mx = np.array(params["bounds_max"], dtype=float)
    center  = (mn + mx) / 2.0
    extents = (mx - mn) / 2.0
    def sdf(X, Y, Z):
        qx = np.abs(X - center[0]) - extents[0]
        qy = np.abs(Y - center[1]) - extents[1]
        qz = np.abs(Z - center[2]) - extents[2]
        return (np.sqrt(np.maximum(qx,0)**2 + np.maximum(qy,0)**2 + np.maximum(qz,0)**2)
                + np.minimum(np.maximum(qx, np.maximum(qy, qz)), 0))
    bbox = (mn - 0.5, mx + 0.5)
    return sdf, bbox

def _sdf_sphere(params):
    cx, cy, cz = params["center"]
    r = float(params["radius"])
    def sdf(X, Y, Z):
        return np.sqrt((X-cx)**2 + (Y-cy)**2 + (Z-cz)**2) - r
    m = r * 1.05 + 0.5
    bbox = (np.array([cx-m, cy-m, cz-m]), np.array([cx+m, cy+m, cz+m]))
    return sdf, bbox

def _sdf_cone(params):
    cx, cy, cz = params["center"]
    r = float(abs(params["radius"]))
    h = float(params["height"])
    h_abs = abs(h)
    def sdf(X, Y, Z):
        Xl = X - cx; Yl = Y - cy; Zl = Z - cz
        if h < 0:
            Zl = -Zl
        # Standard cone: apex at Z=h_abs, base radius r at Z=0
        dist_xy = np.sqrt(Xl**2 + Yl**2)
        k = r / max(h_abs, 1e-6)
        dist_surface = dist_xy - r + k * np.clip(Zl, 0, h_abs)
        dist_bottom  = -Zl
        dist_top     = Zl - h_abs
        return np.maximum(dist_surface, np.maximum(dist_bottom, dist_top))
    m = max(r, h_abs) * 0.05 + 1.0
    bbox = (np.array([cx-r-m, cy-r-m, cz-m]), np.array([cx+r+m, cy+r+m, cz+h_abs+m]))
    return sdf, bbox

def _sdf_torus(params):
    cx, cy, cz = params["center"]
    R = float(params["major_r"])
    r = float(params["minor_r"])
    def sdf(X, Y, Z):
        q = np.sqrt((X-cx)**2 + (Y-cy)**2) - R
        return np.sqrt(q**2 + (Z-cz)**2) - r
    m = r * 0.05 + 0.5
    total = R + r
    bbox = (np.array([cx-total-m, cy-total-m, cz-r-m]),
            np.array([cx+total+m, cy+total+m, cz+r+m]))
    return sdf, bbox

def _sdf_boolean(params, children_sdfs):
    """Compose child SDFs with the given operation."""
    op = params.get("op", "union")
    sdf_a, bbox_a = children_sdfs[0]
    sdf_b, bbox_b = children_sdfs[1]

    if op == "union":
        def sdf(X, Y, Z): return np.minimum(sdf_a(X,Y,Z), sdf_b(X,Y,Z))
    elif op == "cut":
        def sdf(X, Y, Z): return np.maximum(sdf_a(X,Y,Z), -sdf_b(X,Y,Z))
    elif op == "intersect":
        def sdf(X, Y, Z): return np.maximum(sdf_a(X,Y,Z), sdf_b(X,Y,Z))
    else:
        def sdf(X, Y, Z): return np.minimum(sdf_a(X,Y,Z), sdf_b(X,Y,Z))

    mn = np.minimum(bbox_a[0], bbox_b[0])
    mx = np.maximum(bbox_a[1], bbox_b[1])
    return sdf, (mn, mx)

_SDF_BUILDERS = {
    "box":    _sdf_box,
    "sphere": _sdf_sphere,
    "cone":   _sdf_cone,
    "torus":  _sdf_torus,
}


# ─────────────────────────────────────────────────────────────────────────────
# Meshing dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def mesh_sdf(sdf_fn, bounds_min, bounds_max, resolution=None, algorithm=None):
    """
    Evaluate sdf_fn on a voxel grid and return (verts, tris).
    algorithm: 'surface_nets' | 'dual_contouring' | 'marching_cubes'
    """
    if resolution is None:
        resolution = get_mesh_resolution()
    if algorithm is None:
        algorithm = get_mesh_algorithm()

    from .sdf_mesher import extract_mesh_numpy

    if algorithm == "marching_cubes":
        return extract_mesh_numpy(sdf_fn, mn=bounds_min, mx=bounds_max,
                                  resolution=resolution, sharp=False)
    else:
        # surface_nets and dual_contouring both use QEF — sharp=True
        return extract_mesh_numpy(sdf_fn, mn=bounds_min, mx=bounds_max,
                                  resolution=resolution, sharp=True)


def _to_mesh_facets(verts, tris):
    """
    Convert (N,3) verts and (M,3) tris to a flat list of facets
    [(v1,v2,v3), ...] where each vertex is a plain (x,y,z) tuple.
    This is what Mesh.Mesh() expects.
    """
    facets = []
    for t in tris:
        v0 = (float(verts[t[0],0]), float(verts[t[0],1]), float(verts[t[0],2]))
        v1 = (float(verts[t[1],0]), float(verts[t[1],1]), float(verts[t[1],2]))
        v2 = (float(verts[t[2],0]), float(verts[t[2],1]), float(verts[t[2],2]))
        facets.append((v0, v1, v2))
    return facets


# ─────────────────────────────────────────────────────────────────────────────
# SDFObjectProxy — used with Mesh::FeaturePython
# ─────────────────────────────────────────────────────────────────────────────

class SDFObjectProxy:
    def __init__(self, obj, sdf_type, params, sdf_op=None, child_names=None):
        obj.Proxy = self
        self.sdf_type    = sdf_type
        self.params      = params
        self.sdf_op      = sdf_op      # None or "union"/"cut"/"intersect"
        self.child_names = child_names or []  # Names of child SDFObjects for booleans

    def execute(self, fp):
        """Called by FreeCAD to recompute — we mesh and write fp.Mesh."""
        if getattr(self, "is_preview", False):
            # During live preview dragging, base.py injects .Mesh directly.
            # Skip slow full-resolution recompute entirely.
            FreeCAD.Console.PrintMessage("DEBUG: proxy.execute skipped for live preview\n")
            return
            
        FreeCAD.Console.PrintMessage(f"DEBUG: execute (full res) for {self.sdf_type}\n")
        try:
            import Mesh as MeshModule
            doc = fp.Document

            # Build SDF function
            if self.sdf_type == "boolean":
                children = [doc.getObjectsByLabel(n)[0] for n in self.child_names]
                child_sdfs = []
                for c in children:
                    bld = _SDF_BUILDERS.get(c.Proxy.sdf_type)
                    if bld:
                        child_sdfs.append(bld(c.Proxy.params))
                sdf_fn, (mn, mx) = _sdf_boolean(self.params, child_sdfs)
            else:
                bld = _SDF_BUILDERS.get(self.sdf_type)
                if bld is None:
                    FreeCAD.Console.PrintError(f"SDFObject: unknown type '{self.sdf_type}'\n")
                    return
                sdf_fn, (mn, mx) = bld(self.params)

            verts, tris = mesh_sdf(sdf_fn, mn, mx)
            if len(verts) == 0:
                FreeCAD.Console.PrintWarning(f"SDFObject: mesher returned no geometry.\n")
                return

            facets = _to_mesh_facets(verts, tris)
            fp.Mesh = MeshModule.Mesh(facets)

        except Exception as e:
            FreeCAD.Console.PrintError(f"SDFObject.execute error: {e}\n")

    def __getstate__(self):
        return {
            "sdf_type":    self.sdf_type,
            "params":      self.params,
            "sdf_op":      self.sdf_op,
            "child_names": self.child_names,
        }

    def __setstate__(self, state):
        self.sdf_type    = state.get("sdf_type", "box")
        self.params      = state.get("params", {})
        self.sdf_op      = state.get("sdf_op")
        self.child_names = state.get("child_names", [])


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def create_sdf_object(name, sdf_type, params, sdf_op=None, child_names=None, is_preview=False):
    """
    Create a Mesh::FeaturePython SDF object.
    Triggers an immediate recompute (execute) to populate obj.Mesh.
    """
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument()

    obj = doc.addObject("Mesh::FeaturePython", name)
    proxy = SDFObjectProxy(obj, sdf_type, params, sdf_op, child_names)
    obj.Proxy.is_preview = is_preview

    # Style the view object (FreeCAD's built-in Mesh ViewProvider)
    if hasattr(obj, "ViewObject") and obj.ViewObject:
        try:
            obj.ViewObject.ShapeColor = (0.65, 0.75, 0.90)
            if get_show_wireframe():
                obj.ViewObject.DisplayMode = "Flat Lines"
            else:
                obj.ViewObject.DisplayMode = "Shaded"
        except Exception:
            pass

    doc.recompute()
    return obj
