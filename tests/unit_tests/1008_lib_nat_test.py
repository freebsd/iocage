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

"""Validate forwarding syntax, applied state and firewall rollback."""
import json
import os
import subprocess

from types import SimpleNamespace
from unittest import mock

import pytest

from iocage_lib import ioc_common as common
from iocage_lib import ioc_nat as nat
from iocage_lib import ioc_list
from iocage_lib.ioc_exceptions import ValidationFailed
from iocage_lib.ioc_json import IOCJson


@pytest.mark.parametrize('rule', [
    'tcp(99999:80)', 'udp(80:65536)', 'tcp(0:80)', 'tcp(90-80)',
    'tcp(81:9000-9001)', 'tcp(80:9000),tcp(81:9000)',
    'tcp/udp(8000-8003),udp(53:8001)', 'string', 'tcp(8)junk'
])
def test_invalid_forwards_fail_on_set_and_preflight(rule):
    config = object.__new__(IOCJson)
    config.silent, config.callback = True, None
    with pytest.raises(ValidationFailed):
        config.json_check_prop('nat_forwards', rule, {})
    with pytest.raises(ValueError):
        nat.forward_ports(rule)


def test_bare_ports_and_dual_protocol_ranges():
    config = object.__new__(IOCJson)
    value, _ = config.json_check_prop('nat_forwards',
                                      '8,tcp/udp(10-12:20-22)', {})
    assert value == 'tcp(8),tcp/udp(10-12:20-22)'
    assert common.parse_nat_forwards(value) == [
        ('tcp', range(8, 9), range(8, 9)),
        ('tcp', range(10, 13), range(20, 23)),
        ('udp', range(10, 13), range(20, 23)),
    ]
    assert nat.forward_ports(value) == {8, 20, 21, 22}


def test_plugin_portal_uses_tcp_range_offset(monkeypatch):
    conf = {
        'nat': True, 'nat_forwards': 'tcp(8001:8888),tcp(8000-8002:9000-9002),'
                                     'udp(8001:9999)'
    }
    monkeypatch.setattr(common.netifaces, 'interfaces', lambda: ['em0'])
    monkeypatch.setattr(common.netifaces, 'ifaddresses', lambda name: {
        common.netifaces.AF_INET: [{'addr': '192.0.2.1'}]
    })
    gateways = {'ipv4': {'interface': 'em0'}}
    portals = common.retrieve_admin_portals(
        conf, False, 'http://%%IP%%:8001/app,http://%%IP%%:3000', gateways
    )
    assert portals == ['http://192.0.2.1:9001/app', 'http://192.0.2.1:3000']


@pytest.fixture
def active_jails(monkeypatch, tmp_path):
    monkeypatch.setattr(nat, 'RUNTIME', tmp_path)
    monkeypatch.setattr(nat, '_module_loaded', lambda module: False)
    active = {}
    monkeypatch.setattr(common, 'get_active_jails', lambda: active)
    return active


def ipfw_routes(subnet, group=1):
    return {
        (group, 100): [f'nat 462 ip4 from {subnet} to any out via em0 '
                       '// iocage'],
        (group, 101): ['nat 462 ip4 from any to any in via em0 // iocage'],
    }


@pytest.mark.parametrize('backend', ['pf', 'ipfw'])
@pytest.mark.parametrize('order', [(2, 22), (22, 2)])
def test_reload_keeps_peer_destinations_and_all_live_subnets(
    active_jails, monkeypatch, backend, order
):
    assert nat.running_ports() == set()
    commands = []

    def run(args, stdin=None):
        commands.append(' '.join(map(str, args)))
        if args[0] == 'pfctl' and '-f' in args:
            commands.append(stdin)
        if args == ['ipfw', 'set', 'show']:
            return 'enable 0 1 2 3'
        return ''

    monkeypatch.setattr(nat, '_run', run)

    def apply(name, jid, address, forwards):
        active_jails[name] = {'jid': jid}
        commands.clear()
        nat.apply(name, address, 'em0', forwards, backend)
        return '\n'.join(commands)

    apply('ioc-outbound', 1, 'em0|172.16.1.1', 'none')
    apply('ioc-stopped', 3, '172.16.2.1', 'none')
    del active_jails['ioc-stopped']
    for suffix in order:
        ip = f'vnet0|172.16.0.{suffix}/30'
        forwards = f'tcp/udp(80:{9000 + suffix})'
        rules = apply(f'ioc-{suffix}', suffix, ip, forwards)
    for suffix in order:
        for proto in ('tcp', 'udp'):
            if backend == 'ipfw':
                expected = f'redirect_port {proto} 172.16.0.{suffix}:80 '
                assert expected + str(9000 + suffix) in rules
            else:
                expected = (f'proto {proto} from any to (em0:0) port = '
                            f'{9000 + suffix} -> 172.16.0.{suffix} port 80')
                assert expected in rules
    assert 'from 172.16.0.0/24' in rules
    assert 'from 172.16.1.0/24' in rules
    assert 'from 172.16.2.0/24' not in rules

    # Even a direct, concurrent writer must recheck the applied port under lock.
    active_jails['ioc-conflict'] = {'jid': 30}
    with pytest.raises(ValueError, match='NAT forwarding ports conflict'):
        nat.apply('ioc-conflict', '172.16.0.30', 'em0', 'tcp(90:9002)',
                  backend)
    assert nat.running_ports() == {9002, 9022}
    apply('ioc-2', 2, '172.16.0.2', 'none')
    assert nat.running_ports() == {9022}


