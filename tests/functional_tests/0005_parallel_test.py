# Copyright (c) 2014-2026, iocage
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

"""Real priority barriers on a disposable FreeBSD jail host and pool."""
import ipaddress
import re
import subprocess
import sys
import uuid

from unittest import mock

import pytest

from iocage_lib import ioc_common, ioc_nat
from iocage_lib.ioc_list import IOCList
from iocage_lib.ioc_start import IOCStart
from iocage_lib.ioc_stop import IOCStop
from iocage_lib.iocage import IOCage

pytestmark = [pytest.mark.require_root, pytest.mark.require_zpool]


@pytest.fixture
def parallel_jails(release, invoke_cli, tmp_path):
    names = [f'parallel_{uuid.uuid4().hex[:8]}_{n}' for n in range(4)]
    hook = tmp_path / 'mark.py'
    # Tests that require overlap ask each hook to wait for its same-priority
    # peer. A timeout turns a missing peer into a test failure.
    hook.write_text('''import pathlib
import sys
import time

directory = pathlib.Path(sys.argv[1])
name, peer, action, phase = sys.argv[2:]
(directory / f'{name}.{action}.{phase}').write_text(str(time.monotonic_ns()))
if phase == 'begin':
    if (directory / 'wait-for-peer').exists():
        marker = directory / f'{peer}.{action}.begin'
        deadline = time.monotonic() + 30
        while not marker.exists():
            if time.monotonic() > deadline:
                sys.exit('Peer hook did not overlap')
            time.sleep(0.05)
    else:
        # Give an unexpected overlap time to show up in the single-worker test.
        time.sleep(0.1)
''')
    created = []

    try:
        for name, peer, priority in (
            (names[0], names[1], 10), (names[1], names[0], 10),
            (names[2], names[3], 20), (names[3], names[2], 20),
        ):
            props = ['boot=off', f'priority={priority}',
                     'ip4_addr=none', 'ip6_addr=none', 'vnet=off',
                     'exec_start=/usr/bin/true', 'exec_stop=/usr/bin/true']

            for property_name, action, phase in (
                ('exec_prestart', 'start', 'begin'),
                ('exec_poststart', 'start', 'end'),
                ('exec_prestop', 'stop', 'begin'),
                ('exec_poststop', 'stop', 'end')
            ):
                props.append(
                    f'{property_name}={sys.executable} {hook} {tmp_path} '
                    f'{name} {peer} {action} {phase}'
                )

            created.append(name)
            invoke_cli(['create', '-r', release, '-n', name, *props])

        # The CLI creates these datasets in separate processes. Refresh the
        # inventory before exercising library calls in the pytest process.
        IOCage.reset_cache()
        yield names, tmp_path
    finally:
        # Cleanup must bypass the hooks, even when a synchronization test fails.
        for name in created:
            invoke_cli(['stop', '-f', name], assert_returncode=False)
            invoke_cli(['destroy', '-f', name], assert_returncode=False)


def events(directory, name, action):
    return tuple(
        int((directory / f'{name}.{action}.{phase}').read_text())
        for phase in ('begin', 'end')
    )


def assert_priority_events(names, directory, action, parallel):
    first = [events(directory, name, action) for name in names[:2]]
    second = [events(directory, name, action) for name in names[2:]]
    if action == 'stop':
        first, second = second, first
    assert max(end for _, end in first) < min(begin for begin, _ in second)

    for group in (first, second):
        last_begin = max(begin for begin, _ in group)
        first_end = min(end for _, end in group)
        assert (last_begin < first_end) is parallel


@pytest.mark.parametrize('jobs', [None, 1, 2])
def test_parallel_priority_barriers(parallel_jails, invoke_cli, jobs):
    names, directory = parallel_jails

    if jobs != 1:
        (directory / 'wait-for-peer').touch()

    options = ['--parallel'] + (['--jobs', str(jobs)] if jobs else [])

    for action in ('start', 'stop'):
        invoke_cli([action, *options, *reversed(names)])
        assert_priority_events(names, directory, action, jobs != 1)

        for name in names:
            assert IOCList.list_get_jid(name)[0] == (action == 'start')


