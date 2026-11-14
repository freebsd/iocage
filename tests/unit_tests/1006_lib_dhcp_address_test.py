"""Read and validate DHCP leases independently of ifconfig line order."""

from unittest import mock

import pytest

from iocage_lib import ioc_start


@pytest.mark.parametrize('prefix', [
    '',
    'epair0b: flags=8843<UP,BROADCAST,RUNNING> metric 0 mtu 1500\n',
    'epair0b: flags=8843<UP,BROADCAST,RUNNING> metric 0 mtu 1500\n'
    '\tdescription: DHCP interface\n\tether 00:00:00:00:00:01\n'
    '\tinet6 fe80::1%epair0b prefixlen 64 scopeid 0x1\n',
])
@pytest.mark.parametrize('lease, expected_address', [
    ('192.0.2.10/24', '192.0.2.10'),
    ('10.0.0.0/31', '10.0.0.0'),
])
@mock.patch('iocage_lib.ioc_common.logit')
@mock.patch('iocage_lib.ioc_stop.IOCStop')
@mock.patch('iocage_lib.ioc_start.su.check_output')
def test_dhcp_cidr_ignores_output_position(
    check_output, stop, logit, prefix, lease, expected_address
):
    check_output.return_value = (
        prefix + f'\tinet {lease}\n'
        '\tinet 192.0.2.11/24 broadcast 192.0.2.255\n'
    ).encode()
    start = ioc_start.IOCStart('test', '/jails/test', unit_test=True)
    start.conf = {'interfaces': 'vnet0:bridge0,vnet1:bridge1'}

    start._check_dhcp_address()

    check_output.assert_called_once_with([
        'jexec', 'ioc-test', 'ifconfig', '-f', 'addr:numeric,inet:cidr',
        'epair0b', 'inet'
    ])
    assert start.ip4_addr == expected_address
    stop.assert_not_called()
    assert logit.call_args.args[0]['message'].endswith(lease)


@pytest.mark.parametrize('description', [b'caf\xe9', b'\xffinterface'])
@mock.patch('iocage_lib.ioc_common.logit')
@mock.patch('iocage_lib.ioc_stop.IOCStop')
@mock.patch('iocage_lib.ioc_start.su.check_output')
def test_dhcp_ignores_non_utf8_descriptions(
    check_output, stop, logit, description
):
    check_output.return_value = (
        b'epair0b: flags=8843<UP> mtu 1500\n\tdescription: ' + description
        + b'\n\tinet 192.0.2.10/24 broadcast 192.0.2.255\n'
    )
    start = ioc_start.IOCStart('test', '/jails/test', unit_test=True)
    start.conf = {'interfaces': 'vnet0:bridge0'}

    start._check_dhcp_address()

    assert start.ip4_addr == '192.0.2.10'
    stop.assert_not_called()
    assert logit.call_args.args[0] == {
        'level': 'INFO', 'message': '  + DHCP Address: 192.0.2.10/24',
    }


@pytest.mark.parametrize('output_format', ['addr:fqdn', 'addr:host'])
@mock.patch('iocage_lib.ioc_common.logit')
@mock.patch('iocage_lib.ioc_stop.IOCStop')
@mock.patch('iocage_lib.ioc_start.su.check_output')
def test_dhcp_ignores_inherited_hostname_format(
    check_output, stop, logit, monkeypatch, output_format
):
    monkeypatch.setenv('IFCONFIG_FORMAT', output_format)

    def ifconfig(command):
        # Model inherited hostname output unless numeric is requested.
        formats = command[command.index('-f') + 1].split(',')
        address = '192.0.2.10' if 'addr:numeric' in formats else 'jail.example'
        return f'\tinet {address}/24\n'.encode()

    check_output.side_effect = ifconfig
    start = ioc_start.IOCStart('test', '/jails/test', unit_test=True)
    start.conf = {'interfaces': 'vnet0:bridge0'}

    start._check_dhcp_address()

    assert start.ip4_addr == '192.0.2.10'
    check_output.assert_called_once()
    stop.assert_not_called()
    assert logit.call_args.args[0] == {
        'level': 'INFO', 'message': '  + DHCP Address: 192.0.2.10/24',
    }


@pytest.mark.parametrize('output', [
    b'\tinet 0.0.0.0/24\n',
    b'epair0b: flags=8843<UP>\n\tether 00:00:00:00:00:01\n',
    b'\tinet invalid/24\n',
    b'\tdescription: caf\xe9\n',
    b'\tinet 192.0.2.\xff/24\n',
    b'\tinet\n',
    ioc_start.su.CalledProcessError(1, 'ifconfig'),
])
@mock.patch('iocage_lib.ioc_common.logit', side_effect=RuntimeError)
@mock.patch('iocage_lib.ioc_stop.IOCStop')
@mock.patch('iocage_lib.ioc_start.su.check_output')
def test_dhcp_failure_stops_jail(check_output, stop, logit, output):
    if isinstance(output, Exception):
        check_output.side_effect = output
    else:
        check_output.return_value = output
    start = ioc_start.IOCStart('test', '/jails/test', unit_test=True)
    start.conf = {'interfaces': 'epair1b:bridge0'}

    with pytest.raises(RuntimeError):
        start._check_dhcp_address()

    stop.assert_called_once_with(
        'test', '/jails/test', force=True, silent=True
    )
    assert logit.call_args.args[0]['level'] == 'EXCEPTION'
    assert 'Stopped test due to DHCP failure' in (
        logit.call_args.args[0]['message']
    )