@pytest.mark.parametrize('failure', ['config', 'remove', 'publish'])
def test_ipfw_rollback_preserves_previous_rules(
    monkeypatch, active_jails, failure
):
    state = nat.RUNTIME / 'ipfw.json'
    state.write_text(json.dumps({'ioc-old': {
        'jid': '1', 'address': '172.16.1.2', 'interface': 'em0',
        'forwards': 'tcp(80:9000)'
    }}))
    previous_state = state.read_bytes()
    active_jails['ioc-old'] = {'jid': 1}
    active_jails['ioc-next'] = {'jid': 2}
    routes = ipfw_routes('172.16.1.0/24')
    routes[0, 500] = ['allow ip from any to any']
    previous_config = nat.IPFW_NAT + ['if', 'em0', 'same_ports',
                                      'redirect_port', 'tcp',
                                      '172.16.1.2:80', '9000']
    monkeypatch.setattr(nat, '_ipfw_rules', lambda: (routes, set()))
    monkeypatch.setattr(nat, '_ipfw_config', lambda: previous_config)
    commands = []

    def run(args, stdin=None):
        commands.append(args)
        if args == ['ipfw', 'set', 'show']:
            return 'enable 0 1 2'
        if failure == 'config' and args[:4] == nat.IPFW_NAT:
            if args != previous_config:
                raise RuntimeError('injected config failure')
        if failure == 'remove' and args == ['ipfw', '-q', 'set', '1',
                                            'delete', '101']:
            raise RuntimeError('injected removal failure')
        return ''

    monkeypatch.setattr(nat, '_run', run)
    if failure == 'publish':
        monkeypatch.setattr(nat.os, 'replace', mock.Mock(
            side_effect=OSError('injected publish failure')))
    with pytest.raises((RuntimeError, OSError), match='injected'):
        nat.apply('ioc-next', '172.16.2.2', 'em0', 'tcp(80:9001)', 'ipfw')

    assert state.read_bytes() == previous_state
    assert previous_config in commands
    assert ['ipfw', '-q', 'flush'] not in commands
    assert ['ipfw', '-q', 'set', '2', 'flush'] in commands
    assert all('500' not in command for command in commands)
    delete_old = ['ipfw', '-q', 'set', '1', 'delete', '100']
    if failure == 'config':
        assert delete_old not in commands
    else:
        assert commands.index(['ipfw', 'set', 'enable', '2']) < commands.index(
            delete_old)
        restored = ['ipfw', '-q', 'add', '100', 'set', '1',
                    *routes[1, 100][0].split()]
        assert restored in commands


def test_failed_pf_load_keeps_applied_state(monkeypatch, active_jails):
    active_jails['ioc-next'] = {'jid': 2}

    def run(args, stdin=None):
        if '-sn' in args:
            return ''
        assert args == ['pfctl', '-a', nat.PF_ANCHOR, '-N', '-f', '-']
        raise subprocess.CalledProcessError(1, args, stderr='bad policy')

    monkeypatch.setattr(nat, '_run', run)
    with pytest.raises(subprocess.CalledProcessError):
        nat.apply('ioc-next', '172.16.0.2', 'em0', 'tcp(80:9002)', 'pf')
    assert nat.running_ports() == set()
    assert not (nat.RUNTIME / 'pf.json').exists()


def test_forward_change_keeps_other_ipfw_rules_with_the_same_number(
    monkeypatch, active_jails
):
    active_jails['ioc-peer'] = {'jid': 1}
    (nat.RUNTIME / 'ipfw.json').write_text(json.dumps({'ioc-peer': {
        'jid': '1', 'address': '172.16.0.2', 'interface': 'em0',
        'forwards': 'none'
    }}))
    routes = ipfw_routes('172.16.0.0/24')
    routes[1, 100].append('allow ip from any to any')
    routes[3, 101] = ['nat 463 ip4 from any to any in via em0 // host']
    monkeypatch.setattr(nat, '_ipfw_rules', lambda: (routes, set()))
    monkeypatch.setattr(nat, '_ipfw_config', lambda: [])
    run = mock.Mock()
    monkeypatch.setattr(nat, '_run', run)
    nat.apply('ioc-peer', '172.16.0.2', 'em0', 'tcp(80:9000)', 'ipfw')
    run.assert_called_once_with(nat.IPFW_NAT + [
        'if', 'em0', 'same_ports', 'redirect_port', 'tcp', '172.16.0.2:80',
        '9000'
    ])


