"""Maintained FreeBSD interfaces retain configured jail parameters."""

from unittest import mock

import pytest

from iocage_lib import ioc_start


@pytest.mark.parametrize('enabled', [False, True])
def test_startup_preserves_current_jail_parameters(tmp_path, enabled):
    start = ioc_start.IOCStart('test', str(tmp_path), unit_test=True)
    start.pool = 'pool'
    start.iocroot = str(tmp_path)
    start.exec_fib = '0'
    start.set = mock.Mock()
    with mock.patch.object(
        ioc_start.iocage_lib.ioc_json.IOCConfiguration,
        'get_mac_prefix', return_value='02ff60'
    ):
        start.conf = (
            ioc_start.iocage_lib.ioc_json.IOCConfiguration
            .retrieve_default_props()
        )
    start.conf['host_hostname'] = 'test'
    permissions = (
        'allow_mount_tmpfs', 'allow_mount_fdescfs', 'allow_mlock',
        'allow_mount_fusefs', 'allow_vmm', 'allow_nfsd'
    )
    for prop in permissions:
        start.conf[prop] = int(enabled)
    start.conf['exec_created'] = '/usr/bin/true'
    start.get = start.conf.__getitem__
    (tmp_path / 'jails' / 'test').mkdir(parents=True)
    (tmp_path / 'jails' / 'test' / 'fstab').touch()

    class ConfigurationReady(Exception):
        pass

    with (
        mock.patch('iocage_lib.ioc_list.IOCList') as jail_list,
        mock.patch('iocage_lib.ioc_common.get_host_gateways'),
        mock.patch('iocage_lib.ioc_fstab.IOCFstab') as fstab,
        mock.patch.object(start, '__check_dhcp_or_accept_rtadv__'),
        mock.patch('iocage_lib.ioc_common.logit'),
        mock.patch('iocage_lib.ioc_common.generate_devfs_ruleset',
                   return_value=(False, '4', '1000')),
        mock.patch('iocage_lib.ioc_json.JailRuntimeConfiguration') as runtime,
    ):
        jail_list.return_value.list_get_jid.return_value = (False, None)
        fstab.return_value.__validate_fstab__ = mock.Mock()
        runtime.return_value.sync_changes.side_effect = ConfigurationReady
        with pytest.raises(ConfigurationReady):
            start.__start_jail__()

    parameters = runtime.call_args.args[1]
    assert 'mount.fdescfs=1' in parameters
    assert 'exec.created=/usr/bin/true' in parameters
    for prop in ('sysvmsg', 'sysvsem', 'sysvshm'):
        assert f'{prop}=new' in parameters
    for prop in permissions:
        parameter = prop.replace('_', '.') + '=1'
        assert (parameter in parameters) == enabled
