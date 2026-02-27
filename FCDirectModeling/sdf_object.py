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
    from FCDirectModeling import dm_logger

    dm_logger.debug(f"mesh_sdf: Starting {algorithm} meshing at res {resolution}...")
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
    dm_logger.debug(f"mesh_sdf: Meshing took {t1-t0:.3f}s. Result: {len(res[0])} verts, {len(res[1])} tris")
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
# SDFObjectProxy — used with Part-Mesh hierarchy
# ─────────────────────────────────────────────────────────────────────────────

class SDFObjectProxy:
    def __init__(self, obj, sdf_type, params, sdf_op=None, children=None, placement=None):
        obj.Proxy = self
        self.is_preview = False
        
        # Use native FreeCAD properties for persistence and parametric updates
        if not hasattr(obj, "SDFType"):
            obj.addProperty("App::PropertyString", "SDFType", "SDF", "Type of SDF primitive")
        if not hasattr(obj, "SDFParams"):
            obj.addProperty("App::PropertyString", "SDFParams", "SDF", "JSON parameters (legacy/backup)")
        if not hasattr(obj, "SDFOp"):
            obj.addProperty("App::PropertyString", "SDFOp", "SDF", "Boolean operation")
        if not hasattr(obj, "SDFChildren"):
            obj.addProperty("App::PropertyLinkList", "SDFChildren", "SDF", "Child SDF objects")
        if not hasattr(obj, "SDFMesh"):
            obj.addProperty("App::PropertyLink", "SDFMesh", "SDF", "Child high-res mesh")

        obj.SDFType = sdf_type
        obj.SDFParams = json.dumps(params)
        obj.SDFOp = sdf_op or "none"
        obj.SDFChildren = children or []
        
        from FCDirectModeling import dm_logger
        dm_logger.debug(f"SDFObjectProxy.__init__: type={sdf_type}, params={params}, has_placement={placement is not None}")
        
        # Handle Placement and Center Offset
        # Standard primitives: we center them locally at [0,0,0] for build_sdf logic.
        # But for Box, we'll use corner-alignment [0,0,0] -> [L,W,H].
        if sdf_type in ["box", "sphere", "cone", "torus"] and not placement:
            # Default center at origin unless we calculate it from click-drag bounds
            center_v = FreeCAD.Vector(0,0,0)
            
            if sdf_type == "box":
                if "bounds_min" in params and "bounds_max" in params:
                    mn, mx = params["bounds_min"], params["bounds_max"]
                    # If no global placement provided, we use the average as the base
                    center_v = FreeCAD.Vector((mn[0]+mx[0])/2, (mn[1]+mx[1])/2, (mn[2]+mx[2])/2)
            elif "center" in params:
                cv = params["center"]
                center_v = FreeCAD.Vector(cv[0], cv[1], cv[2])

            obj.Placement.Base = center_v
            dm_logger.debug(f"SDFObjectProxy: Default placement set to: {obj.Placement.Base}")

        if placement:
            obj.Placement = placement
            dm_logger.debug(f"SDFObjectProxy: Final obj.Placement set to: {obj.Placement.Base}")

        # Add typed properties for parametric editing
        if sdf_type == "box":
            for p in ["Length", "Width", "Height"]:
                if not hasattr(obj, p): obj.addProperty("App::PropertyFloat", p, "Box", p)
            if "bounds_min" in params and "bounds_max" in params:
                mn, mx = params["bounds_min"], params["bounds_max"]
                obj.Length = abs(mx[0] - mn[0])
                obj.Width  = abs(mx[1] - mn[1])
                obj.Height = abs(mx[2] - mn[2])
            else:
                obj.Length = params.get("length", 10.0)
                obj.Width  = params.get("width", 10.0)
                obj.Height = params.get("height", 10.0)
                
        elif sdf_type == "sphere":
            if not hasattr(obj, "Radius"): obj.addProperty("App::PropertyFloat", "Radius", "Sphere", "Radius")
            obj.Radius = params.get("radius", 5.0)
            
        elif sdf_type == "cone":
            if not hasattr(obj, "Radius"): obj.addProperty("App::PropertyFloat", "Radius", "Cone", "Radius")
            if not hasattr(obj, "Height"): obj.addProperty("App::PropertyFloat", "Height", "Cone", "Height")
            obj.Radius = params.get("radius", 5.0)
            obj.Height = params.get("height", 10.0)
            
        elif sdf_type == "torus":
            if not hasattr(obj, "MajorRadius"): obj.addProperty("App::PropertyFloat", "MajorRadius", "Torus", "Major Radius")
            if not hasattr(obj, "MinorRadius"): obj.addProperty("App::PropertyFloat", "MinorRadius", "Torus", "Minor Radius")
            obj.MajorRadius = params.get("major_r", 10.0)
            obj.MinorRadius = params.get("minor_r", 2.0)

    def build_sdf(self, fp):
        """Reconstruct (sdf_fn, (bounds_min, bounds_max)) from stored properties."""
        sdf_type = fp.SDFType
        
        if sdf_type == "boolean":
            params = json.loads(fp.SDFParams)
            child_sdfs = []
            for child in fp.SDFChildren:
                if hasattr(child, "Proxy") and hasattr(child.Proxy, "build_sdf"):
                    child_sdfs.append(child.Proxy.build_sdf(child))
                else:
                    from FCDirectModeling import dm_logger
                    dm_logger.warn(f"build_sdf: child '{child.Label}' is not an SDF object")
            
            # Ensure 'op' is available for _sdf_boolean
            if "op" not in params:
                params["op"] = fp.SDFOp
                
            return _sdf_boolean(params, child_sdfs)
        
        # Primitives: Prefer typed properties, fallback to SDFParams JSON
        if sdf_type == "box":
            l, w, h = abs(fp.Length), abs(fp.Width), abs(fp.Height)
            # Corner-aligned at local origin [0,0,0]
            # This ensures it scales from the Placement point (the click point)
            params = {"bounds_min": [0.0, 0.0, 0.0], "bounds_max": [l, w, h]}
            return _sdf_box(params)
            
        elif sdf_type == "sphere":
            r = fp.Radius
            params = {"center": [0, 0, 0], "radius": r}
            return _sdf_sphere(params)
            
        elif sdf_type == "cone":
            r, h = fp.Radius, fp.Height
            params = {"center": [0, 0, 0], "radius": r, "height": h}
            return _sdf_cone(params)
            
        elif sdf_type == "torus":
            R, r = fp.MajorRadius, fp.MinorRadius
            params = {"center": [0, 0, 0], "major_r": R, "minor_r": r}
            return _sdf_torus(params)

        # Catch-all for unknown or non-linkable types
        params = json.loads(fp.SDFParams)
        bld = _SDF_BUILDERS.get(sdf_type)
        if bld is None:
            raise ValueError(f"Unknown SDF type '{sdf_type}'")
        return bld(params)

    def execute(self, fp):
        """Called by FreeCAD to recompute — we mesh and write to the child Mesh object."""
        if getattr(self, "is_preview", False):
            # During live preview dragging, base.py injects .Mesh directly.
            # Skip slow full-resolution recompute entirely.
            from FCDirectModeling import dm_logger
            dm_logger.debug("SDFObjectProxy: execute skipped for live preview")
            return
            
        try:
            import Mesh as MeshModule
            import Part
            from FCDirectModeling import dm_logger
            import time

            # Ensure the object has a shape (even if empty) to satisfy Part::Feature
            if not hasattr(fp, "Shape") or fp.Shape.isNull():
                fp.Shape = Part.Shape()

            # Build SDF function using the new reusable method
            sdf_fn, (mn, mx) = self.build_sdf(fp)
            
            dm_logger.debug(f"SDFObjectProxy: Executing high-res mesh for {fp.SDFType}...")
            verts, tris = mesh_sdf(sdf_fn, mn, mx)
            
            if len(verts) == 0:
                dm_logger.warn(f"SDFObject: mesher returned no geometry for {fp.SDFType}.")
                return

            dm_logger.debug(f"SDFObjectProxy: Converting {len(tris)} triangles to Mesh facets...")
            t_conv_0 = time.time()
            facets = _to_mesh_facets(verts, tris)
            t_conv_1 = time.time()
            dm_logger.debug(f"SDFObjectProxy: Conversion took {t_conv_1-t_conv_0:.3f}s")

            # Get linked child mesh surface
            mesh_obj = getattr(fp, "SDFMesh", None)
            
            if not mesh_obj:
                # Fallback search in OutList
                for child in fp.OutList:
                    if child.isDerivedFrom("Mesh::Feature"):
                        mesh_obj = child
                        break
            
            if not mesh_obj:
                dm_logger.warn(f"SDFObjectProxy: No child mesh found for {fp.Label}")
                return

            mesh_obj.Mesh = MeshModule.Mesh(facets)
            dm_logger.debug(f"SDFObjectProxy: Mesh assigned to {mesh_obj.Label}")
            
            # Sync mesh placement with the container's placement
            # This ensures the local geometry [0,0,0] coincides with the object's world position.
            mesh_obj.Placement = fp.Placement
            
            # Explicitly force a view update if in GUI mode
            if FreeCAD.GuiUp:
                vobj = getattr(mesh_obj, "ViewObject", None)
                if vobj:
                    vobj.Visibility = True
                    # Discover available modes
                    try:
                        modes = vobj.getPropertyEnumeration("DisplayMode")
                        if "Flat Lines" in modes:
                            vobj.DisplayMode = "Flat Lines"
                        elif "Shaded" in modes:
                            vobj.DisplayMode = "Shaded"
                        elif len(modes) > 0:
                            vobj.DisplayMode = modes[0]
                    except Exception as ve:
                        _ = ve # Silently fail
                    
                    vobj.update()
                    dm_logger.debug(f"ViewObject updated for {mesh_obj.Label}")

        except Exception as e:
            from FCDirectModeling import dm_logger
            dm_logger.error(f"SDFObject.execute error: {e}")

    def __setstate__(self, state):
        pass