@pytest.mark.parametrize('stop', [False, True])
@pytest.mark.parametrize('foreign', [
    {}, {(0, 500): ['prob 0.5 nat 462 ip4 from any to any via em0']},
    {(2, 101): ['nat 462 ip4 from any to any in via em0 // host']},
    {(0, 100): ['nat 462 ip4 from 172.16.0.0/24 to any out via em0']}
])
def test_ipfw_refuses_a_host_nat_instance_before_writing(
    active_jails, monkeypatch, stop, foreign
):
    monkeypatch.setattr(nat, '_module_loaded', lambda module: True)
    active_jails['ioc-jail'] = {'jid': 1}
    state = nat.RUNTIME / 'ipfw.json'
    if stop:
        state.write_text(json.dumps({'ioc-jail': {
            'jid': '1', 'address': '172.16.0.2', 'interface': 'em0',
            'forwards': 'tcp(80:9000)'
        }}))
    reserved = nat.running_ports()
    routes = dict(foreign)
    if foreign:
        routes[2, 100] = [
            'nat 462 ip4 from 172.16.0.0/24 to any out via em0 // iocage']
    monkeypatch.setattr(nat, '_ipfw_rules', lambda: (routes, set()))
    monkeypatch.setattr(nat, '_ipfw_config', lambda:
                        nat.IPFW_NAT + ['if', 'em0', 'same_ports'])
    run = mock.Mock()
    monkeypatch.setattr(nat, '_run', run)
    if stop:
        with pytest.raises(ValueError, match='462'):
            nat.prepare_stop('ioc-jail', 1)
    with pytest.raises(ValueError, match='462'):
        if stop:
            with nat.removing('ioc-jail', 1):
                pytest.fail('The jail should still own its address')
        else:
            nat.apply('ioc-jail', '172.16.0.2', 'em0', 'tcp(80:9000)', 'ipfw')
    run.assert_not_called()
    assert nat.running_ports() == reserved


def test_matching_ipfw_rule_number_does_not_claim_custom_nat_config(
    active_jails, monkeypatch
):
    active_jails['ioc-jail'] = {'jid': 1}
    routes = ipfw_routes('172.16.0.0/24')
    monkeypatch.setattr(nat, '_ipfw_rules', lambda: (routes, set()))
    monkeypatch.setattr(nat, '_ipfw_config', lambda:
                        nat.IPFW_NAT + ['if', 'em0', 'same_ports', 'deny_in'])
    run = mock.Mock()
    monkeypatch.setattr(nat, '_run', run)
    with pytest.raises(ValueError, match='configuration outside iocage'):
        nat.apply('ioc-jail', '172.16.0.2', 'em0', 'none', 'ipfw')
    run.assert_not_called()


@pytest.mark.parametrize('known_subnet', [False, True])
def test_ipfw_preserves_unowned_redirect_on_matching_routes(
    active_jails, monkeypatch, known_subnet
):
    if known_subnet:
        active_jails['ioc-peer'] = {'jid': 1}
        (nat.RUNTIME / 'ipfw.json').write_text(json.dumps({'ioc-peer': {
            'jid': '1', 'address': '192.0.2.7', 'interface': 'em0',
            'forwards': 'none'
        }}))
    active_jails['ioc-new'] = {'jid': 2}
    routes = ipfw_routes('192.0.2.0/24')
    monkeypatch.setattr(nat, '_ipfw_rules', lambda: (routes, set()))
    monkeypatch.setattr(nat, '_ipfw_config', lambda: nat.IPFW_NAT + [
        'if', 'em0', 'same_ports', 'redirect_port', 'tcp', '192.0.2.50:80',
        '8050'
    ])
    run = mock.Mock()
    monkeypatch.setattr(nat, '_run', run)
    with pytest.raises(ValueError, match='outside iocage'):
        nat.apply('ioc-new', '192.0.2.11', 'em0', 'none', 'ipfw')
    run.assert_not_called()


