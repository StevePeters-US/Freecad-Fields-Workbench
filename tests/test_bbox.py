import FreeCAD
import pivy.coin as coin

root = coin.SoSeparator()

# Invisible Draw Style
sep1 = coin.SoSeparator()
draw1 = coin.SoDrawStyle()
draw1.style.setValue(coin.SoDrawStyle.INVISIBLE)
sep1.addChild(draw1)
coords1 = coin.SoCoordinate3()
coords1.point.setValues(0, 2, [(0,0,0), (10,10,10)])
sep1.addChild(coords1)
pts1 = coin.SoPointSet()
pts1.numPoints.setValue(2)
sep1.addChild(pts1)
root.addChild(sep1)

# Action
viewport = coin.SbViewportRegion(400, 400)
bbox_action = coin.SoGetBoundingBoxAction(viewport)
bbox_action.apply(root)
box = bbox_action.getBoundingBox()
print("Invisible DrawStyle BBox:", box.getMin(), box.getMax())

root2 = coin.SoSeparator()
# Transparent point set
sep2 = coin.SoSeparator()
mat2 = coin.SoMaterial()
mat2.transparency.setValue(1.0)
sep2.addChild(mat2)
coords2 = coin.SoCoordinate3()
coords2.point.setValues(0, 2, [(0,0,0), (10,10,10)])
sep2.addChild(coords2)
pts2 = coin.SoPointSet()
pts2.numPoints.setValue(2)
sep2.addChild(pts2)
root2.addChild(sep2)

bbox_action.apply(root2)
box2 = bbox_action.getBoundingBox()
print("Transparent BBox:", box2.getMin(), box2.getMax())
