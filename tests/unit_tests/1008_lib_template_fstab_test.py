"""Startup must validate the fstab belonging to its actual jail path."""

from collections import defaultdict
from unittest import mock

import pytest

from iocage_lib import ioc_start


@pytest.mark.parametrize('kind', ['jails', 'templates'])
def test_start_reads_fstab_from_actual_path(tmp_path, kind):
    path = tmp_path / kind / 'test-jail'
    path.mkdir(parents=True)
    (path / 'fstab').write_text('# comment\nsource target nullfs ro 0 0\n')
    start = ioc_start.IOCStart('test-jail', str(path), unit_test=True)
    start.iocroot = str(tmp_path)
    start.exec_fib = '0'
    start.conf = defaultdict(lambda: 0, ip4_addr='none', ip6_addr='none')

    class Validated(Exception):
        pass

    with mock.patch.object(ioc_start.iocage_lib.ioc_list, 'IOCList') as ls, \
            mock.patch.object(ioc_start.os, 'uname',
                              return_value=('', '', '15.1-RELEASE')), \
            mock.patch.object(ioc_start.iocage_lib.ioc_common,
                              'get_host_gateways'), \
            mock.patch.object(ioc_start.iocage_lib.ioc_fstab,
                              'IOCFstab') as fstab:
        ls.return_value.list_get_jid.return_value = (False, '-')
        validate = mock.Mock(side_effect=Validated)
        fstab.return_value.__validate_fstab__ = validate
        with pytest.raises(Validated):
            start.__start_jail__()
    validate.assert_called_once_with(
        ['', 'source target nullfs ro 0 0'], 'all'
    )