@pytest.mark.parametrize('logging,disabled', [
    pytest.param(True, False, id='logging-enabled'),
    pytest.param(False, True, id='rule-set-disabled'),
])
def test_ipfw_stop_preserves_peer_and_set_state(
    active_jails, monkeypatch, logging, disabled
):
    base = nat.IPFW_NAT + ['if', 'em0']
    if logging:
        base.append('log')
    base.append('same_ports')
    installed = base.copy()
    records = {}
    for jid, name in enumerate(('one', 'two'), 1):
        address = f'172.16.0.{jid}'
        port = 8080 + jid
        records[f'ioc-{name}'] = {
            'jid': str(jid), 'interface': 'em0', 'address': address,
            'forwards': f'tcp(80:{port})'
        }
        active_jails[f'ioc-{name}'] = {
            'jid': jid, 'path': f'/iocage/jails/{name}/root'
        }
        installed.extend(['redirect_port', 'tcp', f'{address}:80', str(port)])
    (nat.RUNTIME / 'ipfw.json').write_text(json.dumps(records))
    routes = ipfw_routes('172.16.0.0/24')
    disabled_sets = {1} if disabled else set()
    monkeypatch.setattr(nat, '_module_loaded', lambda m: m == 'ipfw_nat')
    monkeypatch.setattr(nat, '_ipfw_rules', lambda: (routes, disabled_sets))
    monkeypatch.setattr(nat, '_ipfw_config', lambda: installed.copy())
    run = mock.Mock()
    monkeypatch.setattr(nat, '_run', run)

    assert nat.running_ports() == {8081, 8082}
    assert nat.running_jails() == ['one', 'two']
    nat.prepare_stop('ioc-one', 1)
    run.assert_not_called()
    with nat.removing('ioc-one', 1):
        del active_jails['ioc-one']
    survivor = base + ['redirect_port', 'tcp', '172.16.0.2:80', '8082']
    run.assert_called_once_with(survivor)
    installed[:] = survivor
    assert nat.running_ports() == {8082}

    disabled_sets.clear()
    with nat.removing('ioc-two', 2):
        del active_jails['ioc-two']
    run.assert_any_call(['ipfw', '-q', 'nat', '462', 'delete'])
    assert nat.running_ports() == set()


def test_last_ipfw_jail_can_restart_with_shared_inbound_rule(
    active_jails, monkeypatch
):
    monkeypatch.setattr(nat, '_module_loaded', lambda module: True)
    state = nat.RUNTIME / 'ipfw.json'
    state.write_text(json.dumps({'ioc-peer': {
        'jid': '1', 'address': '192.0.2.7', 'interface': 'em0',
        'forwards': 'tcp(80:8080)'
    }}))
    active_jails['ioc-peer'] = {'jid': 1}
    routes = ipfw_routes('192.0.2.0/24')
    inbound = routes[1, 101][0]
    host_rule = 'allow ip from 192.0.2.55 to any'
    routes[1, 101].append(host_rule)
    config = nat.IPFW_NAT + ['if', 'em0', 'same_ports']
    forward = ['redirect_port', 'tcp', '192.0.2.7:80', '8080']
    installed = config + forward
    monkeypatch.setattr(nat, '_ipfw_rules', lambda: (routes, set()))
    monkeypatch.setattr(nat, '_ipfw_config', lambda: installed.copy())

    def run(args):
        if args[:4] == nat.IPFW_NAT:
            installed[:] = args
        elif 'delete' in args:
            assert args[-1] == '100'
            del routes[1, 100]
        elif args == ['ipfw', 'set', 'show']:
            return 'enable 0 1 2'

    monkeypatch.setattr(nat, '_run', run)
    with nat.removing('ioc-peer', 1):
        del active_jails['ioc-peer']
    assert installed == config
    assert routes == {(1, 101): [inbound, host_rule]}
    assert nat.running_ports() == set()
    active_jails['ioc-peer'] = {'jid': 2}
    nat.apply('ioc-peer', '192.0.2.7', 'em0', 'tcp(80:8080)', 'ipfw')
    assert installed == config + forward
    assert routes[1, 101] == [inbound, host_rule]
    assert set(json.loads(state.read_text())) == {'ioc-peer'}


@pytest.mark.parametrize('hooks', [[], nat.PF_HOOKS])
@pytest.mark.parametrize('ingress,destination', [
    ('on em0 ', '(em0:0)'), ('on em0 ', '198.51.100.1'), ('', '198.51.100.1')
])
def test_unowned_root_redirect_blocks_overlapping_pf_forward(
    active_jails, monkeypatch, hooks, ingress, destination
):
    active_jails['ioc-new'] = {'jid': 2, 'ipv4_addrs': ['172.16.0.2']}
    monkeypatch.setattr(common.netifaces, 'ifaddresses', lambda iface: {
        common.netifaces.AF_INET: [{'addr': '198.51.100.1'}]
    })
    orphan = (f'rdr pass {ingress}inet proto tcp from any to {destination} '
              'port 8090:8092 -> 172.16.0.2 port 80:82')
    run = mock.Mock(return_value='\n'.join([orphan, *hooks]))
    monkeypatch.setattr(nat, '_run', run)
    with pytest.raises(ValueError, match='8090:8092 on em0'):
        nat.apply('ioc-new', '172.16.0.2', 'em0', 'tcp(80-81:8092-8093)', 'pf')
    run.assert_called_once_with(nat.PF_LIST)
    assert nat.running_ports() == set()


