import importlib.util
import io
import tempfile

from pathlib import Path
from unittest import mock

import pytest

from iocage_lib import ioc_common
from iocage_lib.ioc_common import get_jail_freebsd_version
from iocage_lib.ioc_create import IOCCreate
from iocage_lib.ioc_fetch import IOCFetch
from iocage_lib.ioc_json import IOCJson
from iocage_lib.ioc_plugin import IOCPlugin
from iocage_lib.ioc_upgrade import IOCUpgrade


@pytest.mark.parametrize('release', ['14.4', '14.5', '15.1'])
def test_configuration_reads_patch_level_and_preserves_fields(
    tmp_path, release
):
    root = tmp_path / 'jails' / 'test' / 'root'
    (root / 'bin').mkdir(parents=True)
    patched = f'{release}-RELEASE-p4'
    (root / 'bin' / 'freebsd-version').write_text(
        f'#!/bin/sh\nUSERLAND_VERSION="{patched}"\n'
    )
    original = f'{release}-RELEASE-p1'
    conf = {
        'host_hostuuid': 'test', 'release': original,
        'ip4_addr': 'em0|192.0.2.1/24', 'allow_mount_tmpfs': 1,
    }
    configuration = IOCJson.__new__(IOCJson)
    configuration.iocroot = str(tmp_path)
    configuration.location = str(root.parent)
    configuration.json_write = mock.Mock()

    result = configuration.check_jail_config(conf.copy())

    assert result == {**conf, 'release': patched, 'cloned_release': original}
    configuration.json_write.assert_called_once_with(result)


def test_version_reader_reports_missing_userland_version(tmp_path):
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin' / 'freebsd-version').write_text('#!/bin/sh\n')
    with pytest.raises(ValueError, match='USERLAND_VERSION not found'):
        get_jail_freebsd_version(str(tmp_path), '15.1-RELEASE')


def test_empty_jail_keeps_empty_release(tmp_path):
    root = tmp_path / 'jails' / 'empty' / 'root'
    (root / 'bin').mkdir(parents=True)
    (root / 'bin' / 'freebsd-version').touch()
    configuration = IOCJson.__new__(IOCJson)
    configuration.iocroot = str(tmp_path)
    configuration.location = str(root.parent)
    configuration.json_write = mock.Mock()
    conf = {'host_hostuuid': 'empty', 'release': 'EMPTY'}

    with mock.patch('iocage_lib.ioc_common.get_jail_freebsd_version') as read:
        result = configuration.check_jail_config(conf)

    read.assert_not_called()
    assert result['release'] == 'EMPTY'


def test_unmounted_basejail_reads_release_from_fstab(tmp_path):
    source = tmp_path / 'releases' / '14.4-RELEASE' / 'root'
    (source / 'bin').mkdir(parents=True)
    (source / 'bin' / 'freebsd-version').write_text(
        'USERLAND_VERSION="14.4-RELEASE-p4"\n'
    )
    root = tmp_path / 'jails' / 'base' / 'root'
    configuration = IOCJson.__new__(IOCJson)
    configuration.iocroot = str(tmp_path)
    configuration.location = str(root.parent)
    configuration.json_write = mock.Mock()
    entry = [str(source / 'bin'), str(root / 'bin'), 'nullfs', 'ro', '0', '0']
    with mock.patch('iocage_lib.ioc_fstab.IOCFstab') as fstab:
        fstab.return_value.fstab = []
        fstab.return_value.__validate_fstab__ = mock.Mock()
        fstab.return_value.fstab_list.return_value = [(0, entry)]
        result = configuration.check_jail_config({
            'host_hostuuid': 'base', 'release': '14.4-RELEASE', 'basejail': 1,
        })

    assert result['release'] == '14.4-RELEASE-p4'
    assert result['cloned_release'] == '14.4-RELEASE'


@pytest.mark.parametrize('release', ['14.4', '14.5', '15.1'])
@pytest.mark.parametrize('missing', [False, True])
def test_plugin_reads_patch_level_and_fetches_missing_release(
    tmp_path, release, missing
):
    plugin = IOCPlugin.__new__(IOCPlugin)
    plugin.iocroot = str(tmp_path)
    plugin.callback = None
    plugin.silent = True
    plugin.plugin = 'example'
    plugin.git_repository = 'https://example.com/plugins'
    root = tmp_path / 'releases' / f'{release}-RELEASE' / 'root'
    patched = f'{release}-RELEASE-p4'

    def write_release(*args):
        (root / 'bin').mkdir(parents=True)
        (root / 'bin' / 'freebsd-version').write_text(
            f'USERLAND_VERSION="{patched}"\n'
        )

    if not missing:
        write_release()
    plugin.__fetch_release__ = mock.Mock(side_effect=write_release)
    fingerprints = {'example': [{'function': 'sha256', 'fingerprint': 'abc'}]}
    conf = {
        'release': f'{release}-RELEASE', 'fingerprints': fingerprints,
        'properties': {},
    }

    with mock.patch('iocage_lib.ioc_common.check_release_newer') as check:
        properties, repos = plugin.__fetch_plugin_props__(
            conf, ['nat=1', 'boot=0'], 0
        )

    assert f'release={patched}' in properties
    assert 'boot=0' in properties
    assert 'nat=1' in properties
    assert 'type=pluginv2' in properties
    assert repos == fingerprints
    check.assert_called_once_with(
        f'{release}-RELEASE', None, True, major_only=True
    )
    if missing:
        plugin.__fetch_release__.assert_called_once_with(f'{release}-RELEASE')
    else:
        plugin.__fetch_release__.assert_not_called()


