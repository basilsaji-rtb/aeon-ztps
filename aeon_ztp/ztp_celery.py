# Copyright 2014-present, Apstra, Inc. All rights reserved.
#
# This source code is licensed under End User License Agreement found in the
# LICENSE file at http://www.apstra.com/community/eula

import subprocess
import logging
import logging.handlers
import os
import socket
import requests
import json
from aeon.eos.device import Device as EosDevice
from aeon.cumulus.device import Device as CumulusDevice
from aeon.nxos.device import Device as NxosDevice
from aeon.exceptions import CommandError, TimeoutError
from celery import Celery

__all__ = ['ztp_bootstrapper']

celery_config = dict()
celery_config['CELERY_BROKER_URL'] = 'amqp://'
celery_config['CELERY_RESULT_BACKEND'] = 'rpc://'

celery = Celery('aeon-ztp', broker=celery_config['CELERY_BROKER_URL'])
celery.conf.update(celery_config)

_AEON_PORT = os.getenv('AEON_HTTP_PORT')
_AEON_DIR = os.getenv('AEON_TOPDIR')
_AEON_LOGFILE = os.getenv('AEON_LOGFILE')


def get_server_ipaddr(dst):
    dst_s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dst_s.connect((dst, 0))
    return dst_s.getsockname()[0]


def post_device_status(server, target, os_name, message=None, state=None):
    requests.put(
        url='http://%s/api/devices/status' % server,
        json=dict(
            os_name=os_name, ip_addr=target,
            state=state, message=message))


def get_device_state(server, target):
    r = requests.get(url='http://{server}/api/devices?ip_addr={ip_addr}'
                     .format(server=server, ip_addr=target))
    try:
        state = r.json()['items'][0]['state']
    except KeyError:
        state = None
    return state


def get_device_facts(server, target):
    r = requests.get(url='http://{server}/api/devices?ip_addr={ip_addr}'
                     .format(server=server, ip_addr=target))

    facts = r.json().get('items')[0]
    if facts and 'facts' in facts:
        facts_column = json.loads(facts.pop('facts'))
        facts.update(facts_column)

        return facts
    else:
        return facts


def setup_logging(logname, target):
    log = logging.getLogger(name=logname)
    log.setLevel(logging.INFO)
    handler = logging.handlers.SysLogHandler(address='/dev/log')
    fmt = logging.Formatter(
        '%(name)s %(levelname)s {target}: %(message)s'.format(target=target))
    handler.setFormatter(fmt)
    log.addHandler(handler)
    return log


