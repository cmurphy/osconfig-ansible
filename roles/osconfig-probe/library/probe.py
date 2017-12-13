#!/usr/bin/python
#
# (c) Copyright 2015-2017 Hewlett Packard Enterprise Development LP
# (c) Copyright 2017 SUSE LLC
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#

import glob
import json
import os
import re
import shlex
import subprocess
import sys


"""
Probe hardware/software and gather various information

Usage:
    - become: true
      probe: ipaddr=<ipaddr> hostname=<hostname>

The information is returned as 'ansible_facts' with the keys described
 below. This allows code such as:

    - name: Probe
      probe:

    - name: Dump BIOS release date
      debug:
        var="{{ ardana_dmi_data.ardana_dmi_data.bios_information[0].release_date }}"

Note: The ansible facts are returned as a dictionary where each item has
a key that in turn contains an item with the same key. The is so that when
used in a j2 file, the first item in the resulting file is the key. The
resulting file is modelled on input model files: a key containing an array
item, where the item contains a dict with ipaddr and hostname as keys. The
hostname item will be null if this is used before running the Ardana
Configuration Processor.

ardana_drive_configuration:

    Lists the name and size of block devices. Each item
    containing two items: name and bytes, where bytes is the size of the
    block device in bytes.

    Example (expressed as YAML):

    ardana_drive_configuration:
      ardana_drive_configuration:
        - ipaddr: 10.0.0.0
          hostname: whatever
          drives:
          - name: /dev/sda
            bytes: 42949672960
            partitions:
            - name: sda1
              bytes: 42949672960
          - name: /dev/sdb
            bytes: 20000000000
            partitions: []


dmi_data:

    Contains the output from dmidecode. All keys and data is lower-cased.
    All top level keys are followed by an array -- frequently one. Each
    item in the array is a dict that contains a 'handle' key, followed
    by the keys from dmidecode.

    Example (small subset):

    ardana_dmi_data:
        ardana_dmi_data:
        - ipaddr: 10.0.0.0
          hostname: whatever
          dmidata:
              system_information:
                - _handle: _0x0001
                  manufacturer: hewlett-packard
                  product_name: hp_z620_workstation
                  serial_number: czc4361dq2
              bios_information:
                - _handle: _0x0000
                  bios_revision: '3.69'
                  characteristics:
                    '0': pci_is_supported
                    '1': pnp_is_supported
                  release_date: 03/25/2014
                  rom_size: 16384_kb
                  runtime_size: 64_kb
                  vendor: hewlett-packard
                  version: j61_v03.69

ardana_interface_configuration:

    Contains a list of ethN devices. The link_detected key is always reported.
    The speed and duplex keys may be absent.

    Example:

    ardana_interface_configuration:
        ardana_interface_configuration:
        - ipaddr: 10.0.0.0
          hostname: whatever
          interfaces:
            - name: eth0
              device:
                name: eth0
                state: UP
                mac: fe:54:00:77:94:8f
                link_detected: yes
            - name: eth1
              device:
                name: eth1
                state: UP
                mac: b2:c9:43:3b:4a:99
                link_detected: no
                duplex: Full
                speed: 1000Mb/s

ardana_meminfo:

    Contains a dict of all items from /proc/meminfo.
    Where needed, the units (e.g. kB) are specified.


    Example (much redacted):

    ardana_meminfo:
        ardana_meminfo:
        - ipaddr: 10.0.0.0
          hostname: whatever
          meminfo:
              memtotal:
                value: 505648
                units: kB
              hugepages_total:
                value: 0
              directmap4k:
                value: 2048
                units: kB

ardana_packages:

    Contains a list of all packages installed on a server. Note the version
    key is a string. Hence (in JSON) it is quoted.

    Example:

    ardana_packages:
        ardana_packages:
        - ipaddr: 10.0.0.0
          hostname: whatever
          packages:
          - name: acl
            status: ii
            version: 2.2.52-2
            section: utils
            architecture: amd64
            description: Access control list utilities
          - name: init-system-helpers
            status: ii
            version: '1.14'
            architecture: all
            description: helper tools for all init systems





"""


