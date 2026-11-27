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

"""Exercise real dispatch against an in-memory host and lifecycle hooks."""
import importlib.util
import logging
import pathlib
import subprocess
import threading
import time

from types import SimpleNamespace
from unittest import mock

import pytest

from click.testing import CliRunner

import iocage_lib.ioc_common as common
import iocage_lib.iocage as ioc


@pytest.fixture
def host(monkeypatch):
    """Run each worker against the same jail inventory and record its calls."""
    configs = {}
    running = {}
    calls = []
    hooks = {}

    def add(name, priority=10, boot=False, depends='none', up=False,
            nat=False, forwards='none'):
        configs[name] = {
            'priority': str(priority), 'boot': boot, 'depends': depends,
            'type': 'jail', 'release': 'EMPTY', 'nat': nat,
            'nat_forwards': forwards, 'nat_backend': 'ipfw'
        }
        running[name] = up

    def initialize(self, jail=None, rc=False, callback=None, silent=False,
                   **kwargs):
        self.jail = jail
        self.rc = rc
        self.callback = callback
        self.silent = silent
        self.is_depend = False
        self.skip_jails = kwargs.get('skip_jails', False)
        self.jails = {name: name for name in configs}
        self._all = bool(jail and 'ALL' in jail)

    def resolve(self):
        matches = [name for name in configs if name.startswith(self.jail)]

        if len(matches) != 1:
            raise ValueError(f'Missing or ambiguous jail: {self.jail}')

        return matches[0], matches[0]

    class Json:

        def __init__(self, path, **kwargs):
            self.path = path

        def json_get_value(self, key):
            assert key == 'all'
            return configs[self.path]

    def start(name, path, callback=None, silent=False, **kwargs):
        calls.append(('start', name, kwargs))

        if name in hooks:
            hooks[name]()

        common.logit({'level': 'INFO', 'message': 'started'},
                     _callback=callback, silent=silent)
        running[name] = True

    def stop(name, path, force=False, callback=None, silent=False, **kwargs):
        calls.append(('stop', name, {'force': force, **kwargs}))

        if name in hooks:
            hooks[name]()

        common.logit({'level': 'INFO', 'message': 'stopped'},
                     _callback=callback, silent=silent)
        running[name] = False

    def list_jails(self, kind, uuid=None):
        if kind == 'uuid':
            return {name: name for name in configs}

        assert kind == 'jid'
        return running[uuid], '1'

    monkeypatch.setattr(common, 'INTERACTIVE', False)
    monkeypatch.setattr(common, 'get_active_jails', lambda: {
        f"ioc-{name.replace('.', '_')}": {'jid': 1}
        for name, up in running.items() if up
    })
    monkeypatch.setattr(ioc.IOCage, '__init__', initialize)
    monkeypatch.setattr(ioc.IOCage, '__check_jail_existence__', resolve)
    monkeypatch.setattr(ioc.IOCage, 'list', list_jails)
    monkeypatch.setattr(ioc.ioc_json, 'IOCJson', Json)
    monkeypatch.setattr(ioc.ioc_start, 'IOCStart', start)
    monkeypatch.setattr(ioc.ioc_stop, 'IOCStop', stop)
    monkeypatch.setattr(common, 'get_used_ips', lambda: [])
    monkeypatch.setattr(ioc.ioc_nat, 'running_ports', lambda: {
        port for name, conf in configs.items() if running[name] and conf['nat']
        for port in ioc.ioc_nat.forward_ports(conf['nat_forwards'])
    })
    return SimpleNamespace(add=add, running=running, calls=calls,
                           hooks=hooks, configs=configs)


def test_names_are_resolved_deduplicated_and_sorted(host):
    host.add('alpha', 20)
    host.add('beta', 10)
    parent = ioc.IOCage()
    assert parent.start(parallel=True, jails=['alpha', 'al', 'beta']) == {}
    assert [call[1] for call in host.calls] == ['beta', 'alpha']
    assert all(host.running.values())
    assert parent.jail is None


def test_boot_selection_includes_shared_off_prerequisites_once(host):
    host.add('a', boot=True, depends='c')
    host.add('b', boot=True, depends='c')
    host.add('c', boot=False)
    host.add('d', boot=False)
    ioc.IOCage(rc=True).start(parallel=True, jobs=1)
    assert [call[1] for call in host.calls] == ['c', 'a', 'b']
    assert host.running == {'a': True, 'b': True, 'c': True, 'd': False}


