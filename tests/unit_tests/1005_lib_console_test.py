"""Console login should prepare its configuration and running state once."""

import subprocess

from pathlib import Path
from unittest import mock

import pytest

from iocage_lib import ioc_common, ioc_exec, ioc_json, iocage
from iocage_lib.ioc_json import IOCJson


@pytest.fixture
def console(tmp_path, monkeypatch):
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', False)
    conf = {'login_flags': '-f root', 'exec_fib': '0', 'type': 'jail'}
    with mock.patch.object(ioc_json, 'IOCJson') as json_class:
        json_class.return_value.pool = 'tank'
        json_class.return_value.iocroot = str(tmp_path)
        json_class.return_value.json_get_value.return_value = conf
        jail = iocage.IOCage(jail='dev', silent=True, skip_jails=True)
        (tmp_path / 'jails' / 'dev').mkdir(parents=True)
        json_class.reset_mock()
        with mock.patch.object(jail, 'get', side_effect=AssertionError), \
                mock.patch.object(jail, 'start') as start, \
                mock.patch.object(iocage.ioc_list.IOCList, '__init__',
                                  side_effect=AssertionError), \
                mock.patch.object(iocage.ioc_list.IOCList, 'list_get_jid',
                                  return_value=(True, '6')) as status, \
                mock.patch.object(ioc_exec.su, 'run') as run, \
                mock.patch.object(ioc_exec.iocage_lib.ioc_start,
                                  'IOCStart') as auto_start:
            yield jail, json_class, conf, status, start, run, auto_start


@pytest.mark.parametrize('name', ['dev', 'dev.example'])
@pytest.mark.parametrize('flags,fib', [('-f root', '0'), ('-f alice', '2')])
def test_running_console_context(console, name, flags, fib):
    jail, json_class, conf, status, start, run, auto_start = console
    jail.jail = name
    path = f'{jail.iocroot}/jails/{name}'
    if name != 'dev':
        Path(path).mkdir()
    conf.update(login_flags=flags, exec_fib=fib)
    with mock.patch.object(jail, 'list', side_effect=AssertionError):
        jail.exec(None, console=True)
    status.assert_called_once_with(name)
    json_class.assert_called_once_with(path)
    json_class.return_value.json_get_value.assert_called_once_with('all')
    command = [
        '/usr/sbin/setfib', fib, 'jexec', '-u', 'root',
        f'ioc-{name.replace(".", "_")}', 'login', '-p', *flags.split()
    ]
    assert run.call_args.args == (command,)
    assert run.call_args.kwargs['check'] is True
    assert run.call_args.kwargs['env']['TERM'] == 'xterm-256color'
    assert run.call_args.kwargs['env']['HOME'] == '/'
    start.assert_not_called()
    auto_start.assert_not_called()


def test_console_uses_inherited_configuration(console):
    jail, json_class, _, _, _, run, _ = console
    # Exercise the real defaults merge, without involving host ZFS state.
    config = json_class.return_value
    config.json_get_value.side_effect = lambda prop: \
        IOCJson.get_full_config(config)
    config.default_config = {'login_flags': '-f root', 'exec_fib': '1'}
    config.json_load.return_value = ({'exec_fib': '3'}, False)
    config.fix_properties.return_value = False
    config.truthy_props = IOCJson.truthy_props
    jail.exec(None, console=True)
    assert run.call_args.args[0] == [
        '/usr/sbin/setfib', '3', 'jexec', '-u', 'root', 'ioc-dev',
        'login', '-p', '-f', 'root'
    ]


@pytest.mark.parametrize('interactive', [False, True])
def test_stopped_console_requires_force(console, monkeypatch, interactive):
    jail, _, _, status, start, run, auto_start = console
    status.return_value = (False, '-')
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', interactive)
    exception = SystemExit if interactive else RuntimeError
    with pytest.raises(exception):
        jail.exec(None, console=True)
    status.assert_called_once()
    start.assert_not_called()
    auto_start.assert_not_called()
    run.assert_not_called()


