# Copyright (c) 2026, iocage
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
import contextlib
import json
import logging

from types import SimpleNamespace
from unittest import mock

import pytest

from iocage_lib import ioc_common, ioc_create, ioc_exceptions

MISSING_PACKAGE = b'pkg: No packages matching missing-package\n'


@pytest.fixture
def installer(monkeypatch):
    """Exercise the real installer with jail and process boundaries mocked."""
    create = ioc_create.IOCCreate.__new__(ioc_create.IOCCreate)
    create.pkglist = ['missing-package', 'nano']
    create.plugin = False
    create.silent = False
    create.callback = None
    create.log = logging.getLogger('iocage')
    outcomes = {}
    attempts = {}
    events = []
    logs = []
    jail_list = mock.Mock()
    jail_list.list_get_jid.side_effect = [(False, None), (True, '42')]
    start = mock.Mock(side_effect=lambda *a, **kw: events.append('start'))
    stop = mock.Mock(side_effect=lambda *a, **kw: events.append('stop'))
    logit = ioc_common.logit

    def record_log(content, **kwargs):
        logs.append(content)

        if content['level'] == 'EXCEPTION':
            events.append('failure')

        return logit(content, **kwargs)

    @contextlib.contextmanager
    def execute(command, *args, **kwargs):
        def output():
            if command[1] == 'install':
                package = command[-1]
                attempts[package] = attempts.get(package, 0) + 1
                events.append(package)
                results = outcomes.get(package, [])
                error = results.pop(0) if results else None

                if error is not None:
                    for chunk in error:
                        yield b'', chunk

                    raise ioc_exceptions.CommandFailed(error)

            yield b'', b''

        stream = output()
        try:
            yield stream
        finally:
            # IOCExec drains the command on exit, even if logging fails.
            for _ in stream:
                pass

    monkeypatch.setattr(ioc_common, 'INTERACTIVE', False)
    monkeypatch.setattr(ioc_common, 'logit', record_log)
    monkeypatch.setattr(ioc_create.iocage_lib.ioc_list, 'IOCList',
                        mock.Mock(return_value=jail_list))
    monkeypatch.setattr(ioc_create.iocage_lib.ioc_start, 'IOCStart', start)
    monkeypatch.setattr(ioc_create.iocage_lib.ioc_stop, 'IOCStop', stop)
    monkeypatch.setattr(ioc_create.iocage_lib.ioc_exec, 'IOCExec', execute)
    monkeypatch.setattr(ioc_create.su, 'run', mock.Mock(
        return_value=SimpleNamespace(returncode=0, stdout=b'')
    ))

    return SimpleNamespace(
        create=create, outcomes=outcomes, attempts=attempts, events=events,
        logs=logs, jail_list=jail_list, start=start, stop=stop
    )


def install(installer):
    return installer.create.create_install_packages(
        'test-jail', '/mock/jail', repo=''
    )


@pytest.mark.parametrize('interactive', [False, True])
@pytest.mark.parametrize('silent', [False, True])
@pytest.mark.parametrize('with_callback', [False, True])
def test_permanent_failure_after_cleanup(
    installer, monkeypatch, caplog, interactive, silent, with_callback
):
    installer.outcomes['missing-package'] = [[MISSING_PACKAGE, b'']] * 3
    installer.create.silent = silent
    installer.create.callback = mock.Mock() if with_callback else None
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', interactive)
    exception = SystemExit if interactive else RuntimeError

    with pytest.raises(exception) as error:
        install(installer)

    message = installer.logs[-1]['message']
    assert 'missing-package :pkg: No packages matching' in message
    assert 'nano' not in message
    assert installer.attempts == {'missing-package': 3, 'nano': 1}
    assert installer.events[-2:] == ['stop', 'failure']

    if interactive:
        assert error.value.code == 1
        assert message in caplog.text
    else:
        assert str(error.value) == message


@pytest.mark.parametrize('chunks, expected', [
    ([MISSING_PACKAGE, b'', b'\n'], MISSING_PACKAGE.decode().strip()),
    ([], 'pkg failed without an error message'),
    ([b'', b''], 'pkg failed without an error message'),
    ([b' \n', b'\t'], 'pkg failed without an error message'),
    ([b'caf\xc3', b'\xa9\n', b''], 'café'),
    ([b'bad byte: \xff\n'], 'bad byte: \ufffd'),
    ([b'repository error\n', MISSING_PACKAGE],
     'repository error\n' + MISSING_PACKAGE.decode().strip()),
])
def test_failure_diagnostics(installer, chunks, expected):
    installer.outcomes['missing-package'] = [chunks] * 3

    with pytest.raises(RuntimeError) as error:
        install(installer)

    assert str(error.value) == '\npkg error:\n  - missing-package :' + expected


def test_multiple_permanent_failures(installer):
    installer.create.pkglist = ['missing-package', 'other-package', 'nano']
    installer.outcomes['missing-package'] = [[MISSING_PACKAGE]] * 3
    installer.outcomes['other-package'] = [[b'repository unavailable\n']] * 3

    with pytest.raises(RuntimeError) as error:
        install(installer)

    assert str(error.value) == (
        '\npkg error:\n  - missing-package :'
        'pkg: No packages matching missing-package\n'
        '  - other-package :repository unavailable'
    )
    assert installer.attempts['other-package'] == 3
    assert installer.attempts['nano'] == 1


