# Copyright 2014-present, Apstra, Inc. All rights reserved.
#
# This source code is licensed under End User License Agreement found in the
# LICENSE file at http://www.apstra.com/community/eula

from aeon.rtbrick.connector import Executor


__all__ = ['RtBrick_AS7712']

class RtBrick_AS7712(object):
    def __init__(self, host, server=None, user="root", password="onl", container="rtbrick3", serial=None, uuid=None):
        self.cname = container
        self.device = Executor(host, user, password)
        self.serial = serial
        self.uuid = uuid
        self.server = server
        self.macaddr = self.execute("ifconfig ma1 | grep HWaddr | sed -e's/  */ /g' | cut -f5 -d' ' | sed -e's/://g'")
        print self.macaddr

    def execute(self, cmd):
        return self.device.execute(cmd)

    def execute_cmd(self, cmd):
        return self.device.execute_cmd(cmd)

    def host_setup(self):
        print "Starting Host setup"
        self.execute("cp /var/lib/lxc/rtbrick-onl-aeon-start /usr/local/bin/")
        self.execute("rtbrick-onl-aeon-start {} {} {}".format(self.cname, self.server, self.macaddr))

    def download_configs(self):
        print "Downloading config from {} for mac address {}".format(self.server, self.macaddr)
        self.execute("curl -O http://{}/downloads/rtbrick/{}/program_configs.tar.gz".format(self.server, self.macaddr))
        print "Extracting configs"
        self.execute("tar -xzf program_configs.tar.gz")

    def config(self):
        self.host_setup()
