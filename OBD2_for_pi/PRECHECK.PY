#! /usr/bin/python

###############################################################################
# Copyright (c) 2008-2010 VMware, Inc.
#
# This file is part of Weasel.
#
# Weasel is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
# version 2 for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin St, Fifth Floor, Boston, MA 02110-1301 USA.
#

# autotest: doctest

import os
import re
import sys
import glob
import operator
import commands
import socket
import struct
import shlex

TASKNAME = 'Precheck'
TASKDESC = 'Preliminary checks'

# Directory where this file is running. Script expects data files, helper
# utilities to exist here.
try:
    # On Python 2.2 (ESX 3.5), __file__ isn't defined for the main script.
    SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
except NameError:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

# Allow us to ship extra Python modules in a zip file.
sys.path.insert(0, os.path.join(SCRIPT_DIR, "esximage.zip"))

# the new ramdisk (resource pool) where we will copy the ISO to
RAMDISK_NAME = '/upgrade_scratch'

ESX_CONF_PATH = '/etc/vmware/esx.conf'

try:
    import logging
    logging.basicConfig(level=logging.DEBUG)
    log = logging.getLogger()
except ImportError:
    class logger:
        def write(self, *args):
            sys.stderr.write(args[0] % args[1:])
            sys.stderr.write("\n")
        critical = write
        debug = write
        error = write
        fatal = write
        info = write
        warn = write
        warning = write
        def log(self, level, *args):
            sys.stderr.write(*args)
    log = logger()

SIZE_MiB = 1024 * 1024


class Result:
    ERROR = "ERROR"
    WARNING = "WARNING"
    SUCCESS = "SUCCESS"

    def __init__(self, name, found, expected,
                 comparator=operator.eq, errorMsg="", mismatchCode=None):
        """Paramters:
              * name         - A string, giving the name of the test.
              * found        - An object or sequence of objects. Each object
                               will be converted to a string representation, so
                               the objects should return an appropriate value
                               via their __str__() method.
              * expected     - Follows the same conventions as the found
                               parameter, but represents the result(s) that the
                               test expected.
              * comparator   - A method use to compare the found and expected
                               parameters. If comparator(found, expected) is
                               True, the value of the object's code attribute
                               will be Result.SUCCESS. Otherwise, the value of
                               the mismatchCode is returned, if specified, or
                               Result.ERROR.
              * mismatchCode - If not None, specifies a code to be assigned to
                               this object's result attribute when
                               comparator(found, expected) is False.
        """
        if not mismatchCode:
            mismatchCode = Result.ERROR

        self.name = name
        self.found = found
        self.expected = expected
        self.errorMsg = errorMsg
        if comparator(self.found, self.expected):
            self.code = Result.SUCCESS
        else:
            self.code = mismatchCode

    def __nonzero__(self):
        return self.code == Result.SUCCESS

    def __str__(self):
        if self.name in ("3RD_PARTY_SOFTWARE",
                         "DISTRIBUTED_VIRTUAL_SWITCH",
                         "POWERPATH"):
            return ('<%s %s: %s %s>'
                    % (self.name, self.code, self.errorMsg, self.expected))
        elif self.name == "MEMORY_SIZE":
            return ('<%s %s: This host has %s of RAM. %s are needed>'
                    % (self.name, self.code,
                     formatValue(self.found[0]), formatValue(self.expected[0])))
        elif self.name == "SPACE_AVAIL_CONFIG":
            return ('<%s %s: Only %s available on the VMFS volume for '
                    'configuration files. %s are needed>'
                    % (self.name, self.code,
                     formatValue(self.found[0]), formatValue(self.expected[0])))
        elif self.name == "SPACE_AVAIL_ISO":
            return ('<%s %s: Only %s available for ISO files. %s are needed>'
                    % (self.name, self.code,
                     formatValue(self.found[0]), formatValue(self.expected[0])))
        elif self.name == "UNSUPPORTED_DEVICES":
            return ('<%s %s: This host has unsupported devices %s>'
                    % (self.name, self.code, self.found))
        elif self.name == "CPU_CORES":
            return ('<%s %s: This host has %s cpu core(s) which is less '
                   'than recommended %s cpu cores>'
                    % (self.name, self.code, self.found, self.expected))
        elif self.name == "PARTITION_LAYOUT":
            return ('<%s %s: %s>'
                    % (self.name, self.code, self.errorMsg))
        elif self.name == "HARDWARE_VIRTUALIZATION":
            return ('<%s %s: Hardware Virtualization is not a '
                   'feature of the CPU, or is not enabled in the BIOS>'
                    % (self.name, self.code))
        elif self.name == "VALIDATE_HOST_HW":
            # Prepare the strings.
            prepStrings = []
            for match, vibPlat, hostPlat in self.found:
                hostStr = "%s VIB for %s found, but host is %s" % \
                          (match, vibPlat, hostPlat)
                prepStrings.append(hostStr)

                return '<%s %s: %s>' % (self.name, self.code, ', '.join(prepStrings))
        elif self.name == "CONFLICTING_VIBS":
            return ('<%s %s: %s %s>'
                    % (self.name, self.code, self.errorMsg, self.found))
        else:
            return ('<%s %s: Found=%s Expected=%s %s>'
                    % (self.name, self.code,
                       self.found, self.expected, self.errorMsg))
    __repr__ = __str__


class PciInfo:
    '''Class to encapsulate PCI data'''
    #
    # TODO: this technique probably won't be sufficient.  I'll need to
    #       check the subdevice info as well.  The easy approach is probably
    #       to import pciidlib.py and extend it with these __eq__ and __ne__
    #       functions.  Also, I'll have to check that pciidlib works on both
    #       ESX and ESXi
    #

    def __init__(self, vendorId, deviceId, subsystem="0000:0000", description=""):
        '''Construct a PciInfo object with the given values: vendorId and
        deviceId should be strings with the appropriate hex values.  Description
        is an english description of the PCI device.'''

        self.vendorId = vendorId.lower()
        self.deviceId = deviceId.lower()
        self.subsystem = subsystem.lower()
        self.description = description

    def __eq__(self, rhs):
        return (self.vendorId == rhs.vendorId and
                self.deviceId == rhs.deviceId and
                self.subsystem == rhs.subsystem)

    def __ne__(self, rhs):
        return (self.vendorId != rhs.vendorId or
                self.deviceId != rhs.deviceId or
                self.subsystem != self.subsystem)

    def __str__(self):
        return "%s [%s:%s %s]" % (self.description, self.vendorId,
                                  self.deviceId, self.subsystem)

    def __repr__(self):
        return "<PciInfo '%s'>" % str(self)

UNSUPPORTED_PCI_IDE_DEVICE_LIST = [
    # eg: PciInfo("10b9", "5228", "ALi15x3"),
    ]