@pytest.mark.parametrize('failures', [0, 1, 2])
@pytest.mark.parametrize('running', [False, True])
def test_successful_retry_and_jail_state(installer, failures, running):
    installer.outcomes['missing-package'] = [[MISSING_PACKAGE]] * failures

    if running:
        installer.jail_list.list_get_jid.side_effect = [(True, '42')]

    assert install(installer) is None
    assert installer.attempts == {'missing-package': failures + 1, 'nano': 1}
    assert 'failure' not in installer.events
    assert installer.start.call_count == int(not running)
    assert installer.stop.call_count == int(not running)


def test_failure_preserves_running_jail(installer):
    installer.outcomes['missing-package'] = [[MISSING_PACKAGE]] * 3
    installer.jail_list.list_get_jid.side_effect = [(True, '42')]

    with pytest.raises(RuntimeError):
        install(installer)

    installer.start.assert_not_called()
    installer.stop.assert_not_called()


@pytest.mark.parametrize('interactive', [False, True])
def test_cleanup_failure_preserves_package_diagnostic(
    installer, monkeypatch, caplog, interactive
):
    installer.outcomes['missing-package'] = [[MISSING_PACKAGE]] * 3
    installer.create.silent = True
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', interactive)

    def fail_stop(*args, **kwargs):
        ioc_common.logit({
            'level': 'EXCEPTION', 'message': 'Unable to stop test-jail'
        })

    installer.stop.side_effect = fail_stop

    with pytest.raises(SystemExit if interactive else RuntimeError) as error:
        install(installer)

    assert 'pkg error:' in caplog.text
    assert MISSING_PACKAGE.decode().strip() in caplog.text
    assert installer.attempts == {'missing-package': 3, 'nano': 1}

    if interactive:
        assert error.value.code == 1
    else:
        assert str(error.value) == 'Unable to stop test-jail'


@pytest.mark.parametrize('failures', [0, 1, 2, 3])
def test_plugin_return_contract(installer, failures):
    installer.create.plugin = True
    installer.create.silent = True
    installer.outcomes['missing-package'] = [[MISSING_PACKAGE, b'']] * failures

    result = install(installer)

    if failures == 3:
        assert result == 'missing-package :' + MISSING_PACKAGE.decode().strip()
    else:
        assert result is None

    assert 'failure' not in installer.events
    installer.stop.assert_called_once()
    assert installer.attempts['nano'] == 1


def test_package_list_from_json(installer, tmp_path):
    pkglist = tmp_path / 'pkgs.json'
    pkglist.write_text(json.dumps({'pkgs': installer.create.pkglist}))
    installer.create.pkglist = str(pkglist)
    installer.outcomes['missing-package'] = [[MISSING_PACKAGE]] * 3

    with pytest.raises(RuntimeError, match='missing-package'):
        install(installer)

    assert installer.attempts == {'missing-package': 3, 'nano': 1}


@pytest.mark.parametrize('readonly', ['on', 'off'])
@pytest.mark.parametrize('failure, plugin', [
    (None, False), ('package', False), ('bootstrap', False),
    ('stop', False), ('start', False), ('package', True)
])
def test_template_installer_restores_readonly(
    installer, monkeypatch, readonly, failure, plugin
):
    installer.create.plugin = plugin
    dataset = mock.Mock(properties={'readonly': readonly})
    dataset.set_property.side_effect = dataset.properties.__setitem__
    monkeypatch.setattr(ioc_create, 'Dataset',
                        mock.Mock(return_value=dataset))

    def start(*args, **kwargs):
        # Saving startup configuration makes templates readonly.
        dataset.properties['readonly'] = 'on'
        if failure == 'start':
            raise RuntimeError('Unable to start test-jail')

    installer.start.side_effect = start

    if failure == 'package':
        installer.outcomes['missing-package'] = [[MISSING_PACKAGE]] * 3
    elif failure == 'bootstrap':
        ioc_create.su.run.return_value = SimpleNamespace(
            returncode=1, stdout=b'pkg bootstrap failed'
        )
    elif failure == 'stop':
        installer.stop.side_effect = RuntimeError('Unable to stop test-jail')

    if failure and not plugin:
        with pytest.raises(RuntimeError):
            installer.create.create_install_packages(
                'test-jail', '/mock/templates/test-jail', repo=''
            )
    else:
        result = installer.create.create_install_packages(
            'test-jail', '/mock/templates/test-jail', repo=''
        )
        if plugin:
            assert 'missing-package' in result
        else:
            assert result is None

    assert dataset.properties['readonly'] == readonly


