import sys
import os

appimage = "/home/steve/Programs/Freecad/FreeCAD_weekly-2026.01.21-Linux-x86_64-py311.AppImage"
sys.path.append(os.path.join(os.path.dirname(appimage), "squashfs-root", "usr", "lib", "python3.11", "site-packages"))

import pivy.coin as coin

coords = coin.SoCoordinate3()
coords.point.setValues(0, 8, [(0,0,0), (100,100,100)] + [(0,0,0)]*6)
coords.point.setValues(8, 4, [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)])

faceset = coin.SoIndexedFaceSet()
# Degenerate triangle 0, 1, 0
faceset.coordIndex.setValues(0, 12, [8, 9, 10, -1, 8, 10, 11, -1, 0, 1, 0, -1])

# Check bbox
action = coin.SoGetBoundingBoxAction(coin.SbViewportRegion())
sep = coin.SoSeparator()
sep.addChild(coords)
sep.addChild(faceset)
action.apply(sep)
bbox = action.getBoundingBox()
print("Bounding box min:", bbox.getMin().getValue())
print("Bounding box max:", bbox.getMax().getValue())

