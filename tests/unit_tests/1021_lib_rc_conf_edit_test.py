# the host edits a jail's etc/rc.conf as text. It never
# sources the file (sysrc -f did, as root on the host) and never writes
# through a link left in its place. Runs on the host's own Python
# (iocage_lib imports libzfs).
import os
import stat
import subprocess

import pytest

import iocage_lib.ioc_common as ioc_common
import iocage_lib.ioc_start as ioc_start

DHCP = 'ifconfig_epair0b="SYNCDHCP"\n'
RTADV = (
    'ifconfig_epair0b_ipv6="inet6 auto_linklocal accept_rtadv autoconf"\n'
)


@pytest.fixture
def jail_root(tmp_path):
    root = tmp_path / 'root'
    (root / 'etc').mkdir(parents=True)
    return root


def rc(jail_root):
    return jail_root / 'etc' / 'rc.conf'


def edit(jail_root, **kwargs):
    return ioc_common.edit_jail_rc_conf(str(jail_root), 'rc.conf', **kwargs)


# --- edit_jail_rc_conf ------------------------------------------------------

def test_set_appends_a_variable_that_is_not_there(jail_root):
    rc(jail_root).write_text('sendmail_enable="NONE"\n')
    assert edit(jail_root, set_values={'ifconfig_epair0b': 'SYNCDHCP'}) == \
        'written'
    assert rc(jail_root).read_text() == 'sendmail_enable="NONE"\n' + DHCP


def test_set_adds_the_missing_last_newline_first(jail_root):
    rc(jail_root).write_text('sendmail_enable="NONE"')
    edit(jail_root, set_values={'ifconfig_epair0b': 'SYNCDHCP'})
    assert rc(jail_root).read_text() == 'sendmail_enable="NONE"\n' + DHCP


def test_set_leaves_a_file_that_already_says_so(jail_root):
    rc(jail_root).write_text("# c\nifconfig_epair0b='SYNCDHCP'\n")
    inode = rc(jail_root).stat().st_ino
    assert edit(jail_root, set_values={'ifconfig_epair0b': 'SYNCDHCP'}) == \
        'unchanged'
    assert rc(jail_root).stat().st_ino == inode
    assert rc(jail_root).read_text() == "# c\nifconfig_epair0b='SYNCDHCP'\n"


def test_set_replaces_the_line_that_decides_and_keeps_the_others(jail_root):
    rc(jail_root).write_text(
        'rtsold_enable="NO"\n# rtsold_enable="MAYBE"\nkeep="1"\n'
        'rtsold_enable="NO"\ntail="2"\n'
    )
    edit(jail_root, set_values={'rtsold_enable': 'YES'})
    assert rc(jail_root).read_text() == (
        'rtsold_enable="NO"\n# rtsold_enable="MAYBE"\nkeep="1"\n'
        'rtsold_enable="YES"\ntail="2"\n'
    )


def test_remove_if_removes_every_assignment_when_the_value_matches(jail_root):
    rc(jail_root).write_text(
        'a="1"\nifconfig_epair0b="DHCP"\nb="2"\n' + DHCP + 'c="3"\n'
    )
    assert edit(jail_root, remove_if={'ifconfig_epair0b': 'SYNCDHCP'}) == \
        'written'
    assert rc(jail_root).read_text() == 'a="1"\nb="2"\nc="3"\n'


def test_remove_if_leaves_another_value_alone(jail_root):
    rc(jail_root).write_text('ifconfig_epair0b="inet 10.0.0.2/24"\n')
    assert edit(jail_root, remove_if={'ifconfig_epair0b': 'SYNCDHCP'}) == \
        'unchanged'
    assert rc(jail_root).read_text() == 'ifconfig_epair0b="inet 10.0.0.2/24"\n'


def test_untouched_lines_keep_their_bytes_and_the_file_its_mode(jail_root):
    raw = b'name="caf\xe9"\n\tindented = "x" # odd\n\n'
    rc(jail_root).write_bytes(raw)
    os.chmod(rc(jail_root), 0o600)
    edit(jail_root, set_values={'rtsold_enable': 'YES'})
    assert rc(jail_root).read_bytes() == raw + b'rtsold_enable="YES"\n'
    assert stat.S_IMODE(os.lstat(rc(jail_root)).st_mode) == 0o600


def test_an_absent_file_is_created_0644_even_with_nothing_to_set(jail_root):
    assert edit(jail_root) == 'written'
    assert rc(jail_root).read_bytes() == b''
    assert stat.S_IMODE(os.lstat(rc(jail_root)).st_mode) == 0o644


def test_an_absent_file_without_create_is_file_not_found(jail_root):
    with pytest.raises(FileNotFoundError):
        edit(jail_root, set_values={'rtsold_enable': 'YES'}, create=False)
    assert not rc(jail_root).exists()