def test_pf_literal_redirect_on_another_address_keeps_its_port(monkeypatch):
    monkeypatch.setattr(common.netifaces, 'ifaddresses', lambda iface: {
        common.netifaces.AF_INET: [{'addr': '198.51.100.1'},
                                   {'addr': '198.51.100.2'}]
    })
    assert not nat._pf_destination_matches('em0', '198.51.100.2')


@pytest.mark.parametrize('selector,members,conflict', [
    ('wan', 'em0\nem1', True), ('wan', 'em1', False),
    ('! wan', 'em0\nem1', False), ('! wan', 'em1', True),
])
def test_pf_redirect_on_interface_group(monkeypatch, selector, members,
                                        conflict):
    run = mock.Mock(return_value=members)
    monkeypatch.setattr(nat, '_run', run)
    rule = (f'rdr pass on {selector} inet proto udp from any to (em0:0) '
            'port = 8080 -> 192.0.2.7 port 80')
    forwards = [('em0', 'udp', range(8080, 8081))]
    if conflict:
        with pytest.raises(ValueError, match='already uses udp port 8080'):
            nat._check_pf_redirect(rule, forwards)
    else:
        nat._check_pf_redirect(rule, forwards)
    run.assert_called_once_with(['ifconfig', '-g', 'wan'])


@pytest.mark.parametrize('child_port,conflict', [(8080, True), (8081, False)])
def test_pf_redirect_in_scoped_child_anchor(monkeypatch, child_port, conflict):
    # The root hook covers only the first of the two requested ports.
    root = ('rdr-anchor "host" on em0 inet proto udp from any to (em0:0) '
            'port = 8080')
    commands = {
        ('pfctl', '-a', 'host', '-P', '-sn'): 'rdr-anchor "services/*" all',
        ('pfctl', '-a', 'host/services', '-s', 'Anchors'):
            'host/services/web\nhost/services/web/deeper',
        ('pfctl', '-a', 'host/services/web', '-P', '-sn'):
            f'rdr pass inet proto udp from any to (em0:0) port = {child_port} '
            '-> 192.0.2.7 port 80',
    }
    monkeypatch.setattr(nat, '_run', lambda args: commands[tuple(args)])
    forwards = [('em0', 'udp', range(8080, 8082))]
    if conflict:
        with pytest.raises(ValueError, match='already uses udp port 8080'):
            nat._check_pf_redirect(root, forwards)
    else:
        nat._check_pf_redirect(root, forwards)


@pytest.mark.parametrize('selector', [
    'on em0 inet proto udp from any to (em0:0)',
    'log on em0 inet proto udp from any to (em0:0)',
    'log (all, to pflog1) on em0 inet proto udp from any to (em0:0)',
    'on em0 inet from any to (em0:0)', 'on em0 inet all'
])
def test_pf_root_redirect_without_ports_blocks_matching_forward(
    active_jails, monkeypatch, selector
):
    active_jails['ioc-new'] = {'jid': 2}
    root = f'rdr pass {selector} -> 192.0.2.11'
    run = mock.Mock(return_value=root)
    monkeypatch.setattr(nat, '_run', run)
    with pytest.raises(ValueError, match='PF redirect already uses udp'):
        nat.apply('ioc-new', '192.0.2.7', 'em0', 'udp(24511:24510)', 'pf')
    run.assert_called_once_with(nat.PF_LIST)
    assert nat.running_ports() == set()


@pytest.mark.parametrize('status', [0, 2, 255])
def test_pf_table_uses_primary_ingress_address(
    active_jails, monkeypatch, status
):
    active_jails['ioc-new'] = {'jid': 2}
    root = ('rdr pass on em0 inet proto udp from any to <public_ips> '
            '-> 192.0.2.11')
    lookup = ['pfctl', '-t', 'public_ips', '-T', 'test', '198.51.100.1']
    monkeypatch.setattr(nat.netifaces, 'ifaddresses', lambda iface: {
        nat.netifaces.AF_INET: [{'addr': '198.51.100.1'}, {'addr': '192.0.2.3'}]
    })

    def pf(args, stdin=None):
        if args == nat.PF_LIST:
            return root
        if args == lookup and status:
            raise subprocess.CalledProcessError(status, args)
        return ''

    run = mock.Mock(side_effect=pf)
    monkeypatch.setattr(nat, '_run', run)
    if status == 2:
        nat.apply('ioc-new', '192.0.2.7', 'em0', 'udp(24511:24510)', 'pf')
        run.assert_any_call(lookup)
        assert nat.running_ports() == {24510}
    else:
        with pytest.raises(ValueError, match='<public_ips>'):
            nat.apply('ioc-new', '192.0.2.7', 'em0', 'udp(24511:24510)', 'pf')
        assert run.call_args_list == [mock.call(nat.PF_LIST), mock.call(lookup)]
        assert not nat.running_ports()


