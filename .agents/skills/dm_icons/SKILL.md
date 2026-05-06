---
name: DM Icon Design Guidelines
description: Guidelines for creating custom SVG icons for the Direct Modeling (DM) workbench.
---

# DM Icon Design Guidelines

When creating custom SVG icons for the Direct Modeling (DM) workbench in FreeCAD, adhere to the following strict styling rules to ensure consistency across the UI.

## File Format & Dimensions

- **Format**: SVG (`<svg xmlns="http://www.w3.org/2000/svg" ...>`)
- **Dimensions**: Viewport size strictly `width="64" height="64"`.
- **Version**: `version="1.1"`

## Styling & Colors

All styling must be applied using inline SVG `style` attributes. Avoid classes or external stylesheets.

### Base Colors
- **Main Shape Fill**: Light gray `#e0e0e0`
- **Secondary / Highlight Shape Fill**: White `#ffffff` or `none` depending on context.
- **Default Stroke**: Black `#000000` with `stroke-width:2`

### Boolean / Modifier Operations Tool Colors
For tools that modify geometry (like Add, Subtract, Intersection):
- **Stroke Color**: Orange `#ffaa00` with `stroke-width:2`. This indicates an active boolean/modifier role.
- **Subtracted / Missing Details**: Use `stroke-dasharray:4,4` to denote regions that are cut away or removed from the geometry.

## Example Templates

### General Tool Icon (e.g., Box)
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
    <rect width="50" height="50" x="7" y="7" style="fill:#ffffff;stroke:#000000;stroke-width:2"/>
    <rect width="40" height="40" x="12" y="12" style="fill:#e0e0e0;stroke:#000000;stroke-width:1"/>
</svg>
```

### Boolean Operation Icon (e.g., Subtract)
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
    <!-- Base Shape -->
    <path d="M 36 16 A 18 18 0 1 0 36 48 A 18 18 0 0 1 36 16 Z" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
    <!-- Missing / Subtracted Shape with Dashes -->
    <circle cx="44" cy="32" r="18" style="fill:none;stroke:#ffaa00;stroke-width:2;stroke-dasharray:4,4"/>
</svg>
```

## Directory
All custom DM icons should be saved to `Resources/icons/` relative to the project root.
