
from nodebox.graphics import *
from math import *
from random import *

def crect(r, w, deg=1):        # radian if deg=0; degree if deg=1
    from math import cos, sin, pi
    if deg:
        w = pi * w / 180.0
    return r * cos(w), r * sin(w)
'''
for i in range(220):
    print i
    print rect(100,i)
'''


def draw(canvas):
    canvas.clear()
        
    translate(250, 250)


    for i in range(220):
        ellipse(crect(200,i+10)[0]-50,crect(200,i+10)[1],5,5)
        ellipse(crect(100,i-70)[0]+120,crect(100,i-70)[1]+120,5,5)
        #print i
    rect(crect(200,100)[0]-50,crect(200,100)[1],10,10)
    
    rect(0,0,10,10)
    
    push()
    strokewidth(10)
    fill(0.25, 0.15, 0.25, 0.5)
    translate(-50,0)
    ellipse(0,0,10,10)
    translate(-2.5,0)
    
    rotate(randrange(-40,40))

    rect(0,0,5,190,origin=(.5,0))
    
    pop()
    
canvas.size = 500, 500
canvas.run(draw)