UNSUPPORTED_PCI_DEVICE_LIST = UNSUPPORTED_PCI_IDE_DEVICE_LIST + [
    # Any other devices we want to warn about?
    # eg: PciInfo("8086", "1229", "Ethernet Pro 100"),
    PciInfo("1000", "0030", "1028:018A", "Dell PERC 4/im, LSI53C1030 (MegaRAID) (U320 SCSI aka PERC4/IM)"),
    PciInfo("1000", "0407", "0000:0000", "LSI Logic MegaRAID"),
    PciInfo("1000", "0407", "1000:0530", "MegaRAID SCSI 320-0X"),
    PciInfo("1000", "0407", "1000:0531", "MegaRAID SCSI 320-4X"),
    PciInfo("1000", "0407", "8086:0530", "Intel RAID Controller SRCZCRX"),
    PciInfo("1000", "0407", "8086:0532", "Intel RAID Controller SRCU42X"),
    PciInfo("1000", "0408", "0000:0000", "LSI Logic MegaRAID"),
    PciInfo("1000", "0408", "1000:0002", "MegaRAID SCSI 320-2E"),
    PciInfo("1000", "0408", "1025:0040", "Dell PERC 4e/DC RAID Controller"),
    PciInfo("1000", "0408", "1028:0002", "MegaRAID  PCI Express ROMB, PERC 4e/DC"),
    PciInfo("1000", "0408", "1033:8287", "MegaRAID PCI Express(TM) ROMB"),
    PciInfo("1000", "0408", "8086:0002", "Intel RAID Controller SRCU42E"),
    PciInfo("1000", "0408", "8086:3431", "Intel RAID Controller SROMBU42E"),
    PciInfo("1000", "0408", "8086:3432", "Intel RAID Controller SROMBU42E"),
    PciInfo("1000", "0408", "8086:3449", "Intel RAID Controller SROMBU42E"),
    PciInfo("1000", "0408", "8086:344c", "Intel RAID Controller SROMBU42E"),
    PciInfo("1000", "0408", "8086:344d", "Intel RAID Controller SROMBU42E"),
    PciInfo("1000", "1960", "0000:0000", "LSI Logic MegaRAID, LSI MEGARAID3"),
    PciInfo("1000", "1960", "1000:0520", "MegaRAID SCSI 320-1"),
    PciInfo("1000", "1960", "1000:0522", "MegaRAID i4 133 RAID Controller"),
    PciInfo("1000", "1960", "1000:0523", "MegaRAID SATA 150-6"),
    PciInfo("1000", "1960", "1000:4523", "MegaRAID SATA 150-4"),
    PciInfo("1000", "1960", "1000:A520", "MegaRAID SCSI 320-0"),
    PciInfo("1000", "1960", "1028:0518", "Dell PERC4/DC RAID Controller"),
    PciInfo("1000", "1960", "1028:0520", "Dell PERC4/SC RAID Controller"),
    PciInfo("1000", "1960", "1734:1105", "RAID 0/1 SAS based on LSI MegaRAID 4P"),
    PciInfo("1000", "1960", "8086:0520", "Intel RAID Controller SRCU41L"),
    PciInfo("1000", "1960", "8086:0523", "Intel RAID Controller SRCS16"),
    PciInfo("1000", "9010", "0000:0000", "LSI Logic MegaRAID"),
    PciInfo("1000", "9060", "0000:0000", "LSI Logic MegaRAID"),
    PciInfo("1014", "01bd", "0000:0000", "ServeRAID Controller 6i"),
    PciInfo("1028", "000f", "0000:0000", "Dell PERC 4"),
    PciInfo("1028", "0013", "0000:0000", "Dell PERC 4E/Si/Di"),
    PciInfo("1028", "0013", "1028:016C", "Dell PERC 4e/Si"),
    PciInfo("1028", "0013", "1028:016D", "Dell PERC 4e/Di"),
    PciInfo("1028", "0013", "1028:0170", "Dell PERC 4e/Di"),
    PciInfo("1028", "0520", "0000:0000", "PERC 4/SC"),
    PciInfo("1077", "2300", "0000:0000", "QLA2300 64-bit Fibre Channel Adapter"),
    PciInfo("1077", "2300", "1077:0009", "QLA2300"),
    PciInfo("1077", "2312", "0000:0000", "QLA2310, QLA2312 1X, QLA231x/2340, QLA2340, QLA2340/2340L, QLA2344"),
    PciInfo("1077", "2312", "1028:018A", "Qlogic QME2342M"),
    PciInfo("1077", "2312", "1077:0009", "QLA2310, QLA2310F, QLA2310FL"),
    PciInfo("1077", "2312", "1077:0100", "QLA2340/2340L"),
    PciInfo("1077", "2312", "1077:0101", "QLA2342L, QLogic QLA2342"),
    PciInfo("1077", "2312", "1077:0102", "QLA2344"),
    PciInfo("1077", "2312", "1077:0109", "QLA2312 1X, QMA2312"),
    PciInfo("1077", "2312", "1077:010a", "SG-XPCI2FC-QF2, StorageTek HBQ032"),
    PciInfo("1077", "2312", "1077:0117", "Qlogic QLE2360"),
    PciInfo("1077", "2312", "1077:0149", "SG-XPCI1FC-QL2 (Qlogic QLA2340)"),
    PciInfo("1077", "2312", "1077:1000", "QLA2340"),
    PciInfo("1077", "2312", "1734:1051", "BX600-FC22Q"),
    PciInfo("1077", "6322", "0000:0000", "SP212-based 2Gb Fibre Channel to PCI-X HBA"),
    PciInfo("1077", "8432", "0000:0000", "ISP2432M-based 10GbE Converged Network Adapter (CNA)"),
    PciInfo("1077", "8432", "1077:010E", "QLE8042"),
    PciInfo("10ec", "8168", "0000:0000", "driver=r8168,class=network"),
    PciInfo("10ec", "8167", "0000:0000", "driver=r8169,class=network"),
    PciInfo("10ec", "8169", "0000:0000", "driver=r8169,class=network"),
    PciInfo("11ab", "4354", "0000:0000", "driver=sky2,class=network"),
    PciInfo("11ab", "4362", "0000:0000", "driver=sky2,class=network"),
    PciInfo("17d5", "5831", "0000:0000", "Xframe I 10 GbE Server/Storage adapter"),
    PciInfo("17d5", "5832", "0000:0000", "NE3008-004"),
    PciInfo("17D5", "5832", "17D5:6020", "IBM 10 GbE Fiber SR Server Adapter, Xframe II SR"),
    PciInfo("17d5", "5832", "17d5:6021", "SGI PCIX-10GENET-ORLP-Z, SGI PCIX-10GENET-OR-Z, Xframe II Sun Fire"),
    PciInfo("17d5", "5832", "17d5:6022", "Xframe E SR"),
    PciInfo("17d5", "5832", "17d5:6420", "Xframe II LR"),
    PciInfo("17d5", "5832", "17d5:6421", "Xframe II Sun Fire LR"),
    PciInfo("17d5", "5832", "17d5:6422", "Xframe E LR"),
    PciInfo("17d5", "5832", "17d5:6c20", "Xframe II CX4"),
    PciInfo("17d5", "5832", "17d5:6c21", "Xframe II Sun Fire CX4"),
    PciInfo("17d5", "5832", "17d5:6c22", "Xframe E CX4"),
    PciInfo("17d5", "5833", "17d5:6030", "X3110 Single Port SR"),
    PciInfo("17d5", "5833", "17d5:6031", "X3120 Dual Port SR"),
    PciInfo("17d5", "5833", "17d5:6430", "X3110 Single Port LR"),
    PciInfo("17d5", "5833", "17d5:6431", "X3120 Dual Port LR"),
    PciInfo("17d5", "5833", "17d5:7830", "X3110 Single Port 10GBase-CR"),
    PciInfo("17d5", "5833", "17d5:7831", "X3120 Dual Port 10GBase-CR"),
    PciInfo("4040", "0001", "0000:0000", "NXB-10GXSR"),
    PciInfo("4040", "0001", "1014:036B", "IBM 10 GbE Expansion Card (CFFh) for Bladecenter, IBM 10 GbE PCIe SR Server Adapter"),
    PciInfo("4040", "0001", "103C:7047", "NC510F, NC510F PCIe 10 Gigabit Server Adapter"),
    PciInfo("4040", "0002", "0000:0000", "NXB-10GCX4"),
    PciInfo("4040", "0002", "103C:7048", "NC510C PCIe 10 Gigabit Server Adapter"),
    PciInfo("4040", "0004", "0000:0000", "BladeCenter-H 10 Gigabit Ethernet High Speed Daughter Card"),
    PciInfo("4040", "0005", "0000:0000", "NetXen Dual Port 10GbE Multifunction Adapter for c-Class"),
    PciInfo("4040", "0005", "103c:170e", "NC512M"),
    PciInfo("9005", "0410", "0000:0000", "AIC-9410"),
    PciInfo("9005", "0411", "0000:0000", "AIC-9410"),
    PciInfo("9005", "0412", "0000:0000", "AIC-9410"),
    PciInfo("9005", "041e", "0000:0000", "AIC-9410"),
    PciInfo("9005", "041e", "1014:02e7", "AIC 9410"),
    PciInfo("9005", "041f", "0000:0000", "AIC-9410"),
    PciInfo("9005", "8000", "0000:0000", "29320LP Ultra320 SCSI, ASC-29320A U320"),
    PciInfo("9005", "800f", "0000:0000", "AIC-7901 U320"),
    PciInfo("9005", "8010", "0000:0000", "39320DB Ultra320 SCSI, ASC-39320 U320"),
    PciInfo("9005", "8011", "0000:0000", "ASC-32320D U320"),
    PciInfo("9005", "8011", "9005:0000", "ASC-39320D U320"),
    PciInfo("9005", "8011", "9005:0041", "39320D Ultra320 SCSI"),
    PciInfo("9005", "8012", "0000:0000", "ASC-29320 U320"),
    PciInfo("9005", "8013", "0000:0000", "ASC-29320B U320"),
    PciInfo("9005", "8014", "0000:0000", "ASC-29320LP U320"),
    PciInfo("9005", "8015", "0000:0000", "AHA-39320B"),
    PciInfo("9005", "8016", "0000:0000", "AHA-39320A"),
    PciInfo("9005", "8017", "0000:0000", "AHA-29320ALP"),
    PciInfo("9005", "8017", "9005:0045", "ASC-29320LPE"),
    PciInfo("9005", "801c", "0000:0000", "AHA-39320DB / AHA-39320DB-HP"),
    PciInfo("9005", "801d", "0000:0000", "AIC-7902B U320 OEM"),
    PciInfo("9005", "801d", "1014:02cc", "Adaptec AIC-7902"),
    PciInfo("9005", "801e", "0000:0000", "AIC-7901A U320"),
    PciInfo("9005", "801f", "0000:0000", "AIC-7902 U320, AIC-7902 Ultra320 SCSI"),
    PciInfo("9005", "8094", "0000:0000", "AHA29320LP_IROC, ASC-29320LP U320 w/HostRAID"),
    PciInfo("9005", "809d", "0000:0000", "AIC-7902(B) U320 w/HostRAID, AIC-7902_B_IROC"),
    PciInfo("9005", "809e", "0000:0000", "AIC-7901A U320 w/HostRAID, AIC-7901A_IROC"),
    PciInfo("9005", "809f", "0000:0000", "AIC-7902 U320 w/HostRAID"),
    ]

class SystemProbe(object):
    def fdiskDashLU(self, diskPath):
        cmd = 'fdisk -lu %s' % diskPath
        log.info('Running %s' % cmd)
        return commands.getoutput(cmd)

    def vmkfstoolsDashP(self, path):
        cmd = 'vmkfstools -P %s' % path
        log.info('Running %s' % cmd)
        status, rawPartitionInfo = commands.getstatusoutput(cmd)
        if status != 0:
            log.error('vmkfstools returned status %d' % status)
            return ''
        return rawPartitionInfo

    #Can't do a staticmethod, because it runs on ESX3.5 (Py2.3)
    def parseVmkfstoolsDashP(self, rawPartitionInfo):
        '''Return a dictionary with the following keys:
        fsType, totalBytes, freeBytes, uuid, diskHBAName, partNum
        with string values that are the result of scraping the output of the
        command `vmkfstools -P path`
        rawPartitionInfo will look something like this:
        >>> info = os.linesep.join([
        ... 'vfat-0.04 file system spanning 1 partitions.',
        ... 'File system label (if any): Hypervisor1',
        ... 'Mode: private',
        ... 'Capacity 261853184 (63929 file blocks * 4096), 164687872 (40207 blocks) avail',
        ... 'UUID: 96f2ab7c-e353fc5f-cc7b-5ca987e270a4',
        ... 'Partitions spanned (on "disks"):',
        ... '   mpx.vmhba1:C0:T0:L0:5']
        ... )
        >>> s = SystemProbe()
        >>> sorted(s.parseVmkfstoolsDashP(info).items())
        [('diskHBAName', 'mpx.vmhba1:C0:T0:L0'), ('freeBytes', '164687872'), ('fsType', 'vfat-0.04'), ('partNum', '5'), ('totalBytes', '261853184'), ('uuid', '96f2ab7c-e353fc5f-cc7b-5ca987e270a4')]
        '''

        pattern = re.compile(r'''
        (?P<fsType>\S+).file.system.spanning     # vfat-0.04 file system spanning
        .*?                                      # eat some stuff
        Capacity\s(?P<totalBytes>\d+)            # Capacity 261853184
        .\([^\)]*\),                             #  (63929 file blocks * 4096),
        .(?P<freeBytes>\d+)                      #  164687872
        .*?                                      # eat some stuff
        UUID:.(?P<uuid>\S+)                      # UUID: 96f2ab7c-e....87e270a4
        .*?                                      # eat some stuff
        \s*(?P<diskHBAName>\S*):(?P<partNum>\d)  #    mpx.vmhba1:C0:T0:L0:5
        ''', re.VERBOSE | re.MULTILINE | re.DOTALL)

        match = pattern.search(rawPartitionInfo)
        if match:
            return match.groupdict()
        else:
            log.error('Could not parse vmkfstools output:')
            log.error('"%s"' % rawPartitionInfo)
            return {}

    def partitionInfo(self, path):
        return self.parseVmkfstoolsDashP(self.vmkfstoolsDashP(path))

    def parseEsxConf(self):
        '''Parse /etc/vmware/esx.conf into a dict:
        { ...
        '/system/uservars/psa/defaultLunMasksInstalled': '1',
        '/system/uuid': '4b8fb067-b95a-e95b-6f8d-000c29c1f19c',
        ... }
        '''
        retval = {}
        try:
            esxconf = open(ESX_CONF_PATH)
            pattern = re.compile(r'(\S+) = "(.*)"')
            for line in esxconf:
                m = pattern.search(line)
                if m:
                    retval[m.group(1)] = m.group(2)
            esxconf.close()
        except (OSError, IOError), e:
            log.error("cannot open esx.conf -- %s" % str(e))
        return retval