def test_a_link_to_a_host_file_is_left_alone(jail_root, tmp_path):
    victim = tmp_path / 'victim'
    victim.write_text('rtsold_enable="NO"\n')
    rc(jail_root).symlink_to(victim)
    assert edit(
        jail_root, set_values={'rtsold_enable': 'YES'},
        remove_if={'rtsold_enable': 'NO'}
    ) == 'not-a-file'
    assert victim.read_text() == 'rtsold_enable="NO"\n'
    assert rc(jail_root).is_symlink()


def test_a_dangling_link_creates_nothing_at_its_target(jail_root, tmp_path):
    target = tmp_path / 'not-there'
    rc(jail_root).symlink_to(target)
    assert edit(jail_root, set_values={'ifconfig_epair0b': 'SYNCDHCP'}) == \
        'not-a-file'
    assert not target.exists() and rc(jail_root).is_symlink()


def test_a_fifo_or_a_directory_is_left_alone(jail_root):
    os.mkfifo(rc(jail_root))
    assert edit(jail_root, set_values={'a': 'b'}) == 'not-a-file'
    os.unlink(rc(jail_root))
    rc(jail_root).mkdir()
    assert edit(jail_root, set_values={'a': 'b'}) == 'not-a-file'


def test_an_etc_that_is_a_link_is_an_oserror(tmp_path):
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    (tmp_path / 'root').mkdir()
    (tmp_path / 'root' / 'etc').symlink_to(host_etc)
    with pytest.raises(OSError):
        edit(tmp_path / 'root', set_values={'a': 'b'})
    assert os.listdir(host_etc) == []


def test_shell_in_the_file_is_text_and_does_not_run(
        jail_root, tmp_path, monkeypatch):
    marker = tmp_path / 'ran'
    rc(jail_root).write_text(
        f'x="$(/usr/bin/touch {marker})"\n`/usr/bin/touch {marker}`\n'
        f'/usr/bin/touch {marker}\n' + DHCP
    )

    def no_process(*args, **kwargs):
        raise AssertionError(f'a process was started: {args}')

    monkeypatch.setattr(subprocess, 'run', no_process)
    monkeypatch.setattr(subprocess, 'Popen', no_process)
    edit(
        jail_root, set_values={'rtsold_enable': 'YES'},
        remove_if={'ifconfig_epair0b': 'SYNCDHCP'}
    )
    assert not marker.exists()
    assert rc(jail_root).read_text() == (
        f'x="$(/usr/bin/touch {marker})"\n`/usr/bin/touch {marker}`\n'
        f'/usr/bin/touch {marker}\nrtsold_enable="YES"\n'
    )


# --- set_rcconf -------------------------------------------------------------

def test_set_rcconf_sets_and_replaces(tmp_path, jail_root):
    rc(jail_root).write_text('hostname="j"\nrtsold_enable="NO"\n')
    ioc_common.set_rcconf(str(tmp_path), 'rtsold_enable', 'YES')
    assert rc(jail_root).read_text() == 'hostname="j"\nrtsold_enable="YES"\n'
    ioc_common.set_rcconf(str(tmp_path), 'other', 'v')
    assert rc(jail_root).read_text() == (
        'hostname="j"\nrtsold_enable="YES"\nother="v"\n'
    )


def test_set_rcconf_does_not_write_through_a_link(tmp_path, jail_root):
    victim = tmp_path / 'victim'
    victim.write_text('rtsold_enable="NO"\n')
    rc(jail_root).symlink_to(victim)
    ioc_common.set_rcconf(str(tmp_path), 'rtsold_enable', 'YES')
    assert victim.read_text() == 'rtsold_enable="NO"\n'


def test_set_rcconf_still_fails_without_the_file(tmp_path, jail_root):
    with pytest.raises(FileNotFoundError):
        ioc_common.set_rcconf(str(tmp_path), 'rtsold_enable', 'YES')


# --- the start steps --------------------------------------------------------

def starter(tmp_path, dhcp=0, interfaces='vnet0:bridge0', ip4_addr='none',
            ip6_addr='none'):
    start = object.__new__(ioc_start.IOCStart)
    start.path = str(tmp_path)
    start.uuid = 'rcconf'
    start.callback = None
    start.silent = True
    start.conf = {'dhcp': dhcp}
    start.ip4_addr = ip4_addr
    start.ip6_addr = ip6_addr
    start.get = lambda key: {'interfaces': interfaces}[key]
    return start


def check_dhcp(start, ipv4, enable):
    start.__check_dhcp_or_accept_rtadv__(ipv4=ipv4, enable=enable)


