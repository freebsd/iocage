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
import json
import os
import pathlib
import subprocess
import threading

from unittest import mock

import pytest

import iocage_lib.ioc_common as common
import iocage_lib.ioc_nat as nat
import iocage_lib.ioc_start as start
import iocage_lib.ioc_stop as stop


def test_concurrent_address_allocations_reserve_before_kernel_use(monkeypatch):
    monkeypatch.setattr(common, 'get_used_ips', lambda: [])
    monkeypatch.setattr(common, 'get_host_ips', lambda: [])
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


def test_batch_rechecks_live_addresses_and_keeps_reservations(monkeypatch):
    scan = mock.Mock(return_value=['172.16.0.1', '172.16.0.9'])
    monkeypatch.setattr(common, 'get_used_ips', scan)
    reserved = {'172.16.0.1', '172.16.0.5'}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        pairs = list(executor.map(
            lambda _: common.gen_nat_ip('172.16', reserved), range(2)
        ))
    assert {ip for pair in pairs for ip in pair} == {
        '172.16.0.13', '172.16.0.14', '172.16.0.17', '172.16.0.18'
    }
    assert scan.call_count == 2
    assert common.gen_nat_ip('172.16') == ['172.16.0.5', '172.16.0.6']


def test_only_vnet_addresses_need_an_interface_lookup(monkeypatch):
    shared = {'jid': '1', 'vnet': 'inherit', 'ip4.addr': ['192.0.2.1'],
              'ip6.addr': ['2001:db8::1']}
    vnet = {'jid': '2', 'vnet': 'new', 'ip4.addr': [], 'ip6.addr': []}
    inventory = {'jail-information': {'jail': [shared, vnet]}}
    monkeypatch.setattr(common.su, 'check_output',
                        lambda args: json.dumps(inventory).encode())
    monkeypatch.setattr(common, 'get_host_ips', lambda: ['127.0.0.1'])
    interfaces = mock.Mock(return_value=mock.Mock(
        stdout='    inet 192.0.2.2 netmask 0xffffffff\n'))
    monkeypatch.setattr(common.su, 'run', interfaces)
    assert set(common.get_used_ips()) == {
        '127.0.0.1', '192.0.2.1', '192.0.2.2', '2001:db8::1'
    }
    assert interfaces.call_args.args[0] == ['ifconfig', '-j', '2', '-a']
    assert interfaces.call_count == 1


@pytest.mark.parametrize('reserved,used,running,error', [
    ({8080, 8081}, (), (), None),
    ({8081}, (), (), 'changed after preflight'),
    ((), (), (), 'changed after preflight'),
    ({8080}, (), (8080,), 'already in use'),
    (None, (8080,), (), 'already in use'),
])
def test_worker_checks_its_nat_ports(
    monkeypatch, reserved, used, running, error
):
    jail = start.IOCStart('alpha', '/alpha', unit_test=True, used_ports=used,
                          reserved_ports=reserved)
    monkeypatch.setattr(nat, 'running_ports', lambda: running)

    if error:
        with pytest.raises(RuntimeError, match=error):
            jail._check_nat_forwards('tcp(80:8080)')
    else:
        jail._check_nat_forwards('tcp(80:8080)')


@pytest.mark.parametrize('interactive', [True, False])
@pytest.mark.parametrize('operation', ['apply', 'running_ports', 'parse'])
def test_nat_command_error_uses_cli_and_library_handlers(
    monkeypatch, interactive, operation, caplog
):
    jail = start.IOCStart('alpha', '/alpha', unit_test=True)
    failure = subprocess.CalledProcessError(1, ['pfctl'], stderr='bad policy')
    if operation != 'parse':
        monkeypatch.setattr(nat, operation, mock.Mock(side_effect=failure))
    message = 'Invalid NAT' if operation == 'parse' else 'bad policy'
    monkeypatch.setattr(common, 'INTERACTIVE', interactive)
    expected = SystemExit if interactive else start.ioc_exceptions.CommandFailed
    with pytest.raises(expected) as error:
        if operation == 'apply':
            jail.__add_nat__('em0', 'none', 'pf')
        else:
            jail._check_nat_forwards('bad' if operation == 'parse' else 'none')
    if interactive:
        assert error.value.code == 1
        assert message in caplog.text
    else:
        assert message in str(error.value)