class SystemProbeESX(SystemProbe):
    def __init__(self, version, skipEsxInfo=False):
        '''query relevant info about the ESX system'''
        self.product = 'esx'
        self.version = version
        self.vibCheckPath = '/'
        self.weaselMode = False

        log.info('Running esxcfg-info')
        if skipEsxInfo:
            self.esxinfo = None
        else:
            self.esxinfo = commands.getoutput("/usr/sbin/esxcfg-info")

        self.pciinfo = self.parsePciInfo(self.lspciDashMN())

        log.info('Reading /etc/shadow')
        self.shadow = open('/etc/shadow').read()
        log.info('Reading /etc/passwd')
        self.passwd = open('/etc/passwd').read()

        self.rootPartPath, self.bootPartPath = self._getPartPaths()
        self.bootDiskPath = splitPath(self.bootPartPath)[0]
        self.bootDiskVMHBAName = self._getVMHBAName()

        esxconfDict = self.parseEsxConf()
        self.cosVMDK = esxconfDict.get('/boot/cosvmdk')
        self._systemUUID = esxconfDict.get('/system/uuid')
        self.esxconfNonempty = bool(os.path.exists(ESX_CONF_PATH)
                                    and os.path.getsize(ESX_CONF_PATH))

        self.bootDiskVMFSPartition = self.findVMFSPartition()

        from vmware.esximage import Vib
        from vmware.esximage.Utils import HostInfo
        vendor, model = HostInfo.GetBiosVendorModel()
        self.hostHw = Vib.HwPlatform(vendor, model)

    def getSystemUUID(self):
        return self._systemUUID

    def findVMFSPartition(self):
        if not self.cosVMDK:
            return None
        if not os.path.exists(self.cosVMDK):
            return None
        resultDict = self.partitionInfo(self.cosVMDK)
        uuid = resultDict.get('uuid')
        if uuid:
            return '/vmfs/volumes/' + uuid
        return None

    def lspciDashMN(self):
        log.info('Running lspci')
        status, lspciOutput = commands.getstatusoutput("/sbin/lspci -mn")
        if status != 0:
            log.error("lspci failed with status %s -- %s" %
                      (status, lspciOutput))
            return
        return lspciOutput

    #Can't do a staticmethod, because it runs on ESX3.5 (Py2.3)
    def parsePciInfo(self, pciInfo):
        '''Parse the output of "lspci -mn" and return a list of PciInfo objects.

        >>> sysprobe = SystemProbeESX([5, 0, 0])
        >>> sysprobe.parsePciInfo( os.linesep.join([
        ...               '00:00.0 "0600" "8086" "277c" "1028" "01de"',
        ...               '00:01.0 "0604" "8086" "277d" "" ""']) )
        [<PciInfo ' [8086:277c 1028:01de]'>, <PciInfo ' [8086:277d 0000:0000]'>]
        '''
        retval = []

        for line in pciInfo.splitlines():
            m = re.match(r'^\w+:\w+\.\w+ "[^"]+" "(\w+)" "(\w+)"(.*)"(\w*)" "(\w*)"', line)
            if not m:
                continue

            vendor = m.group(1)
            device = m.group(2)
            subven = m.group(4)
            subdev = m.group(5)

            if not subven:
                subven = "0000"

            if not subdev:
                subdev = "0000"

            retval.append(PciInfo(vendor, device, subven + ':' + subdev))

        return retval

    def _getVMHBAName(self):
        assert self.bootDiskPath
        cmd = "/usr/sbin/esxcfg-scsidevs -c"
        status, output = commands.getstatusoutput(cmd)
        if status != 0:
            log.error("%s failed with status %s -- %s" % (cmd, status, output))
            return ''
        for line in output.splitlines():
            columns = line.split()
            if len(columns) < 3:
                continue
            if columns[2] == self.bootDiskPath:
                return columns[0]
        return ''

    def _getPartPaths(self):
        '''Return the partition paths (eg, "/dev/sda1") of the root (/)
        and boot (/boot) partitions
        '''
        # I was tempted to use the output of `mount` or /etc/mtab, but
        # apparently those can randomly go out of sync, so use /proc/mounts
        log.info('Reading /proc/mounts')
        procMounts = open('/proc/mounts')

        def parseProcMounts(procMounts):
            '''
            >>> procMounts = os.linesep.join(
            ... 'rootfs / rootfs rw 0 0',
            ... '/dev /dev tmpfs rw 0 0',
            ... '/dev/root / ext3 rw,data=ordered 0 0',
            ... '/dev/sda1 /boot ext3 rw,data=ordered 0 0',
            ... )
            >>> parseProcMounts(procMounts)
            ('/dev/root', '/dev/sda1')
            '''
            rootPartPath = None
            bootPartPath = None
            pattern = re.compile(r'^(\S+) (\S+) (\S+)')
            for line in procMounts:
                match = pattern.search(line)
                if not match:
                    continue
                devicePath, mountPoint, fsType = match.groups()
                if mountPoint == '/' and devicePath != 'rootfs':
                    rootPartPath = devicePath
                if mountPoint == '/boot':
                    bootPartPath = devicePath
            log.info('Found partition paths for / (%s) and /boot (%s)'
                     % (rootPartPath, bootPartPath))
            if not bootPartPath:
                raise ValueError('No /boot partition found in /proc/mounts')
            return rootPartPath, bootPartPath

        return parseProcMounts(procMounts)

# -----------------------------------------------------------------------------
class SystemProbeESXi(SystemProbe):
    def __init__(self, version, skipEsxInfo=False):
        '''query relevant info about the ESXi system'''
        self.product = 'esxi'
        self.version = version
        self.vibCheckPath = '/'
        self.weaselMode = False
        self._systemUUID = None

        log.info('Running esxcfg-info')
        if skipEsxInfo:
            self.esxinfo = None
        else:
            self.esxinfo = commands.getoutput("/usr/sbin/esxcfg-info")


        self.pciinfo = self.parsePciInfo(self.lspciDashN())

        self.bootDiskVMHBAName = self.getBootDiskVMHBAName()
        if self.bootDiskVMHBAName:
            self.bootDiskPath = '/vmfs/devices/disks/' + self.bootDiskVMHBAName
        else:
            # if we can't find bootdisk then bootDiskPath is empty. That
            # happens when it's a stateless (PXE booted) host
            self.bootDiskPath = ''

        self.esxconfNonempty = bool(os.path.exists(ESX_CONF_PATH)
                                    and os.path.getsize(ESX_CONF_PATH))

        log.info('Reading /etc/shadow')
        self.shadow = open('/etc/shadow').read()
        log.info('Reading /etc/passwd')
        self.passwd = open('/etc/passwd').read()

        from vmware.esximage import Vib
        from vmware.esximage.Utils import HostInfo
	vendor, model = HostInfo.GetBiosVendorModel()
        self.hostHw = Vib.HwPlatform(vendor, model)

    def getBootDiskVMHBAName(self):
        cmd = 'esxcfg-info -b'
        status, bootuuid = commands.getstatusoutput(cmd)
        bootuuid = bootuuid.strip()
        if not bootuuid:
            return ''
        bootVolume = '/vmfs/volumes/' + bootuuid

        resultDict = self.partitionInfo(bootVolume)
        diskHBAName = resultDict.get('diskHBAName')
        if diskHBAName:
            return diskHBAName
        else:
            log.error('Disk name not found in vmkfstools output')
            return ''

    def getSystemUUID(self):
        if self._systemUUID != None:
            return self._systemUUID
        m = re.search(r'System UUID\.+(.*)', self.esxinfo)
        if not m:
            log.error('could not get system uuid')
        else:
            self._systemUUID = m.group(1)

        return self._systemUUID

    def findVMFSPartition(self):
        if not systemProbe.bootDiskPath:
            return None
        volumesDir = '/vmfs/volumes/'
        for fname in os.listdir(volumesDir):
            path = volumesDir + fname
            if os.path.islink(path) or not os.path.isdir(path):
                continue
            resultDict = self.partitionInfo(path)
            diskHBAName = resultDict.get('diskHBAName')
            if not diskHBAName or not self.bootDiskPath.endswith(diskHBAName):
                continue # this partition wasn't on the boot disk
            fsType = resultDict.get('fsType')
            if fsType and 'vmfs' in fsType.lower():
                return path # found the VMFS partition!
        return None

    def lspciDashN(self):
        log.info('Running lspci')
        status, lspciOutput = commands.getstatusoutput("/sbin/lspci -np")
        if status != 0:
            log.error("lspci failed with status %s -- %s" %
                      (status, lspciOutput))
            return
        return lspciOutput

    #Can't do a staticmethod, because it runs on ESX3.5 (Py2.3)
    def parsePciInfo(self, pciInfo):
        '''Parse the output of "lspci -np" and return a list of PciInfo objects

        >>> sysprobe = SystemProbeESXi([4, 1, 0])
        >>> sysprobe.parsePciInfo(os.linesep.join([
        ...        'Bus:S1.F Vend:Dvid Subv:Subd ISA/irq/Vec P M Module       Name',
        ...        '00:00.00 8086:7190 15ad:1976               V',
        ...        '00:07.03 8086:7113 15ad:1976 255/   /     @ V ide          vmhba0',
        ...        '02:00.00 8086:100f 15ad:0750 10/ 10/0x91 A V e1000        vmnic0'
        ...        ]))
        [<PciInfo ' [8086:7190 15ad:1976]'>, <PciInfo ' [8086:7113 15ad:1976]'>, <PciInfo ' [8086:100f 15ad:0750]'>]
        '''
        retval = []

        # Skip the first line. The first line describes the columns.
        for line in pciInfo.splitlines()[1:]:
            m = re.match(r'[\.:0-9]+ (\w+):(\w+) (\w+):(\w+)', line)
            if not m:
                continue

            vendor = m.group(1)
            device = m.group(2)
            subven = m.group(3)
            subdev = m.group(4)

            retval.append(PciInfo(vendor, device, subven + ':' + subdev))
        return retval

class IsoMetadata(object):
    def __init__(self):
        self.vibs = []
        self.profile = None
        if systemProbe.weaselMode:
           self._loadVibDatabase()
        else:
           self._loadVibMetadata()
        self._calcIsoSize()

    def _loadVibDatabase(self):
        # Loads VIBs from esximage database on Visor FS.
        from vmware.esximage import Database
        d = Database.Database("/var/db/esximg", dbcreate=False)
        d.Load()
        self.vibs = d.vibs.values()
        self.profile = d.profile

    def _loadVibMetadata(self):
        from vmware.esximage import Metadata
        m = Metadata.Metadata()
        m.ReadMetadataZip(os.path.join(SCRIPT_DIR, "metadata.zip"))

        if len(m.profiles) != 1:
            raise Exception("Multiple or no image profiles in metadata!")

        self.vibs = [m.vibs[vid] for vid in m.profiles.values()[0].vibIDs]
        self.profile = m.profiles.values()[0]
        self.profile.vibs = m.vibs

    def _calcIsoSize(self):
        # The Metadata instance doesn't have any statistics about the size of the image
        # so we need to interate over all of the payloads to properly calculate.
        totalSize = 0
        for vib in self.vibs:
            for payload in vib.payloads:
                totalSize += payload.size

        # The total space for the ISO is estimated at the total payload size plus a 10MB
        # fudge factor to account for additional bits on the ISO that aren't strickly
        # reported in the VIB
        self.sizeOfISO = totalSize + (10 * SIZE_MiB)