@pytest.fixture
def no_sysrc(monkeypatch):
    def no_process(*args, **kwargs):
        raise AssertionError(f'a process was started: {args}')

    monkeypatch.setattr(ioc_start.su, 'run', no_process)
    monkeypatch.setattr(ioc_start.su, 'Popen', no_process)


def test_a_dhcp_jail_gets_its_line_and_keeps_it(tmp_path, jail_root, no_sysrc):
    rc(jail_root).write_text('sendmail_enable="NONE"\n')
    start = starter(tmp_path, dhcp=1)
    check_dhcp(start, ipv4=True, enable=True)
    assert rc(jail_root).read_text() == 'sendmail_enable="NONE"\n' + DHCP
    check_dhcp(start, ipv4=True, enable=True)
    assert rc(jail_root).read_text() == 'sendmail_enable="NONE"\n' + DHCP


def test_dhcp_on_one_interface_of_two(tmp_path, jail_root, no_sysrc):
    rc(jail_root).write_text('')
    start = starter(
        tmp_path, interfaces='vnet0:bridge0,vnet1:bridge1',
        ip4_addr='vnet0|10.0.0.5/24,vnet1|DHCP'
    )
    check_dhcp(start, ipv4=True, enable=True)
    assert rc(jail_root).read_text() == 'ifconfig_epair1b="SYNCDHCP"\n'


def test_a_jail_that_is_no_longer_dhcp_loses_the_line(
        tmp_path, jail_root, no_sysrc):
    rc(jail_root).write_text('a="1"\n' + DHCP + 'b="2"\n')
    check_dhcp(starter(tmp_path), ipv4=True, enable=False)
    assert rc(jail_root).read_text() == 'a="1"\nb="2"\n'


def test_a_static_address_set_in_the_jail_is_not_removed(
        tmp_path, jail_root, no_sysrc):
    rc(jail_root).write_text('ifconfig_epair0b="inet 10.0.0.2/24"\n')
    check_dhcp(starter(tmp_path), ipv4=True, enable=False)
    assert rc(jail_root).read_text() == 'ifconfig_epair0b="inet 10.0.0.2/24"\n'


def test_accept_rtadv_sets_and_removes_the_ipv6_line(
        tmp_path, jail_root, no_sysrc):
    rc(jail_root).write_text('')
    start = starter(tmp_path, ip6_addr='vnet0|accept_rtadv')
    check_dhcp(start, ipv4=False, enable=True)
    assert rc(jail_root).read_text() == RTADV
    start.__check_rtsold__()
    assert rc(jail_root).read_text() == RTADV + 'rtsold_enable="YES"\n'


def test_an_absent_rc_conf_is_created_at_start(tmp_path, jail_root, no_sysrc):
    check_dhcp(starter(tmp_path), ipv4=True, enable=False)
    assert rc(jail_root).read_bytes() == b''


def test_the_jails_shell_does_not_run_at_start(tmp_path, jail_root, no_sysrc):
    marker = tmp_path / 'ran'
    rc(jail_root).write_text(f'x="$(/usr/bin/touch {marker})"\n')
    start = starter(tmp_path, dhcp=1, ip6_addr='vnet0|accept_rtadv')
    check_dhcp(start, ipv4=True, enable=True)
    start.__check_rtsold__()
    check_dhcp(start, ipv4=False, enable=True)
    assert not marker.exists()
    assert rc(jail_root).read_text() == (
        f'x="$(/usr/bin/touch {marker})"\n' + DHCP + 'rtsold_enable="YES"\n'
        + RTADV
    )


def test_a_link_at_rc_conf_is_left_alone_and_the_start_goes_on(
        tmp_path, jail_root, no_sysrc):
    victim = tmp_path / 'victim'
    victim.write_text('# a host file\n')
    rc(jail_root).symlink_to(victim)
    start = starter(tmp_path, dhcp=1, ip6_addr='vnet0|accept_rtadv')
    check_dhcp(start, ipv4=True, enable=True)
    start.__check_rtsold__()
    assert victim.read_text() == '# a host file\n'

    target = tmp_path / 'not-there'
    os.unlink(rc(jail_root))
    rc(jail_root).symlink_to(target)
    check_dhcp(start, ipv4=True, enable=True)
    check_dhcp(starter(tmp_path), ipv4=True, enable=False)
    assert not target.exists()


def test_an_etc_link_skips_the_step_and_the_start_goes_on(
        tmp_path, no_sysrc):
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    (tmp_path / 'root').mkdir()
    (tmp_path / 'root' / 'etc').symlink_to(host_etc)
    check_dhcp(starter(tmp_path, dhcp=1), ipv4=True, enable=True)
    assert os.listdir(host_etc) == []