@pytest.fixture(params=[False, True], ids=['library', 'cli'])
def version_error(request, monkeypatch, caplog):
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', request.param)
    caplog.set_level('ERROR', logger='iocage')
    return SystemExit if request.param else RuntimeError


@pytest.fixture
def release_root(tmp_path):
    root = tmp_path / 'releases' / '15.1-RELEASE' / 'root'
    (root / 'bin').mkdir(parents=True)
    return root


def assert_version_error(error, caplog, root):
    if isinstance(error, SystemExit):
        assert error.code == 1
        message = caplog.text
    else:
        assert isinstance(error, RuntimeError)
        message = str(error)
    expected = f'USERLAND_VERSION not found in {root}/bin/freebsd-version'
    assert expected in message


@pytest.mark.parametrize('missing', [False, True])
def test_create_reports_version_errors(
    tmp_path, release_root, version_error, caplog, missing
):
    if not missing:
        (release_root / 'bin/freebsd-version').write_text('#!/bin/sh\n')
    # Bypass constructors so the tests never query or alter a ZFS pool.
    create = IOCCreate.__new__(IOCCreate)
    create.iocroot = str(tmp_path)
    create.release = '15.1-RELEASE'
    create.migrate = create.template = create.clone = create.empty = False
    create.callback = None
    create.silent = True
    create.uuid = 'test'
    create.short = False
    create.create_config = mock.Mock()

    with mock.patch.object(ioc_common, 'match_to_dir', return_value=None), \
            mock.patch('iocage_lib.ioc_create.Dataset') as dataset:
        with pytest.raises(version_error) as caught:
            create.create_jail()

    if missing:
        message = caplog.text if version_error is SystemExit else str(
            caught.value
        )
        assert 'RELEASE: 15.1-RELEASE not found!' in message
    else:
        assert_version_error(caught.value, caplog, release_root)
    create.create_config.assert_not_called()
    dataset.assert_not_called()


@pytest.fixture
def fetch_plugin(tmp_path, monkeypatch):
    plugin = IOCPlugin.__new__(IOCPlugin)
    plugin.iocroot = str(tmp_path)
    plugin.callback = None
    plugin.silent = True
    plugin.plugin = 'example'
    plugin.jail = 'test'
    plugin.hardened = False
    plugin.keep_jail_on_failure = True
    plugin.git_repository = 'https://example.com/plugins'
    plugin.fetch_plugin_index = mock.Mock(return_value={})
    plugin.retrieve_plugin_json = mock.Mock(return_value={
        'release': '15.1-RELEASE', 'fingerprints': {}, 'properties': {},
    })
    plugin.__fetch_release__ = mock.Mock()
    plugin.__fetch_plugin_inform__ = mock.Mock()
    plugin.__fetch_plugin_create__ = mock.Mock()
    plugin.__fetch_plugin_install_packages__ = mock.Mock()
    plugin.__fetch_plugin_post_install__ = mock.Mock()
    monkeypatch.setattr(ioc_common, 'check_release_newer', mock.Mock())
    monkeypatch.setattr(ioc_common, 'validate_plugin_manifest', mock.Mock())
    return plugin


@pytest.mark.parametrize('after_fetch', [False, True])
def test_plugin_fetch_reports_version_errors(
    fetch_plugin, release_root, version_error, caplog, after_fetch
):
    def malformed_release(*args):
        (release_root / 'bin/freebsd-version').write_text('#!/bin/sh\n')

    if after_fetch:
        fetch_plugin.__fetch_release__.side_effect = malformed_release
    else:
        malformed_release()

    with mock.patch('iocage_lib.ioc_destroy.IOCDestroy') as destroy:
        with pytest.raises(version_error) as caught:
            fetch_plugin.fetch_plugin(['nat=1'], 0, False)

    assert_version_error(caught.value, caplog, release_root)
    assert fetch_plugin.__fetch_release__.call_count == int(after_fetch)
    fetch_plugin.__fetch_plugin_inform__.assert_not_called()
    fetch_plugin.__fetch_plugin_create__.assert_not_called()
    fetch_plugin.__fetch_plugin_install_packages__.assert_not_called()
    destroy.assert_not_called()


