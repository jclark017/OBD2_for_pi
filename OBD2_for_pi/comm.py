'''
Created on Mar 31, 2013

@author: Jeff Clarke
@
Credit to: Donour Sizemore (donour@uchicago.edu)
'''
import serial
import wx
import time
import string

from serial.tools import list_ports


import obd_sensors
from powertrain_codes import pcodes
from network_codes    import ucodes

GET_DTC_COMMAND   = "03"
CLEAR_DTC_COMMAND = "04"

debug_file = open("obd_io_debug", "w")
def obd_debug(s):
    debug_file.write(str(s)+"\n")
    debug_file.flush()


class comm:

  def __init__(self,portnum):
    """Initializes port by resetting device and gettings supported PIDs. """
    # These should really be set by the user.
    baud     = 9600
    databits = 8
    par      = serial.PARITY_NONE  # parity
    sb       = 1                   # stop bits
    to       = 2
    
    try:
      self.port = serial.Serial(portnum,baud, \
      parity = par, stopbits = sb, bytesize = databits,timeout = to)
    except "FIXME": #serial.serialutil.SerialException:
      raise "PortFailed"
    
    obd_debug( self.port.portstr)
    ready = "ERROR"
    while ready == "ERROR":
      self.send_command("atz")   # initialize
      obd_debug( [self.get_result_line()])
      self.send_command("ate0")  # echo off
      obd_debug( [self.get_result_line()])
      self.send_command("0100")
      ready = self.get_result_line()[-6:-1]
      print ready

  def close(self):
    """ Resets device and closes all associated filehandles"""
    self.port.send_command("atz")
    self.port.close()
    self.port = None

  def send_command(self, cmd):
    """Internal use only: not a public interface"""
    if self.port:
      self.port.flushOutput()
      self.port.flushInput()
      for c in cmd:
        self.port.write(c)
      self.port.write("\r")

  def interpret_result(self,code):
    """Internal use only: not a public interface"""
    # Code will be the string returned from the device.
    # It should look something like this:
    # '41 11 0 0\r\r'
    
    # 9 seems to be the length of the shortest valid response
    if len(code) < 7:
        raise "BogusCode"
     
    # get the first thing returned, echo should be off
    code = string.split(code, "\r")
    code = code[0]
    
    #remove whitespace
    code = string.split(code)
    code = string.join(code, "")
          

    if code[:6] == "NODATA": # there is no such sensor
        return "NODATA"
    # first 4 characters are code from ELM
    code = code[4:]
    return code
  
  def get_result_line(self):
    if self.port:
      buff = ""
      while 1:
        c = self.port.readline()
        if c == "":
          break
        else:
          buff = buff + c
      return buff
        
    
  def get_result(self):
    """Internal use only: not a public interface"""
    if self.port:
      buff = ""
      
      while 1:
        
        c = self.port.read(1)
        
        if c == '\n' and len(buff) > 0 and buff[-1] == '\r':
          break
        else:
          buff = buff + c
      return buff
    return None

  # get sensor value from command
  def get_sensor_value(self,sensor):
    """Internal use only: not a public interface"""
    cmd = sensor.cmd
    self.send_command(cmd)
    data = self.get_result()
    if data:
      data = self.interpret_result(data)
      if data != "NODATA":
        data = sensor.value(data)
    else:
      raise "NORESPONSE"
    return data
  
  # return string of sensor name and value from sensor index
  def sensor(self , sensor_index):
    """Returns 3-tuple of given sensors. 3-tuple consists of
    (Sensor Name (string), Sensor Value (string), Sensor Unit (string) ) """
    sensor = obd_sensors.SENSORS[sensor_index]
    try:
      r = self.get_sensor_value(sensor)
    except "NORESPONSE":
      r = "NORESPONSE"
    return (sensor.name,r, sensor.unit)

  def sensor_names(self):
    """Internal use only: not a public interface"""
    names = []
    for s in obd_sensors.SENSORS:
      names.append(s.name)
    return names


  #
  # fixme: j1979 specifies that the program should poll until the number
  # of returned DTCs matches the number indicated by a call to PID 01
  #
  def get_dtc(self):
    """Returns a list of all pending DTC codes. Each element consists of
    a 2-tuple: (DTC code (string), Code description (string) )"""
    r = self.sensor(1)
    num = r[0]
    # get all DTC, 3 per mesg response
    self.send_command(GET_DTC_COMMAND)
    #for i in range(0, ceil(num/3.0)):
    res = self.get_result()
    print res
    return res
      # fixme: finish

  def clear_dtc(self):
    """Clears all DTCs and freeze frame data"""
    self.send_command(CLEAR_DTC_COMMAND)     
    r = self.get_result()
    return r
     
  def log(self, sensor_index, filename): 
    logfile = open(filename, "w")
    start_time = time.time() 
    if logfile:
      data = self.sensor(sensor_index)
      logfile.write("%s     \t%s(%s)\n" % \
                ("Time", string.strip(data[0]), data[2])) 
      while 1:
        now = time.time()
        data = self.sensor(sensor_index)
        line = "%.6f,\t%s\n" % (now - start_time, data[1])
        logfile.write(line)
        logfile.flush()


def test():  
  #print list_ports.comports()
  c = comm(2)
  
  print c.sensor(12)  
  print c.sensor(5)
  print c.sensor(11)
  
  i = 0
  while i < 100:
    print c.sensor(12)
    i = i + 1
  
if __name__ == '__main__':
  test()    
  
    