# -----------------------------------------------------------------------------
class DummySystemProbe(object):
    def __init__(self, version, product):
        self.product = product
        self.version = version
        self.esxinfo = 'dummy esxinfo'
        self.pciinfo = []
        self.passwords = 'foo:asdf:1:2:3:4\nasdf:qwer:1:2:3:4'
        self.bootDiskPath = '/dev/sda'
        self.vibCheckPath = '/'
        self.weaselMode = False
        self._systemUUID = 'dummy-system-uuid-asdf'

        class dummyStatVfs:
            f_frsize = 512
            f_bavail = 10
            def __call__(self, *args):
                return self
        os.statvfs = dummyStatVfs()

    def fdiskDashLU(self, diskPath):
        return '\n'.join([
        'Disk /dev/sda: 12.8 GB, 12884901888 bytes',
        '255 heads, 63 sectors/track, 1566 cylinders, total 25165824 sectors',
        'Units = sectors of 1 * 512 = 512 bytes',
        '',
        '   Device Boot      Start         End      Blocks   Id  System',
        '/dev/sda1   *          63     2249099     1124518+  83  Linux',
        '/dev/sda2         2249100     2474009      112455   fc  VMwareVMKCORE',
        '/dev/sda3         2474010    25157789    11341890    5  Extended',
        '/dev/sda5         2474073    25157789    11341858+  fb  VMware VMFS',
        ])

    def vmkfstoolsDashP(self, path):
        log.info('Running vmkfstools -P %s' % path)
        return '\n'.join([
          'vfat-0.04 file system spanning 1 partitions.',
          'File system label (if any): Hypervisor1',
          'Mode: private',
          'Capacity 100 (63929 file blocks * 4096), 50 (40207 blocks) avail',
          'UUID: abcd1234-6d044bfb-c40b-27f4e4179518',
          'Partitions spanned (on "disks"):',
          '   mpx.vmhba1:C0:T0:L0:3',
          ])

    def parseEsxConf(self):
        return {'/system/uservars/psa/defaultLunMasksInstalled': '1',
        '/system/uuid': '4b8fb067-b95a-e95b-6f8d-000c29c1f19c',
        '/boot/cosvmdk': '/vmfs/volumes/asdf-123/esxconsole-asdf-123/esxconsole.vmdk'
        }

class DummyMetadata(object):
    def __init__(self):
        self.sizeOfISO = 50 * SIZE_MiB
        self.vibs = set((("VMware", "base"), ("VMware", "tools")))

# -----------------------------------------------------------------------------
def run(cmd):
    # commands.getoutput causes problems with esxcfg-advcfg
    # so invoke os.popen instead.
    log.info('Running command %s' % cmd)
    p = os.popen(cmd, 'r')
    output = p.read()
    returncode = p.close()
    if returncode == None:
        returncode = 0
    log.info('Got return code %s' % returncode)
    return output

def formatValue(B=None, KiB=None, MiB=None):
    '''Takes an int value defined by one of the keyword args and returns a
    nicely formatted string like "2.6 GiB".  Defaults to taking in bytes.
    >>> formatValue(B=1048576)
    '1.00 MiB'
    >>> formatValue(MiB=1048576)
    '1.00 TiB'
    '''
    SIZE_KiB = (1024.0)
    SIZE_MiB = (SIZE_KiB * 1024)
    SIZE_GiB = (SIZE_MiB * 1024)
    SIZE_TiB = (SIZE_GiB * 1024)

    assert len([x for x in [KiB, MiB, B] if x != None]) == 1

    # Convert to bytes ..
    if KiB:
        value = KiB * SIZE_KiB
    elif MiB:
        value = MiB * SIZE_MiB
    else:
        value = B

    if value >= SIZE_TiB:
        return "%.2f TiB" % (value / SIZE_TiB)
    elif value >= SIZE_GiB:
        return "%.2f GiB" % (value / SIZE_GiB)
    elif value >= SIZE_MiB:
        return "%.2f MiB" % (value / SIZE_MiB)
    else:
        return "%s bytes" % (value)

def _parseMemory(infoText):
    m = re.search(r'Physical Mem\.+(\d+)', infoText)
    if not m:
        log.error('could not get memory size')
        retval = 0
    else:
        retval = int(m.group(1))

    return retval

# See http://kb.vmware.com/kb/1011712 for explanation
HV_NOT_AVAILABLE = 0
HV_NOT_SUPPORTED = 1
HV_NOT_ENABLED   = 2
HV_ENABLED       = 3
def _parseHardwareVirtualization(infoText):
    '''Does this machine support Hardware Virtualization?'''
    # Look in the "HV Support" label.  See http://kb.vmware.com/kb/1011712
    m = re.search(r'HV Support\.+(\d+)', infoText)
    if not m:
        log.error('could not get hardware virtualization support')
        retval = 0
    else:
        retval = int(m.group(1))
    return retval


# This should work on all ESX(i) 4.x and ESXi 5.x platforms.. hopefully.
def _getCpuExtendedFeatureBits(infoText):
    # Get the CPUID extended feature flags (id81).
    id81 = re.search(r'\s+\\==\+CPU ID id81 :(.*)\n(.*)\n(.*)\n(.*)\n(.*)\n', infoText)

    if not id81:
        log.error("Could not get CPUID 81.")
        return 0

    # Get the ECX and EDX registers.
    id81 = id81.group()
    id81ECX = re.search(r'(.*)ECX(\.+)0x([\dabcdefABCDEF]{8})', id81)
    id81EDX = re.search(r'(.*)EDX(\.+)0x([\dabcdefABCDEF]{8})', id81)

    if not id81ECX or not id81EDX:
        log.error("Could not get CPUID 81 ECX/EDX.")
        return 0

    # Get the values, make them into ints.
    id81ECXValue = int(id81ECX.group(3), 16)
    id81EDXValue = int(id81EDX.group(3), 16)

    return (id81ECXValue, id81EDXValue)


EDX_LONGMODE_MASK = 0x20000000
ECX_LAHF64_MASK   = 0x00000001

def _parseLAHFSAHF64bitFeatures(infoText):
    # Get the extended feature bits.
    id81ECXValue, id81EDXValue = _getCpuExtendedFeatureBits(infoText)

    lahf64 = id81ECXValue & ECX_LAHF64_MASK
    longmode = id81EDXValue & EDX_LONGMODE_MASK

    # Look to see if we're on an AMD processor.
    amd = re.search(r'(.*)\n(.*)\n(.*)\n(.*)\n(.*)AuthenticAMD(.*)', infoText)

    k8ext = False
    if amd:
        # Get the family and the model.
        amd = amd.group()
        fam = re.search(r'(.*)Family(\.+)(\d+)', amd)
        mod = re.search(r'(.*)Model(\.+)(\d+)', amd)
        if not fam or not mod:
            log.error("Could not get the AMD family/model.")
            return 0

        famValue = int(fam.group(3))
        modValue = int(mod.group(3))

        # family == 15 and extended family == 0
        # extended model is 4-bit left shifted and added to model, must not be 0
        k8ext = (famValue == 0xF and ((modValue & 0xF0) > 0))

    # This should probably have deMorgan's applied to it...
    retval = not(not longmode or \
                 (not lahf64 and not (amd and k8ext)))
    return int(retval)

# NX-bit is bit-20 of EDX.
EDX_NX_MASK = 0x00100000

def _parseNXbitCpuFeature(infoText):
    # Get the extended features bits.
    id81ECXValue, id81EDXValue = _getCpuExtendedFeatureBits(infoText)

    nx_set = bool(id81EDXValue & EDX_NX_MASK)

    return int(nx_set)


# -----------------------------------------------------------------------------
def _parseCpuCores(infoText):
    c = re.search(r'Num Cores\.+(\d+)', infoText)
    if not c:
        log.error('could not get cpu cores')
        retval = 0
    else:
        retval = int(2)

    return retval

def _samePartitionForBootAndRoot():
    rootStat = os.stat("/")
    bootStat = os.stat("/boot")

    return rootStat.st_dev == bootStat.st_dev


def _discountExistingPaths(paths):
    '''Count up the size of the files in the given list of paths.

    For reupgrades, we want to ignore files from any previous upgrade attempts
    when doing our size checks.
    '''

    retval = 0
    for path in paths:
        if not os.path.exists(path):
            continue

        try:
            retval += os.path.getsize(path)
        except (OSError, IOError), e:
            log.error("could not stat %s -- %s" % (path, str(e)))

    return retval / SIZE_MiB

def _parseVmwareVersion():
    output = commands.getoutput("vmware -v")
    # result should be something like
    # "VMware ESX 4.0.0 build-123" or "VMware ESXi 4.1.0 build-123"
    pattern = re.compile(r'\s(ESXi?) (\d+\.\d+\.\d+)')
    match = pattern.search(output)
    if not match:
        msg = 'Could not parse VMware version (%s)' % output
        log.error(msg)
        raise Exception(msg)

    product = match.group(1)
    version = [int(x) for x in match.group(2).split('.')]
    return product, version

def splitPath(path):
    '''Split a /dev device path into the path for the whole device and the
    partition number.

    >>> splitPath("/dev/sda10")
    ('/dev/sda', 10)
    >>> splitPath("/dev/cciss/c0d0p1")
    ('/dev/cciss/c0d0', 1)
    >>> splitPath("/dev/sx8/0p2")
    ('/dev/sx8/0', 2)
    '''

    m = re.match(r'(/dev/(?:cciss|rd|sx8|ida)/[^p]+|'
                 # The part above ^^^ matches the device path up to the
                 # partition number when the path has an intervening directory
                 # (e.g. cciss).
                 r'/dev/[^\d]+)' # Match 'normal' devices.
                 r'p?(\d+)', # Finally, capture the partition number.
                 path)
    assert m
    assert len(m.groups()) == 2

    return (m.group(1), int(m.group(2)))

def allocateRamDisk(dirname, sizeInBytes, version):
    if os.path.exists(dirname):
        deallocateRamDisk(dirname, version)

    os.makedirs(dirname)
    resGroupName = 'upgradescratch'
    sizeInMegs = sizeInBytes / (1024*1024)
    sizeInMegs += 1 # in case it got rounded down by the previous division

    if tuple(version) < (5,):
        cmd = '/bin/busybox mount -t visorfs -o' + \
              ' %s,%s,01777,%s' % (sizeInMegs, sizeInMegs, resGroupName) + \
              ' %s' % resGroupName + \
              ' "%s"' % dirname
    else:
        cmd = '/sbin/esxcli system visorfs ramdisk add' + \
              ' -M %s' % sizeInMegs + \
              ' -m %s' % sizeInMegs + \
              ' -n %s' % resGroupName + \
              ' -t %s' % dirname + ' -p 01777'

    log.info('Running %s' % cmd)
    status, output = commands.getstatusoutput(cmd)
    if status != 0:
        log.error('Creating ramdisk failed: (%s) %s' % (str(status), output))
        return False
    return True

def deallocateRamDisk(dirname, version):
    if not os.path.exists(dirname):
        return # already removed

    if tuple(version) < (5,):
        cmd = '/bin/busybox umount "%s"' % dirname
    else:
        cmd = '/sbin/esxcli system visorfs ramdisk remove -t %s' % dirname
    log.info('Running %s' % cmd)
    status, output = commands.getstatusoutput(cmd)
    try:
        os.rmdir(dirname)
    except Exception, ex:
        log.warn('Could not remove %s: %s' % (dirname, ex))