def test_global_nat_changes_check_the_running_instance(active_jails):
    active_jails['ioc-db_jail'] = {
        'jid': 1, 'path': '/mnt/iocage/jails/db.jail/root'
    }
    # A thick jail can still use the previously configured backend.
    active_jails['ioc-next'] = {'jid': 2}
    (nat.RUNTIME / 'ipfw.json').write_text(json.dumps({
        'ioc-db_jail': {
            'jid': '1', 'address': '172.16.0.2', 'forwards': 'tcp(80:9000)',
            'interface': 'em0'
        }
    }))
    assert nat.running_ports() == {9000}
    with pytest.raises(ValueError, match='NAT forwarding ports conflict'):
        nat.apply('ioc-next', '172.16.0.6', 'em0', 'tcp(81:9000)', 'pf')
    config = object.__new__(IOCJson)
    with pytest.raises(RuntimeError, match='db\\.jail'):
        config.json_check_prop('nat_backend', 'ipfw', {'nat_backend': 'pf'},
                               default=True)


def test_state_directory_rejects_group_write_access(active_jails):
    nat.RUNTIME.chmod(0o770)
    with pytest.raises(PermissionError, match='Unsafe NAT state directory'):
        nat.running_ports()
    assert list(nat.RUNTIME.iterdir()) == []


@pytest.mark.parametrize('mask', [0o027, 0o077])
def test_state_snapshots_remain_readable_with_restrictive_umask(
    active_jails, monkeypatch, mask
):
    monkeypatch.setattr(nat, 'RUNTIME', nat.RUNTIME / 'nat')
    monkeypatch.setattr(nat, '_run', mock.Mock(return_value=''))
    active_jails['ioc-web'] = {'jid': 1}
    before = os.umask(mask)
    try:
        nat.apply('ioc-web', '172.16.0.2', 'em0', 'tcp(80:8080)', 'pf')
    finally:
        os.umask(before)
    assert nat.RUNTIME.stat().st_mode & 0o777 == 0o711
    assert (nat.RUNTIME / 'pf.json').stat().st_mode & 0o777 == 0o644


@pytest.fixture
def pf_policy(monkeypatch):
    rules = {'root': '', 'anchor': ''}

    def pfctl(args, stdin=None):
        target = 'anchor' if '-a' in args else 'root'
        if '-sn' in args:
            assert '-P' in args
            return rules[target]
        if '-ss' in args:
            return ''
        assert args[-3:] == ['-N', '-f', '-']
        rules[target] = stdin
        return ''

    monkeypatch.setattr(nat, '_run', pfctl)
    monkeypatch.setattr(nat, '_module_loaded', lambda name: name == 'pf')
    return rules


def test_outbound_only_jail_blocks_global_nat_changes(active_jails, pf_policy):
    active_jails['ioc-web'] = {'jid': 1, 'path': '/iocage/jails/web/root'}
    nat.apply('ioc-web', '172.16.0.2', 'em0', 'none', 'pf')

    assert nat.running_ports() == set()
    with pytest.raises(RuntimeError, match='web'):
        object.__new__(IOCJson).json_check_prop(
            'nat_backend', 'ipfw', {'nat_backend': 'pf'}, default=True
        )


def test_port_reservations_belong_to_the_jid(active_jails, pf_policy):
    active_jails['ioc-web'] = {'jid': 1}
    nat.apply('ioc-web', '172.16.0.2', 'em0', 'tcp(80:443)', 'pf')
    assert nat.running_ports() == {443}

    active_jails['ioc-web']['jid'] = 2
    assert nat.running_ports() == set()


def test_pf_start_leaves_host_owned_anchor_intact(active_jails, pf_policy):
    pf_policy['root'] = 'rdr-anchor "iocage-nat" on em0 inet all'
    pf_policy['anchor'] = ('rdr pass on em0 inet proto tcp from any to '
                           '(em0:0) port = 8000 -> 192.0.2.3 port 80')
    before = pf_policy.copy()
    active_jails['ioc-web'] = {'jid': 1}

    with pytest.raises(ValueError, match='anchor iocage-nat.*outside iocage'):
        nat.apply('ioc-web', '172.16.0.2', 'em0', 'tcp(80:443)', 'pf')

    assert pf_policy == before
    assert nat.running_ports() == set()