def test_plugin_fetch_recovers_missing_version_file(
    fetch_plugin, release_root, tmp_path
):
    def fetched_release(*args):
        (release_root / 'bin/freebsd-version').write_text(
            'USERLAND_VERSION="15.1-RELEASE-p4"\n'
        )

    fetch_plugin.__fetch_release__.side_effect = fetched_release
    jaildir = tmp_path / 'jails/test'
    jaildir.mkdir(parents=True)
    fetch_plugin.__fetch_plugin_create__.return_value = (str(jaildir), {}, '')
    with mock.patch('iocage_lib.ioc_list.IOCList') as listing:
        listing.return_value.list_get_jid.return_value = (False, None)
        fetch_plugin.fetch_plugin(['nat=1'], 0, False)

    fetch_plugin.__fetch_release__.assert_called_once_with('15.1-RELEASE')
    properties = fetch_plugin.__fetch_plugin_create__.call_args.args[0]
    assert 'release=15.1-RELEASE-p4' in properties
    fetch_plugin.__fetch_plugin_install_packages__.assert_called_once()
    fetch_plugin.__fetch_plugin_post_install__.assert_called_once()


def test_custom_release_reports_version_error(
    tmp_path, release_root, version_error, caplog
):
    # Load the command without the CLI package's process-wide initialization.
    path = Path(__file__).resolve().parents[2] / 'iocage_cli/create.py'
    spec = importlib.util.spec_from_file_location('create_command', path)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    (release_root / 'bin/freebsd-version').write_text('#!/bin/sh\n')

    with mock.patch.object(command.ioc, 'PoolAndDataset') as pool, \
            mock.patch.object(command.ioc, 'IOCage') as iocage, \
            mock.patch.object(ioc_common, 'check_release_newer',
                              side_effect=ValueError('Custom release')):
        pool.return_value.get_iocroot.return_value = str(tmp_path)
        with pytest.raises(version_error) as caught:
            command.cli.callback(
                release='15.1-RELEASE', template=None, count=1, props=(),
                pkglist=None, basejail=False, clone_basejail=False,
                thickjail=False, empty=False, short=False, name='test',
                _uuid=None, thickconfig=False, proxy=None,
            )

    assert_version_error(caught.value, caplog, release_root)
    iocage.assert_not_called()


def test_upgrade_reports_version_error(
    tmp_path, release_root, version_error, caplog
):
    (release_root / 'bin/freebsd-version').write_text('#!/bin/sh\n')
    (release_root / 'etc').mkdir()
    (release_root / 'etc/freebsd-update.conf').touch()
    upgrade = IOCUpgrade.__new__(IOCUpgrade)
    upgrade.iocroot = str(tmp_path)
    upgrade.path = str(release_root)
    upgrade.freebsd_version = upgrade.new_release = '15.1-RELEASE'
    upgrade.jail_release = '14.4-RELEASE'
    upgrade.uuid = 'test'
    upgrade.callback = None
    upgrade.silent = True
    upgrade.interactive = False
    link = tmp_path / 'update-install'
    link.symlink_to('pending-install')
    upgrade.freebsd_install_link = str(link)
    upgrade.__upgrade_check_conf__ = mock.Mock()
    upgrade.__upgrade_install__ = mock.Mock(side_effect=link.unlink)
    script = tempfile.NamedTemporaryFile(dir=tmp_path, delete=False)

    with mock.patch.object(ioc_common, 'tmp_dataset_checks'), \
            mock.patch.object(ioc_common, 'consume_and_log'), \
            mock.patch('iocage_lib.ioc_exec.IOCExec'), \
            mock.patch('iocage_lib.ioc_upgrade.urllib.request.urlopen',
                       return_value=io.BytesIO(b'#!/bin/sh\n')), \
            mock.patch('iocage_lib.ioc_upgrade.tempfile.NamedTemporaryFile',
                       return_value=script), \
            mock.patch('iocage_lib.ioc_json.IOCJson') as configuration:
        with pytest.raises(version_error) as caught:
            upgrade.upgrade_jail()

    assert_version_error(caught.value, caplog, release_root)
    assert script.closed
    assert not Path(script.name).exists()
    upgrade.__upgrade_install__.assert_called_once_with(script.name)
    configuration.assert_not_called()