def test_stop_rc_does_not_expand_dependencies(host):
    host.add('a', boot=True, depends='b', up=True)
    host.add('b', boot=False, up=True)
    ioc.IOCage(rc=True).stop(parallel=True, force=True)
    assert len(host.calls) == 1
    assert host.calls[0][1:] == ('a', {
        'force': True, 'suppress_exception': False
    })
    assert host.running == {'a': False, 'b': True}


@pytest.mark.parametrize('rc', [False, True])
def test_reused_bulk_instance_refreshes_dataset_inventory(host, rc):
    host.add('old', boot=True)
    parent = ioc.IOCage(rc=rc, jail=None if rc else 'ALL')
    parent.start(parallel=True)
    del host.configs['old']
    del host.running['old']
    host.add('new', boot=True, up=True)

    assert parent.stop(parallel=True) == {}
    assert [name for _, name, _ in host.calls] == ['old', 'new']
    assert not host.running['new']


@pytest.mark.parametrize('stage', ['preflight', 'verification'])
def test_stop_fails_when_kernel_state_is_unknown(host, monkeypatch, stage):
    host.add('db', up=True)
    stop_attempts = []
    error = subprocess.CalledProcessError(1, ['jls'])

    def inventory():
        if stage == 'preflight' or stop_attempts:
            raise error
        return {'ioc-db': {'jid': 7}}

    def lookup(command, **kwargs):
        if stop_attempts:
            raise error
        return '7\n'

    monkeypatch.setattr(common, 'get_active_jails', inventory)
    monkeypatch.setattr(common, 'checkoutput', lookup)
    monkeypatch.setattr(ioc.IOCage, 'list', lambda self, kind, uuid:
                        ioc.ioc_list.IOCList.list_get_jid(uuid))
    monkeypatch.setattr(ioc.ioc_stop, 'IOCStop', lambda name, *args, **kwargs:
                        stop_attempts.append(name))
    with pytest.raises(RuntimeError, match='jls'):
        ioc.IOCage(jail='db').stop(parallel=True)

    assert host.running['db']
    assert stop_attempts == ([] if stage == 'preflight' else ['db'])


@pytest.mark.parametrize('up', [False, True])
def test_failed_named_jid_lookup_uses_checked_inventory(host, monkeypatch, up):
    host.add('db.one', up=up)

    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(common, 'checkoutput', fail)
    assert ioc.ioc_list.IOCList.list_get_jid('db.one') == (
        up, '1' if up else '-'
    )


@pytest.mark.parametrize('rc', [False, True])
@pytest.mark.parametrize('parallel', [False, True])
def test_force_reaches_every_bulk_stop(host, rc, parallel):
    host.add('a', boot=True, up=True)
    host.add('b', boot=True, up=True)
    ioc.IOCage(jail=None if rc else 'ALL', rc=rc).stop(
        parallel=parallel, force=True
    )
    assert len(host.calls) == 2
    assert all(call[2]['force'] for call in host.calls)


def test_parallel_stop_handles_name_containing_all(host):
    host.add('SMALL', up=True)
    host.add('other', up=True)
    ioc.IOCage().stop(parallel=True, jails=['SMALL'])
    assert [call[1] for call in host.calls] == ['SMALL']
    assert host.running['other']


@pytest.mark.parametrize('action', ['start', 'stop'])
@pytest.mark.parametrize('ignore', [False, True])
def test_failure_reporting_and_ignore(host, action, ignore, caplog):
    host.add('a', 10 if action == 'start' else 20, up=action == 'stop')
    host.add('b', 20 if action == 'start' else 10, up=action == 'stop')

    def fail():
        raise SystemExit('broken hook')

    host.hooks['a'] = fail
    operate = getattr(ioc.IOCage(jail='ALL'), action)

    if ignore:
        failures = operate(parallel=True, ignore_exception=True)
        assert failures == {'a': 'broken hook'}
        assert 'a: broken hook' in caplog.text
    else:
        with pytest.raises(RuntimeError, match='a: broken hook'):
            operate(parallel=True)

    expected = ['a', 'b'] if ignore or action == 'stop' else ['a']
    assert [call[1] for call in host.calls] == expected


