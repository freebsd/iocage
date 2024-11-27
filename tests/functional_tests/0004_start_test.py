# Copyright (c) 2014-2019, iocage
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted providing that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
# IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import pytest
import inspect
import tempfile
import os
import re


require_root = pytest.mark.require_root
require_zpool = pytest.mark.require_zpool


@require_root
@require_zpool
def test_01_start(resource_selector, invoke_cli):
    for jail in resource_selector.startable_jails_and_not_running:
        if not jail.is_rcjail:
            invoke_cli(
                ['start', jail.name],
                f'Jail {jail} failed to start'
            )

            assert jail.running is True, f'Failed to start {jail.name}'


@require_root
@require_zpool
def test_02_start_rc_jail(invoke_cli, resource_selector):
    invoke_cli(
        ['start', '--rc'],
        'Failed to start --rc jails'
    )

    for jail in resource_selector.rcjails:
        assert jail.running is True, f'{jail.name} not running'

# Network-related tests belong here because the code is only executed at jail
# start time.

@require_root
@require_zpool
def test_03_create_and_start_nobridge_vnet_jail(release, jail, invoke_cli):
    jail = jail('nobridge_jail')

    fd, path = tempfile.mkstemp()

    try:
        with os.fdopen(fd, 'w') as tmp:
            tmp.write(inspect.cleandoc(f"""
                #!/bin/sh
                jailname=ioc-{jail.name}
                jid=$(jls -j $jailname jid)
                iface=vnet0.$jid
                ifconfig $iface inet6 fe80::1/64
            """))
        os.chmod(path, 0o755)

        invoke_cli([
            'create', '-r', release, '-n', jail.name,
            'boot=on', 'vnet=on',
            'interfaces=vnet0:none', 'vnet_default_interface=none',
            f'ip4_addr=none', 'ip6_addr=vnet0|fe80::2/64',
            'defaultrouter6=none', 'defaultrouter=none',
            f'exec_poststart={path}'
        ])

        assert jail.exists is True
        assert jail.running is True

        stdout, stderr = jail.run_command(['ifconfig'])
        assert bool(stderr) is False, f'Ifconfig returned an error: {stderr}'
        assert 'fe80::2%epair0b' in stdout

        stdout, stderr = jail.run_command(['ifconfig'], jailed=False)

        assert bool(stderr) is False, f'Ifconfig returned an error: {stderr}'
        assert re.search(r'bridge[0-9]', stdout) is None, 'Unexpected bridge was created.'
        assert f'fe80::1%vnet0.{jail.jid}' in stdout
        assert f'description: associated with jail: {jail.name} as nic: epair0b'

        stdout, stderr = jail.run_command(['ping', '-c', '1', f'fe80::2%vnet0.{jail.jid}'], jailed=False)
        assert bool(stderr) is False, f'Ping returned an error: {stderr}'

        invoke_cli([
            'destroy', jail.name, '-f'
        ])

    finally:
        os.remove(path)


# TODO: Let's also start jails in a single command to test that out

@require_root
@require_zpool
def test_04_vnet_jail_with_loopback_alias(release, jail, invoke_cli):
    jail = jail('loopback_alias_jail')

    invoke_cli([
        'create', '-r', release, '-n', jail.name,
        'boot=on', 'vnet=on', 'defaultrouter=none',
        f'ip4_addr=lo0|192.168.2.10'
    ])

    assert jail.exists is True
    assert jail.running is True

    stdout, stderr = jail.run_command(['ifconfig', 'lo0'])
    assert bool(stderr) is False, f'Ifconfig returned an error: {stderr}'
    assert '192.168.2.10' in stdout, (
        'Could not set address on loopback interface.'
    )

    invoke_cli([
        'destroy', jail.name, '-f'
    ])