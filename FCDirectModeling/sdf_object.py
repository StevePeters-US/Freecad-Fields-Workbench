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
    """Return the user's chosen voxel resolution (default 48 for final objects)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("MeshResolution", 48)

def set_mesh_algorithm(algo):
    FreeCAD.ParamGet(_PARAM_PATH).SetString("MeshAlgorithm", algo)

def set_mesh_resolution(res):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("MeshResolution", int(res))

def get_show_wireframe():
    """Return whether to show wireframe for SDF previews/objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("ShowWireframe", False)

def set_show_wireframe(show):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("ShowWireframe", bool(show))

def get_preview_resolution():
    """Return the user's chosen voxel resolution for live previews (default 15)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("PreviewResolution", 15)

def set_preview_resolution(res):
    """Set the user's chosen voxel resolution for live previews."""
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("PreviewResolution", int(res))


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
    from FCDirectModeling import sdf_logger

    sdf_logger.debug(f"mesh_sdf: Starting {algorithm} meshing at res {resolution}...")
    import time
    t0 = time.time()
    
    if algorithm == "marching_cubes":
        res = extract_mesh_numpy(sdf_fn, mn=bounds_min, mx=bounds_max,
                                   resolution=resolution, sharp=False)
    else:
        # surface_nets and dual_contouring both use QEF — sharp=True
        res = extract_mesh_numpy(sdf_fn, mn=bounds_min, mx=bounds_max,
                                   resolution=resolution, sharp=True)
    
    t1 = time.time()
    sdf_logger.debug(f"mesh_sdf: Meshing took {t1-t0:.3f}s. Result: {len(res[0])} verts, {len(res[1])} tris")
    return res


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
    def __init__(self, obj, sdf_type, params, sdf_op=None, child_names=None, placement=None):
        obj.Proxy = self
        self.is_preview = False
        
        # Use native FreeCAD properties for persistence and parametric updates
        if not hasattr(obj, "SDFType"):
            obj.addProperty("App::PropertyString", "SDFType", "SDF", "Type of SDF primitive")
        if not hasattr(obj, "SDFParams"):
            obj.addProperty("App::PropertyString", "SDFParams", "SDF", "JSON parameters for the SDF")
        if not hasattr(obj, "SDFOp"):
            obj.addProperty("App::PropertyString", "SDFOp", "SDF", "Boolean operation")
        if not hasattr(obj, "SDFChildren"):
            obj.addProperty("App::PropertyStringList", "SDFChildren", "SDF", "Names of child objects")

        obj.SDFType = sdf_type
        obj.SDFParams = json.dumps(params)
        obj.SDFOp = sdf_op or "none"
        obj.SDFChildren = child_names or []
        if placement:
            obj.Placement = placement

    def execute(self, fp):
        """Called by FreeCAD to recompute — we mesh and write fp.Mesh."""
        import FreeCAD
        FreeCAD.Console.PrintMessage(f"DEBUG: SDFObjectProxy.execute for {fp.Label} (is_preview={getattr(self, 'is_preview', False)})\n")
        if getattr(self, "is_preview", False):
            # During live preview dragging, base.py injects .Mesh directly.
            # Skip slow full-resolution recompute entirely.
            from FCDirectModeling import sdf_logger
            sdf_logger.debug("SDFObjectProxy: execute skipped for live preview")
            return
            
        try:
            import Mesh as MeshModule
            doc = fp.Document
            
            # Read from properties
            sdf_type = fp.SDFType
            params = json.loads(fp.SDFParams)
            sdf_op = fp.SDFOp
            child_names = fp.SDFChildren

            # Build SDF function
            if sdf_type == "boolean":
                children = [doc.getObjectsByLabel(n)[0] for n in child_names]
                child_sdfs = []
                for c in children:
                    # Prefer reading from properties if available
                    c_type = getattr(c, "SDFType", c.Proxy.sdf_type if hasattr(c.Proxy, "sdf_type") else "box")
                    c_params = json.loads(c.SDFParams) if hasattr(c, "SDFParams") else c.Proxy.params
                    bld = _SDF_BUILDERS.get(c_type)
                    if bld:
                        child_sdfs.append(bld(c_params))
                sdf_fn, (mn, mx) = _sdf_boolean(params, child_sdfs)
            else:
                bld = _SDF_BUILDERS.get(sdf_type)
                if bld is None:
                    from FCDirectModeling import sdf_logger
                    sdf_logger.error(f"SDFObject: unknown type '{sdf_type}'")
                    return
                sdf_fn, (mn, mx) = bld(params)

            from FCDirectModeling import sdf_logger
            import time
            
            sdf_logger.debug(f"SDFObjectProxy: Executing high-res mesh for {sdf_type}...")
            FreeCAD.Console.PrintMessage(f"DEBUG: mesh_sdf(type={sdf_type}, res={get_mesh_resolution()}, algo={get_mesh_algorithm()})\n")
            verts, tris = mesh_sdf(sdf_fn, mn, mx)
            FreeCAD.Console.PrintMessage(f"DEBUG: mesh_sdf result: {len(verts)} verts, {len(tris)} tris\n")
            
            if len(verts) == 0:
                sdf_logger.warning(f"SDFObject: mesher returned no geometry for {sdf_type}.")
                return

            sdf_logger.debug(f"SDFObjectProxy: Converting {len(tris)} triangles to Mesh facets...")
            t_conv_0 = time.time()
            facets = _to_mesh_facets(verts, tris)
            t_conv_1 = time.time()
            sdf_logger.debug(f"SDFObjectProxy: Conversion took {t_conv_1-t_conv_0:.3f}s")

            fp.Mesh = MeshModule.Mesh(facets)
            FreeCAD.Console.PrintMessage(f"DEBUG: Mesh assigned to {fp.Label}. VertCount={fp.Mesh.CountPoints}, FacetCount={fp.Mesh.CountFacets}\n")
            FreeCAD.Console.PrintMessage(f"DEBUG: Mesh Bounding Box: {fp.Mesh.BoundBox}\n")
            sdf_logger.debug(f"SDFObjectProxy: Mesh assigned to {fp.Label}")
            
            # Explicitly force a view update if in GUI mode
            if FreeCAD.GuiUp:
                vobj = getattr(fp, "ViewObject", None)
                if vobj:
                    vobj.Visibility = True
                    # Discover available modes
                    try:
                        modes = vobj.getPropertyEnumeration("DisplayMode")
                        FreeCAD.Console.PrintMessage(f"DEBUG: Available DisplayModes for {fp.Label}: {modes}\n")
                        if "Flat Lines" in modes:
                            vobj.DisplayMode = "Flat Lines"
                        elif "Shaded" in modes:
                            vobj.DisplayMode = "Shaded"
                        elif len(modes) > 0:
                            vobj.DisplayMode = modes[0]
                    except Exception as ve:
                        FreeCAD.Console.PrintMessage(f"DEBUG: Could not set DisplayMode: {ve}\n")
                    
                    vobj.update()
                    sdf_logger.debug(f"ViewObject updated for {fp.Label}")

        except Exception as e:
            from FCDirectModeling import sdf_logger
            sdf_logger.error(f"SDFObject.execute error: {e}")

    def __setstate__(self, state):
        pass