def drive_configuration():
    """ Get drive (block device) names and sizes """
    block_devices = []
    for device in os.listdir('/sys/block'):
        if os.path.exists('/sys/block/%s/device' % device):
            # Is a disk drive
            with open('/sys/block/%s/size' % device) as dev:
                sectors = dev.read()
            with open('/sys/block/%s/queue/hw_sector_size' % device) as dev:
                hw_sector_size = dev.read()
            size = int(sectors) * int(hw_sector_size)
            pathname = os.path.join('/dev', device)
            partitions = []
            for partition_number in range(1, 99):
                partition_path = '/sys/block/%s/%s%s' % (device, device,
                                                         partition_number)
                if os.path.exists(partition_path):
                    with open(os.path.join(partition_path, 'size')) as part:
                        partition_size = int(part.read()) * int(hw_sector_size)
                        partitions.append({
                            'partition': '%s%s' % (device, partition_number),
                            'bytes': partition_size})
            block_devices.append({'name': pathname, 'bytes': size,
                                  'partitions': partitions})

    # Iterate through the multipath devices and assign sizes
    mpath_devices = glob.glob("/dev/mapper/mpath*")
    device_list=[]
    dm_devs={}
    for mdev in mpath_devices:
        dm_dev = os.path.basename(os.path.realpath(mdev))
        # The devices that have a "hold" on this device
        # Could be a partition on an LVM volume
        holders = os.listdir("/sys/block/%s/holders" % dm_dev)
        slaves = os.listdir("/sys/block/%s/slaves" % dm_dev)
        dev_entry = {"mdev": mdev, "dm_dev": dm_dev, "holders": holders,
                     "slaves": slaves, "partitions": []}
        device_list.append(dev_entry)
        with open('/sys/block/%s/size' % dm_dev) as dev:
                sectors = dev.read()
        with open('/sys/block/%s/queue/hw_sector_size' % dm_dev) as dev:
                hw_sector_size = dev.read()
        size = int(sectors) * int(hw_sector_size)
        dm_devs[dm_dev] = {'name': mdev, 'bytes': size}

    # Find and attach the partitions to the structure
    for dev in device_list:
        # since device can have no slaves or holders in which case it is empty
        # set default to drive
        dev["type"] = "drive"
        for slave in dev["slaves"]:
            # The slave is a multipath device
            if slave in dm_devs.keys():
                dev["type"] = "partition"
                assert (len(dev["slaves"]) == 1), 'device %s: slaves !=1'%dev
        partitions = []
        for holder in dev["holders"]:
            if holder in dm_devs.keys():
                assert (dev["type"] == "drive"), 'device holder %s: type !=drive'%holder
                my_partition = {'partition': dm_devs[holder]['name'],
                                'bytes': dm_devs[holder]['bytes']}
                partitions.append(my_partition)
        dev["partitions"] = partitions

    # validation check
    for dev in device_list:
        assert (dev["type"] != ""), 'device %s: type = ""'%dev

    # Add the multipath data to the existing list
    for dev in device_list:
        if dev["type"] == "drive":
            dm_dev = dm_devs[dev["dm_dev"]]
            block_devices.append({'name': dev['mdev'], 'bytes': dm_dev['bytes'],
                                  'partitions': dev['partitions']})

    return block_devices


def dmidecode():
    """ Get dmidebode and lspci information """
    hardware = {}
    try:
        output = subprocess.check_output(['/usr/sbin/dmidecode'])
    except subprocess.CalledProcessError:
        output = ''
    state = 'need_handle'
    for line in output.split('\n'):
        if state == 'need_handle':
            if line.startswith('Handle '):
                handle = '_' + line[7:13]
                state = 'need_dmi'
                continue
        if state == 'need_dmi':
            dmi = line.lower().strip().replace(' ', '_')
            if not hardware.get(dmi):
                hardware[dmi] = {}
            hardware[dmi][handle] = {}
            state = 'need_attributes'
            continue
        if state == 'need_attributes':
            if line.strip() == '':
                state = 'need_handle'
            else:
                if ':' in line:
                    attr_val = line.split(':')
                    if len(attr_val[1]) > 1:
                        #print 'DEBUG: found value %s' %\
                        #  attr_val[1].strip().lower().replace(' ', '_')
                        attr = attr_val[0].strip().lower().replace(' ', '_')
                        val = attr_val[1].strip().lower().replace(' ', '_')
                        hardware[dmi][handle][attr] = val
                    else:
                        attr = attr_val[0].strip().lower().replace(' ', '_')
                        state = 'reading_array'
                        hardware[dmi][handle][attr] = {}
                        idx = 0
                else:
                    # Attribute value contains newline -- ignore
                    pass
            continue
        if state == 'reading_array':
            if line.strip() == '':
                state = 'need_handle'
            elif ':' in line:
                state = 'need_attributes'
                attr_val = line.split(':')
                attr = attr_val[0].strip().lower().replace(' ', '_')
                val = attr_val[1].strip().lower().replace(' ', '_')
                hardware[dmi][handle][attr] = val
            else:
                hardware[dmi][handle][attr][str(idx)] = \
                    line.strip().lower().replace(' ', '_')
                idx += 1
            continue

    dmidata = {}
    for key in hardware.keys():
        dmidata[key] = []
        for handle in hardware[key]:
            newvalue = hardware.get(key).get(handle)
            newvalue['_handle'] = handle
            dmidata[key].append(newvalue)

    return dmidata