class SDFViewProvider:
    """ViewProvider for SDF objects. Shows an orange part icon."""
    def __init__(self, vobj):
        vobj.Proxy = self
        
    def attach(self, vobj):
        self.Object = vobj.Object
        
    def getDisplayModes(self, vobj):
        return ["Standard"]
        
    def getDefaultDisplayMode(self):
        return "Standard"
        
    def updateData(self, fp, prop):
        pass

    def getIcon(self):
        # Orange stairstep icon (Part)
        return """
            /* XPM */
            static char * orange_part_xpm[] = {
            "16 16 3 1",
            " 	c None",
            ".	c #FFA500",
            "+	c #000000",
            "                ",
            "  ++++++        ",
            "  +....++       ",
            "  +.....+       ",
            "  +..+..+       ",
            "  +..+..+       ",
            "  +..++++++     ",
            "  +..+....++    ",
            "  +..+.....+    ",
            "  +..+..+..+    ",
            "  ++++..++++++  ",
            "     +..+....++ ",
            "     +..+.....+ ",
            "     +..+..+..+ ",
            "     ++++++++++ ",
            "                "};
            """

    def claimChildren(self):
        # Allow child mesh and sub-SDFs to appear in the tree hierarchy
        return self.Object.OutList

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def create_sdf_object(name, sdf_type, params, sdf_op=None, children=None, is_preview=False, placement=None):
    """
    Create an SDF container (App::DocumentObjectGroupPython) with a child Mesh::Feature.
    The container holds the SDF definition (type, params, children) and the mesh
    is the visual output nested underneath in the tree.
    """
    from FCDirectModeling import dm_logger
    
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument()

    try:
        dm_logger.debug(f"create_sdf_object: name={name}, type={sdf_type}, has_placement={placement is not None}")
        # Part::FeaturePython supports Python Proxy + Placement + child objects via claimChildren
        obj = doc.addObject("Part::FeaturePython", name)
        proxy = SDFObjectProxy(obj, sdf_type, params, sdf_op, children, placement=placement)
        obj.Proxy.is_preview = is_preview

        # Style the view object 
        if FreeCAD.GuiUp:
            SDFViewProvider(obj.ViewObject)
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                try:
                    obj.ViewObject.Visibility = True
                except Exception:
                    pass
        
        # Create child mesh surface immediately (link it via property for nesting)
        mesh_obj = doc.addObject("Mesh::Feature", f"SDF_{obj.Name}_Mesh")
        mesh_obj.Label = f"{obj.Label} Mesh"
        obj.SDFMesh = mesh_obj
        
        if FreeCAD.GuiUp and hasattr(mesh_obj, "ViewObject"):
            mesh_obj.ViewObject.ShapeColor = (0.65, 0.75, 0.90)

        dm_logger.debug(f"create_sdf_object: Created {name} ({sdf_type}), triggering recompute...")
        obj.touch()
        doc.recompute()
        
        import FreeCADGui
        if FreeCAD.GuiUp:
            try:
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(obj)
                
                active_view = FreeCADGui.ActiveDocument.ActiveView
                if active_view:
                    active_view.viewSelection()
                
                FreeCADGui.updateGui()
            except Exception:
                pass
        
        dm_logger.debug(f"create_sdf_object: {name} created successfully")
        return obj
        
    except Exception as e:
        dm_logger.error(f"create_sdf_object FAILED: {e}")
        import traceback
        dm_logger.error(traceback.format_exc())
        return None