def test_parallel_dependencies_and_force(parallel_jails, invoke_cli):
    names, directory = parallel_jails
    invoke_cli(['set', f'depends={names[0]}', names[1]])
    invoke_cli(['start', '--parallel', *names])
    assert events(directory, names[0], 'start')[1] < events(
        directory, names[1], 'start'
    )[0]
    invoke_cli(['stop', '--parallel', '-f', *names])

    for name in names:
        assert not IOCList.list_get_jid(name)[0]
        assert not (directory / f'{name}.stop.begin').exists()


@pytest.mark.parametrize('ignore', [False, True])
def test_parallel_failed_hook_blocks_later_priority(
    parallel_jails, invoke_cli, ignore
):
    names, directory = parallel_jails
    failure = directory / 'fail.sh'
    failure.write_text('#!/bin/sh\necho injected-failure >&2\nexit 1\n')
    failure.chmod(0o755)
    before = subprocess.check_output(
        ['devfs', 'rule', 'showsets'], text=True
    ).splitlines()
    failed_ruleset = 61000

    while str(failed_ruleset) in before:
        failed_ruleset += 1

    invoke_cli(['set', f'exec_prestart={failure}', 'devfs_ruleset=0',
                'min_dyn_devfs_ruleset=61000', names[0]])
    options = ['--ignore'] if ignore else []
    result = invoke_cli(['start', '--parallel', *options, *names],
                        assert_returncode=False)
    assert (result.returncode == 0) is ignore
    stderr = result.stderr.decode()
    assert names[0] in stderr
    assert f'{names[0]}: 1\n' not in stderr
    assert IOCList.list_get_jid(names[1])[0]
    remaining = subprocess.check_output(
        ['devfs', 'rule', 'showsets'], text=True
    ).splitlines()
    assert str(failed_ruleset) not in remaining

    for name in names[2:]:
        assert IOCList.list_get_jid(name)[0] is ignore
        assert (directory / f'{name}.start.begin').exists() is ignore


def test_failed_jail_creation_releases_devfs_ruleset(
    parallel_jails, invoke_cli
):
    names, directory = parallel_jails
    name = names[0]
    # Move only this fixture jail's generated config after preflight, so its
    # hook succeeds but jail -c fails before it can claim the ruleset.
    runtime = f'/var/run/jail.ioc-{name}.conf'
    saved = directory / 'generated.conf'
    invoke_cli(['set', f'exec_prestart=/bin/mv {runtime} {saved}',
                'devfs_ruleset=0', name])
    result = invoke_cli(['start', '--parallel', name], assert_returncode=False)
    assert saved.is_file()
    assert result.returncode != 0
    assert b'Start FAILED' in result.stderr
    assert not IOCList.list_get_jid(name)[0]
    # Read the actual reservation so unrelated host rulesets can change.
    ruleset = re.search(r'devfs_ruleset = "(\d+)";', saved.read_text())
    assert ruleset, 'Expected a devfs ruleset in the generated jail config'
    remaining = subprocess.check_output(['devfs', 'rule', 'showsets'],
                                        text=True)
    assert ruleset.group(1) not in remaining.splitlines()