@pytest.mark.parametrize('created,cleanup_fails', [
    (False, False), (True, False), (True, True)
])
def test_start_failure_rolls_back_only_its_own_jid(
    monkeypatch, created, cleanup_fails
):
    config = mock.Mock()
    config.json_get_value.return_value = {'exec_fib': '0'}
    monkeypatch.setattr(start.iocage_lib.ioc_json, 'IOCJson',
                        mock.Mock(return_value=config))

    def fail(jail):
        if created:
            jail._created_jid = '17'
        raise RuntimeError('dataset is in use')

    monkeypatch.setattr(start.IOCStart, '__start_jail__', fail)
    cleanup = mock.Mock(side_effect=RuntimeError('cleanup') if cleanup_fails
                        else None)
    monkeypatch.setattr(stop, 'IOCStop', cleanup)
    with pytest.raises(RuntimeError, match='dataset is in use'):
        start.IOCStart('db.jail', '/db')
    if created:
        cleanup.assert_called_once_with('db.jail', '/db', force=True,
                                        silent=True, expected_jid='17',
                                        cleanup_devfs=True)
    else:
        cleanup.assert_not_called()


@pytest.mark.parametrize('failed', ['showsets', 'delset', 'pfctl'])
def test_stop_reports_cleanup_failure_after_unmounts(monkeypatch, tmp_path,
                                                     failed):
    jail = object.__new__(stop.IOCStop)
    jail.uuid, jail.path, jail.jid = 'alpha', str(tmp_path), '1'
    jail.force, jail.status, jail.expected_jid = True, True, None
    jail.cleanup_devfs = True
    monkeypatch.setattr(nat, 'RUNTIME', tmp_path / 'nat')
    monkeypatch.setattr(common, 'get_active_jails', lambda: {})
    jail.conf = dict(ip4_addr='none', ip6_addr='none', dhcp=0, vnet=0, nat=0,
                     exec_fib='0', exec_poststop='/usr/bin/true')
    jail.silent, jail.callback = True, None
    monkeypatch.setattr(common, 'DEVFS_LOCK_PATH', str(tmp_path / 'lock'))
    monkeypatch.setattr(common, 'runscript', lambda *args: ('', None))
    json_lib = stop.iocage_lib.ioc_json
    config = mock.Mock()
    config.json_get_value.return_value = '1000'
    monkeypatch.setattr(json_lib, 'IOCJson', mock.Mock(return_value=config))
    rctl = mock.Mock()
    rctl.rctl_rules_exist.return_value = False
    monkeypatch.setattr(json_lib, 'IOCRCTL', mock.Mock(return_value=rctl))
    monkeypatch.setattr(json_lib, 'JailRuntimeConfiguration',
                        mock.Mock(return_value=mock.Mock(data={})))

    def devfs(command, **kwargs):
        if command[-1] == failed:
            raise subprocess.CalledProcessError(1, command, stderr='busy')
        return '1000\n'

    monkeypatch.setattr(stop.su, 'check_output', devfs)
    monkeypatch.setattr(stop.su, 'run', devfs)
    process = mock.Mock(returncode=0)
    process.communicate.return_value = (b'', b'')
    popen = mock.Mock(return_value=process)
    monkeypatch.setattr(stop.su, 'Popen', popen)
    monkeypatch.setattr(nat, '_module_loaded', lambda name: False)
    if failed == 'pfctl':
        nat.running_ports()
        path = nat.RUNTIME / 'pf.json'
        nat._save_records(path, {'ioc-alpha': {
            'jid': '1', 'address': '192.0.2.7', 'interface': 'em0',
            'forwards': 'none'
        }})
        monkeypatch.setattr(common, 'get_active_jails',
                            lambda: {'ioc-alpha': {'jid': 1}})
        monkeypatch.setattr(nat, '_module_loaded', lambda name: name == 'pf')
        monkeypatch.setattr(nat, '_apply_pf', mock.Mock())
        error = subprocess.CalledProcessError(1, ['pfctl'], stderr='busy')
        monkeypatch.setattr(nat, '_clear_pf_states',
                            mock.Mock(side_effect=[None, error]))
        expected = pytest.raises(subprocess.CalledProcessError, match='pfctl')
    else:
        expected = pytest.raises(RuntimeError, match='devfs ruleset 1000')
    with expected:
        stop.IOCStop.__stop_jail__(jail)
    commands = [call.args[0][0] for call in popen.call_args_list]
    assert commands == ['jail'] + ['umount'] * 5
    if failed == 'pfctl':
        assert path.read_text() == '{}'


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