def do_finalize(server, os_name, target, log, finally_script=None):
    profile_dir = os.path.join(_AEON_DIR, 'etc', 'profiles', os_name)
    os_sel = os.path.join(profile_dir, 'os-selector.cfg')
    if not finally_script:
        log.info(
            'Skipping finally script: No finally script specified for {target} in {os_sel}.'.format(target=target,
                                                                                                    os_sel=os_sel))
        return 0, None
    finalizer = os.path.join(profile_dir, finally_script)

    if not os.path.isfile(finalizer):
        log.info('no user provided finally script found at: "{}"'.format(profile_dir))
        return 0, None

    json_facts = json.dumps(get_device_facts(server, target))

    cmd_args = [
        finalizer,
        '-t %s' % target,
        '-s %s' % server,
        '-u AEON_TUSER',
        '-p AEON_TPASSWD',
        '-l %s' % _AEON_LOGFILE,
        "-f '{}'".format(json_facts)
    ]

    cmd_str = ' '.join(cmd_args)
    this_env = os.environ.copy()
    this_env.update(dict(
        AEON_LOGFILE=_AEON_LOGFILE,
        AEON_TARGET=target,
        AEON_SERVER=server,
        FACTS=json_facts))

    child = subprocess.Popen(
        cmd_str, shell=True, env=this_env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    message = "executing 'finally' script:[pid={pid}] {cmd}".format(pid=child.pid, cmd=cmd_str)
    log.info(message)
    post_device_status(server=server,
                       os_name=os_name, target=target,
                       state='FINALLY', message=message)

    _stdout, _stderr = child.communicate()
    rc = child.returncode

    log.info("finally script complete: rc={}".format(rc))
    if len(_stderr):
        log.info("finally stderr=[{}]".format(_stderr))

    return rc, _stderr


def do_bootstrapper(server, os_name, target, log):
    prog = '%s/bin/%s_bootstrap*' % (_AEON_DIR, os_name)

    cmd_args = [
        prog,
        '--target %s' % target,
        '--server %s' % server,
        '--topdir %s' % _AEON_DIR,
        '-U AEON_TUSER',
        '-P AEON_TPASSWD',
        '--logfile %s' % _AEON_LOGFILE
    ]

    cmd_str = ' '.join(cmd_args)

    # must pass command as a single string; using shell=True

    this = subprocess.Popen(
        cmd_str, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    log.info("starting bootstrapper[pid={pid}] [{cmd_str}]".format(
        pid=this.pid, cmd_str=cmd_str))

    _stdout, _stderr = this.communicate()
    rc = this.returncode

    log.info("bootstrapper complete: rc={}".format(rc))
    if len(_stderr):
        log.error("stderr={}".format(_stderr))

    return rc, _stderr


@celery.task
def ztp_bootstrapper(os_name, target):

    server = "{}:{}".format(get_server_ipaddr(target), _AEON_PORT)

    log = setup_logging(logname='aeon-bootstrapper', target=target)
    try:
        state = get_device_state(server, target)
        if state and state not in ('ERROR', 'DONE'):
            log.warning('Device at {} has already registered. This is likely a duplicate bootstrap run and will '
                        'be terminated.'.format(target))
            return
        if state == 'DONE':
            log.warning('Device at {} has previously successfully completed ZTP process. '
                        'ZTP process has been initiated again.'.format(target))
        got = requests.post(
            url='http://%s/api/devices' % server,
            json=dict(
                ip_addr=target, os_name=os_name,
                state='REGISTERED',
                message='device registered, waiting for bootstrap start'))

        if not got.ok:
            body = got.json()
            log.error('Unable to register device: %s' % body['message'])
            return got.status_code

        rc, _stderr = do_bootstrapper(server=server, os_name=os_name, target=target, log=log)
        if 0 != rc:
            post_device_status(server=server,
                               os_name=os_name, target=target,
                               state='ERROR', message='Error running bootstrapper: {}'.format(_stderr))
            return rc

        facts = get_device_facts(server, target)
        finally_script = facts.get('finally_script', None)
        rc, _stderr = do_finalize(server=server, os_name=os_name, target=target, log=log, finally_script=finally_script)
        if rc != 0:
            post_device_status(server=server,
                               os_name=os_name,
                               target=target,
                               state='ERROR',
                               message='Error running finally script: {}'.format(_stderr))
            return rc

        post_device_status(server=server,
                           os_name=os_name, target=target,
                           state='DONE', message='device bootstrap completed')
    finally:
        log.handlers.pop()
    return rc


@celery.task
def ztp_finalizer(os_name, target):
    server = "{}:{}".format(get_server_ipaddr(target), _AEON_PORT)
    facts = get_device_facts(server, target)
    finally_script = facts.get('finally_script', None)

    log = setup_logging(logname='aeon-finalizer', target=target)

    try:
        rc, _stderr = do_finalize(server=server, os_name=os_name, target=target, log=log, finally_script=finally_script)
        if 0 != rc:
            post_device_status(server=server,
                               os_name=os_name, target=target,
                               state='ERROR', message='Error running finally script: {}'.format(_stderr))
            return rc, _stderr
    finally:
        log.handlers.pop()


@celery.task
def retry_ztp(target=None, nos=None, user='admin', password='admin'):
    cumulus_lease_file = '/var/lib/dhcp/dhclient.eth0.leases'
    dev_table = {
        'eos': {
            'dev_obj': EosDevice,
            'cmds': ['write erase now', 'reload now']
        },
        'cumulus': {
            'dev_obj': CumulusDevice,
            'cmds': [
                "sudo su",
                "sed -i '/vrf mgmt/d' /etc/network/interfaces",
                'ztp -R',
                'reboot'
            ],
            'virt_cmds': [
                "sudo ztp -v -r $(cat %s | grep 'cumulus-provision-url'| tail -1 | cut -f2 -d\\\")" % cumulus_lease_file
            ]
        },
        'nxos': {
            'dev_obj': NxosDevice,
            'cmds': ['write erase', 'reload']
        }
    }
    if not any(nos.lower() in x for x in dev_table):
        raise ValueError('%s not in list of supported NOSs')
    d = dev_table[nos.lower()]
    dev = d['dev_obj'](target, user=user, passwd=password)

    try:
        # CumulusVX doesn't always boot into ZTP mode without network errors
        # Use different retry commands for CVX
        if dev.facts['os_name'] == 'cumulus' and dev.facts['virtual']:
            ok, output = dev.api.execute(d['virt_cmds'])
        else:
            ok, output = dev.api.execute(d['cmds'])
    except CommandError as e:
        if 'IncompleteRead' in e[0]:
            pass
        return False, e
    except TimeoutError as e:
        return False, 'Device %s unreachable' % target
    return ok, output
