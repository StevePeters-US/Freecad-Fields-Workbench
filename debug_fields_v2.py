from pivy import coin
t = coin.SoTexture3()
for i in range(t.getNumFields()):
    print(t.getField(i).getName().getString())