def test_exhausted_devfs_ids_never_touch_another_ruleset(monkeypatch, tmp_path):
    monkeypatch.setattr(common, 'DEVFS_LOCK_PATH', str(tmp_path / 'devfs.lock'))
    listed = mock.Mock(return_value=subprocess.CompletedProcess(
        [], 0, stdout='65535\n'
    ))
    monkeypatch.setattr(common.su, 'run', listed)
    conf = {
        'devfs_ruleset': '4', 'min_dyn_devfs_ruleset': '65535',
        'allow_mount_fusefs': False, 'bpf': False, 'allow_tun': False
    }
    with pytest.raises(ValueError, match='No available dynamic devfs ruleset'):
        common.generate_devfs_ruleset(conf)
    assert [call.args[0] for call in listed.call_args_list] == [
        ['devfs', 'rule', 'showsets']
    ]


@pytest.mark.parametrize('configured,fail_at', [
    ('42', ['showsets']),
    ('42', ['show']),
    ('42', ['add', 'path', 'zero', 'unhide']),
    ('4', ['add', 'path', 'null', 'unhide']),
    ('0', ['add', 'path', '*']),
])
def test_devfs_failure_discards_only_the_partial_ruleset(
    monkeypatch, tmp_path, configured, fail_at
):
    monkeypatch.setattr(common, 'DEVFS_LOCK_PATH', str(tmp_path / 'devfs.lock'))
    existing = {'4', '42', '1000'}
    rulesets = existing.copy()
    deleted = []

    def run(command, **kwargs):
        assert common.DEVFS_LOCK.locked()
        output = ''
        if command[-1] == 'showsets':
            output = '\n'.join(rulesets)
        elif command[-1] == 'show' and configured == '42':
            output = '100 hide\n200 path zero unhide\n'
        elif 'add' in command:
            rulesets.add(command[3])
        elif command[-1] == 'delset':
            deleted.append(command[3])
            rulesets.remove(command[3])

        failed = command[-len(fail_at):] == fail_at
        result = subprocess.CompletedProcess(command, int(failed), output)
        if kwargs.get('check'):
            result.check_returncode()
        return result

    monkeypatch.setattr(common.su, 'run', run)
    conf = {
        'devfs_ruleset': configured, 'min_dyn_devfs_ruleset': '1000',
        'allow_mount_fusefs': False, 'bpf': False, 'allow_tun': False
    }
    with pytest.raises(subprocess.CalledProcessError) as error:
        common.generate_devfs_ruleset(conf)

    assert error.value.cmd[-len(fail_at):] == fail_at
    assert rulesets == existing
    assert deleted == (['1001'] if fail_at[0] == 'add' else [])


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

    jail._reserved_devfs_ruleset = '1000'
    monkeypatch.setattr(start.su, 'check_output', lambda *args, **kwargs: '')

    if failed:
        with pytest.raises(RuntimeError, match='hook failed'):
            try:
                jail.__run_prestart__('script', None)
            finally:
                jail._release_reserved_devfs_ruleset()
    else:
        jail.__run_prestart__('script', None)

    assert rulesets == ({'4', '1001'} if failed else {'4', '1000', '1001'})
    assert stopped == []


