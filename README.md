# FreeCAD Fields Workbench

> **Support the Project:** If you find this workbench useful, consider supporting its development on [Patreon](https://www.patreon.com/cw/AttackPotato).

> [!WARNING]
> **Active Development:** This workbench is an experimental work in progress. Tools, underlying data structures, and file storage formats are subject to change. **Backwards compatibility is not guaranteed** across updates.

The **Fields Workbench** is a companion workbench for FreeCAD that provides implicit modeling, sculpting, and volumetric deformation tools using **Signed Distance Fields (SDF)**.

Designed to accompany FreeCAD's parametric toolsets, it provides an alternative way to model organic shapes, soft transitions, and volumetric textures without relying on boundary face stitching or traditional B-Rep boolean operations.

---

## 1. What Are Signed Distance Fields (SDF)?

Most CAD modeling tools represent an object by its boundary surface—such as stitched parametric faces or polygonal meshes.

An **SDF** instead represents a shape as a continuous spatial field. For any point in 3D space, the field returns the distance to the nearest surface of the object:

- **Inside the solid:** Distance values are **negative**.
- **On the surface boundary:** Distance is **zero**.
- **Outside in empty space:** Distance values are **positive**.

<!-- Place screenshot of an SDF shape with distance contours here -->
<!-- ![Signed Distance Field Concept](docs/images/sdf_concept.png) -->

### Practical Characteristics:

- **Continuous Blending:** Intersecting shapes can blend together smoothly with an adjustable transition radius.
- **Robust Booleans:** Union, cut, and intersection operations evaluate the field directly, avoiding topological failures or degenerate trimmed edges.
- **Volumetric Modifiers:** Procedural noise, space deformations, lattices, and digital sculpting can be applied directly to the field.
- **Companion to FreeCAD:** Shapes created in Fields can be converted to standard meshes or sliced into 2D profile curves for use with standard FreeCAD workbenches.

---

## 2. Integrated into FreeCAD

Fields objects integrate directly into the FreeCAD environment:

<!-- Place FreeCAD interface screenshot showing the 3D viewport, Tree View, and Modifier Stack here -->
<!-- ![FreeCAD Fields Workbench Overview](docs/images/freecad_fields_overview.png) -->

- **Document Objects:** Fields objects live in the FreeCAD Tree View as standard `Part::FeaturePython` objects. Their parameters and placements can be adjusted in the Property view.
- **Modifier Stack:** Deformations, noise layers, sculpt data, and booleans are organized in a non-destructive stack. Modifiers can be toggled, reordered, or edited at any time.
- **Interactive Viewport Controls:** Edit objects using on-screen 3D gizmos, cage handles, and a dynamic workplane.
- **Two-Color Grouping:** Press **`Q`** to toggle an object between **Group 1 (Additive)** and **Group 2 (Subtractive)** for quick visual booleans.

### Viewport Display vs. Internal Evaluation

Evaluating continuous distance fields across 3D space is computationally demanding. To maintain usable viewport frame rates while preserving modeling accuracy, Fields separates display from exact calculation:

1. **Real-Time Viewport Render:** A lightweight GPU preview rendered directly in FreeCAD's 3D viewport for interactive navigation, handle dragging, and previewing edits.
2. **Exact Internal Model:** A high-precision representation evaluated on demand when an operation requires exact geometric values—such as generating a mesh or calculating 2D slice contours.

---

## 3. Major Tool Families

The workbench organizes modeling tools into several families:

### 1. Primitives
<!-- ![Primitives](docs/images/tools_primitives.png) -->
Basic analytical 3D shapes:
- **Box**, **Sphere**, **Cylinder**, and **Torus**.

### 2. Procedural Noise *(Node Graph in Progress)*
<!-- ![Procedural Noise & Node Graph](docs/images/tools_noise.png) -->
- A **visual node graph editor** in progress 

### 3. Heightmap Displacement
<!-- ![Heightmap Displacement](docs/images/tools_heightmap.png) -->
Import grayscale images or elevation data to create 3D relief surfaces and embossed textures with adjustable depth and smoothing.

### 4. Spatial Deformers
<!-- ![Spatial Deformers](docs/images/tools_deformers.png) -->
Non-destructively alter coordinate space across the field:
- **Twist:** Rotational torsion along a chosen axis.
- **Bend:** Curvature along a radius.
- **Array:** Linear or circular repetition.

### 5. Lattice and Cage Deform
<!-- ![Lattice and Cage Deform](docs/images/tools_cage.png) -->
Enclose a shape in a 3D cage or lattice grid. Moving the cage vertices deforms the enclosed volume, useful for shaping organic silhouettes and ergonomic contours.

### 6. Voxel Sculpt
<!-- ![Voxel Sculpt](docs/images/tools_sculpt.png) -->
Sculpt volumetric details directly on objects using voxel layers and interactive 3D brushes
- Adjustable radius, strength, and falloff profiles via the Brush Editor panel.

### 7. SDF to Mesh
<!-- ![SDF to Mesh](docs/images/tools_sdf_to_mesh.png) -->
Extract a standard polygon mesh from the implicit field (via Surface Nets, Marching Cubes, or Dual Contouring) for export (STL/OBJ) or downstream operations in FreeCAD.

### 8. Slice SDF
<!-- ![SDF Slice Toolpath Contours](docs/images/tools_sdf_slice.png) -->
Cut an SDF with a plane to extract 2D cross-section contour curves as FreeCAD `BSplineCurve` objects.
---

## 4. Interactive Modeling & Keyboard Reference

### Dynamic Workplane
All geometry creation snaps to a dynamic workplane:
- **Hovering over a face:** Aligns normal to the hovered surface (green tint).
- **Hovering in empty space:** Falls back to the camera-facing viewport plane (blue tint).
- **First Click:** Locks the workplane in place.
- **Second Click:** Drag to size

### Keyboard Shortcuts

**Object Selection / Navigation**
| Key | Action |
|---|---|
| `Tab` / `E` | Enter edit mode for the selected object |
| `Q` | Toggle Group 1 (Additive) ↔ Group 2 (Subtractive) |
| `D` | Open Fields context menu (or right-click) |
| `Ctrl + Space` | Toggle viewport maximize |

**While a Tool is Active**
| Key | Action |
|---|---|
| `Esc` | Cancel tool and restore original state |
| `Enter` | Commit and finish tool |
| `Tab` | Exit edit mode / close tool |
| `X` / `Y` / `Z` | Constrain movement to world X, Y, or Z axis |
| `Shift + X/Y/Z` | Constrain movement to perpendicular plane (`Shift+Z` → XY plane) |
| `D` | Tool options menu |

**Modal Transforms (Blender-style)**
Press `G`, `R`, or `S` in edit mode. Middle-mouse view navigation remains live throughout:
| Key | Action |
|---|---|
| `G` | Grab (translate / move) |
| `R` | Rotate |
| `S` | Scale |
| `X` / `Y` / `Z` | Lock to axis |
| `Shift + X/Y/Z` | Lock to perpendicular plane |
| `0`–`9`, `.`, `-` | Type exact numeric value (mm, degrees, factor) |
| `Backspace` | Delete last typed digit |
| `Enter` or Left-Click | Apply transform |
| `Esc` or Right-Click | Cancel transform and restore original position |

---

## 5. Installation

1. Clone or symlink this repository into your FreeCAD `Mod` directory:
   - **Linux**: `~/.FreeCAD/Mod/Fields`
   - **macOS**: `~/Library/Application Support/FreeCAD/Mod/Fields`
   - **Windows**: `%APPDATA%\FreeCAD\Mod\Fields`
2. Restart FreeCAD.
3. Select **Fields** from the workbench dropdown menu.

---

## Attributions

- **Inigo Quilez** ([iquilezles.org](https://iquilezles.org/)) — Foundational mathematical formulations for distance primitives, GLSL raymarching algorithms, and smooth boolean operators (`smoothMin`/`smoothMax`).