@pytest.mark.parametrize('action', ['start', 'stop'])
def test_early_return_is_a_failure(host, monkeypatch, action):
    host.add('a', up=action == 'stop')

    if action == 'start':
        monkeypatch.setattr(ioc.IOCage, '__start_jail__',
                            lambda *args, **kwargs: (False, None))
    else:
        monkeypatch.setattr(ioc.ioc_stop, 'IOCStop',
                            lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match=f'Jail did not {action}'):
        getattr(ioc.IOCage(jail='a'), action)(parallel=True)


@pytest.mark.parametrize('problem', ['cycle', 'missing', 'priority'])
def test_dependency_errors_are_fatal_even_with_ignore(host, problem):
    dependency = {'cycle': 'a', 'missing': 'absent', 'priority': 'b'}[problem]
    host.add('a', depends=dependency)

    if problem == 'priority':
        host.add('b', priority=20)

    with pytest.raises(RuntimeError):
        ioc.IOCage(jail='a').start(parallel=True, ignore_exception=True)

    assert host.calls == []


def test_callbacks_are_serialized_and_include_identity(host):
    host.add('a')
    host.add('b')
    host.add('c', priority=20)
    host.add('d', priority=20)
    barrier = threading.Barrier(2)
    for name in 'abcd':
        host.hooks[name] = lambda: barrier.wait(timeout=5)
    active = 0
    messages = []

    def callback(content, exception):
        nonlocal active
        active += 1
        assert active == 1
        time.sleep(0.01)
        messages.append(content['message'])
        active -= 1

    ioc.IOCage(jail='ALL', silent=True, callback=callback).start(parallel=True)
    assert sorted(messages) == [f'{name}: started' for name in 'abcd']


@pytest.mark.parametrize('message', ['hook failed', 'a: hook failed'])
def test_cli_worker_errors_keep_their_message_and_identity(
    host, monkeypatch, caplog, message
):
    host.add('a')
    host.add('b', priority=20)
    monkeypatch.setattr(common, 'INTERACTIVE', True)

    def fail():
        common.logit({'level': 'EXCEPTION', 'message': message})

    host.hooks['a'] = fail
    result = CliRunner().invoke(load_cli('start'), ['--parallel', 'ALL'])
    assert result.exit_code == 1
    assert [call[1] for call in host.calls] == ['a']
    assert 'a: hook failed' in caplog.text
    assert 'a: 1' not in caplog.text
    assert 'a: a:' not in caplog.text


def test_parallel_log_context_does_not_change_sequential_errors(
    host, monkeypatch
):
    host.add('a')
    ioc.IOCage(jail='a').start(parallel=True)
    monkeypatch.setattr(common, 'INTERACTIVE', True)

    with pytest.raises(SystemExit) as error:
        common.logit({'level': 'EXCEPTION', 'message': 'sequential error'})

    assert error.value.code == 1


@pytest.mark.parametrize('conflict', ['pending', 'running', 'supplied'])
@pytest.mark.parametrize('forwards', ['tcp(81:8080)', 'udp(8078-8080)'])
def test_nat_conflicts_fail_before_lifecycle(host, conflict, forwards):
    # Compare a single host port with the upper end of a range, including
    # when one of the reservations came from a running jail or the caller.
    host.add('a', nat=True, forwards=forwards, up=conflict == 'running')

    if conflict != 'supplied':
        host.add('b', nat=True, forwards='tcp(80:8080)')

    with pytest.raises(RuntimeError, match='NAT forwarding ports conflict'):
        ioc.IOCage(jail='ALL').start(
            parallel=True, used_ports=[8080] if conflict == 'supplied' else None
        )

    assert host.calls == []


def test_workers_receive_only_their_ports_and_share_address_reservations(
    host, monkeypatch
):
    host.add('a', nat=True, forwards='tcp(80:8080)')
    host.add('b', nat=True, forwards='tcp(8081-8083)')
    peers = threading.Barrier(2)

    def allocate():
        common.gen_nat_ip('172.16', host.calls[0][2]['used_ips'])
        peers.wait(timeout=5)

    host.hooks['a'] = host.hooks['b'] = allocate
    scan = mock.Mock(return_value=['172.16.0.2'])
    monkeypatch.setattr(common, 'get_used_ips', scan)
    monkeypatch.setattr(common, 'get_host_ips', lambda: [])
    ioc.IOCage(jail='ALL').start(parallel=True)
    arguments = {name: kwargs for _, name, kwargs in host.calls}
    assert arguments['a']['reserved_ports'] == {8080}
    assert arguments['b']['reserved_ports'] == {8081, 8082, 8083}
    assert arguments['a']['used_ips'] is arguments['b']['used_ips']
    assert '172.16.0.2' in arguments['a']['used_ips']
    assert scan.call_count == 2


