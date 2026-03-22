# Copyright (c) 2014-2019, iocage
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
import mock
import pytest
import iocage_lib.ioc_start as ioc_start


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
def test_vnet_addr_dhcp_in_ip4_addr_string_still_applies_ipv6(mock_checkoutput):
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