def test_basejail_reports_version_error_before_changes(
    tmp_path, release_root, version_error, caplog
):
    (release_root / 'bin/freebsd-version').write_text('#!/bin/sh\n')
    upgrade = IOCUpgrade.__new__(IOCUpgrade)
    upgrade.iocroot = str(tmp_path)
    upgrade.path = str(tmp_path / 'jails/test/root')
    upgrade.freebsd_version = upgrade.new_release = '15.1-RELEASE'
    upgrade.callback = None
    upgrade.silent = True
    upgrade.__snapshot_jail__ = mock.Mock()
    upgrade.__upgrade_replace_basejail_paths__ = mock.Mock()

    with mock.patch('iocage_lib.ioc_exec.SilentExec') as execute, \
            mock.patch('iocage_lib.ioc_json.IOCJson') as configuration:
        with pytest.raises(version_error) as caught:
            upgrade.upgrade_basejail()

    assert_version_error(caught.value, caplog, release_root)
    upgrade.__snapshot_jail__.assert_not_called()
    upgrade.__upgrade_replace_basejail_paths__.assert_not_called()
    execute.assert_not_called()
    configuration.assert_not_called()


def test_basejail_upgrade_keeps_detected_patch_level(tmp_path, release_root):
    (release_root / 'bin/freebsd-version').write_text(
        'USERLAND_VERSION="15.1-RELEASE-p4"\n'
    )
    source = release_root / 'usr/src'
    source.mkdir(parents=True)
    (source / 'Makefile').touch()
    upgrade = IOCUpgrade.__new__(IOCUpgrade)
    upgrade.iocroot = str(tmp_path)
    upgrade.path = str(tmp_path / 'jails/test/root')
    upgrade.freebsd_version = upgrade.new_release = '15.1-RELEASE'
    upgrade.uuid = 'test'
    upgrade.callback = None
    upgrade.silent = True
    upgrade.__snapshot_jail__ = mock.Mock()
    upgrade.__upgrade_replace_basejail_paths__ = mock.Mock()

    with mock.patch('iocage_lib.ioc_exec.SilentExec') as execute, \
            mock.patch('iocage_lib.ioc_json.IOCJson') as configuration:
        assert upgrade.upgrade_basejail() == '15.1-RELEASE-p4'

    configuration.return_value.json_set_value.assert_called_once_with(
        'release=15.1-RELEASE-p4'
    )
    assert execute.call_args.args[0] == [
        'umount', '-f', f'{upgrade.path}/iocage_upgrade'
    ]


@pytest.mark.parametrize('verify', [False, True])
@pytest.mark.parametrize('malformed', [False, True])
def test_update_validates_version_and_cleans_up(
    tmp_path, release_root, version_error, caplog, verify, malformed
):
    (release_root / 'bin/freebsd-version').write_text(
        '#!/bin/sh\n' if malformed else 'USERLAND_VERSION="15.1-RELEASE-p4"\n'
    )
    (release_root / 'etc').mkdir()
    (release_root / 'etc/freebsd-update.conf').write_text(
        'ServerName example\n'
    )
    fetch = IOCFetch.__new__(IOCFetch)
    fetch.iocroot = str(tmp_path)
    fetch.release = '15.1-RELEASE'
    fetch.callback = None
    fetch.silent = True
    fetch.verify = verify
    script = tempfile.NamedTemporaryFile(dir=tmp_path, delete=False)

    with mock.patch.object(ioc_common, 'tmp_dataset_checks'), \
            mock.patch.object(ioc_common, 'consume_and_log'), \
            mock.patch('iocage_lib.ioc_exec.IOCExec'), \
            mock.patch('iocage_lib.ioc_fetch.su.Popen') as process, \
            mock.patch('iocage_lib.ioc_fetch.urllib.request.urlopen',
                       return_value=io.BytesIO(b'#!/bin/sh\n')), \
            mock.patch('iocage_lib.ioc_fetch.tempfile.NamedTemporaryFile',
                       return_value=script), \
            mock.patch('iocage_lib.ioc_list.IOCList') as listing, \
            mock.patch('iocage_lib.ioc_json.IOCJson') as configuration:
        listing.return_value.list_datasets.return_value = {'test': 'test-path'}
        configuration.return_value.json_get_value.return_value = {
            'basejail': True, 'release': '15.1-RELEASE',
        }
        if malformed:
            with pytest.raises(version_error) as caught:
                fetch.fetch_update()
            assert_version_error(caught.value, caplog, release_root)
        else:
            fetch.fetch_update()

    unmount = mock.call(['umount', f'{release_root}/dev'])
    assert unmount in process.call_args_list
    if verify:
        assert script.closed
        assert not Path(script.name).exists()
    else:
        script.close()
        Path(script.name).unlink()
    if malformed:
        listing.assert_not_called()
        configuration.assert_not_called()
    else:
        configuration.return_value.json_set_value.assert_called_once_with(
            'release=15.1-RELEASE-p4'
        )