def test_pf_failed_stop_restores_its_forward_and_preserves_peer(
    active_jails, pf_policy
):
    active_jails['ioc-web'] = {'jid': 1, 'ipv4_addrs': ['172.16.0.2']}
    active_jails['ioc-peer'] = {'jid': 2, 'ipv4_addrs': ['172.16.1.2']}
    nat.apply('ioc-web', '172.16.0.2', 'em0', 'tcp(80:443)', 'pf')
    nat.apply('ioc-peer', '172.16.1.2', 'em1', 'tcp(80:9000)', 'pf')
    before = pf_policy.copy()

    with pytest.raises(RuntimeError, match='jail removal failed'):
        with nat.removing('ioc-web', 1):
            assert 'port = 443' not in pf_policy['anchor']
            assert 'port = 9000' in pf_policy['anchor']
            raise RuntimeError('jail removal failed')

    assert pf_policy == before
    assert nat.running_ports() == {443, 9000}


@pytest.mark.parametrize('can_read', [False, True])
def test_failed_teardown_keeps_forwards_out_when_vnet_address_is_gone(
    active_jails, monkeypatch, can_read
):
    active_jails['ioc-peer'] = {'jid': 7, 'ipv4_addrs': []}
    path = nat.RUNTIME / 'pf.json'
    path.write_text(json.dumps({'ioc-peer': {
        'jid': '7', 'address': '172.16.0.2', 'interface': 'em0',
        'forwards': 'tcp(80:8080)'
    }}))
    monkeypatch.setattr(nat, '_module_loaded', lambda module: True)
    firewall = mock.Mock()
    monkeypatch.setattr(nat, '_apply_pf', firewall)

    def run(args):
        if args[0] == 'pfctl':
            return ''
        if args == ['jls', '-j', '7', 'vnet']:
            return 'new'
        assert args == ['ifconfig', '-j', '7', '-a', 'inet']
        if not can_read:
            raise subprocess.CalledProcessError(1, args)
        return '    inet 127.0.0.1 netmask 0xff000000\n'

    monkeypatch.setattr(nat, '_run', run)
    with pytest.raises(RuntimeError, match='jail removal failed'):
        with nat.removing('ioc-peer', 7):
            raise RuntimeError('jail removal failed')
    assert json.loads(path.read_text()) == {}
    firewall.assert_called_once()


def test_pf_stop_kills_only_translations_for_the_withdrawn_address(
    active_jails, monkeypatch
):
    active_jails['ioc-peer'] = {'jid': 7, 'ipv4_addrs': ['192.0.2.7']}
    (nat.RUNTIME / 'pf.json').write_text(json.dumps({'ioc-peer': {
        'jid': '7', 'address': '192.0.2.7', 'interface': 'em0',
        'forwards': 'udp(18444:18443)'
    }}))
    # pfctl prints the two forms of a translated endpoint together.
    states = [
        '192.0.2.7:18444 (198.51.100.1:18443) <- 198.51.100.2:20429',
        '198.51.100.1:4000 (192.0.2.7:4000) -> 198.51.100.2:53',
        '192.0.2.70:18444 (198.51.100.1:18443) <- 192.0.2.7:20429',
        '192.0.2.7:1000 -> 192.0.2.8:53'
    ]
    listing = ''.join(f'all udp {line}  MULTIPLE:MULTIPLE\n'
                      f'   age 00:00:01\n   id: {n:016x} creatorid: 1234abcd\n'
                      for n, line in enumerate(states, 1))
    monkeypatch.setattr(nat, '_module_loaded', lambda module: True)
    run = mock.Mock(side_effect=lambda args, stdin=None:
                    listing if '-ss' in args else '')
    monkeypatch.setattr(nat, '_run', run)
    with nat.removing('ioc-peer', 7):
        killed = [call.args[0][-1] for call in run.call_args_list
                  if call.args[0][:3] == ['pfctl', '-k', 'id']]
        assert killed == [f'{n:016x}/1234abcd' for n in (1, 2)]
        # A final outbound packet used the peer's subnet rule during teardown.
        listing = (f'all udp {states[1]}  SINGLE:NO_TRAFFIC\n'
                   '   id: 0000000000000005 creatorid: 1234abcd\n')
        del active_jails['ioc-peer']
        run.reset_mock()
    run.assert_has_calls([
        mock.call(['pfctl', '-P', '-vv', '-ss']),
        mock.call(['pfctl', '-k', 'id', '-k', '0000000000000005/1234abcd'])
    ])