@pytest.fixture
def creation(installer, monkeypatch, tmp_path):
    """Use real creation control flow with temporary files and mocked ZFS."""
    create = installer.create
    create.pool = 'test-pool'
    create.iocroot = str(tmp_path)
    create.release = '15.1-RELEASE'
    create.migrate = True
    create.template = create.clone = create.empty = create.basejail = False
    create.thickjail = create.thickconfig = False
    create.props = ['boot=on']
    create.config = {
        'ip4_addr': 'lo1|192.0.2.1', 'ip6_addr': 'none',
        'host_hostname': 'test-jail', 'type': 'jail'
    }
    create.create_rc = mock.Mock()

    # Avoid DNS probes while exercising the actual package installer.
    install_packages = create.create_install_packages
    monkeypatch.setattr(
        create, 'create_install_packages',
        lambda uuid, path: install_packages(uuid, path, repo='')
    )
    hosts = tmp_path / 'releases' / create.release / 'root/etc/hosts'
    hosts.parent.mkdir(parents=True)
    hosts.write_text('127.0.0.1 localhost\n')

    for kind in ('jails', 'templates'):
        (tmp_path / kind / 'test-jail/root/etc').mkdir(parents=True)

    jail_json = mock.Mock()
    jail_json.json_check_prop.side_effect = lambda k, v, conf: (v, conf)
    monkeypatch.setattr(ioc_create.iocage_lib.ioc_json, 'IOCJson',
                        mock.Mock(return_value=jail_json))
    monkeypatch.setattr(
        ioc_common, 'match_to_dir', mock.Mock(return_value=None)
    )
    monkeypatch.setattr(ioc_create.su, 'check_call', mock.Mock())
    monkeypatch.setattr(ioc_create.su, 'Popen', mock.Mock())
    dataset = mock.Mock(properties={'readonly': 'off'})
    dataset.set_property.side_effect = (
        lambda key, value: installer.events.append(f'{key}:{value}')
    )
    monkeypatch.setattr(ioc_create, 'Dataset', mock.Mock(return_value=dataset))
    destroy = mock.Mock()
    monkeypatch.setattr(
        ioc_create.iocage_lib.ioc_destroy, 'IOCDestroy', destroy
    )

    return SimpleNamespace(
        installer=installer, path=str(tmp_path / 'jails/test-jail'),
        dataset=dataset, destroy=destroy
    )


@pytest.mark.parametrize('template', [False, True])
@pytest.mark.parametrize('interactive', [False, True])
def test_creation_failure_restores_state(
    creation, monkeypatch, template, interactive
):
    installer = creation.installer
    installer.outcomes['missing-package'] = [[MISSING_PACKAGE]] * 3
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', interactive)

    if template:
        installer.create.props.append('template=yes')

    with pytest.raises(SystemExit if interactive else RuntimeError):
        installer.create._create_jail('test-jail', creation.path)

    installer.start.assert_called_once()
    creation.destroy.assert_not_called()
    assert not any('successfully' in log['message'] for log in installer.logs)

    if template:
        assert installer.events[-4:] == [
            'stop', 'failure', 'readonly:off', 'readonly:on'
        ]
        assert creation.dataset.set_property.call_args == mock.call(
            'readonly', 'on'
        )
    else:
        creation.dataset.set_property.assert_not_called()


@pytest.mark.parametrize('template', [False, True])
def test_creation_success_is_reported_after_setup(creation, template):
    installer = creation.installer

    if template:
        installer.create.props.append('template=yes')

    assert installer.create._create_jail('test-jail', creation.path) == (
        'test-jail'
    )
    assert installer.logs[-1]['message'] == 'test-jail successfully created!'
    assert installer.events[-1] == ('readonly:on' if template else 'start')
    assert installer.start.call_count == (1 if template else 2)


@pytest.mark.parametrize('template', [False, True])
@pytest.mark.parametrize('skip', ['no_packages', 'no_network'])
def test_creation_skips_package_installation(creation, template, skip):
    installer = creation.installer

    if template:
        installer.create.props.append('template=yes')

    if skip == 'no_packages':
        installer.create.pkglist = None
    else:
        installer.create.config['ip4_addr'] = 'none'

    assert installer.create._create_jail('test-jail', creation.path) == (
        'test-jail'
    )
    assert not installer.attempts
    installer.stop.assert_not_called()
    assert installer.start.call_count == (0 if template else 1)
    assert installer.logs[-1]['message'] == 'test-jail successfully created!'

    if template:
        assert installer.events[-1] == 'readonly:on'


@pytest.mark.parametrize('interactive', [False, True])
def test_creation_does_not_report_success_when_boot_start_fails(
    creation, monkeypatch, interactive
):
    installer = creation.installer
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', interactive)

    def start(*args, **kwargs):
        if installer.start.call_count == 2:
            ioc_common.logit({
                'level': 'EXCEPTION', 'message': 'Unable to start test-jail'
            })

    installer.start.side_effect = start
    with pytest.raises(SystemExit if interactive else RuntimeError):
        installer.create._create_jail('test-jail', creation.path)

    assert installer.attempts == {'missing-package': 1, 'nano': 1}
    installer.stop.assert_called_once()
    creation.destroy.assert_not_called()
    assert not any('successfully' in log['message'] for log in installer.logs)
