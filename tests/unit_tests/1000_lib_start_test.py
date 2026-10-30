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
from unittest import mock
import pytest
import iocage_lib.ioc_start as ioc_start


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
        'jexec', 'ioc-test', 'ifconfig', '-f', 'inet:cidr', 'epair0b', 'inet'
    ])
    assert start.ip4_addr == expected_address
    stop.assert_not_called()
    assert logit.call_args.args[0]['message'].endswith(lease)


@pytest.mark.parametrize('output', [
    b'\tinet 0.0.0.0/24\n',
    b'epair0b: flags=8843<UP>\n\tether 00:00:00:00:00:01\n',
    b'\tinet invalid/24\n',
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


@mock.patch('iocage_lib.ioc_common.checkoutput')
def test_should_return_mtu_of_first_member(mock_checkoutput):
    mock_checkoutput.side_effect = [bridge_if_config, member_if_config]

    mtu = ioc_start.IOCStart("", "", unit_test=True).find_bridge_mtu('bridge0')
    assert mtu == '1500'
    mock_checkoutput.assert_has_calls([mock.call(["ifconfig", "bridge0"]),
                                       mock.call(["ifconfig", "bge0"])])


@mock.patch('iocage_lib.ioc_common.checkoutput')
def test_should_return_mtu_of_first_member_with_description(mock_checkoutput):
    mock_checkoutput.side_effect = [bridge_with_description_if_config,
                                    member_if_config]

    mtu = ioc_start.IOCStart("", "", unit_test=True).find_bridge_mtu('bridge0')
    assert mtu == '1500'
    mock_checkoutput.assert_has_calls([mock.call(["ifconfig", "bridge0"]),
                                       mock.call(["ifconfig", "bge0"])])


# @mock.patch('iocage_lib.ioc_common.checkoutput')
# def test_should_return_default_mtu_if_no_members(mock_checkoutput):
#     mock_checkoutput.side_effect = [bridge_with_no_members_if_config,
#                                     member_if_config]
#
#     # IOCStart.get() is not implemented in test mode.
#     # We need it for this test.
#     # So provide a dummy implementation which gives us the default MTU.
#     def _mock_iocstart_get(prop):
#         if prop=='vnet_default_mtu':
#             return "1500"
#         raise AttributeError(prop)
#
#     iocs = ioc_start.IOCStart("", "", unit_test=True)
#     iocs.get = _mock_iocstart_get
#     mtu = iocs.find_bridge_mtu('bridge0')
#     assert mtu == '1500'
#     mock_checkoutput.called_with(["ifconfig", "bridge0"])


@mock.patch('iocage_lib.ioc_common.logit')
@pytest.mark.parametrize('test_input,expected', [
    ({'host_gateways': {'ipv4': {'gateway': '217.29.43.254',
                                 'interface': 'inet0'},
                        'ipv6': {'gateway': None,
                                 'interface': None}}},
     'inet0'),
    ({'host_gateways': {'ipv4': {'gateway': '217.29.43.254',
                                 'interface': 'inet0'},
                        'ipv6': {'gateway': 'fe80::8%mgmt0',
                                 'interface': 'mgmt0'}}},
     'inet0'),
    ({'host_gateways': {'ipv4': {'gateway': None,
                                 'interface': None},
                        'ipv6': {'gateway': 'fe80::8%mgmt0',
                                 'interface': 'mgmt0'}}},
     'mgmt0'),
    ({'host_gateways': {'ipv4': {'gateway': None,
                                 'interface': None},
                        'ipv6': {'gateway': None,
                                 'interface': None}}},
     Exception)])
def test_should_return_default_interface(mock_logit, test_input, expected):
    iocstart = ioc_start.IOCStart("", "", unit_test=True)
    iocstart.host_gateways = test_input['host_gateways']
    actual = iocstart.get_default_interface()
    if expected != Exception:
        assert actual == expected
        mock_logit.assert_not_called()
    else:
        mock_logit.assert_called_once_with(
            {'level': 'EXCEPTION', 'message': 'No default interface found'},
            _callback=None,
            silent=False)


@pytest.mark.parametrize('test_input,expected', [
    ({'host_gateways': {'ipv4': {'gateway': None,
                                 'interface': None},
                        'ipv6': {'gateway': None,
                                 'interface': None}}},
     {'ipv4': 'none',
      'ipv6': 'none'}),
    ({'host_gateways': {'ipv4': {'gateway': '217.29.43.254',
                                 'interface': 'inet0'},
                        'ipv6': {'gateway': 'fe80::8%inet0',
                                 'interface': 'inet0'}}},
     {'ipv4': '217.29.43.254',
      'ipv6': 'fe80::8%inet0'}),
    ({'host_gateways': {'ipv4': {'gateway': None,
                                 'interface': None},
                        'ipv6': {'gateway': 'fe80::8%mgmt0',
                                 'interface': 'mgmt0'}}},
     {'ipv4': 'none',
      'ipv6': 'fe80::8%mgmt0'}),
    ({'host_gateways': {'ipv4': {'gateway': None,
                                 'interface': None},
                        'ipv6': {'gateway': 'fe80::8%inet0',
                                 'interface': 'inet0'}}},
     {'ipv4': 'none',
      'ipv6': 'fe80::8%inet0'}),
    ({'host_gateways': {'ipv4': {'gateway': None,
                                 'interface': None},
                        'ipv6': {'gateway': 'fe80::8%inet0',
                                 'interface': 'inet0'}}},
     {'ipv4': 'none',
      'ipv6': 'fe80::8%inet0'})])
def test_should_return_default_gateway(test_input, expected):
    iocstart = ioc_start.IOCStart("", "", unit_test=True)
    iocstart.host_gateways = test_input['host_gateways']
    assert iocstart.get_default_gateway() == expected['ipv4']
    assert iocstart.get_default_gateway('ipv4') == expected['ipv4']
    assert iocstart.get_default_gateway('ipv6') == expected['ipv6']


bridge_if_config = """\
bridge0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
        ether 00:00:00:00:00:00
        nd6 options=1<PERFORMNUD>
        groups: bridge
        id 00:00:00:00:00:00 priority 32768 hellotime 2 fwddelay 15
        maxage 20 holdcnt 6 proto rstp maxaddr 2000 timeout 1200
        root id 00:00:00:00:00:00 priority 32768 ifcost 0 port 0
            member: bge0 flags=143<LEARNING,DISCOVER,AUTOEDGE,AUTOPTP>
            ifmaxaddr 0 port 1 priority 128 path cost 20000
"""

bridge_with_description_if_config = """\
bridge0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
        description: first-bridge
        ether 00:00:00:00:00:00
        nd6 options=1<PERFORMNUD>
        groups: bridge
        id 00:00:00:00:00:00 priority 32768 hellotime 2 fwddelay 15
        maxage 20 holdcnt 6 proto rstp maxaddr 2000 timeout 1200
        root id 00:00:00:00:00:00 priority 32768 ifcost 0 port 0
            member: bge0 flags=143<LEARNING,DISCOVER,AUTOEDGE,AUTOPTP>
            ifmaxaddr 0 port 1 priority 128 path cost 20000
"""

bridge_with_no_members_if_config = """\
bridge0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
        description: first-bridge
        ether 00:00:00:00:00:00
        nd6 options=1<PERFORMNUD>
        groups: bridge
        id 00:00:00:00:00:00 priority 32768 hellotime 2 fwddelay 15
        maxage 20 holdcnt 6 proto rstp maxaddr 2000 timeout 1200
        root id 00:00:00:00:00:00 priority 32768 ifcost 0 port 0
"""

member_if_config = (
    "bge0: flags=8943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST>"
    " metric 0 mtu 1500\n"
    "        options=c019b<RXCSUM,TXCSUM,VLAN_MTU,VLAN_HWTAGGING,"
    "VLAN_HWCSUM,TSO4,VLAN_HWTSO,LINKSTATE>\n"
    "        ether 00:00:00:00:00:00\n"
    "        inet6 fe80::0000:0000:0000:0000%bge0 prefixlen 64 scopeid 0x1\n"
    "        inet 10.2.3.4 netmask 0xffffff00 broadcast 10.2.3.255\n"
    "        nd6 options=21<PERFORMNUD,AUTO_LINKLOCAL>\n"
    "        media: Ethernet autoselect (1000baseT <full-duplex>)\n"
    "        status: active\n"
    )


# ─── Tests for start_network_vnet_addr ───────────────────────────────────────
#
# Regression tests for the fix where IPv4 DHCP settings incorrectly
# prevented static IPv6 addresses from being assigned.

def _make_iocstart_for_addr(**overrides):
    """Create an IOCStart instance with properties needed for addr tests."""
    iocstart = ioc_start.IOCStart("test-jail", "", unit_test=True)
    iocstart.exec_fib = '0'
    iocstart.ip4_addr = overrides.get('ip4_addr', 'none')

    dhcp_val = overrides.get('dhcp', 0)
    iocstart.get = lambda prop: dhcp_val if prop == 'dhcp' else 'auto'

    return iocstart


@mock.patch('iocage_lib.ioc_common.checkoutput')
def test_vnet_addr_ipv6_static_applied_when_dhcp_enabled(mock_checkoutput):
    """Static IPv6 must be assigned even when IPv4 DHCP is enabled."""
    iocstart = _make_iocstart_for_addr(dhcp=1)
    iocstart.start_network_vnet_addr(
        'vnet0', '2001:db8::1/64', 'fe80::1', ipv6=True
    )
    mock_checkoutput.assert_called_once()
    args = mock_checkoutput.call_args[0][0]
    assert 'inet6' in args
    assert '2001:db8::1/64' in args


@mock.patch('iocage_lib.ioc_common.checkoutput')
def test_vnet_addr_ipv4_skipped_when_dhcp_enabled(mock_checkoutput):
    """IPv4 ifconfig should be skipped when DHCP is handling it."""
    iocstart = _make_iocstart_for_addr(dhcp=1)
    iocstart.start_network_vnet_addr(
        'vnet0', '192.168.1.10/24', '192.168.1.1', ipv6=False
    )
    mock_checkoutput.assert_not_called()


@mock.patch('iocage_lib.ioc_common.checkoutput')
def test_vnet_addr_ipv4_applied_when_dhcp_disabled(mock_checkoutput):
    """IPv4 static must be assigned when DHCP is off."""
    iocstart = _make_iocstart_for_addr(dhcp=0)
    iocstart.start_network_vnet_addr(
        'vnet0', '192.168.1.10/24', '192.168.1.1', ipv6=False
    )
    mock_checkoutput.assert_called_once()
    args = mock_checkoutput.call_args[0][0]
    assert '192.168.1.10/24' in args


@mock.patch('iocage_lib.ioc_common.checkoutput')
def test_vnet_addr_ipv6_applied_when_dhcp_disabled(mock_checkoutput):
    """IPv6 static must be assigned when DHCP is off."""
    iocstart = _make_iocstart_for_addr(dhcp=0)
    iocstart.start_network_vnet_addr(
        'vnet0', '2001:db8::1/64', 'fe80::1', ipv6=True
    )
    mock_checkoutput.assert_called_once()
    args = mock_checkoutput.call_args[0][0]
    assert 'inet6' in args
    assert '2001:db8::1/64' in args


@mock.patch('iocage_lib.ioc_common.checkoutput')
def test_vnet_addr_accept_rtadv_never_calls_ifconfig(mock_checkoutput):
    """accept_rtadv addresses should never invoke ifconfig."""
    iocstart = _make_iocstart_for_addr(dhcp=0)
    iocstart.start_network_vnet_addr(
        'vnet0', 'accept_rtadv', 'fe80::1', ipv6=True
    )
    mock_checkoutput.assert_not_called()


@mock.patch('iocage_lib.ioc_common.checkoutput')
def test_vnet_addr_dhcp_in_ip4_addr_string_skips_ipv4(mock_checkoutput):
    """ip4_addr containing DHCP should also suppress IPv4 ifconfig."""
    iocstart = _make_iocstart_for_addr(dhcp=0, ip4_addr='vnet0|DHCP')
    iocstart.start_network_vnet_addr(
        'vnet0', '192.168.1.10/24', '192.168.1.1', ipv6=False
    )
    mock_checkoutput.assert_not_called()


@mock.patch('iocage_lib.ioc_common.checkoutput')
def test_vnet_addr_dhcp_in_ip4_addr_string_still_applies_ipv6(
    mock_checkoutput
):
    """ip4_addr containing DHCP must not prevent IPv6 assignment."""
    iocstart = _make_iocstart_for_addr(dhcp=0, ip4_addr='vnet0|DHCP')
    iocstart.start_network_vnet_addr(
        'vnet0', '2001:db8::1/64', 'fe80::1', ipv6=True
    )
    mock_checkoutput.assert_called_once()
    args = mock_checkoutput.call_args[0][0]
    assert 'inet6' in args


# ─── Tests for start_network_interface_vnet address spoofing ─────────────────
#
# Regression tests ensuring IPv4 DHCP address spoofing does not affect
# IPv6 static address entries in net_configs.

def _make_iocstart_for_iface(**overrides):
    """Create an IOCStart instance with properties needed for iface tests."""
    iocstart = ioc_start.IOCStart("test-jail", "", unit_test=True)
    iocstart.exec_fib = '0'
    iocstart.ip4_addr = overrides.get('ip4_addr', 'vnet0|192.168.1.9')
    iocstart.ip6_addr = overrides.get(
        'ip6_addr', 'vnet0|2001:db8::1/64')

    dhcp_val = overrides.get('dhcp', 0)
    mtu_val = overrides.get('mtu', '1500')

    def mock_get(prop):
        if prop == 'dhcp':
            return dhcp_val
        if prop.endswith('_mtu'):
            return mtu_val
        return 'auto'

    iocstart.get = mock_get
    return iocstart


@mock.patch.object(ioc_start.IOCStart, 'start_network_vnet_addr',
                   return_value=None)
@mock.patch.object(ioc_start.IOCStart, 'start_network_vnet_iface',
                   return_value=None)
def test_iface_vnet_dhcp_does_not_spoof_ipv6_address(
    mock_iface, mock_addr
):
    """When dhcp=1, IPv6 static address must pass through unspoofed."""
    iocstart = _make_iocstart_for_iface(dhcp=1)
    net_configs = (
        (iocstart.ip4_addr, '192.168.1.1', False),
        (iocstart.ip6_addr, 'fe80::1', True),
    )
    iocstart.start_network_interface_vnet('vnet0:bridge0', net_configs, '42')

    # Collect the (ip, ipv6) pairs from all calls to start_network_vnet_addr
    addr_calls = [(c[0][1], c[0][3]) for c in mock_addr.call_args_list]

    # The IPv6 address must arrive intact (not spoofed to empty)
    ipv6_calls = [(ip, v6) for ip, v6 in addr_calls if v6]
    assert len(ipv6_calls) == 1
    assert ipv6_calls[0][0] == '2001:db8::1/64'


@mock.patch.object(ioc_start.IOCStart, 'start_network_vnet_addr',
                   return_value=None)
@mock.patch.object(ioc_start.IOCStart, 'start_network_vnet_iface',
                   return_value=None)
def test_iface_vnet_dhcp_does_spoof_ipv4_address(
    mock_iface, mock_addr
):
    """When dhcp=1, IPv4 address should be spoofed (DHCP will provide it)."""
    iocstart = _make_iocstart_for_iface(dhcp=1)
    net_configs = (
        (iocstart.ip4_addr, '192.168.1.1', False),
        (iocstart.ip6_addr, 'fe80::1', True),
    )
    iocstart.start_network_interface_vnet('vnet0:bridge0', net_configs, '42')

    addr_calls = [(c[0][1], c[0][3]) for c in mock_addr.call_args_list]

    # The IPv4 address should have been spoofed to empty
    ipv4_calls = [(ip, v6) for ip, v6 in addr_calls if not v6]
    assert len(ipv4_calls) == 1
    assert ipv4_calls[0][0] == "''"


@mock.patch.object(ioc_start.IOCStart, 'start_network_vnet_addr',
                   return_value=None)
@mock.patch.object(ioc_start.IOCStart, 'start_network_vnet_iface',
                   return_value=None)
def test_iface_vnet_no_dhcp_preserves_both_addresses(
    mock_iface, mock_addr
):
    """When dhcp=0, both IPv4 and IPv6 addresses pass through intact."""
    iocstart = _make_iocstart_for_iface(dhcp=0)
    net_configs = (
        (iocstart.ip4_addr, '192.168.1.1', False),
        (iocstart.ip6_addr, 'fe80::1', True),
    )
    iocstart.start_network_interface_vnet('vnet0:bridge0', net_configs, '42')

    addr_calls = [(c[0][1], c[0][3]) for c in mock_addr.call_args_list]

    ipv4_calls = [(ip, v6) for ip, v6 in addr_calls if not v6]
    ipv6_calls = [(ip, v6) for ip, v6 in addr_calls if v6]

    assert len(ipv4_calls) == 1
    assert ipv4_calls[0][0] == '192.168.1.9'
    assert len(ipv6_calls) == 1
    assert ipv6_calls[0][0] == '2001:db8::1/64'
