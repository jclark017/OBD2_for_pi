'''
Created on Mar 31, 2013

@author: Jeff Clark
'''
import serial
import wx

def main():
  print('opening port')
  ser = serial.Serial(2)
  print (ser.portstr)
  
  while 1:
    ser.flushInput()
    ser.flushOutput()
    ser.write("01 05")
    ser.write("\r")
    
    buf = reader(ser)
  
    print buf
  
def reader(ser):
  buff = ""
  while 1:
    c = ser.read(1)
    #if c == '\r' and len(buff) > 0 and buff[-1] == '\r':
    if c == '>':
      break
    else:
      buff = buff + c
  return buff
  
  
if __name__ == '__main__':
  main()    
  
    