def test_setup_failure_cleans_up_before_retrying_dependencies(
    parallel_jails, invoke_cli, monkeypatch
):
    names, directory = parallel_jails
    prerequisite, dependent = names[:2]
    invoke_cli(['set', f'depends={prerequisite}', dependent])
    created = []

    def fail_after_creation(jail):
        created.append(jail._created_jid)
        raise RuntimeError('resolver setup failed')

    with monkeypatch.context() as patch:
        patch.setattr(IOCStart, 'start_generate_resolv', fail_after_creation)
        with pytest.raises(RuntimeError, match='resolver setup failed'):
            IOCage(jail=dependent).start(parallel=True)
    assert len(created) == 1 and created[0].isdigit()
    assert not IOCList.list_get_jid(prerequisite)[0]
    assert not (directory / f'{dependent}.start.begin').exists()

    invoke_cli(['start', '--parallel', dependent])
    assert events(directory, prerequisite, 'start')[1] < events(
        directory, dependent, 'start'
    )[0]
    worker = IOCage(jail=prerequisite)
    uuid, path = worker.__check_jail_existence__()
    IOCStop(uuid, path, force=True, expected_jid=created[0])
    assert IOCList.list_get_jid(prerequisite)[0]


def test_parallel_boot_selection(parallel_jails, invoke_cli):
    names, directory = parallel_jails
    (directory / 'wait-for-peer').touch()

    for name in names[:2]:
        invoke_cli(['set', 'boot=on', name])

    invoke_cli(['start', '--parallel', '--rc'])

    for name in names[:2]:
        assert IOCList.list_get_jid(name)[0]

    for name in names[2:]:
        assert not IOCList.list_get_jid(name)[0]

    invoke_cli(['stop', '--parallel', '--rc'])

    for name in names:
        assert not IOCList.list_get_jid(name)[0]


def test_parallel_empty_devfs_rulesets(parallel_jails, invoke_cli):
    names, directory = parallel_jails
    (directory / 'wait-for-peer').touch()

    for name in names:
        invoke_cli(['set', 'devfs_ruleset=0', name])

    # Peer prestart hooks synchronize before jail creation. Empty clones
    # must already reserve distinct IDs at that point.
    invoke_cli(['start', '--parallel', *names])
    rulesets = {
        invoke_cli(['get', 'devfs_ruleset', name]).output.strip()
        for name in names
    }
    assert len(rulesets) == len(names)
    invoke_cli(['stop', '--parallel', *names])
    remaining = subprocess.check_output(
        ['devfs', 'rule', 'showsets'], text=True
    ).splitlines()
    assert not rulesets.intersection(remaining)


def installed_nat_rules(invoke_cli):
    backend = invoke_cli(['get', 'nat_backend', 'default']).output.strip()
    command = (['pfctl', '-a', ioc_nat.PF_ANCHOR, '-P', '-sn']
               if backend == 'pf' else
               ['ipfw', 'nat', 'show', 'config'])
    return backend, subprocess.check_output(command, text=True)