class SDFViewProvider:
    """ViewProvider for SDF objects. Ensures they use the Mesh rendering engine."""
    def __init__(self, vobj):
        vobj.Proxy = self
        
    def attach(self, vobj):
        self.Object = vobj.Object
        
    def getDisplayModes(self, vobj):
        return ["Flat Lines", "Shaded", "Wireframe", "Points"]
        
    def getDefaultDisplayMode(self):
        return "Flat Lines"
        
    def updateData(self, fp, prop):
        pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def create_sdf_object(name, sdf_type, params, sdf_op=None, child_names=None, is_preview=False, placement=None):
    """
    Create a Mesh::FeaturePython SDF object.
    Triggers an immediate recompute (execute) to populate obj.Mesh.
    """
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument()

    obj = doc.addObject("Mesh::FeaturePython", name)
    FreeCAD.Console.PrintMessage(f"DEBUG: create_sdf_object: created {obj.Name} for type {sdf_type}\n")
    proxy = SDFObjectProxy(obj, sdf_type, params, sdf_op, child_names, placement=placement)
    obj.Proxy.is_preview = is_preview

    # Style the view object 
    if FreeCAD.GuiUp:
        # Explicitly attach our ViewProvider
        SDFViewProvider(obj.ViewObject)
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            try:
                obj.ViewObject.ShapeColor = (0.65, 0.75, 0.90)
                # We'll set DisplayMode inside execute() for better robustness
                obj.ViewObject.Visibility = True
            except Exception:
                pass
    
    obj.touch()
    FreeCAD.Console.PrintMessage(f"DEBUG: create_sdf_object: calling doc.recompute() for {obj.Name}\n")
    doc.recompute()
    
    # Check if mesh survived recompute
    if hasattr(obj, "Mesh"):
        FreeCAD.Console.PrintMessage(f"DEBUG: POST-RECOMPUTE: {obj.Name}.Mesh has {obj.Mesh.CountPoints} points\n")
    else:
        FreeCAD.Console.PrintMessage(f"DEBUG: POST-RECOMPUTE: {obj.Name} HAS NO MESH PROPERTY!\n")

    FreeCAD.Console.PrintMessage(f"DEBUG: create_sdf_object: recompute done for {obj.Name}\n")
    
    import FreeCADGui
    if FreeCAD.GuiUp:
        try:
            # Select and Zoom to show if it's placed somewhere unexpected
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(obj)
            
            # Safer way to focus: viewSelection() method on ActiveView
            active_view = FreeCADGui.ActiveDocument.ActiveView
            if active_view:
                active_view.viewSelection()
                FreeCAD.Console.PrintMessage(f"DEBUG: View zoomed to selection for {obj.Name}\n")
            
            FreeCADGui.updateGui()
        except Exception as e:
            FreeCAD.Console.PrintMessage(f"DEBUG: Post-creation UI update failed: {e}\n")
    return obj
