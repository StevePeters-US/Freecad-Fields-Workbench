# Fields Workbench

[![Patreon](https://img.shields.io/badge/Support-Patreon-F96854?style=flat&logo=patreon)](https://www.patreon.com/cw/AttackPotato)

> [!WARNING]
> **Active Development:** This workbench is an experimental work in progress. Tools, underlying data structures, and file storage formats are subject to change. **Backwards compatibility is not guaranteed** across updates.

The **Fields Workbench** is an implicit modeling workbench for FreeCAD, built on Signed Distance Fields (SDF). Traditional CAD tools work on boundary representations (B-Rep) and polygon meshes. Fields evaluates shapes as continuous 3D volumetric fields instead. This allows real-time volumetric booleans, procedural surface displacement with noise, and spatial deformations directly in FreeCAD, with tools to extract native 2D curves or polygon meshes for downstream workflows.

![A vase built from a sphere, cylinders and a torus joined with a smooth blend, with a carved opening](docs/images/hero.png)

## Tools

### Point

Places a point in the view. Shortcut: `P`.

![Six points placed at different positions in 3D space](docs/images/tools_point.png)

### Curve

Creates a curve through the points you click in the view. Supports 2D and 3D curves.

![A 3D curve passing through five control points in space](docs/images/tools_curve.png)

### Work Plane

Places a permanent work plane object. New geometry is created on a visible work plane. Shortcut: `W`.

![A work plane placed on the surface of a sphere](docs/images/tools_work_plane.png)

### Fill Curve

Creates an SDF face that fills a closed curve. Select the closed curve first. Shortcut: `Ctrl+F`.

![A closed curve filled with a flat SDF face on a work plane](docs/images/tools_fill_curve.png)

### Extrude

Extrudes an SDF face into a solid. Select the face, then drag in the view to set the depth.

![The filled face extruded upward into a solid](docs/images/tools_extrude.png)

### Primitives

Creates box, sphere, cylinder, and torus SDF objects with interactive dimensions and positioning. Objects can be combined with Add, Subtract, and Intersection booleans. These are evaluated on the distance field, so they don't rely on edge-intersection solvers, and a join can be given a smooth blend radius.

![Analytical SDF primitives: box, sphere, cylinder, and torus](docs/images/tools_primitives.png)

### Noise

Pushes the surface of an SDF object in and out along a direction you set with the gizmo. Choose from Waves, Square, Sawtooth, Perlin and Voronoi patterns, in 2D or 3D. The 2D patterns affect the side of the object facing the direction arrow. The 3D patterns wrap around every face. Amplitude and wavelength update in real time, and custom patterns can be built in the node graph editor.

![The vase with wave noise adding horizontal ribs around its body](docs/images/tools_noise.png)

### Twist

Twists an SDF object about an axis. 

![A square-bodied vase twisted about its vertical axis so its faces spiral](docs/images/tools_twist.png)

### Bend

Bends an SDF object about an axis. Choose the axis (X, Y, or Z), the bend angle, and where along the axis the bend is centred.

![The vase bent 40 degrees, seen in profile, so its neck leans well to one side](docs/images/tools_bend.png)

### Lattice

Encloses a solid in a lattice of control points. Moving the points deforms the solid smoothly, which suits tapering, flaring, and shearing a form without changing the original.

![A sphere inside its lattice of control points, with one corner point pulled out so the sphere stretches toward it](docs/images/tools_lattice.png)

### Cage Deform

Encloses any SDF solid within an interactive 3D control cage. Extrude faces, insert edge loops, and move cage vertices to deform the underlying volume.

![A sphere with one top face of its deform cage extruded outward, and a cage vertex selected on the right with the transform gizmo on it](docs/images/tools_cage.png)

### Panel

Cuts a solid into flat boards stacked along an axis. 

![A sphere divided into seven stacked flat boards with gaps between them](docs/images/tools_panel.png)

### Voxel Sculpt

Converts an SDF solid into an editable voxel volume that you can add and carve using 3D brushes. Use it for freeform digital clay sculpting with adjustable brush radius, strength, and falloff.

![The vase sculpted with the voxel brush: a raised ridge built up on one side and a scoop carved out of the other](docs/images/tools_sculpt.png)

### Matcap Shading

Sets how a Fields object is shaded in the viewport. A matcap is a captured material that is looked up by surface angle, so an object keeps the same look from any viewing direction. The built-in set includes clay, studio, metal, and gold, plus diagnostic views such as zebra stripes and curvature.

![The same vase shaded four ways, from left to right: standard shading, clay, metal, and zebra stripes](docs/images/tools_matcap.png)

### SDF to Shape

Creates a Part solid from implicit fields using Surface Nets, Marching Cubes, or Dual Contouring. Use FreeCAD's standard export to save STL/OBJ files for 3D printing or downstream solid modeling.

![The vase after SDF to Shape: a Part solid made of triangles, with the triangle edges drawn](docs/images/tools_sdf_to_mesh.png)

### Slice SDF

Cuts an SDF object with a plane and creates the cross-section as native FreeCAD BSplineCurves. A single plane or an array of parallel planes can be sliced at once, set by count or by spacing. The curves are suited to profile extraction, laser cutting, and CNC toolpaths, and can be used in Sketcher and Part workflows.

![Fourteen horizontal slices through the vase, shown as stacked cross-section curves with the inner ring of the opening included](docs/images/tools_sdf_slice.png)

## Requirements

- FreeCAD 26.3 or newer

## Installation

**Manual:** Clone or download https://github.com/StevePeters-US/Freecad-Fields-Workbench into the `Mod` folder of your FreeCAD user data directory (the Python console prints it: `App.getUserAppDataDir()`). Name the folder `Fields`, then restart FreeCAD.


### Mouse and keyboard

1. **Editing:** double-click an object in the 3D viewport to edit it; `Enter` accepts and commits changes, `Esc` cancels.
2. **Right-click:** opens the active tool's context menu. Every tool shortcut is listed on its menu item, with its key shown.
3. **Gizmo:**
   - Drag a handle to move, rotate, or scale.
   - Hold `Ctrl` while dragging to snap to increments.
   - `Ctrl + Wheel` changes the snap step increment.
   - Type digits (`0`–`9`, `.`, `-`) to enter an exact value, `Tab` for the next value component, `Enter` to accept, and `Esc` to step back.
4. **Creating:** the same numeric keys type an exact size while dragging out a primitive shape.
5. **Command keys:**
   - `P`: Point
   - `W`: Work Plane
   - `Ctrl+F`: Fill Curve
   - `S, K`: Sketcher
6. **Tool-specific shortcuts:**
   - **Cage Editor:** `E` (Extrude Face), `Ctrl+R` (Insert Edge Loop), `Ctrl+D` (Subdivide), `M` (Weld), `Alt+Z` (Toggle X-ray), `Return` (Commit Extrusion), `Esc` (Cancel Extrusion)
   - **Sculpt Brush:** `[` (Smaller Brush), `]` (Larger Brush)
   - **Curve Editor:** `Del` (Delete selected points)
   - **Selection:** `Shift+A` (Select All)

## Known issues

- Active development: tools, underlying data structures, and file formats may change between releases. Backwards compatibility is not guaranteed across updates.
- Lattice and Cage Deform, Voxel Sculpt, and SDF to Shape are experimental.
- To report a bug, open an issue at https://github.com/StevePeters-US/Freecad-Fields-Workbench/issues and include your FreeCAD version (Help → About FreeCAD → Copy to clipboard), the steps to reproduce it, the relevant lines from `Fields.log` (in your FreeCAD user data directory), and a small `.FCStd` file that shows it.

## Support

If you find this workbench useful, consider supporting its development on [Patreon](https://www.patreon.com/cw/AttackPotato).

## License

CC BY-NC-SA 4.0 — see [LICENSE](LICENSE).

## Attributions

- **Inigo Quilez** ([iquilezles.org](https://iquilezles.org/)) — Foundational mathematical formulations for distance primitives, GLSL raymarching algorithms, and smooth boolean operators (`smoothMin`/`smoothMax`).