@pytest.mark.parametrize('priority', [10, 20])
def test_later_start_sees_addresses_assigned_by_a_hook(host, monkeypatch,
                                                       priority):
    host.add('first')
    host.add('second', priority=priority, nat=True, depends='first')
    live = []
    monkeypatch.setattr(common, 'get_used_ips', lambda: live.copy())
    monkeypatch.setattr(common, 'get_host_ips', lambda: [])
    host.hooks['first'] = lambda: live.append('172.16.0.1')
    pairs = []

    def allocate():
        reservations = host.calls[-1][2]['used_ips']
        pairs.append(common.gen_nat_ip('172.16', reservations))

    host.hooks['second'] = allocate
    assert ioc.IOCage(jail='ALL').start(parallel=True, jobs=1) == {}
    assert pairs == [['172.16.0.5', '172.16.0.6']]


def test_active_worker_refreshes_jail_ips_at_allocation(host, monkeypatch):
    host.add('first')
    host.add('waiting', nat=True)
    live = []
    monkeypatch.setattr(common, 'get_used_ips', lambda: live.copy())
    address_ready = threading.Event()
    allocated = threading.Event()
    pairs = []

    def assign():
        live.append('172.16.0.1')
        address_ready.set()
        assert allocated.wait(timeout=5)

    def allocate():
        try:
            assert address_ready.wait(timeout=5)
            reservations = host.calls[0][2]['used_ips']
            pairs.append(common.gen_nat_ip('172.16', reservations))
        finally:
            allocated.set()

    # The first hook has assigned its address but cannot finish until the
    # second worker makes its reservation.
    host.hooks.update(first=assign, waiting=allocate)
    assert ioc.IOCage(jail='ALL').start(parallel=True, jobs=2) == {}
    assert pairs == [['172.16.0.5', '172.16.0.6']]


def test_pending_static_addresses_are_reserved(host):
    host.add('a')
    host.add('b')
    host.configs['b'].update({
        'ip4_addr': 'lo0|172.16.0.1/32', 'assign_localhost': True,
        'localhost_ip': '127.0.0.2'
    })
    ioc.IOCage(jail='ALL').start(parallel=True)
    assert host.calls[0][2]['used_ips'] == {'172.16.0.1', '127.0.0.2'}


@pytest.mark.parametrize('rc', [False, True])
def test_bulk_start_with_disabled_thick_vnet_localhost(host, rc):
    host.add('vnet', boot=True)
    host.add('other', boot=True)
    host.configs['vnet'].update(vnet=1, assign_localhost=0, localhost_ip=0)
    ioc.IOCage(rc=rc, jail=None if rc else 'ALL').start(parallel=True)
    assert all(host.running.values())


def test_silent_without_consumer_callback_stays_silent(host, caplog):
    host.add('a', boot=True)
    caplog.set_level(logging.INFO, logger='iocage')
    ioc.IOCage(rc=True, silent=True).start(parallel=True)
    assert caplog.records == []


def test_empty_boot_selection_succeeds(host):
    host.add('a', boot=False)
    assert ioc.IOCage(rc=True).start(parallel=True) == {}
    assert host.calls == []


@pytest.mark.parametrize('rc', [False, True])
def test_bulk_start_skips_unsupported_inventory(host, rc, caplog):
    host.add('unsupported', boot=True)
    host.configs['unsupported']['type'] = 'unknown'
    host.add('healthy', boot=True)
    parent = ioc.IOCage(jail=None if rc else 'ALL', rc=rc, silent=rc)
    assert parent.start(parallel=True) == {}
    assert 'Skipping unsupported:' in caplog.text
    assert [name for _, name, _ in host.calls] == ['healthy']
    assert host.running == {'unsupported': False, 'healthy': True}