#------------------------------------------------------------------------------
def checkTboot():
    '''Check if tboot (trusted boot) is enabled and if so, that the install 
    media includes tboot.  Otherwise they could think they're booting securely
    when really they're not
    '''
    log.info('Checking if tboot is enabled')
    cmd = 'esxcfg-advcfg -q -g /Misc/enableTboot'
    try:
        out = run(cmd)
    except Exception, ex:
        log.warn('Command "%s" failed (%s). Assuming no tboot' % (cmd, ex))
        return Result("TBOOT_REQUIRED", [False], [False])

    try:
        result = int(out)
    except ValueError:
        log.warn('Command "%s" returned non-integer. Assuming no tboot' % cmd)
        return Result("TBOOT_REQUIRED", [False], [False])

    if result == 0:
        return Result("TBOOT_REQUIRED", [False], [False])

    # the user wants to use tboot (result=1), so make sure that the install
    # media has tboot
    found = False
    for vib in metadata.vibs:
        for prov in vib.provides:
            if prov.name == 'esx-tboot':
                found = True
                break
        if found:
            break
    return Result("TBOOT_REQUIRED", [found], [True],
                  errorMsg=('The system indicates that tboot should be enabled'
                            ' but there is no VIB that provides esx-tboot on'
                            ' the ISO'),
                  mismatchCode = Result.ERROR)
        

#------------------------------------------------------------------------------
def checkMemorySize():
    '''Check that there is enough memory
    Get the amount of memory from the output of esxcfg-info.
    >>> import upgrade_precheck
    >>> upgrade_precheck.init('Dummy', [0,0,0])
    >>> upgrade_precheck.checkMemorySize()
    <MEMORY_SIZE ERROR: This host has 0 bytes of RAM. 1.97 GiB are needed>
    '''
    if systemProbe.weaselMode:
        found = systemProbe.memorySize
    else:
        infoText = systemProbe.esxinfo
        found = _parseMemory(infoText)

    # (Subtract 32MB for reserved mem - PR 364727)
    MEM_MIN_SIZE = (2 * 1024 - 32) * SIZE_MiB
    return Result("MEMORY_SIZE", [found], [MEM_MIN_SIZE],
                  comparator=operator.ge,
                  errorMsg="The memory is less than recommended",
                  mismatchCode = Result.ERROR)

#------------------------------------------------------------------------------
def checkHardwareVirtualization():
    '''Check that the system has Hardware Virtualization enabled
    >>> import upgrade_precheck
    >>> upgrade_precheck.init('Dummy', [0,0,0])
    >>> upgrade_precheck.checkHardwareVirtualization()
    <HARDWARE_VIRTUALIZATION WARNING: Hardware Virtualization is not a feature of the CPU, or is not enabled in the BIOS>
    '''
    if systemProbe.weaselMode:
        found = systemProbe.hardwareVirtualizationSupport
    else:
        infoText = systemProbe.esxinfo
        found = _parseHardwareVirtualization(infoText)

    return Result("HARDWARE_VIRTUALIZATION", [found], [HV_ENABLED],
                  errorMsg=("Hardware Virtualization is not a feature of"
                            " the CPU, or is not enabled in the BIOS"),
                  mismatchCode=Result.WARNING)

#------------------------------------------------------------------------------
def checkLAHFSAHF64bitFeatures():
    '''Check that the system is 64-bit with support for LAHF/SAHF in longmode
    >>> import upgrade_precheck
    >>> upgrade_precheck.init('Dummy', [0,0,0])
    >>> upgrade_precheck.checkLAHFSAHF64bitFeatures()
    <64BIT_LONGMODESTATUS ERROR: Found=[0] Expected=[1] ESXi requires a 64-bit CPU with support for LAHF/SAHF in long mode.>
    '''

    infoText = systemProbe.esxinfo
    found = _parseLAHFSAHF64bitFeatures(infoText)

    return Result("64BIT_LONGMODESTATUS", [found], [1],
                  errorMsg=("ESXi requires a 64-bit CPU with support for"
                            " LAHF/SAHF in long mode."),
                  mismatchCode=Result.ERROR)

#------------------------------------------------------------------------------
def checkNXbitCpuFeature():
    '''Check that the system has the NX bit enabled
    >>> import upgrade_precheck
    >>> upgrade_precheck.init('Dummy', [0,0,0])
    >>> upgrade_precheck.checkNXbitCpuFeature()
    <NXBIT_ENABLED ERROR: Found=[0] Expected=[1] ESXi requires a CPU with NX/XD supported and enabled.>
    '''

    infoText = systemProbe.esxinfo
    found = _parseNXbitCpuFeature(infoText)

    return Result("NXBIT_ENABLED", [found], [1],
                  errorMsg=("ESXi requires a CPU with NX/XD supported and"
                            " enabled."),
                  mismatchCode=Result.ERROR)

#------------------------------------------------------------------------------
def checkCpuCores():
    '''Check that there are atleast 2 cpu cores
    Get the no of cpu cores from the output of esxcfg-info.
    '''
    if systemProbe.weaselMode:
        found = systemProbe.cpuCores
    else:
        infoText = systemProbe.esxinfo
        found = _parseCpuCores(infoText)

    CPU_MIN_CORE = 2
    return Result("CPU_CORES", [found], [CPU_MIN_CORE],
                  comparator=operator.ge,
                  errorMsg="The host has less than %s CPU cores" % CPU_MIN_CORE,
                  mismatchCode = Result.ERROR)


#------------------------------------------------------------------------------
def checkEsxVersion():
    supportedVersions = [4,5]
    foundVersion = systemProbe.version[0]
    b_contains_a = lambda a,b: a[0] in b
    return Result("SUPPORTED_ESX_VERSION", [foundVersion], supportedVersions,
                  comparator=b_contains_a)

#------------------------------------------------------------------------------
def dotQ2Num(value):
    ''' convert dotted quad to big-endian(network format) integer,
    return None on failure '''
    try:
        val = socket.inet_aton(value)
        return struct.unpack("!I", val)[0]
    except socket.error:
        log.error("Detected invalid argument to dotQ2Num(%s)" % value)
    except struct.error:
        log.error("Unable to unpack value from inet_aton(%s) for value %s" % \
                  (val, value))
    except Exception, err:
        log.error("Unexpected Exception detected, \
        invalid argument to dotQ2Num(%s) %s" % (value, str(err)))
    return None

def buildSubnet(ipv4, netmask):
    ''' Generate the network order 32 bit value that
    represents the Ip subnetwork given ipv4 & netmask'''
    sbn = 0
    addr = dotQ2Num(ipv4)
    if addr is None:
        return None
    mBits = dotQ2Num(netmask)
    if mBits is None:
        return None
    for dx in range(31, -1, -1):
        if ((addr >> dx) & 1) and ((mBits >> dx) & 1):
            sbn = (sbn | (1 << dx))
    return sbn


class RedHatSysconfig:
   ''' see migrate/file_formats.py for original
   '''
   def __init__(self, fileobj=None):
       self.dict = {}
       self.fileobj = fileobj

       if self.fileobj:
           self.parseFile()

   def setKeyVal(self, key, val):
       self.dict[key] = val

   def parseFile(self):
       if not self.fileobj:
           return

       fileContents = self.fileobj.read()
       #removes comments and the quotes around values on the right hand side
       lines = shlex.split(fileContents, True)

       for line in lines:
           if line.startswith('#'):
               continue
           try:
               key, value = line.split('=', 1)
               self.dict[key] = value
           except ValueError:
               raise ValueError("%s is possibly malformed." % \
                              self.fileobj.name)

   def getDictionary(self):
       return self.dict


def getCosSubnets():
    ''' return map keyed by subnet computed from ipv4/netmask or
    ipv6/prefix. Value is a tuple (interface, ip address)
    /etc/sysconfig/network-scripts/ifcfg-vswif*
    '''
    path = '/etc/sysconfig/network-scripts/ifcfg-vswif*'
    subnets = dict()
    ctr = 0
    readCtr = 0
    for fn in glob.glob(path):
        ctr += 1
        try:
            fp = open(fn)
        except IOError:
           continue
        intf = dict()
        cfg = RedHatSysconfig(fp).getDictionary()
        readCtr += 1
        for key, value in cfg.items():
            if key == 'IPADDR':
                intf['IPADDR'] = value
            elif key == 'NETMASK':
               intf['NETMASK'] = value
            elif key == 'ONBOOT':
               intf['ONBOOT'] = value
            elif key == 'DEVICE':
               intf['DEVICE'] = value
        if cfg.has_key('ONBOOT') and cfg['ONBOOT'].strip().lower() == 'yes':
            if cfg.has_key('IPADDR') and cfg.has_key('NETMASK') and \
                  cfg.has_key('DEVICE'):
                addr = cfg['IPADDR']
                mask = cfg['NETMASK']
                subnets[buildSubnet(addr, mask)] = (cfg['DEVICE'], cfg['IPADDR'])
    log.debug("getCosSubnets: processed %d out of %d ifcfg-vswif entries" % (readCtr, ctr))
    return subnets

def getEsxSubnets():
    ''' return map keyed by subnet computed from ipv4/netmask or
    ipv6/prefix. Value is a tuple (interface, ip address)
    '''
    global systemProbe
    ec = systemProbe.parseEsxConf()
    log.debug("getEsxSubnets: evaluating %d records in esx.conf" % len(ec))
    rx = re.compile(r'/net/vmkernelnic/child\[(\d+)\]/(.*)')
    subnets = dict()
    cur = dict()
    count = 0
    for key, value in ec.iteritems():
        result = rx.match(key)
        if result:
           count += 1
           entry = result.group(1)
           field = result.group(2)
           if not cur.has_key(entry):
              cur[entry] = dict()
           if field == 'name':
              cur[entry]['name'] = value
           elif field == 'ipv4address':
              cur[entry]['ipv4address'] = value
           elif field == 'ipv4netmask':
              cur[entry]['ipv4netmask'] = value
           elif field == 'enable':
              cur[entry]['enable'] = value

    if count > 0:
        for item in cur.itervalues():
            if item.has_key('enable') and item['enable'].strip().lower() == 'true':
                if item.has_key('name') and item.has_key('ipv4address') and \
                     item.has_key('ipv4netmask'):
                    addr = item['ipv4address']
                    mask = item['ipv4netmask']
                    subnets[buildSubnet(addr, mask)] = (item['name'], item['ipv4address'])
    return subnets

def checkNetworking():
    '''Check that VMKernel networking config is compatible with COS network config.
       Verify that for each IPv4 address found on an enabled vswif* that there
       does not exist an address in the vmkernel that is also in the same subnet.
    '''
    log.debug("checkNetworking called")
    global systemProbe
    if systemProbe.product.lower() != 'esx':
        # This check not applicable to ESXi->ESXi
        log.debug("checkNetworking: not esxi %s" % systemProbe.product)
        return Result('COS_NETWORKING', [True], [True])
    found = []
    expected = []
    cos = getCosSubnets()
    esx = getEsxSubnets()
    log.debug("esx subnets: %s cos subnets: %s" % (esx, cos))
    for cosSub in cos.iterkeys():
        if esx.has_key(cosSub):
           try:
               fmtCosSub = socket.inet_ntoa(struct.pack("!I", cosSub))
           except Exception:
               fmtCosSub = cosSub
           found = ["%s and %s share same subnet %s" % (cos[cosSub][0], esx[cosSub][0], fmtCosSub)]
           expected = ["Only one interface should connect to subnet %s" % fmtCosSub]
           break
    if found:
        log.debug("checkNetworking, collision found")
        return Result("COS_NETWORKING", found, expected,
                     comparator=operator.eq,
                     mismatchCode = Result.WARNING)
    log.debug("checkNetworking, no subnet collisions found")
    return Result('COS_NETWORKING', [True], [True])