@pytest.mark.parametrize('inventory, should_release', [
    pytest.param('ioc-other 1001', True, id='unused'),
    pytest.param('ioc-alpha 1000', False, id='jail-still-present'),
    pytest.param('ioc-other 1000', False, id='ruleset-in-use'),
    pytest.param(None, False, id='cannot-list-jails'),
])
def test_release_reserved_ruleset(
    monkeypatch, tmp_path, inventory, should_release
):
    monkeypatch.setattr(common, 'DEVFS_LOCK_PATH', str(tmp_path / 'devfs.lock'))
    jail = start.IOCStart('alpha', '/alpha', unit_test=True)
    jail._reserved_devfs_ruleset = '1000'
    list_jails = mock.Mock(return_value=inventory)
    delete = mock.Mock()

    if inventory is None:
        list_jails.side_effect = OSError('jls unavailable')

    monkeypatch.setattr(start.su, 'check_output', list_jails)
    monkeypatch.setattr(start.su, 'run', delete)
    jail._release_reserved_devfs_ruleset()
    # A failed prestart hook and the outer finally can both request cleanup.
    jail._release_reserved_devfs_ruleset()

    list_jails.assert_called_once_with(
        ['jls', '-d', 'name', 'devfs_ruleset'], text=True
    )

    if should_release:
        delete.assert_called_once_with(
            ['devfs', 'rule', '-s', '1000', 'delset'],
            stdout=subprocess.PIPE, check=True
        )
    else:
        delete.assert_not_called()


@pytest.mark.parametrize('returncode', [0, 1], ids=['created', 'failed'])
def test_ruleset_ownership_after_jail_command(monkeypatch, returncode):
    monkeypatch.setattr(common, 'INTERACTIVE', False)
    jail = start.IOCStart('alpha', '/alpha', unit_test=True)
    jail._reserved_devfs_ruleset = '1000'
    command = ['jail', '-i', '-f', 'alpha.conf', '-c']
    result = subprocess.CompletedProcess(command, returncode,
                                         stdout=b'' if returncode else b'17\n',
                                         stderr=b'creation failed')
    monkeypatch.setattr(start.su, 'run', mock.Mock(return_value=result))

    if returncode:
        with pytest.raises(RuntimeError, match='creation failed'):
            jail._create_jail(command, {}, False)

        assert jail._reserved_devfs_ruleset == '1000'
    else:
        jail._create_jail(command, {}, False)
        assert jail._reserved_devfs_ruleset is None
        assert jail._created_jid == '17'


@pytest.mark.parametrize('interrupted, live_ruleset, expected_jid', [
    (False, '1000', '17'), (True, '1000', '17'), (True, '1001', None)
])
def test_partial_creation_has_one_ruleset_owner(
    monkeypatch, tmp_path, interrupted, live_ruleset, expected_jid
):
    monkeypatch.setattr(common, 'INTERACTIVE', False)
    monkeypatch.setattr(common, 'DEVFS_LOCK_PATH', str(tmp_path / 'devfs'))
    config = mock.Mock()
    config.json_get_value.return_value = {'exec_fib': '0'}
    monkeypatch.setattr(start.iocage_lib.ioc_json, 'IOCJson',
                        mock.Mock(return_value=config))
    deletes = []

    def run(args, **kwargs):
        if args[0] == 'jail':
            if interrupted:
                raise KeyboardInterrupt
            return subprocess.CompletedProcess(args, 1, b'17\n', b'hook failed')
        if args[0] == 'jls':
            assert args == ['jls', '-j', 'ioc-alpha', 'jid', 'devfs_ruleset']
            return subprocess.CompletedProcess(args, 0, f'17 {live_ruleset}\n')
        assert args == ['devfs', 'rule', '-s', '1000', 'delset']
        deletes.append(args)
        return subprocess.CompletedProcess(args, 0)

    def create(jail):
        jail._reserved_devfs_ruleset = '1000'
        jail._create_jail(['jail', '-i'], {}, False)

    monkeypatch.setattr(start.IOCStart, '__start_jail__', create)
    monkeypatch.setattr(start.su, 'run', run)
    monkeypatch.setattr(start.su, 'check_output', lambda *a, **kw: '')
    cleanup = mock.Mock()
    monkeypatch.setattr(stop, 'IOCStop', cleanup)
    with pytest.raises(KeyboardInterrupt if interrupted else RuntimeError):
        start.IOCStart('alpha', '/alpha')
    if expected_jid:
        cleanup.assert_called_once_with('alpha', '/alpha', force=True,
                                        silent=True, expected_jid=expected_jid,
                                        cleanup_devfs=False)
    else:
        cleanup.assert_not_called()
    assert len(deletes) == 1


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
