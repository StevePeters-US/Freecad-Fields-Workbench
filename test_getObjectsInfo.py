import FreeCAD
import FreeCADGui
import Part

# Setup document
doc = FreeCAD.newDocument()
sphere = doc.addObject("Part::Feature", "Sphere")
sphere.Shape = Part.makeSphere(50)

# Setup view
FreeCADGui.showMainWindow()
view = FreeCADGui.ActiveDocument.ActiveView
view.viewAxonometric()
view.fitAll()

# Wait for render
import time
time.sleep(1)

# Raycast at center of screen
width = view.getSize()[0]
height = view.getSize()[1]
cx = width // 2
cy = height // 2

print(f"Testing center hit at {cx}, {cy}")

# getObjectsInfo
infos = view.getObjectsInfo((cx, cy))
print("getObjectsInfo results:")
if infos:
    for i, info in enumerate(infos):
        print(f"Hit {i}:")
        for k in sorted(info.keys()):
            print(f"  {k}: {info[k]}")
else:
    print("None")
    
FreeCADGui.exec_loop()
