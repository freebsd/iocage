import subprocess
from unittest import mock

import pytest

from iocage_lib.ioc_create import IOCCreate
from iocage_lib.iocage import IOCage
from iocage_lib.dataset import Dataset


@pytest.mark.parametrize('properties, locked', [
    ({'encryption': 'off'}, False),
    ({'encryption': 'aes-256-gcm', 'keystatus': 'available'}, False),
    ({'encryption': 'aes-256-gcm', 'keystatus': 'unavailable'}, True),
])
def test_dataset_lock_state_uses_current_zfs_properties(properties, locked):
    dataset = Dataset.__new__(Dataset)
    # ZFS omits keystatus from `get all` on unencrypted datasets.
    with mock.patch.object(
        Dataset, 'properties', new_callable=mock.PropertyMock,
        return_value=properties,
    ):
        assert dataset.locked is locked


@pytest.mark.parametrize(
    'release', ['14.4-RELEASE', '14.5-RELEASE', '15.1-RELEASE']
)
@pytest.mark.parametrize('arch', ['amd64', 'arm64'])
def test_fetch_uses_current_distribution_archives(release, arch):
    iocage = IOCage.__new__(IOCage)
    iocage.silent = True
    iocage.callback = None
    expected = ['MANIFEST', 'base.txz', 'src.txz']
    if arch == 'amd64':
        expected.insert(2, 'lib32.txz')

    with (
        mock.patch('iocage_lib.iocage.os.uname',
                   return_value=('FreeBSD', 'test', '15.1-RELEASE', '', arch)),
        mock.patch('iocage_lib.iocage.ioc_common.checkoutput',
                   return_value='15.1-RELEASE'),
        mock.patch('iocage_lib.iocage.ioc_fetch.IOCFetch') as fetch,
    ):
        iocage.fetch(release=release)

    assert fetch.call_args.args == (release,)
    assert fetch.call_args.kwargs['files'] == expected
    fetch.return_value.fetch_release.assert_called_once_with()


def test_fetch_preserves_explicit_archive_selection():
    iocage = IOCage.__new__(IOCage)
    iocage.silent = True
    iocage.callback = None
    files = ['MANIFEST', 'base.txz']
    with (
        mock.patch('iocage_lib.iocage.ioc_common.checkoutput',
                   return_value='15.1-RELEASE'),
        mock.patch('iocage_lib.iocage.ioc_fetch.IOCFetch') as fetch,
    ):
        iocage.fetch(release='15.1-RELEASE', files=files)

    assert fetch.call_args.kwargs['files'] == files


def test_fstab_preserves_long_destination_and_jail_root():
    iocage = IOCage.__new__(IOCage)
    iocage.iocroot = '/iocage'
    iocage.__check_jail_existence__ = mock.Mock(
        return_value=('example', '/jail')
    )
    destination = '/mnt/' + 'directory/' * 12
    with (
        mock.patch('iocage_lib.iocage.ioc_fstab.IOCFstab') as fstab,
        mock.patch('iocage_lib.iocage.ioc_common.logit') as log,
    ):
        iocage.fstab('add', '/source', destination, 'nullfs', 'ro', '0', '0',
                     add_path=True)

    expected = f'/iocage/jails/example/root{destination}'
    assert fstab.call_args.args[3] == expected
    log.assert_not_called()


def test_package_install_dns_keeps_search_domain_behavior():
    create = IOCCreate.__new__(IOCCreate)
    create.callback = None
    create.silent = True
    create.plugin = False
    create.pkglist = []
    create.log = mock.Mock()
    with (
        mock.patch('iocage_lib.ioc_list.IOCList') as jail_list,
        mock.patch('dns.resolver.resolve') as resolve,
        mock.patch('iocage_lib.ioc_exec.SilentExec'),
        mock.patch('iocage_lib.ioc_exec.IOCExec'),
        mock.patch(
            'iocage_lib.ioc_create.su.run',
            return_value=subprocess.CompletedProcess([], 0, b'')
        ) as run,
        mock.patch('iocage_lib.ioc_common.consume_and_log'),
        mock.patch('iocage_lib.ioc_common.logit'),
        mock.patch('iocage_lib.ioc_stop.IOCStop') as stop,
    ):
        jail_list.return_value.list_get_jid.return_value = (True, '42')
        create.create_install_packages(
            'example', '/jail', repo='https://packages/repo'
        )

    resolve.assert_called_once_with('packages', search=True)
    assert run.call_args.args[0] == [
        'pkg-static', '-j', '42', 'install', '-q', '-y', 'pkg'
    ]
    stop.assert_not_called()