def test_force_starts_once_and_refreshes_context(console):
    jail, json_class, conf, status, start, run, auto_start = console
    status.side_effect = [(False, '-'), (True, '7')]
    json_class.return_value.json_get_value.side_effect = [
        conf, dict(conf, exec_fib='2', login_flags='-f alice')
    ]
    jail.exec(None, console=True, start_jail=True)
    start.assert_called_once_with()
    assert status.call_count == 2
    assert json_class.call_count == 2
    assert run.call_args.args[0][1] == '2'
    assert run.call_args.args[0][-1] == 'alice'
    auto_start.assert_not_called()


def test_failed_start_does_not_launch_or_restart(console):
    jail, _, _, status, start, run, auto_start = console
    status.return_value = (False, '-')
    with pytest.raises(RuntimeError, match='not running'):
        jail.exec(None, console=True, start_jail=True)
    start.assert_called_once()
    auto_start.assert_not_called()
    run.assert_not_called()


def test_disappearing_jail_is_not_restarted(console):
    jail, _, _, status, start, run, auto_start = console
    run.side_effect = subprocess.CalledProcessError(1, 'jexec')
    with pytest.raises(ioc_exec.iocage_lib.ioc_exceptions.CommandFailed):
        jail.exec(None, console=True)
    status.assert_called_once()
    start.assert_not_called()
    auto_start.assert_not_called()


@pytest.mark.parametrize('error', [
    ioc_exec.iocage_lib.ioc_exceptions.JailCorruptConfiguration,
    ioc_exec.iocage_lib.ioc_exceptions.JailMissingConfiguration
])
def test_configuration_failure_prevents_console(console, error):
    jail, json_class, _, status, _, run, _ = console
    json_class.return_value.json_get_value.side_effect = error('bad config')
    with pytest.raises(error):
        jail.exec(None, console=True)
    status.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize('matches', [[], ['dev'], ['dev', 'dev.two']])
def test_prefix_resolution(console, matches):
    jail, _, _, status, _, run, _ = console
    jail.jail = 'de'
    mapping = {name: f'{jail.iocroot}/jails/{name}' for name in matches}
    with mock.patch.object(jail, 'list', return_value=mapping) as listing:
        if len(matches) == 1:
            jail.exec(None, console=True)
            status.assert_called_once_with('dev')
        else:
            message = 'Multiple jails' if matches else 'not found'
            with pytest.raises(RuntimeError, match=message):
                jail.exec(None, console=True)
            run.assert_not_called()
        listing.assert_called_once_with('uuid', quick=True)


def test_default_prefix_resolution_keeps_existing_listing(console):
    jail, _, _, _, _, _, _ = console
    jail.jail = 'de'
    path = f'{jail.iocroot}/jails/dev'
    with mock.patch.object(jail, 'list', return_value={'dev': path}) as ls:
        assert jail.__check_jail_existence__() == ('dev', path)
    ls.assert_called_once_with('uuid')


def test_console_all_keeps_bulk_execution(console):
    jail, _, _, status, _, run, _ = console
    jail.jail = 'ALL'
    jail._all = True
    jail.jails = {'dev': f'{jail.iocroot}/jails/dev'}
    jail.exec(None, console=True)
    status.assert_called_once_with('dev')
    run.assert_called_once()


def test_executor_defaults_still_resolve_context():
    conf = {'exec_fib': '0', 'type': 'jail'}
    with mock.patch.object(ioc_exec.iocage_lib.ioc_list, 'IOCList') as ls, \
            mock.patch.object(ioc_json, 'IOCJson') as js:
        ls.return_value.list_get_jid.return_value = (True, '6')
        js.return_value.json_get_value.return_value = conf
        executor = ioc_exec.IOCExec(['true'], '/iocage/jails/dev', uuid='dev')
        ls.return_value.list_get_jid.assert_called_once_with('dev')
        js.return_value.json_get_value.assert_called_once_with('all')
        assert executor.cmd[-1] == 'true'