def ip():
    interfaces = []
    try:
        output = subprocess.check_output(['/sbin/ip', 'link'])
    except subprocess.CalledProcessError:
        output = ''
    parser = 'need_name'
    name = ''
    state = ''
    for line in output.split('\n'):
        pieces = line.split()
        if len(pieces) == 0:
            continue
        if parser == 'need_name':
            name = pieces[1].split(':')[0]
            state = pieces[8]
            parser = 'need_mac'
        else:
            is_ether = False
            if pieces[0] == 'link/ether' and name.startswith('eth'):
                is_ether = True
                mac = pieces[1]

            if is_ether:
                interfaces.append({'name': name,
                                   'device': [{'name': name,
                                               'state': state,
                                               'macaddr': mac}]})
            parser = 'need_name'

    # Add additional information to the device data
    for interface in interfaces:
        name = interface['name']
        try:
            output = subprocess.check_output(['/sbin/ethtool',  '%s' % name])
        except subprocess.CalledProcessError:
            output = ''
        dev_info = interface.get('device')[0]
        for line in output.split('\n'):
            if line.strip().startswith('Link detected:'):
                dev_info['link_detected'] = line.strip().split()[2]
            elif line.strip().startswith('Duplex:'):
                dev_info['duplex'] = line.strip().split()[1]
            elif line.strip().startswith('Speed:'):
                dev_info['speed'] = line.strip().split()[1]

    return interfaces


def meminfo():
    mem_info = {}
    with open('/proc/meminfo') as lines:
        for line in lines:
            item = {}
            name = line.split()[0].split(':')[0].lower()
            item['value'] = int(line.split()[1].strip())
            try:
                item['units'] = line.split()[2].strip()
            except IndexError:
                pass
            mem_info[name] = item
    return mem_info


def package_info():
    if os.path.exists('/usr/bin/dpkg'):
        return dpkg()
    elif os.path.exists('/usr/bin/zypper'):
        return zypper()
    else:
        raise NotImplementedError('Could not determine package manager to use')

def dpkg():
    packages = []
    try:
        output = subprocess.check_output(['/usr/bin/dpkg', '-l'])
    except subprocess.CalledProcessError:
        output = ''
    parser = 'need_divider'
    for line in output.split('\n'):
        if parser == 'need_divider':
            if line.startswith('+++-=='):
                parser = 'consume_items'
        else:
            pieces = line.split()
            if len(pieces) == 0:
                continue  # blank/empty line (probably at end of listing)
            package = {
                'status': pieces[0],
                'name': pieces[1],
                'version': str(pieces[2]),
                'architecture': pieces[3],
                'description': ' '.join(pieces[4:])}
            packages.append(package)

    return packages


def zypper():
    package_versions = {}
    package_version_lines = zypper_lines(['/usr/bin/zypper', 'search', '-i', '-s'])
    for pieces in package_version_lines:
        if len(pieces) == 6 and pieces[2] == 'package':
            package_versions[pieces[1]] = (pieces[3], pieces[4])

    packages = []
    package_lines = zypper_lines(['/usr/bin/zypper', 'search', '-i'])
    for pieces in package_lines:
        if len(pieces) == 4 and pieces[3] == 'package':
            package_version = package_versions.get(pieces[1], ('', ''))
            package = {
                'status': pieces[0],
                'name': pieces[1],
                'version': package_version[0],
                'architecture': package_version[1],
                'description': pieces[2]
            }
            packages.append(package)

    return packages


def zypper_lines(command):
    output_lines = []
    try:
        output = subprocess.check_output(command)
    except subprocess.CalledProcessError:
        output = ''
    parser = 'need_divider'
    for line in output.split('\n'):
        if parser == 'need_divider':
            if line.startswith('---+'):
                parser = 'consume_items'
        else:
            output_lines.append(re.split('\s*\|\s*', line))
    return output_lines


def main():
    ipaddr = None
    hostname = None
    try:
        args_file = sys.argv[1]
        args_data = file(args_file).read()
        arguments = shlex.split(args_data)
    except (IndexError, IOError):
        arguments = sys.argv  # Running interactively
    for arg in arguments:
        if "=" in arg:
            (key, value) = arg.split('=')
            if key == 'ipaddr':
                ipaddr = value
            if key == 'hostname':
                hostname = value
    ret={}
    try:
        failed_msg = ''
        discovered_drives = drive_configuration()
        dmidata = dmidecode()
        interfaces = ip()
        mem_info = meminfo()
        packages = package_info()
    except AssertionError as msg:
        failed_msg = msg
        ret['failed'] = True
        ret['rc'] = 1
        ret['msg'] = str(failed_msg)
        print(json.dumps(ret))
        sys.exit(1)

    ret['failed'] = False
    ret['rc'] = 0
    ret['ansible_facts'] = \
    {
        'ardana_meminfo': {
            'ardana_meminfo': [{
                'ipaddr': ipaddr,
                'hostname': hostname,
                'meminfo': mem_info
            }]
        },
        'ardana_drive_configuration': {
            'ardana_drive_configuration': [{
                'ipaddr': ipaddr,
                'hostname': hostname,
                'drives': discovered_drives
            }]
        },
        'ardana_interface_configuration': {
            'ardana_interface_configuration': [{
                'ipaddr': ipaddr,
                'hostname': hostname,
                'interfaces': interfaces
            }]
        },
        'ardana_dmi_data': {
            'ardana_dmi_data': [{
                'ipaddr': ipaddr,
                'hostname': hostname,
                'dmidata': dmidata
            }]
        },
        'ardana_packages': {
            'ardana_packages': [{
                'ipaddr': ipaddr,
                'hostname': hostname,
                'packages': packages
            }]
        }
    }

    print(json.dumps(ret))


if __name__ == '__main__':
    main()