@pytest.mark.parametrize('selection', ['explicit', 'dependency'])
def test_unsupported_start_request_fails_preflight(host, selection):
    host.add('unsupported')
    host.configs['unsupported']['type'] = 'unknown'
    host.add('healthy', depends='unsupported')
    target = 'unsupported' if selection == 'explicit' else 'ALL'
    with pytest.raises(RuntimeError, match='unknown is not a supported'):
        ioc.IOCage(jail=target).start(parallel=True)
    assert host.calls == []


@pytest.mark.parametrize('action', ['start', 'stop'])
def test_singular_jail_cannot_widen_parallel_selection(host, action):
    host.add('alpha', up=action == 'stop')
    host.add('beta', up=action == 'stop')
    operate = getattr(ioc.IOCage(jail='ALL'), action)

    with pytest.raises(ValueError, match='Use jails='):
        operate(jail='alpha', parallel=True)

    assert host.calls == []


@pytest.mark.parametrize('rc', [False, True])
def test_bulk_start_skips_templates(host, rc):
    host.add('a', boot=True)
    host.add('template', boot=True)
    host.configs['template']['type'] = 'template'
    parent = ioc.IOCage(rc=True) if rc else ioc.IOCage(jail='ALL')
    assert parent.start(parallel=True) == {}
    assert [call[1] for call in host.calls] == ['a']
    assert host.running == {'a': True, 'template': False}


@pytest.mark.parametrize('action', ['start', 'stop'])
@pytest.mark.parametrize('rc', [False, True])
def test_bulk_selection_can_load_a_skipped_inventory(
    host, monkeypatch, action, rc
):
    host.add('a', boot=True, up=action == 'stop')
    parent = ioc.IOCage(jail=None if rc else 'ALL', rc=rc, skip_jails=True)
    del parent.jails  # Match the real constructor's lazy-inventory path.
    list_jail = ioc.IOCage.list
    inventories = []

    def list_host(self, kind, **kwargs):
        if kind == 'uuid':
            inventories.append(kind)
            return {name: name for name in host.configs}

        return list_jail(self, kind, **kwargs)

    monkeypatch.setattr(ioc.IOCage, 'list', list_host)
    assert getattr(parent, action)(parallel=True) == {}
    assert inventories == ['uuid']
    assert len(host.calls) == 1
    assert host.running['a'] == (action == 'start')


@pytest.mark.parametrize('dependency', [False, True])
def test_explicit_template_start_still_fails_preflight(host, dependency):
    host.add('template')
    host.configs['template']['type'] = 'template'
    selected = 'template'

    if dependency:
        host.add('a', depends='template')
        selected = 'a'

    with pytest.raises(RuntimeError, match='convert back to a jail'):
        ioc.IOCage(jail=selected).start(parallel=True)

    assert host.calls == []


@pytest.mark.parametrize('action', ['start', 'stop'])
def test_sequential_named_jail_keeps_existing_path(host, action):
    host.add('a', up=action == 'stop')
    getattr(ioc.IOCage(jail='a'), action)()
    assert len(host.calls) == 1
    assert host.running['a'] == (action == 'start')


def load_cli(action):
    path = pathlib.Path(__file__).parents[2] / 'iocage_cli' / f'{action}.py'
    spec = importlib.util.spec_from_file_location(f'test_cli_{action}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.cli


@pytest.mark.parametrize('action', ['start', 'stop'])
@pytest.mark.parametrize('arguments', [
    ['--jobs', '2', 'a'],
    ['--parallel', '--jobs', '0', 'a'],
    ['--parallel', '--jobs', '-1', 'a'],
    ['--parallel', 'ALL', 'a'],
    ['--parallel', '--rc', 'a']
])
def test_cli_rejects_invalid_combinations(host, action, arguments):
    result = CliRunner().invoke(load_cli(action), arguments)
    assert result.exit_code == 2
    assert host.calls == []


@pytest.mark.parametrize('action', ['start', 'stop'])
def test_cli_parallel_dispatch(host, action):
    host.add('a', up=action == 'stop')
    host.add('b', up=action == 'stop')
    result = CliRunner().invoke(
        load_cli(action), ['--parallel', '--jobs', '1', 'b', 'a']
    )
    assert result.exit_code == 0, result.exception
    assert len(host.calls) == 2
    assert all(state == (action == 'start') for state in host.running.values())