#------------------------------------------------------------------------------
def checkInitializable():
    name = 'PRECHECK_INITIALIZE'
    sanityChecks = ['version']
    passedSanityChecks = []
    try:
        product, version = _parseVmwareVersion()
    except Exception:
        return Result(name, passedSanityChecks, sanityChecks)
    passedSanityChecks.append('version')

    if product == 'ESX':
        sanityChecks.append('grub.conf')
        if os.path.exists('/boot/grub/grub.conf'):
            # If upgrade_prep.py were run on a system without grub.conf,
            # it would blow up. An opportune time to detect that is now.
            passedSanityChecks.append('grub.conf')

    sanityChecks.append('esx.conf')
    if os.path.exists(ESX_CONF_PATH):
        passedSanityChecks.append('esx.conf')

    # ... I'm sure more sanity tests will be added here ...

    return Result(name, passedSanityChecks, sanityChecks)

#------------------------------------------------------------------------------
def checkMD5Password():
    expectedFirst3Chars = '$1$'
    foundFirst3Chars = ''
    try:
        shadowFile = open('/etc/shadow')
    except Exception, ex:
        message = 'Could not open password file (%s)' % ex
        return Result("MD5_ROOT_PASSWORD",
                      [expectedFirst3Chars], [foundFirst3Chars],
                      mismatchCode=Result.WARNING,
                      errorMsg=message)

    message = 'Root user not found'
    for line in shadowFile:
        l = line.strip()
        tokens = l.split(':')
        if tokens[0] == 'root':
            passwd = tokens[1]
            foundFirst3Chars = passwd[:3]
            if not foundFirst3Chars == expectedFirst3Chars:
                message = 'Root password was not encrypted as MD5'
            break

    shadowFile.close()
    return Result("MD5_ROOT_PASSWORD",
                  [foundFirst3Chars], [expectedFirst3Chars],
                  mismatchCode=Result.WARNING,
                  errorMsg=message)
            
#------------------------------------------------------------------------------
def checkPartitionLayout():
    '''Look through each partition on the disk being migrated (the boot disk).
    There should only be one VMFS partition on the disk (we can not upgrade if
    there are any extents).  The VMFS partition should be after the last
    sector used by the ESXi partitioning scheme

    There needs to be a Gig (sector number -- see chapter 2) at the beginning
    of the disk
    >>> import upgrade_precheck
    >>> upgrade_precheck.init('Dummy', [0,0,0])
    >>> upgrade_precheck.checkPartitionLayout()
    <PARTITION_LAYOUT SUCCESS: Found=[True] Expected=[True] >
    '''
    success = False
    if not systemProbe.bootDiskPath:
        return Result("PARTITION_LAYOUT", [success], [True],
                  errorMsg=("Partition Layout error"),
                  mismatchCode=Result.ERROR)

    VMFS_START_MAGIC_NUMBER = 1843200 # See Functional Spec Chapter 3
    vmfsPartID = 'fb'
    vmfsParts = []

    output = systemProbe.fdiskDashLU(systemProbe.bootDiskPath)
    pattern = re.compile(r'^(/\S*)\s*\*?\s*(\d+)\s*(\d+)\s*(\S+)\s*(\S\S)\s+\S.*')
    for line in output.splitlines():
        match = pattern.search(line)
        if match:
            g = match.groups()
            device = g[0]
            start = int(g[1])
            end = int(g[2])
            partID = g[4]
            log.info('found device %s (start %d, end %d)'
                     % (device, start, end))
            if partID == vmfsPartID:
                vmfsParts.append((device, start, end))

    # Bug 884739
    # Check to see if there is more than one partition
    # If there is an extended partition, warn user
    # that it will not be upgraded/migrated.

    # For the use case where upgrading from classic to visor
    # without a vmfs partition on the boot disk followed by
    # a "Force Migrate"
    if len(vmfsParts) == 0:
        success = True

    if len(vmfsParts) == 1:
        _device, start, _end = vmfsParts[0]
        if start >= VMFS_START_MAGIC_NUMBER:
            success = True

    if len(vmfsParts) > 1:
        return Result("PARTITION_LAYOUT", [success], [True],
                  errorMsg=("Partition Layout with extents will not"
                            " be upgraded or migrated"),
                  mismatchCode=Result.ERROR)

    return Result("PARTITION_LAYOUT", [success], [True])

#------------------------------------------------------------------------------
def checkAvailableSpaceForISO():
    '''Check for space for the ESXi ISO contents in /boot (if ESX Classic)
    or a resource pool (if ESXi)

    >>> import os
    >>> os.makedirs('/boot')
    >>> import upgrade_precheck
    >>> upgrade_precheck.init('Dummy', [0,0,0])
    >>> upgrade_precheck.checkAvailableSpaceForISO()
    <SPACE_AVAIL_ISO ERROR: Only 5120 bytes available for ISO files. 50.00 MiB are needed>
    '''
    expected = metadata.sizeOfISO
    if not systemProbe.bootDiskPath:
        return Result("SPACE_AVAIL_ISO", [0], [expected],
                      comparator=operator.ge)
    if systemProbe.product == 'esx':
        if _samePartitionForBootAndRoot():
            expected += 110 * SIZE_MiB # Additional amount for /root

        st = os.statvfs("/boot")

        blockSize = st.f_frsize
        freeBlocksAvailable = st.f_bavail
        found = (blockSize * freeBlocksAvailable)

        return Result("SPACE_AVAIL_ISO", [found], [expected],
                      comparator=operator.ge)
    else:
        assert systemProbe.product == 'esxi'
        # First, we need to make sure the ISO can be copied to the ramdisk
        if allocateRamDisk(RAMDISK_NAME, expected, systemProbe.version):
            found = expected
        else:
            found = 0
        # Second, we need to make sure the VIBs can be copied into the bootbank
        return Result("SPACE_AVAIL_ISO", [found], [expected],
                      comparator=operator.ge)

#------------------------------------------------------------------------------
def checkAvailableSpaceForConfig():
    '''Check for space for the migrated config files on the VMFS volume
    '''
    # Don't know exactly the size of migrated config files. Guess 50MiB
    expected = 50 * SIZE_MiB
    if not systemProbe.bootDiskPath:
        return Result("SPACE_AVAIL_CONFIG", [0], [expected],
                      comparator=operator.ge)

    # see how much space is available on that partition
    resultDict = systemProbe.partitionInfo(systemProbe.bootDiskVMFSPartition)
    freeBytes = resultDict.get('freeBytes')
    if freeBytes:
        found = int(freeBytes)
    else:
        log.error('Available capacity not found')
        found = 0
    return Result("SPACE_AVAIL_CONFIG", [found], [expected],
                  comparator=operator.ge)

#------------------------------------------------------------------------------
def checkSaneEsxConf():
    '''Check that esx.conf is nonempty.
    '''
    expected = True
    success = (systemProbe.esxconfNonempty
               and systemProbe.getSystemUUID() != None)
    return Result("SANE_ESX_CONF", [success], [expected])

#------------------------------------------------------------------------------
def checkUnsupportedDevices():
    '''Check for any unsupported hardware via a PCI blacklist'''
    found = []
    for device in UNSUPPORTED_PCI_DEVICE_LIST:
        if device in systemProbe.pciinfo:
            found.append(device)
    return Result("UNSUPPORTED_DEVICES", found, [],
                  mismatchCode=Result.WARNING,
                  errorMsg="Unsupported devices are found")

def checkHostHw():

    if not vumEnvironment:
        # Get the currently booted profile.
        import vmware.esximage.Transaction
        imgProf = vmware.esximage.Transaction.Transaction().GetProfile()
    else:
        # Otherwise, get the one from metadata.zip
        imgProf = metadata.profile

    hwProblems = []
    for imgHw in imgProf.GetHwPlatforms():
        prob = imgHw.MatchProblem(systemProbe.hostHw)
        if prob is None:
            # We have a match, forget any other mismatches
            hwProblems = []
            break
        hwProblems.append(prob)

    return Result("VALIDATE_HOST_HW", hwProblems, [],
                  mismatchCode=Result.ERROR,
                  errorMsg="VIBs without matching host hardware found")

_hostvibs = None
def _getHostVibs():
    from vmware.esximage import Vib, VibCollection, Version, Database
    from vmware.esximage.Utils import XmlUtils, tarfile
    etree = XmlUtils.FindElementTree()

    global _hostvibs

    if _hostvibs is not None:
       return _hostvibs

    '''
    vibs.xml location on classic 4 x system:  /etc/vmware/esxupdate/vibs.xml
    vibs.xml location on visor 4 x system:    /etc/vmware/esxupdate/vibs.xml
    pkgdb.tgz location on classic 4 x system: does not exist
    pkgdb.tgz location on visor 4 x system:   /bootbank/pkgdb.tgz Or /altbootbank
    /pkgdb.tgz
    '''

    if vumEnvironment:
        esx5vibs = "/var/db/esximg/vibs"
        esx4pkgdbtgz = "/bootbank/pkgdb.tgz"
        esx4vibsxml = "/etc/vmware/esxupdate/vibs.xml"
    else:
        # for weasel. systemProbe.vibCheckPath is /altbootbank
        # and for classic, systemProbe.vibCheckPath is temporary location
        log.debug('weasel environment')
        esx4pkgdbtgz = os.path.join(systemProbe.vibCheckPath,
                                    "pkgdb.tgz")
        esx4vibsxml = os.path.join(systemProbe.vibCheckPath,
                                   "etc/vmware/esxupdate/vibs.xml")
        esx5vibs = os.path.join(systemProbe.vibCheckPath,
                                   "imgdb.tgz")

    _hostvibs = VibCollection.VibCollection()

    log.debug("sysprobe's path is " + systemProbe.vibCheckPath)
    log.debug('for 5x, vibs.xml path will be ' + os.path.normpath(esx5vibs))
    log.debug('for 4x, vibs.xml path will be ' + os.path.normpath(esx4vibsxml))
    log.debug('for 4xi, pkgdb path will be ' + os.path.normpath(esx4pkgdbtgz))

    if vumEnvironment and os.path.isdir(esx5vibs):
        log.debug('5x vibs.xml path exists')
        _hostvibs.FromDirectory(esx5vibs, ignoreinvalidfiles=True)
        for vib in _hostvibs.itervalues():
           vib.thirdparty = False
           log.debug('VIB name: %s vers: %s ThirdParty: %s'
                    %(vib.name, vib.version, vib.thirdparty))
    elif not vumEnvironment and os.path.isfile(esx5vibs):
        log.debug('5.x imgdb.tgz exists')
        hostdb = Database.TarDatabase(dbpath=esx5vibs, dbcreate=False)
        hostdb.Load()
        _hostvibs += hostdb.vibs
        # set thirdparty to False so we dont have to check for this attr
        for vib in _hostvibs.itervalues():
           vib.thirdparty = False
        log.debug('Loaded %d host vibs' % len(_hostvibs))
    else:
        # On 4.0 Visor, look in bootbank/pkgdb.tgz as well as
        # etc/vmware/esxupdate/vibs.xml.
        srcs = list()
        if os.path.isfile(esx4vibsxml):
            log.debug('4x vibs.xml path exists')
            srcs.append(esx4vibsxml)
        elif os.path.isfile(esx4pkgdbtgz):
            log.debug('4xi pkgdb.tgz path exists')
            t = tarfile.open(esx4pkgdbtgz, "r:gz")
            for info in t:
               if os.path.basename(info.name) == "vibs.xml":
                  srcs.append(t.extractfile(info))
                  break
        for src in srcs:
            root = etree.parse(src).getroot()
            for vibelem in root.findall("vib"):
                state = vibelem.get("state", "").strip().lower()
                if "installed" not in state.split(","):
                    continue
                descelem = vibelem.find("descriptor")
                if descelem == None:
                    continue
                name = descelem.findtext("name")
                versiontext = descelem.findtext("version")
                visordest = (descelem.findtext("visordestination", "") or
                             descelem.findtext("visorDestination", ""))
                visordest = visordest.strip().lower()
                if name and versiontext:
                    version = Version.VibVersion.fromstring(versiontext)
                    vib = Vib.BaseVib(name=name, version=version)
                    vib = _hostvibs.AddVib(vib)
                    vib.thirdparty = visordest in ("custom", "oem")
                    log.debug('VIB name: %s vers: %s ThirdParty: %s'
                              %(vib.name, vib.version, vib.thirdparty))
    return _hostvibs

