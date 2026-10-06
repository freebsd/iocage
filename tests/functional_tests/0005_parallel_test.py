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
import subprocess
import sys
import uuid

import pytest

from iocage_lib.ioc_list import IOCList

pytestmark = [pytest.mark.require_root, pytest.mark.require_zpool]


@pytest.fixture
def parallel_jails(release, invoke_cli, tmp_path):
    names = [f'parallel_{uuid.uuid4().hex[:8]}_{n}' for n in range(4)]
    hook = tmp_path / 'mark.py'
    mode = tmp_path / 'mode'
    mode.write_text('parallel')
    # Host hooks rendezvous with the other jail at the same priority. A timeout
    # makes missing overlap fail instead of leaving a blocked lifecycle hook.
    hook.write_text('''import pathlib
import sys
import time

directory = pathlib.Path(sys.argv[1])
name, action, phase = sys.argv[2:]
names = (directory / 'names').read_text().splitlines()
(directory / f'{name}.{action}.{phase}').write_text(str(time.monotonic_ns()))
if phase == 'begin':
    if (directory / 'mode').read_text() == 'parallel':
        # Adjacent entries are the pairs at priorities 10 and 20.
        peer = names[names.index(name) ^ 1]
        marker = directory / f'{peer}.{action}.begin'
        deadline = time.monotonic() + 30
        while not marker.exists():
            if time.monotonic() > deadline:
                sys.exit('Peer hook did not overlap')
            time.sleep(0.05)
    else:
        time.sleep(0.1)
''')
    (tmp_path / 'names').write_text('\n'.join(names))
    created = []

    try:
        for index, name in enumerate(names):
            props = ['boot=off', f'priority={10 if index < 2 else 20}',
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
                    f'{name} {action} {phase}'
                )

            created.append(name)
            invoke_cli(['create', '-r', release, '-n', name, *props])

        yield names, tmp_path, mode
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
    first, second = (names[:2], names[2:]) if action == 'start' else (
        names[2:], names[:2]
    )
    assert max(events(directory, n, action)[1] for n in first) < min(
        events(directory, n, action)[0] for n in second
    )

    for group in (first, second):
        a, b = [events(directory, n, action) for n in group]
        overlap = max(a[0], b[0]) < min(a[1], b[1])
        assert overlap is parallel


@pytest.mark.parametrize('jobs', [None, 1, 2])
def test_parallel_priority_barriers(parallel_jails, invoke_cli, jobs):
    names, directory, mode = parallel_jails

    if jobs == 1:
        mode.write_text('serial')

    options = ['--parallel'] + (['--jobs', str(jobs)] if jobs else [])

    for action in ('start', 'stop'):
        invoke_cli([action, *options, *reversed(names)])
        assert_priority_events(names, directory, action, jobs != 1)

        for name in names:
            assert IOCList.list_get_jid(name)[0] == (action == 'start')


def test_parallel_dependencies_and_force(parallel_jails, invoke_cli):
    names, directory, mode = parallel_jails
    mode.write_text('serial')
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
    names, directory, mode = parallel_jails
    mode.write_text('serial')
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


def test_parallel_boot_selection(parallel_jails, invoke_cli):
    names, _, _ = parallel_jails

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
    names, _, _ = parallel_jails

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


@pytest.mark.require_nat
def test_parallel_vnet_nat_allocations_and_cleanup(
    parallel_jails, invoke_cli, ping_ip
):
    names, _, _ = parallel_jails

    for index, name in enumerate(names):
        invoke_cli(['set', 'vnet=on', 'nat=on',
                    f'nat_forwards=tcp(80:{18080 + index})', name])

    invoke_cli(['start', '--parallel', *names])
    addresses = []
    rulesets = []
    interfaces = []

    for name in names:
        addresses.append(invoke_cli(['get', 'ip4_addr', name]).output.strip())
        rulesets.append(
            invoke_cli(['get', 'devfs_ruleset', name]).output.strip()
        )
        interfaces.append(f'vnet0.{IOCList.list_get_jid(name)[1]}')
        invoke_cli(['exec', name, 'ping', '-c', '1', ping_ip])

    assert len(set(addresses)) == len(names)
    assert len(set(rulesets)) == len(names)
    invoke_cli(['stop', '--parallel', *names])
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
