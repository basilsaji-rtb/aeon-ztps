# Copyright 2014-present, Apstra, Inc. All rights reserved.
#
# This source code is licensed under End User License Agreement found in the
# LICENSE file at http://www.apstra.com/community/eula


from aeon import exceptions
from aeon.base.device import BaseDevice
from aeon.rtbrick.connector import Connector


__all__ = ['Device']


class Device(BaseDevice):
    OS_NAME = 'OpenNetworkLinux'
    DEFAULT_PROBE_TIMEOUT = 3
    DEFAULT_USER = 'admin'
    DEFAULT_PASSWD = 'admin'

    def __init__(self, target, **kwargs):
        """
        :param target: hostname or ipaddr of target device
        :param kwargs:
            'user' : login user-name, defaults to "admin"
            'passwd': login password, defaults to "admin
        """
        self.target = target
        self.api = Connector(hostname=self.target,
                             user=kwargs.get('user', self.DEFAULT_USER),
                             passwd=kwargs.get('passwd', self.DEFAULT_PASSWD))

        self.facts = {}

        if 'no_gather_facts' not in kwargs:
            self.gather_facts()

    def _serial_from_link(self, link_name):
        good, got = self.api.execute(['ip link show dev %s' % link_name])
        data = got[0]['stdout']
        macaddr = data.partition('link/ether ')[-1].split()[0]
        return macaddr.replace(':', '').upper()

    def gather_facts(self):

        facts = self.facts
        facts['os_name'] = self.OS_NAME

        good, got = self.api.execute([
            'hostname',
            'cat /etc/lsb-release | grep RELEASE | cut -d= -f2',
            'test -e /usr/cumulus/bin/decode-syseeprom'
        ])

        facts['fqdn'] = got[0]['stdout'].strip()
        facts['hostname'] = "rtbrick"
        facts['os_version'] = "8.0"
        facts['virtual'] = False
        facts['mac_address'] = self._serial_from_link("ma1")

        good, got = self.api.execute([
            'sudo dmidecode -t system | grep Manufact | cut -f2 -d " "',
        ])
        facts['vendor'] = got[0]['stdout'].strip()

        good, got = self.api.execute([
            'sudo dmidecode -t system | grep Serial | cut -f3 -d " "',
        ])
        facts['serial_number'] = got[0]['stdout'].strip()

        good, got = self.api.execute([
            'sudo dmidecode -t system | grep Product | cut -f3 -d " "',
        ])
        facts['hw_model'] = got[0]['stdout'].strip()
        facts['hw_part_number'] = "None"

        good, got = self.api.execute([
            'sudo dmidecode -t system | grep Version | cut -f2 -d " "',
        ])
        facts['hw_version'] = got[0]['stdout'].strip()

        good, got = self.api.execute([
            'sudo dmidecode -t system | grep UUID | cut -f2 -d " "',
        ])
        facts['service_tag'] = got[0]['stdout'].strip()
