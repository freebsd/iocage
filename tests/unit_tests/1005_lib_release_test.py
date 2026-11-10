from unittest import mock

import pytest

from iocage_lib.ioc_common import get_jail_freebsd_version
from iocage_lib.ioc_json import IOCJson
from iocage_lib.ioc_plugin import IOCPlugin


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
