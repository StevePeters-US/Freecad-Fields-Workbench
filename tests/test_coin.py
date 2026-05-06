import sys
# Try to find FreeCAD
paths = [
    '/usr/lib/freecad/lib',
    '/usr/lib/freecad-daily/lib',
    '/usr/lib/FreeCAD/lib',
]
for p in paths:
    sys.path.append(p)
try:
    import FreeCAD
    import pivy.coin as coin
    print("Found FreeCAD Coin3D")
    bboxes = [n for n in dir(coin) if 'BBox' in n or 'BoundingBox' in n]
    print(bboxes)
except ImportError:
    print("Could not import FreeCAD/Coin3D")

