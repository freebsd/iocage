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

"""Coordinate NAT updates for jails that share the host firewall.

PF jails share one iocage anchor. IPFW jails share one NAT instance.
Starting a jail must preserve its peers' forwards. Stopping it must withdraw
its forwards before its address can be reused. This module holds the same
file lock for both updates and for any rollback.

For each backend, iocage records the kernel jail name, jail ID (JID), IPv4
address, interface, and forwards it installed. Port checks and plugin listings
use these records because a jail's configuration can change before it restarts.
"""
import collections
import contextlib
import fcntl
import ipaddress
import json
import logging
import os
import re
import stat
import subprocess as su

from pathlib import Path

import netifaces

import iocage_lib.ioc_common as common

# Each backend has a root-owned record of the rules installed for each JID.
RUNTIME = Path('/var/run/iocage_nat')
BACKENDS = {'ipfw': 'ipfw_nat', 'pf': 'pf'}


def apply(name, configured_address, interface, forwards, backend):
    """Install this jail's mappings alongside those of its running peers."""
    first = configured_address.split(',', 1)[0].rsplit('|', 1)[-1]
    address = str(ipaddress.IPv4Interface(first).ip)
    if backend not in BACKENDS:
        raise ValueError(f'Invalid NAT backend: {backend}')

    with _lock():
        active = common.get_active_jails()
        # The saved records identify our installed rules. For the next update,
        # keep the live peers and replace this jail's own entry.
        saved = {kind: _read_records(kind) for kind in BACKENDS}
        applied = {kind: _live(records, active)
                   for kind, records in saved.items()}
        records = applied[backend]
        records.pop(name, None)

        if not forward_ports(forwards).isdisjoint(_occupied(applied)):
            raise ValueError(f'{name}: NAT forwarding ports conflict')
        records[name] = {'jid': str(active[name]['jid']), 'address': address,
                         'forwards': forwards, 'interface': interface}

        with contextlib.ExitStack() as undo:
            _replace(backend, saved[backend], records, undo)
            undo.pop_all()


def prepare_stop(name, jid):
    """Check firewall ownership before shutdown hooks can change the jail.

    With undo=None the backend only validates the update. removing() checks
    again under the lock when it actually withdraws the forwards.
    """
    with _lock():
        active = common.get_active_jails()
        for backend, module in BACKENDS.items():
            saved = _read_records(backend)
            remaining = _live(saved, active)
            record = remaining.pop(name, None)
            if record and record['jid'] == str(jid) and _module_loaded(module):
                apply_rules = _apply_ipfw if backend == 'ipfw' else _apply_pf
                apply_rules(saved, remaining, undo=None, stopping=True)


@contextlib.contextmanager
def removing(name, jid):
    """Withdraw this JID's forwards, then yield to jail teardown.

    For a NAT jail the lock stays held through teardown. If teardown fails,
    restoring the old forwards is safe only while this JID owns the address.
    """
    with _lock(), contextlib.ExitStack() as undo:
        active = common.get_active_jails()
        addresses = set()
        pf_address = None

        for backend in BACKENDS:
            saved = _read_records(backend)
            records = _live(saved, active)
            if name in records and records[name]['jid'] == str(jid):
                address = records.pop(name)['address']
                addresses.add(address)
                _replace(backend, saved, records, undo,
                         removing_address=address)
                if backend == 'pf':
                    pf_address = address

        if addresses:
            restore = False
            try:
                yield
            except BaseException:
                # Teardown may have destroyed the epair before jail -r failed.
                restore = _jail_owns_addresses(name, jid, addresses)
                raise
            finally:
                if not restore:
                    undo.pop_all()
                    # A peer's subnet rule could translate traffic between
                    # the first sweep and the jail releasing its address.
                    if pf_address and _module_loaded('pf'):
                        _clear_pf_states(pf_address)
            return

    # Ordinary stops can proceed concurrently outside the firewall lock.
    yield


def forward_ports(forwards):
    """Return the host ports requested by a nat_forwards setting."""
    return {port for _, _, host in common.parse_nat_forwards(forwards)
            for port in host}


def running_ports():
    """Return the host ports reserved by running NAT jails."""
    with _lock():
        return _occupied(_live_state(common.get_active_jails()))


def running_jails():
    """Return running NAT jail names, including jails with no port forwards."""
    with _lock():
        active = common.get_active_jails()
        state = _live_state(active)
        return sorted({Path(active[name]['path']).parent.name
                       for records in state.values() for name in records})


