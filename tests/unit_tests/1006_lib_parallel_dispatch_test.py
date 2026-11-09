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
import threading
import time

import pytest

from click.testing import CliRunner

import iocage_lib.ioc_common as common
import iocage_lib.iocage as ioc


@pytest.fixture
def host(monkeypatch):
    configs = {}
    running = {}
    calls = []
    hooks = {}

    def add(name, priority=10, boot=False, depends='none', up=False,
            nat=False, forwards='none'):
        configs[name] = {
            'priority': str(priority), 'boot': boot, 'depends': depends,
            'type': 'jail', 'release': 'EMPTY', 'nat': nat,
            'nat_forwards': forwards
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

    start.__parse_nat_fwds__ = ioc.ioc_start.IOCStart.__parse_nat_fwds__

    def stop(name, path, force=False, callback=None, silent=False, **kwargs):
        calls.append(('stop', name, {'force': force, **kwargs}))

        if name in hooks:
            hooks[name]()

        common.logit({'level': 'INFO', 'message': 'stopped'},
                     _callback=callback, silent=silent)
        running[name] = False

    monkeypatch.setattr(common, 'INTERACTIVE', False)
    monkeypatch.setattr(ioc.IOCage, '__init__', initialize)
    monkeypatch.setattr(ioc.IOCage, '__check_jail_existence__', resolve)
    monkeypatch.setattr(ioc.IOCage, 'list',
                        lambda self, kind, uuid: (running[uuid], '1'))
    monkeypatch.setattr(ioc.ioc_json, 'IOCJson', Json)
    monkeypatch.setattr(ioc.ioc_start, 'IOCStart', start)
    monkeypatch.setattr(ioc.ioc_stop, 'IOCStop', stop)
    monkeypatch.setattr(common, 'get_jails_with_config', lambda *args: {
        name: {**conf, 'state': 'up'}
        for name, conf in configs.items()
        if running[name] and conf['nat'] and conf['nat_forwards'] != 'none'
    })
    return add, running, calls, hooks, configs


def test_names_are_resolved_deduplicated_and_sorted(host):
    add, running, calls, _, _ = host
    add('alpha', 20)
    add('beta', 10)
    parent = ioc.IOCage()
    assert parent.start(parallel=True, jails=['alpha', 'al', 'beta']) == {}
    assert [call[1] for call in calls] == ['beta', 'alpha']
    assert all(running.values())
    assert parent.jail is None


def test_boot_selection_includes_shared_off_prerequisites_once(host):
    add, running, calls, _, _ = host
    add('a', boot=True, depends='c')
    add('b', boot=True, depends='c')
    add('c', boot=False)
    add('d', boot=False)
    ioc.IOCage(rc=True).start(parallel=True, jobs=1)
    assert [call[1] for call in calls] == ['c', 'a', 'b']
    assert running == {'a': True, 'b': True, 'c': True, 'd': False}


def test_stop_rc_does_not_expand_dependencies(host):
    add, running, calls, _, _ = host
    add('a', boot=True, depends='b', up=True)
    add('b', boot=False, up=True)
    ioc.IOCage(rc=True).stop(parallel=True, force=True)
    assert len(calls) == 1
    assert calls[0][1:] == ('a', {
        'force': True, 'suppress_exception': False
    })
    assert running == {'a': False, 'b': True}


@pytest.mark.parametrize('rc', [False, True])
@pytest.mark.parametrize('parallel', [False, True])
def test_force_reaches_every_bulk_stop(host, rc, parallel):
    add, _, calls, _, _ = host
    add('a', boot=True, up=True)
    add('b', boot=True, up=True)
    ioc.IOCage(jail=None if rc else 'ALL', rc=rc).stop(
        parallel=parallel, force=True
    )
    assert len(calls) == 2
    assert all(call[2]['force'] for call in calls)


def test_parallel_stop_handles_name_containing_all(host):
    add, running, calls, _, _ = host
    add('SMALL', up=True)
    add('other', up=True)
    ioc.IOCage().stop(parallel=True, jails=['SMALL'])
    assert [call[1] for call in calls] == ['SMALL']
    assert running['other']


@pytest.mark.parametrize('action', ['start', 'stop'])
@pytest.mark.parametrize('ignore', [False, True])
def test_failure_reporting_and_ignore(host, action, ignore, caplog):
    add, _, calls, hooks, _ = host
    add('a', 10 if action == 'start' else 20, up=action == 'stop')
    add('b', 20 if action == 'start' else 10, up=action == 'stop')

    def fail():
        raise SystemExit('broken hook')

    hooks['a'] = fail
    operate = getattr(ioc.IOCage(jail='ALL'), action)

    if ignore:
        failures = operate(parallel=True, ignore_exception=True)
        assert failures == {'a': 'broken hook'}
        assert 'a: broken hook' in caplog.text
    else:
        with pytest.raises(RuntimeError, match='a: broken hook'):
            operate(parallel=True)

    expected = ['a', 'b'] if ignore or action == 'stop' else ['a']
    assert [call[1] for call in calls] == expected


@pytest.mark.parametrize('action', ['start', 'stop'])
def test_early_return_is_a_failure(host, monkeypatch, action):
    add, _, _, _, _ = host
    add('a', up=action == 'stop')

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
    add, _, calls, _, _ = host
    add('a', depends={'cycle': 'a', 'missing': 'absent',
                      'priority': 'b'}[problem])

    if problem == 'priority':
        add('b', priority=20)

    with pytest.raises(RuntimeError):
        ioc.IOCage(jail='a').start(parallel=True, ignore_exception=True)

    assert calls == []


def test_callbacks_are_serialized_and_include_identity(host):
    add, _, _, hooks, _ = host
    add('a')
    add('b')
    add('c', priority=20)
    add('d', priority=20)
    barrier = threading.Barrier(2)
    hooks.update({name: lambda: barrier.wait(timeout=5) for name in 'abcd'})
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


def test_cli_worker_errors_keep_their_message_and_identity(
    host, monkeypatch, caplog
):
    add, _, calls, hooks, _ = host
    add('a')
    add('b', priority=20)
    monkeypatch.setattr(common, 'INTERACTIVE', True)

    def fail():
        common.logit({'level': 'EXCEPTION', 'message': 'hook failed'})

    hooks['a'] = fail
    result = CliRunner().invoke(load_cli('start'), ['--parallel', 'ALL'])
    assert result.exit_code == 1
    assert [call[1] for call in calls] == ['a']
    assert 'a: hook failed' in caplog.text
    assert 'a: 1' not in caplog.text
    assert 'a: a:' not in caplog.text


def test_parallel_log_context_does_not_change_sequential_errors(
    host, monkeypatch
):
    add, _, _, _, _ = host
    add('a')
    ioc.IOCage(jail='a').start(parallel=True)
    monkeypatch.setattr(common, 'INTERACTIVE', True)

    with pytest.raises(SystemExit) as error:
        common.logit({'level': 'EXCEPTION', 'message': 'sequential error'})

    assert error.value.code == 1


@pytest.mark.parametrize('conflict', ['pending', 'running', 'supplied'])
def test_nat_conflicts_fail_before_lifecycle(host, conflict):
    add, _, calls, _, _ = host
    add('a', nat=True, forwards='tcp(80:8080)')

    if conflict != 'supplied':
        add('b', nat=True, forwards='tcp(81:8080)', up=conflict == 'running')

    with pytest.raises(RuntimeError, match='NAT forwarding ports conflict'):
        ioc.IOCage(jail='ALL').start(
            parallel=True, used_ports=[8080] if conflict == 'supplied' else None
        )

    assert calls == []


def test_workers_receive_other_ports_and_same_address_reservations(host):
    add, _, calls, _, _ = host
    add('a', nat=True, forwards='tcp(80:8080)')
    add('b', nat=True, forwards='tcp(81:8081)')
    ioc.IOCage(jail='ALL').start(parallel=True)
    arguments = {name: kwargs for _, name, kwargs in calls}
    assert arguments['a']['used_ports'] == {8081}
    assert arguments['b']['used_ports'] == {8080}
    assert arguments['a']['used_ips'] is arguments['b']['used_ips']


def test_pending_static_addresses_are_reserved(host):
    add, _, calls, _, configs = host
    add('a')
    add('b')
    configs['b'].update({
        'ip4_addr': 'lo0|172.16.0.1/32', 'localhost_ip': '127.0.0.2'
    })
    ioc.IOCage(jail='ALL').start(parallel=True)
    assert calls[0][2]['used_ips'] == {'172.16.0.1', '127.0.0.2'}


def test_silent_without_consumer_callback_stays_silent(host, caplog):
    add, _, _, _, _ = host
    add('a', boot=True)
    caplog.set_level(logging.INFO, logger='iocage')
    ioc.IOCage(rc=True, silent=True).start(parallel=True)
    assert caplog.records == []


def test_empty_boot_selection_succeeds(host):
    add, _, calls, _, _ = host
    add('a', boot=False)
    assert ioc.IOCage(rc=True).start(parallel=True) == {}
    assert calls == []


@pytest.mark.parametrize('rc', [False, True])
def test_bulk_start_skips_templates(host, rc):
    add, running, calls, _, configs = host
    add('a', boot=True)
    add('template', boot=True)
    configs['template']['type'] = 'template'
    parent = ioc.IOCage(rc=True) if rc else ioc.IOCage(jail='ALL')
    assert parent.start(parallel=True) == {}
    assert [call[1] for call in calls] == ['a']
    assert running == {'a': True, 'template': False}


@pytest.mark.parametrize('action', ['start', 'stop'])
@pytest.mark.parametrize('rc', [False, True])
def test_bulk_selection_can_load_a_skipped_inventory(
    host, monkeypatch, action, rc
):
    add, running, calls, _, configs = host
    add('a', boot=True, up=action == 'stop')
    parent = ioc.IOCage(jail=None if rc else 'ALL', rc=rc, skip_jails=True)
    del parent.jails  # Match the real constructor's lazy-inventory path.
    list_jail = ioc.IOCage.list
    inventories = []

    def list_host(self, kind, **kwargs):
        if kind == 'uuid':
            inventories.append(kind)
            return {name: name for name in configs}

        return list_jail(self, kind, **kwargs)

    monkeypatch.setattr(ioc.IOCage, 'list', list_host)
    assert getattr(parent, action)(parallel=True) == {}
    assert inventories == ['uuid']
    assert len(calls) == 1
    assert running['a'] == (action == 'start')


@pytest.mark.parametrize('dependency', [False, True])
def test_explicit_template_start_still_fails_preflight(host, dependency):
    add, _, calls, _, configs = host
    add('template')
    configs['template']['type'] = 'template'
    selected = 'template'

    if dependency:
        add('a', depends='template')
        selected = 'a'

    with pytest.raises(RuntimeError, match='convert back to a jail'):
        ioc.IOCage(jail=selected).start(parallel=True)

    assert calls == []


@pytest.mark.parametrize('action', ['start', 'stop'])
def test_sequential_named_jail_keeps_existing_path(host, action):
    add, running, calls, _, _ = host
    add('a', up=action == 'stop')
    getattr(ioc.IOCage(jail='a'), action)()
    assert len(calls) == 1
    assert running['a'] == (action == 'start')


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
    assert host[2] == []


@pytest.mark.parametrize('action', ['start', 'stop'])
def test_cli_parallel_dispatch(host, action):
    add, running, calls, _, _ = host
    add('a', up=action == 'stop')
    add('b', up=action == 'stop')
    result = CliRunner().invoke(
        load_cli(action), ['--parallel', '--jobs', '1', 'b', 'a']
    )
    assert result.exit_code == 0, result.exception
    assert len(calls) == 2
    assert all(state == (action == 'start') for state in running.values())