@pytest.mark.require_nat
def test_parallel_vnet_nat_allocations_and_cleanup(
    parallel_jails, invoke_cli, ping_ip
):
    names, directory = parallel_jails
    (directory / 'wait-for-peer').touch()

    for index, name in enumerate(names):
        protocol = 'tcp/udp' if index == 0 else 'tcp'
        invoke_cli(['set', 'vnet=on', 'nat=on',
                    f'nat_forwards={protocol}(80:{18080 + index})', name])

    invoke_cli(['start', '--parallel', *names[:2]])
    # Editing a running jail takes effect on restart. Its current forward
    # must survive both preflight and the next successful firewall reload.
    invoke_cli(['set', 'nat_forwards=none', names[0]])
    invoke_cli(['set', 'nat_forwards=tcp(80:18080)', names[2]])
    conflict = invoke_cli(['start', '--parallel', names[2]],
                          assert_returncode=False)
    assert conflict.returncode != 0
    assert b'NAT forwarding ports conflict' in conflict.stderr
    assert not IOCList.list_get_jid(names[2])[0]
    invoke_cli(['set', 'nat_forwards=tcp(80:18082)', names[2]])
    invoke_cli(['start', '--parallel', *names[2:]])
    addresses = []
    rulesets = []
    interfaces = []
    backend, nat_rules = installed_nat_rules(invoke_cli)

    for index, name in enumerate(names):
        configured = invoke_cli(['get', 'ip4_addr', name]).output.strip()
        address = configured.split('|')[-1].split('/')[0]
        addresses.append(address)
        port = 18080 + index
        for protocol in ('tcp', 'udp') if index == 0 else ('tcp',):
            if backend == 'pf':
                mapping = f'port = {port} -> {address} port 80'
                assert any(f'proto {protocol} ' in line and mapping in line
                           for line in nat_rules.splitlines())
            else:
                assert f'redirect_port {protocol} {address}:80 {port}' in (
                    nat_rules)
        rulesets.append(
            invoke_cli(['get', 'devfs_ruleset', name]).output.strip()
        )
        interfaces.append(f'vnet0.{IOCList.list_get_jid(name)[1]}')
        invoke_cli(['exec', name, 'ping', '-c', '1', ping_ip])

    assert len(set(addresses)) == len(names)
    assert len(set(rulesets)) == len(names)
    invoke_cli(['stop', '--parallel', *names])
    _, remaining_forwards = installed_nat_rules(invoke_cli)
    assert all(str(p) not in remaining_forwards for p in range(18080, 18084))
    remaining_sets = subprocess.check_output(
        ['devfs', 'rule', 'showsets'], text=True
    ).splitlines()
    remaining_ifaces = subprocess.check_output(
        ['ifconfig', '-l'], text=True
    ).split()
    assert not set(rulesets).intersection(remaining_sets)
    assert not set(interfaces).intersection(remaining_ifaces)

    for name in names:
        assert not IOCList.list_get_jid(name)[0]


@pytest.mark.require_nat
def test_failed_nat_start_restores_peer_and_can_retry(
    parallel_jails, invoke_cli, ping_ip, monkeypatch
):
    names, _ = parallel_jails
    peer, new = names[:2]
    for name, forward in [(peer, '80-82:18080-18082'), (new, '80:18083')]:
        invoke_cli(['set', 'vnet=on', 'nat=on',
                    f'nat_forwards=tcp({forward})', name])

    invoke_cli(['start', peer])
    backend, previous_nat = installed_nat_rules(invoke_cli)
    assert {18080, 18081, 18082}.issubset(ioc_nat.running_ports())

    # Reserve the peer's /24 so this start must replace the outbound routes.
    addr = invoke_cli(['get', 'ip4_addr', peer]).output.strip().split('|')[-1]
    subnet = ipaddress.ip_interface(addr).network.supernet(new_prefix=24)
    occupied = list(ioc_common.get_used_ips()) + [str(ip) for ip in subnet]
    if backend == 'ipfw':
        list_rules = ['ipfw', '-S', 'list']
        previous_routes = subprocess.check_output(list_rules)

    # Fail after the kernel has accepted the new rules, so both the rollback
    # and the cleanup of the just-created jail run against real resources.
    with monkeypatch.context() as patch:
        patch.setattr(ioc_common, 'get_used_ips', lambda: occupied)
        patch.setattr(ioc_nat.os, 'replace',
                      mock.Mock(side_effect=OSError('could not publish state')))
        with pytest.raises(OSError, match='could not publish state'):
            IOCage(jail=new, silent=True).start()

    assert not IOCList.list_get_jid(new)[0]
    assert installed_nat_rules(invoke_cli) == (backend, previous_nat)
    if backend == 'ipfw':
        assert subprocess.check_output(list_rules) == previous_routes
    invoke_cli(['exec', peer, 'ping', '-c', '1', ping_ip])
    invoke_cli(['start', new])
    invoke_cli(['exec', new, 'ping', '-c', '1', ping_ip])
    invoke_cli(['stop', new])
    _, remaining = installed_nat_rules(invoke_cli)
    assert '18083' not in remaining and '18080' in remaining
    invoke_cli(['exec', peer, 'ping', '-c', '1', ping_ip])