def portal_records(active):
    """Plugin listings can read the published port mappings without root."""
    result = {}
    for backend in BACKENDS:
        try:
            records = _read_records(backend)
        except PermissionError:
            if os.geteuid() == 0:
                raise
            # Keep readable ports. Only a NAT jail with unknown settings
            # needs to report a permission error in the listing.
            for name in active:
                result.setdefault(name, None)
        else:
            result.update(_live(records, active))
    return result


def _replace(backend, previous, records, undo, *, removing_address=None):
    """Update the firewall before publishing the records that describe it.

    Register undo actions for both. The caller discards them on success.
    """
    path = RUNTIME / f'{backend}.json'
    # Stop only needs the snapshot when the kernel backend is gone.
    stopping = removing_address is not None
    installed = not stopping or _module_loaded(BACKENDS[backend])
    if installed:
        apply_rules = _apply_ipfw if backend == 'ipfw' else _apply_pf
        apply_rules(previous, records, undo, stopping=stopping)

    _save_records(path, records)
    undo.callback(_save_records, path, previous)
    if installed and backend == 'pf' and stopping:
        _clear_pf_states(removing_address)


@contextlib.contextmanager
def _lock():
    RUNTIME.mkdir(mode=0o711, exist_ok=True)
    info = RUNTIME.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022):
        raise PermissionError(f'Unsafe NAT state directory: {RUNTIME}')
    # Plugin listings can read the published forwards. Only root can write.
    if stat.S_IMODE(info.st_mode) != 0o711:
        RUNTIME.chmod(0o711)
    with (RUNTIME / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _read_records(backend):
    """Read the jail settings last applied to this backend."""
    try:
        with (RUNTIME / f'{backend}.json').open() as state:
            return json.load(state)
    except FileNotFoundError:
        return {}


def _save_records(path, records):
    with common.tempfile(dir=RUNTIME) as state:
        Path(state).write_text(json.dumps(records))
        os.chmod(state, 0o644)
        os.replace(state, path)


def _live(records, active):
    return {name: record for name, record in records.items()
            if name in active and str(active[name]['jid']) == record['jid']}


def _live_state(active):
    return {backend: _live(_read_records(backend), active)
            for backend in BACKENDS}


def _occupied(state):
    ports = set()
    for records in state.values():
        for record in records.values():
            ports.update(forward_ports(record['forwards']))
    return ports


def _jail_owns_addresses(name, jid, addresses):
    """Check whether it is safe to restore forwards after a failed stop."""
    jid = str(jid)
    try:
        jail = common.get_active_jails().get(name)
        if not jail or str(jail['jid']) != jid:
            return False
        assigned = jail.get('ipv4_addrs') or []
        if not assigned and _run(['jls', '-j', jid, 'vnet']).strip() == 'new':
            # jls omits VNET addresses. Query that jail's network stack.
            output = _run(['ifconfig', '-j', jid, '-a', 'inet'])
            assigned = re.findall(r'^\s*inet ([0-9.]+) ', output, re.MULTILINE)
        return addresses.issubset(assigned)
    except (OSError, su.SubprocessError, KeyError, ValueError):
        # Keep the forwards withdrawn if we cannot verify ownership.
        return False


def _module_loaded(module):
    status = su.run(['kldstat', '-q', '-m', module])
    if status.returncode == 1:
        return False
    status.check_returncode()
    return True


def _run(args, stdin=None):
    return su.run(args, input=stdin, capture_output=True, text=True,
                  check=True).stdout


def _restore(args, stdin=None):
    # Report a rollback problem without hiding the error that triggered it.
    try:
        _run(args, stdin)
    except (OSError, su.CalledProcessError) as error:
        detail = getattr(error, 'stderr', '') or error
        logging.getLogger('iocage').error('Could not restore firewall: %s',
                                          detail)


def _translations(records):
    networks = set()
    redirects = []
    for record in records.values():
        address = record['address']
        interface = record['interface']
        network = ipaddress.IPv4Network(f'{address}/24', strict=False)
        networks.add((interface, network))
        for proto, jail, host in common.parse_nat_forwards(record['forwards']):
            redirects.append((interface, address, proto, jail, host))
    return sorted(networks), redirects


def _port_text(ports, separator='-'):
    if len(ports) == 1:
        return str(ports.start)
    return f'{ports.start}{separator}{ports.stop - 1}'


PF_LIST = ['pfctl', '-P', '-sn']
PF_ANCHOR = 'iocage-nat'
PF_HOOKS = [f'{kind}-anchor "{PF_ANCHOR}" all' for kind in ('nat', 'rdr')]
# pfctl -P -sn lists the interface and protocol before the destination.
PF_RDR_SCOPE = re.compile(r'rdr(?:-anchor "([^"]+)"|'
                          r'(?: pass)?(?: log(?: \([^)]*\))?)?)'
                          r'(?: on (! ?)?(\S+))?(?: (inet6?))?'
                          r'(?: proto (\S+))? ')
PF_RDR_TARGET = re.compile(r'(?:from .+? to (! ?)?(\S+)|all)'
                           r'(?: port (?:= )?(\d+(?::\d+)?))?(?: -> |$)')


def _apply_pf(previous, records, undo, *, stopping=False):
    """Check host redirects and our anchor before replacing its rules."""
    root = _run(PF_LIST)
    lines = root.splitlines()
    anchor = ['pfctl', '-a', PF_ANCHOR]
    managed = all(hook in lines for hook in PF_HOOKS)
    if not stopping:
        changed = {name: record for name, record in records.items()
                   if record != previous.get(name)}
        _, translations = _translations(changed)
        redirects = [(iface, proto, host) for iface, _, proto, _, host
                     in translations]
        for line in lines:
            _check_pf_redirect(line, redirects)

    before = _run(anchor + ['-P', '-sn'])
    expected = set(_pf_rules(previous)) if managed else set()
    for line in before.strip().splitlines():
        rule = line.replace(' round-robin static-port', ' static-port')
        if rule not in expected:
            raise ValueError(f'PF anchor {PF_ANCHOR} contains a rule '
                             f'outside iocage: {line}')
    if undo is None:
        return

    _load_pf(anchor, _pf_rules(records), before, undo)
    if not managed:
        missing = [hook for hook in PF_HOOKS if hook not in lines]
        _load_pf(['pfctl'], lines + missing, root, undo)


def _pf_rules(records):
    networks, redirects = _translations(records)
    rules = [f'nat on {iface} inet from {net} to any -> ({iface}:0) static-port'
             for iface, net in networks]
    for iface, address, proto, jail, host in redirects:
        # Use PF's numeric listing format to compare the installed rules.
        operator = '= ' if len(host) == 1 else ''
        rules.append(f'rdr pass on {iface} inet proto {proto} from any '
                     f'to ({iface}:0) port {operator}{_port_text(host, ":")} '
                     f'-> {address} port {_port_text(jail, ":")}')
    return rules


def _check_pf_redirect(line, redirects, parents=('',)):
    """Follow matching rdr anchors and check the forwards they can receive."""
    line = line.removesuffix(' {')
    scope = PF_RDR_SCOPE.match(line)
    if not scope or not redirects:
        return
    child, negated_interface, interface, family, protocol = scope.groups()
    if family == 'inet6':
        return
    anchor = parents[-1]  # The empty name denotes the root ruleset.
    if child == PF_ANCHOR and not anchor:
        return  # Our anchor is checked separately before it is replaced.
    target = PF_RDR_TARGET.match(line, scope.end())
    first, last = 1, 65535
    if target:
        negated_destination, destination, ports = target.groups()
        # Without a destination port selector the rule covers every port.
        ports = ports or '1:65535'
        bounds = list(map(int, ports.split(':')))
        first, last = bounds[0], bounds[-1]
    selected = []
    for iface, proto, host in redirects:
        overlap = range(max(first, host.start), min(last + 1, host.stop))
        if not overlap or protocol not in (None, proto):
            continue
        if interface:
            matches = (iface == interface or
                       iface in _run(['ifconfig', '-g', interface]).split())
            # A leading "!" reverses an interface or destination match.
            if matches == bool(negated_interface):
                continue
        if not target:
            raise ValueError(f'Cannot check PF redirect: {line}')
        try:
            matches = _pf_destination_matches(iface, destination or 'any',
                                              anchor)
        except (ValueError, su.CalledProcessError) as error:
            raise ValueError(f'Cannot check PF redirect: {line}') from error
        if matches == bool(negated_destination):
            continue
        if not child:
            raise ValueError(f'PF redirect already uses {proto} port {ports} '
                             f'on {iface}: {line}')
        selected.append((iface, proto, overlap))
    if not selected:
        return
    # A child anchor sees only ports matched by every parent on this path.
    for path in _pf_child_anchors(anchor, child):
        if path == PF_ANCHOR:
            continue
        if path in parents:
            raise ValueError(f'Cannot check recursive PF anchor: {path}')
        for rule in _run(['pfctl', '-a', path, '-P', '-sn']).splitlines():
            _check_pf_redirect(rule, selected, (*parents, path))


def _pf_destination_matches(interface, destination, anchor=''):
    if destination in ('any', f'({interface})', f'({interface}:0)'):
        return True
    # Our redirects use (interface:0), the primary IPv4 address.
    addrs = netifaces.ifaddresses(interface).get(netifaces.AF_INET)
    if not addrs:
        return False
    address = addrs[0]['addr']
    if destination.startswith('<') and destination.endswith('>'):
        # PF uses an anchor's private table first, then the root table.
        table = destination[1:-1]
        pfctl = ['pfctl']
        if anchor and table in _run(
            ['pfctl', '-a', anchor, '-s', 'Tables']
        ).split():
            pfctl.extend(['-a', anchor])
        try:
            _run(pfctl + ['-t', table, '-T', 'test', address])
        except su.CalledProcessError as error:
            # Status 2 means the address is excluded or absent.
            if error.returncode == 2:
                return False
            raise
        return True
    return ipaddress.IPv4Address(address) in ipaddress.IPv4Network(
        destination, strict=False)


def _pf_child_anchors(parent, target):
    path = os.path.normpath(os.path.join('/', parent, target)).lstrip('/')
    parent, _, name = path.rpartition('/')
    if name != '*':
        return [path]
    # A wildcard calls each immediate child, including dynamically loaded ones.
    pfctl = ['pfctl', '-a', parent] if parent else ['pfctl']
    return [name for name in _run(pfctl + ['-s', 'Anchors']).split()
            if name.rpartition('/')[0] == parent]


def _load_pf(pfctl, rules, before, undo):
    load = pfctl + ['-N', '-f', '-']
    _run(load, '\n'.join(rules) + '\n')
    undo.callback(_restore, load, before)


def _clear_pf_states(address):
    # PF keeps existing translations when a rule is removed. In pfctl's
    # state listing a translated endpoint is followed by its other address
    # in parentheses. Kill only those states, using their kernel IDs.
    matched = False
    for line in _run(['pfctl', '-P', '-vv', '-ss']).splitlines():
        if line and not line[0].isspace():
            pairs = re.findall(r'(?<!\S)([0-9.]+)(?::\d+)? '
                               r'\(([0-9.]+)(?::\d+)?\)', line)
            matched = any(address in pair for pair in pairs)
        elif matched:
            ident = re.match(r'\s+id: (\w+) creatorid: (\w+)', line)
            if ident:
                _run(['pfctl', '-k', 'id', '-k', f'{ident[1]}/{ident[2]}'])


IPFW_NAT = ['ipfw', 'nat', '462', 'config']
IPFW_OUT = re.compile(r'nat 462 ip4 from ([0-9.]+/24) to any out via '
                      r'(?P<interface>\S+)')
IPFW_IN = re.compile(r'nat 462 ip4 from any to any in via (?P<interface>\S+)')
IPFW_OPTIONS = re.compile(r'if (?P<interface>\S+)(?P<log> log)? same_ports'
                          r'(?: redirect_port (?:tcp|udp) [0-9.]+:[0-9-]+ '
                          r'[0-9-]+)*')


def _apply_ipfw(saved, records, undo, *, stopping=False):
    """Update NAT instance 462 and the routes that feed it.

    IPFW deletes every rule with a given set and number. Identify the groups
    iocage owns before staging their replacements in an empty set.
    """
    interface, desired, forwards = _ipfw_policy(records)
    _, _, old_forwards = _ipfw_policy(saved)
    groups, disabled = _ipfw_rules()
    owned = {}
    current = set()
    shared = set()
    route_interfaces = set()

    for (group, number), rules in groups.items():
        routes = []
        for rule in rules:
            match = _ipfw_route(number, rule)
            if match:
                routes.append((number, match[0]))
                route_interfaces.add(match['interface'])
            elif re.search(r'\bnat\s+462\b', rule.partition(' // ')[0]):
                raise ValueError(f'IPFW rule {number} in set {group} uses '
                                 'NAT instance 462 outside iocage')
        if not routes:
            continue
        if len(routes) == len(rules):
            owned[group, number] = rules
            if group not in disabled:
                current.update(routes)
        elif not records:
            # This number also belongs to the host. Leave the shared rule
            # and its NAT instance in place, with all jail forwards removed.
            shared.update(routes)
        elif group in disabled:
            continue
        elif desired.issuperset(routes):
            shared.update(routes)
        else:
            raise ValueError(f'Cannot remove obsolete IPFW rule {number} in '
                             f'set {group} without deleting an unrelated rule')

    previous = _ipfw_config()
    logging_enabled = _check_ipfw_config(previous, route_interfaces,
                                         old_forwards)
    desired -= shared
    if stopping and records and route_interfaces and not (current or shared):
        # Every route is disabled. Withdraw this jail's forwards without
        # activating its peers or moving their routes out of the disabled set.
        desired.clear()
        owned.clear()

    routing_changed = current != desired
    stage = None
    if routing_changed and desired:
        # Populate an empty, disabled set while the old routes carry traffic.
        enabled = _run(['ipfw', 'set', 'show']).partition('enable ')[2]
        occupied = {group for group, _ in groups}
        # Set 0 is the default for host rules. Set 31 holds the default rule.
        free = set(map(int, enabled.split())) - occupied - {0, 31}
        if not free:
            raise ValueError('No empty IPFW set available to stage NAT rules')
        stage = str(max(free))

    if undo is None:
        return

    if stage is not None:
        _run(['ipfw', 'set', 'disable', stage])
        undo.callback(_restore, ['ipfw', 'set', 'enable', stage])
        undo.callback(_restore, ['ipfw', '-q', 'set', stage, 'flush'])
        for number, rule in sorted(desired):
            _run(['ipfw', '-q', 'add', str(number), 'set', stage,
                  *rule.split(), '//', 'iocage'])

    delete_nat = ['ipfw', '-q', 'nat', '462', 'delete']
    if records:
        config = IPFW_NAT + ['if', interface]
        if logging_enabled:
            config.append('log')
        config.append('same_ports')
        for forward in forwards:
            config.extend(['redirect_port', *forward])
        if previous:
            undo.callback(_restore, previous)
        _run(config)
        if not previous:
            undo.callback(_restore, delete_nat)

    # Enable the replacement before removing old routes. Never enable an
    # existing disabled set, which may contain unrelated host rules.
    if stage is not None:
        _run(['ipfw', 'set', 'enable', stage])
    for (group, number), rules in owned.items():
        if routing_changed or group in disabled:
            _run(['ipfw', '-q', 'set', str(group), 'delete', str(number)])
            for rule in rules:
                undo.callback(_restore, ['ipfw', '-q', 'add', str(number),
                                         'set', str(group), *rule.split()])
    if not records and previous:
        if shared:
            if 'redirect_port' in previous:
                _run(previous[:previous.index('redirect_port')])
        else:
            _run(delete_nat)
        undo.callback(_restore, previous)


def _ipfw_policy(records):
    networks, redirects = _translations(records)
    interfaces = {iface for iface, _ in networks}
    if len(interfaces) > 1:
        raise ValueError('IPFW NAT instance 462 requires all running NAT '
                         'jails to use the same external interface')
    routes = {(100, f'nat 462 ip4 from {net} to any out via {iface}')
              for iface, net in networks}
    interface = next(iter(interfaces), None)
    if interface:
        routes.add((101, f'nat 462 ip4 from any to any in via {interface}'))
    forwards = [(proto, f'{address}:{_port_text(jail)}', _port_text(host))
                for _, address, proto, jail, host in redirects]
    return interface, routes, forwards


def _ipfw_rules():
    """Read rule groups, including disabled sets so we never stage over one."""
    groups = collections.defaultdict(list)
    disabled = set()
    for line in _run(['ipfw', '-S', 'list']).splitlines():
        fields = line.removeprefix('# DISABLED ').split(None, 3)
        number, _, group, rule = fields
        groups[int(group), int(number)].append(rule)
        if line.startswith('# DISABLED '):
            disabled.add(int(group))
    return groups, disabled


def _ipfw_route(number, rule):
    spec, _, label = rule.partition(' // ')
    pattern = {100: IPFW_OUT, 101: IPFW_IN}.get(number)
    if pattern and label == 'iocage':
        return pattern.fullmatch(spec)


def _ipfw_config():
    for line in _run(['ipfw', 'nat', 'show', 'config']).splitlines():
        words = line.split()
        if words[:4] == IPFW_NAT:
            return words
    return []


def _check_ipfw_config(config, interfaces, forwards):
    """Verify instance 462 belongs to us and return its logging setting."""
    if not config:
        return False
    settings = ' '.join(config[4:])
    options = IPFW_OPTIONS.fullmatch(settings)
    if not options or options['interface'] not in interfaces:
        raise ValueError('IPFW NAT instance 462 already has a '
                         'configuration outside iocage')
    installed = {tuple(item.split())
                 for item in settings.split(' redirect_port ')[1:]}
    unknown = installed.difference(forwards)
    if unknown:
        rules = ', '.join(' '.join(rule) for rule in sorted(unknown))
        raise ValueError(f'IPFW NAT instance 462 has redirects outside '
                         f'iocage: {rules}')
    return bool(options['log'])