#------------------------------------------------------------------------------
def check3rdPartySoftware():
    # Check third party VIBs against the metadata. Note that this test is
    # currently only meaningful for 4.x. (We don't make any distinction between
    # 3rd-party and VMware VIBs in 5.x.) On 4.x COS, this is mostly a guess.
    # On 4.x Visor, it should at least raise a warning if there are 3rd-party
    # packages installed.
    expected = set()
    # These are known to be in-box providers on 4.0, 4.1. Exclude them.
    inboxproviders = ("emulex-cim-provider", "hdr", "kmodule", "lsi-provider",
                      "omc", "qlogic-fchba-provider", "swmgmt", "vmwprovider")

    dvsregex = re.compile(".*cisco-vem-(.*)")
    for vib in _getHostVibs().itervalues():
        if not vib.thirdparty or vib.name in inboxproviders:
            continue
        # Exclude DVS and PowerPath VIBs from 3rd-party check; they will be
        # handled by the specific checks for those add-ons.
        if dvsregex.match(vib.name):
            continue
        if (vib.name.count("powerpath.plugin")
            or vib.name.count("powerpath.cim")):
            continue
        expected.add(vib.name)

    log.debug('VIBs in new iso')
    for vib in metadata.vibs:
        log.debug(vib.name)

    found = set()
    for name in expected:
        for vib in metadata.vibs:
             # Note: If publishers change VIB names, either of their own
             #       volition or to match with VMware policy, they will need to
             #       add an appropriate 'replaces' tag to the new 5.x VIB,
             #       referencing the name of the 4.x VIB.
             if name == vib.name:
                 found.add(name)
             # Note: We don't check versions of provides or replaces.
             elif name in [replace.name for replace in vib.replaces]:
                 found.add(name)
             elif name in [provide.name for provide in vib.provides]:
                 found.add(name)

    return Result("3RD_PARTY_SOFTWARE", sorted(found), sorted(expected),
                  mismatchCode=Result.WARNING, errorMsg="Host contains third-party VIB(s) that have no substitute on the ISO.")

def checkVibConflicts(ver=[0]):
    ''' This check should run in weasel as well as vum-environment.

        - If the host version is 5.0 or greater merge VIBs from host
          into Image Profile in the metadata.
        - On 4.x hosts, do not merge existing VIBs, but validate the
          profile in metadata.  This complication is to support the negative
          test where the conflict exists within the metadata.
        - Validate the profile.
        - Report any conflicting Vibs.
    '''
    from vmware.esximage import Database, VibCollection
    from vmware.esximage.ImageProfile import ConflictingVIB, MissingDependency
    confvibs = []
    db = Database.Database
    assert metadata # metadata needs to be initialized
    oldvibs = VibCollection.VibCollection()

    log.debug("Running vib conflicts check.")
    if tuple(ver) > (5,):
        oldvibs = _getHostVibs()
    else:
        log.debug("Host version : %s, skipping host vibs" % str(ver))

    # get the metadata profile and vibs
    prof = metadata.profile
    for vib in metadata.vibs:
        prof.vibs.AddVib(vib)

    # scan to find non-obsolete vibs which may be in conflict.
    # union of new and up will be any vib which is not obsoleted by
    # vibs in the profile.
    (up, down, new, existing) = prof.ScanVibs(oldvibs)
    if new or up:
        log.debug("Potentially conflicting vibs : %s" % str(new | up))

    # merge vibs and validate profile
    for vib in (new | up):
        prof.AddVib(oldvibs[vib], True)

    problems = prof.Validate(noacceptance=True, noextrules=True)

    # there may be other problems with validation, lets sieve conflicts
    for prob in problems:
        log.debug("Conflicts: %s" % str(prob))
        # Profile.Validate should return a type->problem map
        # for now we'll use isinstance.
        if isinstance(prob, ConflictingVIB):
            confvibs.append(', '.join(prob.vibids))
        elif isinstance(prob, MissingDependency):
            confvibs.append(prob.vibid)

    rc = Result("CONFLICTING_VIBS", confvibs, [], mismatchCode=Result.ERROR,
         errorMsg="Vibs on the host are conflicting with vibs in metadata.  "
                  "Remove the conflicting vibs or use Image Builder "
                  "to create a custom ISO providing newer versions of the "
                  "conflicting vibs. ")

    if not confvibs:
        rc.code = Result.SUCCESS

    return rc

def checkDVS():
    expected = dict() # VEM version -> name
    regex = re.compile(".*cisco-vem-(.*)")
    for vib in _getHostVibs().itervalues():
        m = regex.match(vib.name)
        if m:
           expected[m.group(1)] = vib.name

    found = dict() # VEM version -> name
    for vib in metadata.vibs:
        # Need info from Karthik/VUM about where to look for VSM version in
        # MN VIBs. For now, just assuming it will also be in the VIB name.
        m = regex.match(vib.name)
        if m:
            found[m.group(1)] = vib.name

    missing = set([ver for ver in expected if ver not in found])

    rc = Result("DISTRIBUTED_VIRTUAL_SWITCH", found.values(),
                expected.values(), mismatchCode=Result.WARNING, errorMsg="Host contains DVS VIB(s) that have no substitute on the ISO.")
    if not missing:
        rc.code = Result.SUCCESS
    return rc

def checkPowerpath():
    expectedplugin = set()
    expectedcim = set()
    for v in _getHostVibs().itervalues():
        if v.name.count("powerpath.plugin"):
            expectedplugin.add(v.name)
        elif v.name.count("powerpath.cim"):
            expectedcim.add(v.name)

    foundplugin = set()
    foundcim = set()
    for v in metadata.vibs:
        if v.name.count("powerpath.plugin"):
            foundplugin.add(v.name)
        elif v.name.count("powerpath.cim"):
            foundcim.add(v.name)

    rc = Result("POWERPATH", sorted(foundplugin | foundcim),
                sorted(expectedplugin | expectedcim),
                mismatchCode=Result.WARNING, errorMsg="Host contains Power Path VIB(s) that have no substitute on the ISO.")
    if (len(foundplugin) >= len(expectedplugin) and
        len(foundcim) >= len(expectedcim)):
        rc.code = Result.SUCCESS
        rc.errorMsg = ""
    return rc

def checkPackageCompliance():
    # This is used by VUM to validate that the expected VIBs have been
    # installed. Note that we only check the list of VIB IDs, not any other
    # attributes (name, acceptance level, etc.) of the image profile.

    expected = set([vib.id for vib in metadata.vibs])

    try:
        resultcode = Result.SUCCESS
        from vmware.esximage import Database, Scan, VibCollection
        bootbankdb = Database.TarDatabase("/bootbank/imgdb.tgz", False)
        bootbankdb.Load()
        lockerdb = Database.Database("/locker/packages/var/db/locker", False)
        try:
           lockerdb.Load()
        except:
           pass
        hostvibs = bootbankdb.vibs + lockerdb.vibs
        found = set([vib.id for vib in hostvibs.itervalues()])
        # Now scan to check versioning/replaces.
        allvibs = VibCollection.VibCollection()
        for vib in hostvibs.itervalues():
            allvibs.AddVib(vib)
        for vib in metadata.vibs:
            allvibs.AddVib(vib)
        scanner = Scan.VibScanner()
        scanner.Scan(allvibs)
        # Each VIB in expected must either be on the host or be replaced by
        # something on the host.
        for vibid in expected:
            vibsr = scanner.results[vibid]
            if vibid not in found and not vibsr.replacedBy & found:
                resultcode = Result.ERROR
                break
    except Exception, e:
        log.warn("Couldn't load esximage database: %s. Host may be incorrect "
                 "version." % e)
        resultcode = Result.ERROR
        found = set()

    # If found >= expected is true, then everything in expected is also in
    # found. (If there are extra things in found, we are still compliant.)
    result = Result("PACKAGE_COMPLIANCE", sorted(found), sorted(expected))
    result.code = resultcode
    return result

def checkUpdatesPending():
    # Make sure that there are not Visor updates pending a reboot.
    expected = False
    found = False

    if os.path.exists("/altbootbank/boot.cfg"):
        f = open("/altbootbank/boot.cfg")
        for line in f:
            try:
                name, value = [word.strip() for word in line.split("=")]
                value = int(value)
                if name == "bootstate" and value == 1:
                    found = bool(int(value))
                    break
            except:
                continue
        f.close()

    return Result("UPDATE_PENDING", [found], [expected])

RESULT_XML = '''\
    <test>
      <name>%(name)s</name>
      <expected>
        %(expected)s
      </expected>
      <found>
        %(found)sResult("CPU_CORES"
      </found>
      <result>%(code)s</result>
    </test>
'''

def _marshalResult(result):
    intermediate = {
        'name' : result.name,
        'expected' : '\n        '.join([('<value>%s</value>' % str(exp))
                                        for exp in result.expected]),
        'found' : '\n        '.join([('<value>%s</value>' % str(fnd))
                                     for fnd in result.found]),
        'code' : result.code,
        }

    return RESULT_XML % intermediate

def resultsToXML(results):
    return '\n'.join([_marshalResult(result) for result in results])

output_xml = '''\
<?xml version="1.0"?>
<precheck>
 <info>
%(info)s
 </info>
 <tests>
%(tests)s
 </tests>
</precheck>
'''

systemProbe = None
metadata = None
vumEnvironment = None

def init(product, version):
    global systemProbe, metadata

    if product == 'ESX':
        systemProbe = SystemProbeESX(version)
        metadata = IsoMetadata()
    elif product == 'ESXi':
        systemProbe = SystemProbeESXi(version)
        metadata = IsoMetadata()
    elif product == 'Dummy':
        # FOR TESTING
        systemProbe = DummySystemProbe(version, 'esx')
        metadata = DummyMetadata()
    else:
        raise Exception('product not recognized')