@pytest.mark.parametrize('loaded', [False, True])
@pytest.mark.parametrize('backend', ['pf', 'ipfw'])
def test_stop_checks_the_firewall_only_while_loaded(
    active_jails, monkeypatch, loaded, backend
):
    active_jails['ioc-peer'] = {'jid': 1}
    path = nat.RUNTIME / f'{backend}.json'
    saved = {'jid': '1', 'address': '172.16.0.2', 'interface': 'em0',
             'forwards': 'tcp(80:8080)'}
    other = dict(saved, jid='2', address='172.16.0.6', forwards='none')
    active_jails['ioc-other'] = {'jid': 2}
    path.write_text(json.dumps({'ioc-peer': saved, 'ioc-other': other}))
    monkeypatch.setattr(nat, '_module_loaded',
                        lambda m: m == nat.BACKENDS[backend] and loaded)
    failure = subprocess.CalledProcessError(1, backend, stderr='read failed')
    run = mock.Mock(side_effect=failure)
    monkeypatch.setattr(nat, '_run', run)
    if loaded:
        with pytest.raises(subprocess.CalledProcessError):
            with nat.removing('ioc-peer', 1):
                pytest.fail('Live firewall error should stop teardown')
        assert 'ioc-peer' in json.loads(path.read_text())
    else:
        nat.prepare_stop('ioc-peer', 1)
        with nat.removing('ioc-peer', 1):
            assert json.loads(path.read_text()) == {'ioc-other': other}
        run.assert_not_called()


def test_ipfw_uses_enabled_routes_and_rejects_mixed_interfaces(
    active_jails, monkeypatch
):
    active_jails['ioc-peer'] = {'jid': 1}
    routes = ('# DISABLED 00100 set 5 nat 462 ip4 from 172.16.0.0/24 to '
              'any out via em0 // iocage\n'
              '# DISABLED 00101 set 5 nat 462 ip4 from any to any in '
              'via em0 // iocage')

    def run(args, stdin=None):
        if args == ['ipfw', '-S', 'list']:
            return routes
        if args == ['ipfw', 'set', 'show']:
            return 'disable 5 enable 0 1 2'
        return ''

    commands = mock.Mock(side_effect=run)
    monkeypatch.setattr(nat, '_run', commands)
    nat.apply('ioc-peer', '172.16.0.2', 'em0', 'tcp(80:9000)', 'ipfw')
    enabled = [call.args[0][-1] for call in commands.call_args_list
               if call.args[0][:3] == ['ipfw', 'set', 'enable']]
    assert enabled == ['2']
    active_jails['ioc-next'] = {'jid': 2}
    commands.reset_mock()
    with pytest.raises(ValueError, match='same external interface'):
        nat.apply('ioc-next', '172.16.1.2', 'em1', 'none', 'ipfw')
    commands.assert_not_called()
    assert nat.running_ports() == {9000}


def test_plugin_list_uses_applied_ports_and_reports_invalid_forwards_per_jail(
    monkeypatch, tmp_path, active_jails
):
    active_jails['ioc-active'] = {'jid': 4}
    (nat.RUNTIME / 'pf.json').write_text(json.dumps({'ioc-active': {
        'jid': '4', 'address': '172.16.0.2', 'interface': 'em0',
        'forwards': 'tcp(80:19001)'
    }}))
    configs = {}
    jails = []
    for name, forwards in [('invalid', 'tcp(80:8080),tcp(81:8080)'),
                           ('active', 'tcp(80:19002)'),
                           ('stopped', 'tcp(80:8081)')]:
        path = tmp_path / name
        (path / 'plugin').mkdir(parents=True)
        (path / 'plugin/ui.json').write_text(json.dumps(
            {'adminportal': 'http://%%IP%%', 'docurl': 'docs'}))
        configs[str(path)] = dict(host_hostuuid=name, release='15.1-RELEASE',
                                  type='pluginv2', nat=name != 'active',
                                  ip4_addr='none', ip6_addr='none', boot='off',
                                  nat_forwards=forwards)
        jails.append(SimpleNamespace(name=name,
                                     properties={'mountpoint': str(path)}))
    monkeypatch.setattr(ioc_list, 'Dataset',
                        lambda name: mock.Mock(exists=False))
    monkeypatch.setattr(ioc_list.iocage_lib.ioc_json, 'IOCJson', lambda path:
                        mock.Mock(json_get_value=lambda key: configs[path]))
    monkeypatch.setattr(common, 'get_host_gateways', lambda:
                        {'ipv4': {'interface': 'em0'}})
    monkeypatch.setattr(common, 'retrieve_ip4_for_jail', lambda *args:
                        {'full_ip4': None, 'short_ip4': None})
    monkeypatch.setattr(common.netifaces, 'interfaces', lambda: ['em0'])
    monkeypatch.setattr(common.netifaces, 'ifaddresses', lambda name: {
        common.netifaces.AF_INET: [{'addr': '192.0.2.1'}]
    })
    listing = object.__new__(ioc_list.IOCList)
    listing.full = listing.plugin = True
    listing.header = listing.basejail_only = listing.plugin_data = False
    listing.sort = 'name'
    rows = {row[1]: row for row in listing.list_all(jails)}
    assert rows['active'][-2] == 'http://192.0.2.1:19001'
    assert rows['stopped'][-2] == 'http://192.0.2.1:8081'
    assert 'Overlapping NAT forwarding ports' in rows['invalid'][-2]
    assert rows['invalid'][-1] == 'docs'
