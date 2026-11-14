"""Only explicit creation calls gain fatal requested-package errors."""

from types import SimpleNamespace
from unittest import mock

import pytest

from iocage_lib import ioc_common, ioc_create, ioc_plugin


@pytest.mark.parametrize('interactive', [False, True])
def test_creation_opt_in_reports_failure_after_cleanup(
    package_install, monkeypatch, interactive
):
    install = package_install
    install.outcomes['missing-package'] = [[b'missing package\n']] * 3
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', interactive)
    with pytest.raises(SystemExit if interactive else RuntimeError) as error:
        install.create.create_install_packages(
            'test', '/mock/jails/test', repo='', raise_on_error=True)
    assert install.state.running is False
    assert install.attempts == {'upgrade': 1, 'missing-package': 3, 'nano': 1}
    assert 'missing-package :missing package' in install.logs[-1]['message']
    if interactive:
        assert error.value.code == 1


@pytest.mark.parametrize('plugin', [False, True])
def test_existing_callers_keep_their_return_contract(package_install, plugin):
    install = package_install
    install.create.plugin = plugin
    install.outcomes['missing-package'] = [[b'missing package\n']] * 3
    result = install.create.create_install_packages(
        'test', '/mock/jails/test', repo='')
    assert result == ('missing-package :missing package' if plugin else None)
    assert install.state.running is False


@pytest.mark.parametrize('chunks, expected', [
    ([], 'pkg failed without an error message'),
    ([b'bad\xff\n'], 'bad\ufffd'),
    ([b'first\n', b'second\n'], 'first\nsecond'),
])
def test_all_diagnostics_are_retained(package_install, chunks, expected):
    install = package_install
    install.outcomes['missing-package'] = [chunks] * 3
    with pytest.raises(RuntimeError) as error:
        install.create.create_install_packages(
            'test', '/mock/jails/test', repo='', raise_on_error=True)
    assert expected in str(error.value)


@pytest.mark.parametrize('interactive', [False, True])
@pytest.mark.parametrize('cleanup_exception', [RuntimeError, SystemExit])
def test_cleanup_failure_preserves_package_diagnostics(
    package_install, monkeypatch, interactive, cleanup_exception
):
    install = package_install
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', interactive)
    install.outcomes['missing-package'] = [
        [b'first line\n', b'second line']
    ] * 3
    install.outcomes['nano'] = [[b'another failure']] * 3
    cleanup_error = cleanup_exception(
        1 if cleanup_exception is SystemExit else 'cleanup failed')
    install.stop.side_effect = cleanup_error

    with pytest.raises(cleanup_exception) as error:
        install.create.create_install_packages(
            'test', '/mock/jails/test', repo='', raise_on_error=True)

    assert error.value is cleanup_error
    assert install.logs[-1] == {
        'level': 'ERROR',
        'message': ('missing-package :first line\nsecond line\n'
                    'nano :another failure')
    }


@pytest.mark.parametrize('stage', ['bootstrap', 'upgrade'])
def test_cli_fatal_diagnostic_survives_cleanup_failure(
    package_install, monkeypatch, caplog, stage
):
    install = package_install
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', True)
    message = f'{stage} failed before package installation'
    if stage == 'bootstrap':
        install.bootstrap.return_value = SimpleNamespace(
            returncode=1, stdout=message.encode())
    else:
        install.outcomes['upgrade'] = [[message.encode()]]
    install.stop.side_effect = RuntimeError('cleanup failed')

    with pytest.raises(RuntimeError, match='cleanup failed'):
        install.create.create_install_packages(
            'test', '/mock/jails/test', repo='', raise_on_error=True)

    assert message in caplog.text
    install.stop.assert_called_once()


def test_secure_plugin_bootstrap_keeps_existing_behavior(
    package_install, tmp_path
):
    install = package_install
    install.outcomes['ca_root_nss'] = [[b'certificate package missing']] * 3
    plugin = ioc_plugin.IOCPlugin.__new__(ioc_plugin.IOCPlugin)
    plugin.release, plugin.jail = '15.1-RELEASE', 'test'
    plugin.callback, plugin.silent = None, True
    plugin.log = install.create.log
    original = ioc_create.IOCCreate
    callers = []

    def create(*args, **kwargs):
        instance = original.__new__(original)
        instance.__dict__.update(install.create.__dict__)
        instance.pkglist = kwargs['pkglist']
        instance.plugin = kwargs.get('plugin', False)
        callers.append(instance.plugin)
        return instance

    conf = {'packagesite': 'https://example.invalid', 'pkgs': []}
    with mock.patch.object(ioc_create, 'IOCCreate', side_effect=create), \
            mock.patch('dns.resolver.query'), \
            mock.patch.object(ioc_create.iocage_lib.ioc_exec, 'SilentExec'):
        plugin.__fetch_plugin_install_packages__(
            str(tmp_path), conf, {}, [],
            str(tmp_path / 'root/usr/local/etc/pkg/repos'))
    assert callers == [False, True]
    assert install.attempts['ca_root_nss'] == 3


@pytest.fixture
def creation(package_install, monkeypatch, tmp_path):
    """Use real creation control flow with temporary files and mocked ZFS."""
    installer = package_install
    installer.events = []
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
        lambda uuid, path, **kw: install_packages(uuid, path, repo='', **kw)
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
@pytest.mark.parametrize('failure', [False, True])
def test_creation_reports_success_only_after_setup(creation, template, failure):
    install = creation.installer
    if template:
        install.create.props.append('template=yes')
    if failure:
        install.outcomes['missing-package'] = [[b'missing package']] * 3
        with pytest.raises(RuntimeError):
            install.create._create_jail('test-jail', creation.path)
        assert not any('successfully' in item['message']
                       for item in install.logs)
        creation.destroy.assert_not_called()
        assert install.state.running is False
    else:
        assert install.create._create_jail(
            'test-jail', creation.path) == 'test-jail'
        assert install.logs[-1]['message'] == 'test-jail successfully created!'
    if template:
        assert creation.dataset.set_property.call_args == mock.call(
            'readonly', 'on')