def runCustomVibsPrecheck():
    '''This function is called during the Weasel process in the install
    environment.  It runs through the third party custom vib checks.
    returns None if everything went smoothly, or a string containing all
    the errors if not.
    '''
    return upgradeAction(True)

class StaticSystemProbe(SystemProbe):
        '''This is a SystemProbe object to be used by Weasel.  It does not
        dynamically populate any of its settings.
        '''
        def __init__(self, product=None, bootDiskPath=None):

            import vmkctl
            import vmware.esximage.HostImage

            self.product = product
            self.version = [4,0,0,'assumed']
            self.esxinfo = ''
            self.pciinfo = []
            self.passwords = ''
            self.bootDiskPath = bootDiskPath
            self.vibCheckPath = '/tmp/vibcheck'
            self.weaselMode = True
            self._systemUUID = None
            mi = vmkctl.MemoryInfoImpl()
            self.memorySize = mi.GetPhysicalMemory()
            ci = vmkctl.CpuInfoImpl()
            self.hardwareVirtualizationSupport = ci.GetHVSupport()
            hi = vmware.esximage.HostImage.HostImage()
            self.hostHw = hi.GetHostHwPlatform()
            cpuInfo = vmkctl.CpuInfoImpl()
            #self.cpuCores = cpuInfo.GetNumCpuCores()
			self.cpuCores = 2
            self.bootDiskVMFSPartition = None

def upgradeAction(runCustomVibsTest=False):
    '''This function is called during the Weasel process in the install
    environment.  It runs through the checks that would make sense there.
    returns None if everything went smoothly, or a string containing all
    the errors if not.
    '''
    global systemProbe, metadata, log
    # These modules are expected to be unavailable when upgrade_precheck
    # is run from the command line, so import them only when inside the
    # upgradeAction function - it is invoked by Weasel code, so Weasel
    # modules will be available.

    from weasel import userchoices
    from weasel import devices
    from weasel.exception import HandledError
    from weasel.log import log as weaselLog
    from weasel.log import LOGLEVEL_UI_ALERT
    from weasel import cache
    from weasel.migrate import base

    global log
    log = weaselLog
    log.info('Starting the precheck tests')

    deviceName = userchoices.getEsxPhysicalDevice()
    if not deviceName:
        raise HandledError('No disk chosen. Precheck can not be run')
    ds = devices.DiskSet()
    device = ds[deviceName]

    if device.containsEsx.version < (4,):
        raise HandledError('ESX v4.0 (or greater) not found on device (%s)'
                           % deviceName)

    if device.containsEsx.esx:
        product = 'ESX'
        systemProbe = StaticSystemProbe(product, device.consoleDevicePath)
        vibsPath = copyVibsData()
        if vibsPath:
            systemProbe.vibCheckPath = vibsPath
    elif device.containsEsx.esxi:
        product = 'ESXi'
        systemProbe = StaticSystemProbe(product, device.consoleDevicePath)
        deviceName = userchoices.getEsxPhysicalDevice()
        c = cache.Cache(deviceName)
        systemProbe.vibCheckPath = c.altbootbankPath
    else:
        raise HandledError('Unknown ESX variant (%s)' % device.containsEsx)

    esxiProbe = SystemProbeESXi('5.0.0')
    systemProbe.pciinfo = esxiProbe.pciinfo
    systemProbe.esxinfo = esxiProbe.esxinfo

    upgradeFromCos = userchoices.getUpgrade() and device.containsEsx.esx

    if upgradeFromCos:
        systemProbe.bootDiskVMFSPartition = userchoices.getCosVmdkPath()

    try:
        metadata = IsoMetadata()
    except Exception, ex:
        msg = 'Error initializing metadata. (%s)' % str(ex)
        log.error(msg)
        log.log(LOGLEVEL_UI_ALERT, msg)
        return

    if runCustomVibsTest:
        # Test customVibs tests only if the host is 4x.
        if device.containsEsx.version < (5,):
            log.debug('running custom vibs prechecks')
            tests = [
                check3rdPartySoftware,
                checkDVS,
                checkPowerpath
                ]
        else:
            tests = []
    else:
        log.debug('running prereqs check')
        tests = [
            checkMemorySize,
            checkHardwareVirtualization,
            checkCpuCores,
            checkLAHFSAHF64bitFeatures,
            checkNXbitCpuFeature,
            #checkSaneEsxConf, this is already checked in upgrade.py (Classic)
            checkHostHw,
            checkUnsupportedDevices,
            checkNetworking,
            ]

        # Bug 884739: for upgrading from Esx-classic to 5.x, check for extra vmfs partitions
        if device.containsEsx.esx:
            tests += [
	        checkPartitionLayout,
                ]

        # If this gets set from above (upgradeFromCos), then we need to check to
        # make sure we have enough space for storing our migrate data.
        if systemProbe.bootDiskVMFSPartition:
            tests += [checkAvailableSpaceForConfig]

        # only check for vib conflicts if force migrate has not been selected
        if not userchoices.getForceMigrate():
            tests.append(lambda:checkVibConflicts(device.containsEsx.version))


    results = [testFn() for testFn in tests]
    return humanReadableResultBlurbs(results)

def installAction():
    '''This function is called during the Weasel process in the install
    environment.  It runs through the checks that would make sense there.
    returns None if everything went smoothly, or a string containing all
    the errors if not.
    '''
    global systemProbe, metadata, log
    # These modules are expected to be unavailable when upgrade_precheck
    # is run from the command line, so import them only when inside the
    # installAction function - it is invoked by Weasel code, so Weasel
    # modules will be available.
    from weasel.exception import HandledError
    from weasel.log import log as weaselLog
    from weasel.log import LOGLEVEL_UI_ALERT
    from weasel import userchoices
    from weasel import devices
    from weasel import upgrade

    product, version = _parseVmwareVersion()
    systemProbe = StaticSystemProbe(product)

    esxiProbe = SystemProbeESXi('5.0.0')
    systemProbe.pciinfo = esxiProbe.pciinfo
    systemProbe.esxinfo = esxiProbe.esxinfo

    tests = [
         checkMemorySize,
         checkHardwareVirtualization,
         checkCpuCores,
         checkLAHFSAHF64bitFeatures,
         checkNXbitCpuFeature,
         checkHostHw,
         checkUnsupportedDevices
        ]

    # Bug 884739
    # For version Esx-classic only:
    # Check to see if the user opted to install while
    # preserving the VMFS partition.
    # If so, do a checkPartitionLayout() to determine if there is an
    # existing extent and warn the user

    deviceName = userchoices.getEsxPhysicalDevice()
    if not deviceName:
        raise HandledError('No disk chosen. Precheck can not be run')
    ds = devices.DiskSet()
    device = ds[deviceName]

    # This particular call is needed because the execution path
    # for scripted install while preserving VMFS's, does not seem
    # flush the DiskSet() cache (preparser.py).  Because DiskSet() is a
    # singleton (devices.py) and gets cached, we need to re-probe the 
    # cached diskList to retrieve accurate valid disk information.

    upgrade.checkForPreviousInstalls(device, forceRecheck=True)

    if device.containsEsx.esx and userchoices.getPreserveVmfs():
       systemProbe2 = StaticSystemProbe(product, device.consoleDevicePath)
       systemProbe.bootDiskPath = systemProbe2.bootDiskPath

       tests += [
                 checkPartitionLayout
               ]

    results = [testFn() for testFn in tests]
    return humanReadableResultBlurbs(results)

def humanReadableResultBlurbs(results):

    from weasel.log import LOGLEVEL_UI_ALERT

    warningFailures = ''
    errorFailures = ''
    errorNewLineNeeded = False
    warningNewLineNeeded = False

    for result in results:
        if not result:
            if result.code == Result.ERROR:
                if errorNewLineNeeded:
                    errorFailures += '\n\n'
                errorFailures += str(result)
                errorNewLineNeeded = True
            else:
                if warningNewLineNeeded:
                    warningFailures += '\n\n'
                warningFailures += str(result)
                warningNewLineNeeded = True

    if errorFailures != '':
        log.error('Precheck Error(s). \n %s' % errorFailures)
    if warningFailures != '':
        log.warn('Precheck Warnings(s). \n %s' % warningFailures)
    return errorFailures, warningFailures

def copyVibsData():
    '''Copies vibs file from cos vmdk to temporary location to be accessed by
    installer, in the installer's environment. In case of failure return None.
    '''
    from weasel import userchoices
    from weasel.migrate import base

    found = False

    try:
        cosVmdkPath = userchoices.getCosVmdkPath()
        if not cosVmdkPath:
            log.debug('No COS VMDK path specified')
            return
        cosRootPart = base.findCosRootPartition(cosVmdkPath)

        tmpDir = '/tmp/esx_installed_vibs'
        try:
            os.makedirs(tmpDir)
        except OSError, ex:
            log.debug(ex)
            pass

        currDir = os.getcwd()
        os.chdir(tmpDir)
        fpath = '/etc/vmware/esxupdate/vibs.xml'
        found = base._copyFromCosPart(cosRootPart, fpath)
        os.chdir(currDir)
    finally:
        # Do this twice to make sure everything's actually cleaned up.
        log.debug("Cleaning up any remaining file devices.")
        base.cleanupMountedDevs()
        base.cleanupMountedDevs()

    if found:
        return tmpDir
    return None

def main(argv):

    global vumEnvironment
    vumEnvironment = True

    # See PR 690953
    # Running "esxupdate syncdb" avoids the user seeing two warnings
    try:
        run('esxupdate syncdb')
    except Exception, ex:
        log.exception(ex)
        # Carry on as usual. Worst that can happen is they'll see two warnings

    results = [checkInitializable()]

    if not results[0]:
        testsSection = resultsToXML(results)
        print output_xml % {
                            'info': '',
                            'tests': testsSection,
                           }
        return 0

    product, version = _parseVmwareVersion()
    init(product, version)


    tests = [
        checkEsxVersion,
        checkAvailableSpaceForISO,
        checkMemorySize,
        checkHardwareVirtualization,
        checkCpuCores,
        checkLAHFSAHF64bitFeatures,
        checkNXbitCpuFeature,
        checkSaneEsxConf,
        checkUnsupportedDevices,
        checkPackageCompliance,
        ]

    # Test 3rd party vibs tests only if the host is 4x.
    if version < [5, ]:
        tests += [
            check3rdPartySoftware,
            checkDVS,
            checkPowerpath,
        ]

    tests += [
        checkHostHw,
        checkUpdatesPending,
        checkNetworking,
        checkTboot,
        ]

    # checkVibConflicts needs to know the version of the esx
    tests.append(lambda:checkVibConflicts(version))

    if product == 'ESX':
        tests += [
                  checkMD5Password,
                  checkPartitionLayout,
                  checkAvailableSpaceForConfig,
                  ]

    results += [testFn() for testFn in tests]

    anyFailures = [result for result in results if not result]
    if anyFailures:
        deallocateRamDisk(RAMDISK_NAME, version)

    testsSection = resultsToXML(results)

    print output_xml % {
                        'info': '',
                        'tests': testsSection,
                        }

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
    #import doctest
    #doctest.testmod()
