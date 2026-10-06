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

"""Shared allocations and rc dispatch must stay safe under concurrency."""
import concurrent.futures
import os
import pathlib
import subprocess
import threading

import pytest

import iocage_lib.ioc_common as common
import iocage_lib.ioc_start as start


def test_concurrent_address_allocations_reserve_before_kernel_use(monkeypatch):
    monkeypatch.setattr(common, 'get_used_ips', lambda: [])
    monkeypatch.setattr(common.netifaces, 'ifaddresses', lambda name: {
        common.netifaces.AF_INET: [{'addr': '127.0.0.1'}]
    })
    reserved = set()
    barrier = threading.Barrier(8)

    def allocate(index):
        barrier.wait(timeout=5)
        return (common.gen_nat_ip('172.16', reserved),
                common.gen_unused_lo_ip(reserved))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(allocate, range(8)))

    pairs, loopbacks = zip(*results)
    addresses = [address for pair in pairs for address in pair]
    assert len(set(addresses)) == 16
    assert len(set(loopbacks)) == 8
    assert '127.0.0.1' not in loopbacks
    assert reserved == set(addresses).union(loopbacks)


def test_nat_host_scans_overlap_while_reservations_stay_unique(monkeypatch):
    scans = threading.Barrier(2)

    def host_addresses():
        scans.wait(timeout=5)
        return ['172.16.0.1']

    monkeypatch.setattr(common, 'get_used_ips', host_addresses)
    reserved = {'172.16.0.5'}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        pairs = list(executor.map(
            lambda index: common.gen_nat_ip('172.16', reserved), range(2)
        ))

    allocated = {address for pair in pairs for address in pair}
    assert allocated == {
        '172.16.0.9', '172.16.0.10', '172.16.0.13', '172.16.0.14'
    }
    assert reserved == allocated.union({'172.16.0.5'})


def test_concurrent_devfs_allocation_is_atomic(monkeypatch, tmp_path):
    monkeypatch.setattr(common, 'DEVFS_LOCK_PATH', str(tmp_path / 'devfs.lock'))
    rulesets = set()
    barrier = threading.Barrier(4)

    def run(command, **kwargs):
        if command[-1] == 'showsets':
            return subprocess.CompletedProcess(
                command, 0, stdout='\n'.join(str(n) for n in rulesets)
            )

        assert command[0:3] == ['devfs', 'rule', '-s']
        rulesets.add(int(command[3]))
        return subprocess.CompletedProcess(command, 0, stdout='')

    monkeypatch.setattr(common.su, 'run', run)
    conf = {
        'devfs_ruleset': '4', 'min_dyn_devfs_ruleset': '1000',
        'allow_mount_fusefs': False, 'bpf': False, 'allow_tun': False
    }

    def allocate(index):
        barrier.wait(timeout=5)
        return common.generate_devfs_ruleset(conf)[2]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(allocate, range(4)))

    assert set(results) == {'1000', '1001', '1002', '1003'}


def test_devfs_lock_is_released_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(common, 'DEVFS_LOCK_PATH', str(tmp_path / 'devfs.lock'))

    with pytest.raises(RuntimeError):
        with common.devfs_ruleset_lock():
            raise RuntimeError('allocation failed')

    with common.devfs_ruleset_lock():
        pass


@pytest.mark.parametrize('configured', ['0', '42'])
@pytest.mark.parametrize('minimum', [0, 1000])
def test_empty_devfs_clones_are_reserved_before_jail_creation(
    monkeypatch, tmp_path, configured, minimum
):
    monkeypatch.setattr(common, 'DEVFS_LOCK_PATH', str(tmp_path / 'devfs.lock'))
    rulesets = {42}
    added = []
    barrier = threading.Barrier(2)

    def run(command, **kwargs):
        if command[-1] == 'showsets':
            output = '\n'.join(str(n) for n in rulesets)
        elif command[-1] == 'show':
            output = ''
        else:
            assert command[4:] == ['add', 'path', '*']
            assert kwargs['check']
            assert int(command[3]) > 0  # Ruleset zero is immutable.
            rulesets.add(int(command[3]))
            added.append(command)
            output = ''

        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(common.su, 'run', run)
    conf = {
        'devfs_ruleset': configured, 'min_dyn_devfs_ruleset': str(minimum)
    }

    def allocate(index):
        barrier.wait(timeout=5)
        return common.generate_devfs_ruleset(conf)[2]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(allocate, range(2)))

    first = max(1, minimum)
    assert set(results) == {str(first), str(first + 1)}
    assert len(added) == 2


@pytest.mark.parametrize('failed', [False, True])
def test_prestart_failure_releases_only_its_allocated_ruleset(
    monkeypatch, tmp_path, failed
):
    monkeypatch.setattr(common, 'DEVFS_LOCK_PATH', str(tmp_path / 'devfs.lock'))
    monkeypatch.setattr(common, 'INTERACTIVE', False)
    monkeypatch.setattr(common, 'runscript',
                        lambda *args: (None, 'hook failed' if failed else None))
    rulesets = {'4', '1000', '1001'}
    stopped = []

    def remove(command, **kwargs):
        assert command == ['devfs', 'rule', '-s', '1000', 'delset']
        rulesets.remove(command[3])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(start.su, 'run', remove)
    monkeypatch.setattr(start.iocage_lib.ioc_stop, 'IOCStop',
                        lambda *args, **kwargs: stopped.append(args[0]))
    jail = start.IOCStart('alpha', '/alpha', unit_test=True)

    if failed:
        with pytest.raises(RuntimeError, match='hook failed'):
            jail.__run_prestart__('script', None, '1000')
    else:
        jail.__run_prestart__('script', None, '1000')

    assert rulesets == ({'4', '1001'} if failed else {'4', '1000', '1001'})
    assert stopped == (['alpha'] if failed else [])


@pytest.mark.parametrize('action', ['start', 'stop'])
@pytest.mark.parametrize('parallel,jobs', [
    ('NO', ''), ('NO', '2'), ('YES', ''), ('YES', '2')
])
def test_rc_script_passes_options_and_quoted_program(
    tmp_path, action, parallel, jobs
):
    source = pathlib.Path(__file__).parents[2] / 'rc.d' / 'iocage'
    # Supply rc.subr's interface so the actual script can run on non-FreeBSD.
    script = source.read_text().replace('. /etc/rc.subr', '''
load_rc_config() { :; }
checkyesno() { eval "value=\\${$1}"; [ "$value" = "YES" ]; }
run_rc_command() { "iocage_$1"; }
''')
    program = tmp_path / 'program with spaces'
    program.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    program.chmod(0o755)
    result = subprocess.run(
        ['sh', '-c', script, 'iocage', action], capture_output=True, text=True,
        env={**os.environ, 'iocage_enable': 'YES',
             'iocage_program': str(program), 'iocage_parallel': parallel,
             'iocage_parallel_jobs': jobs}
    )
    assert result.returncode == 0, result.stderr
    expected = [action]

    if parallel == 'YES':
        expected.append('--parallel')

        if jobs:
            expected.extend(['--jobs', jobs])

    expected.append('--rc')
    assert result.stdout.splitlines()[1:] == expected
