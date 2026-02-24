"""
Curve Extraction from SDF Objects

Pipeline: point cloud → segmentation → surface fitting → intersection curves
→ SDF-based trimming

Produces analytical curves (lines, circles) as numpy arrays of 3D points
suitable for rendering as Coin3D line sets or converting to Part.Edge objects.
"""

import numpy as np
from FCDirectModeling import surface_fitting


def extract_curves(sdf_object, resolution=32, edge_threshold=0.2,
                   normal_similarity=0.9, samples_per_curve=64):
    """
    Top-level entry point: extract feature curves from an SDF object.
    
    This now uses Direct Mathematical Ridge Tracing instead of point-cloud 
    segmentation and KDTree surface fitting.
    
    Returns:
        curves: list of (N, 3) numpy arrays, each a polyline of 3D points
        patch_fits: list of (type, params) for each fitted patch (empty now)
    """
    import FreeCAD
    
    FreeCAD.Console.PrintMessage("CurveExtraction: Checking for explicit edges...\n")
    
    if hasattr(sdf_object, 'get_edges'):
        explicit_edges = sdf_object.get_edges()
        if explicit_edges and len(explicit_edges) > 0:
            FreeCAD.Console.PrintMessage(f"CurveExtraction: Found {len(explicit_edges)} explicit analytical edges.\n")
            return explicit_edges, []
            
    FreeCAD.Console.PrintMessage("CurveExtraction: Tracing mathematical edges as fallback...\n")
    
    # 1. Directly trace the analytical curves from the SDF math
    # We use 200 seed points to ensure we find all disconnected features
    edge_pts = sdf_object.trace_edges(num_seeds=200, variance_threshold=edge_threshold)
    
    # The new trace_edges currently returns a dense numpy array of JUST the 
    # exact edge points mathematically evaluated. 
    # For now, to keep the UI drawing it as a "curve", we just return this single
    # block as one "curve". The renderer SoPointSet or SoLineSet can handle it.
    
    if edge_pts is None or len(edge_pts) < 2:
        FreeCAD.Console.PrintWarning("CurveExtraction: No edges found by tracer.\n")
        return [], []
        
    FreeCAD.Console.PrintMessage(f"CurveExtraction: Extracted {len(edge_pts)} edge points.\n")
    
    # Return as a single curve array for now. 
    # Because `trace_edges` returns a 2D numpy array of points (N, 3), 
    # we wrap it in a list to match the old `curves` return format: [ (N, 3) ]
    return [edge_pts], []
    
    # 4. Fit primitives to each patch
    patch_fits = []
    for i, patch in enumerate(patches):
        patch_pts = points[patch]
        patch_norms = normals[patch]
        fit_type, (error, params) = surface_fitting.best_fit(patch_pts, patch_norms)
        patch_fits.append((fit_type, params, error))
        FreeCAD.Console.PrintMessage(
            f"  Patch {i}: {fit_type} (N={len(patch)}, err={error:.6f})\n"
        )
    
    # 5. Merge coplanar patches to avoid spurious edges between same-face fragments
    merged_fits, merge_map = _merge_coplanar_patches(patch_fits, patches, points)
    
    # Rebuild adjacency with merged patch IDs
    merged_adjacency = {}
    for (pi, pj), shared_edge_pts in adjacency.items():
        mi = merge_map[pi]
        mj = merge_map[pj]
        if mi == mj:
            continue  # Same merged patch — no edge
        key = (min(mi, mj), max(mi, mj))
        if key not in merged_adjacency:
            merged_adjacency[key] = []
        merged_adjacency[key].extend(shared_edge_pts)
    
    FreeCAD.Console.PrintMessage(
        f"CurveExtraction: After merge: {len(merged_fits)} patches, "
        f"{len(merged_adjacency)} adj pairs.\n"
    )
    
    # 6. Compute intersection curves between adjacent patches
    curves = []
    
    for (pi, pj), shared_edge_pts in merged_adjacency.items():
        if pi >= len(merged_fits) or pj >= len(merged_fits):
            continue
            
        fit_a = merged_fits[pi]
        fit_b = merged_fits[pj]
        
        edge_pts = points[shared_edge_pts] if len(shared_edge_pts) > 0 else None
        
        curve = intersect_surfaces(
            fit_a, fit_b, 
            bound_min, bound_max,
            edge_hint_points=edge_pts,
            n_samples=samples_per_curve
        )
        
        if curve is not None and len(curve) >= 2:
            # CRITICAL: Trim curve to SDF surface
            trimmed = _trim_curve_to_sdf(curve, sdf_object, tolerance=step * 0.5)
            if trimmed is not None and len(trimmed) >= 2:
                curves.append(trimmed)
    
    FreeCAD.Console.PrintMessage(f"CurveExtraction: Extracted {len(curves)} curves.\n")
    return curves, merged_fits


# ==== Legacy Point-Cloud KDTree logic removed. ====
# The Direct Mathematical Ridge Tracing algorithm bypasses point cloud 
# patch-fitting entirely by directly crawling the SDF mathematical surface
# along points of maximum principal curvature.
