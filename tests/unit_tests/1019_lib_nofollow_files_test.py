# The files iocage writes inside a jail's root at start are never written
# through a link. Runs on the host's own Python (iocage_lib imports libzfs).
import os
import stat

import pytest

import iocage_lib.ioc_common as ioc_common
import iocage_lib.ioc_start as ioc_start


def mode_of(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


# --- read_/write_jail_etc_file (#52) ----------------------------------------

@pytest.fixture
def jail_root(tmp_path):
    root = tmp_path / 'root'
    (root / 'etc').mkdir(parents=True)
    return root


def test_write_replaces_a_plain_file(jail_root):
    (jail_root / 'etc' / 'resolv.conf').write_text('old\n')
    ioc_common.write_jail_etc_file(str(jail_root), 'resolv.conf',
                                   data=b'nameserver 10.0.0.1\n', mode=0o644)
    target = jail_root / 'etc' / 'resolv.conf'
    assert target.read_text() == 'nameserver 10.0.0.1\n'
    assert mode_of(target) == 0o644
    assert sorted(os.listdir(jail_root / 'etc')) == ['resolv.conf']


def test_write_replaces_a_link_and_never_writes_through_it(
        jail_root, tmp_path):
    host_file = tmp_path / 'host-passwd'
    host_file.write_text('root:*:0:0\n')
    link = jail_root / 'etc' / 'resolv.conf'
    link.symlink_to(host_file)
    ioc_common.write_jail_etc_file(str(jail_root), 'resolv.conf',
                                   data=b'nameserver 10.0.0.1\n', mode=0o644)
    assert not link.is_symlink()
    assert link.read_text() == 'nameserver 10.0.0.1\n'
    assert host_file.read_text() == 'root:*:0:0\n'


def test_write_can_place_a_symlink(jail_root):
    ioc_common.write_jail_etc_file(
        str(jail_root), 'localtime', link='/usr/share/zoneinfo/Europe/Oslo')
    target = jail_root / 'etc' / 'localtime'
    assert os.readlink(target) == '/usr/share/zoneinfo/Europe/Oslo'


def test_write_refuses_an_etc_that_is_a_link(tmp_path):
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'etc').symlink_to(host_etc)
    with pytest.raises(OSError):
        ioc_common.write_jail_etc_file(str(root), 'hosts', data=b'x\n')
    assert os.listdir(host_etc) == []


def test_read_returns_a_plain_files_text(jail_root):
    (jail_root / 'etc' / 'hosts').write_text('127.0.0.1 localhost\n')
    assert ioc_common.read_jail_etc_file(str(jail_root), 'hosts') == \
        '127.0.0.1 localhost\n'


def test_read_does_not_follow_a_link_or_open_a_fifo(jail_root, tmp_path):
    secret = tmp_path / 'secret'
    secret.write_text('host only\n')
    (jail_root / 'etc' / 'hosts').symlink_to(secret)
    os.mkfifo(jail_root / 'etc' / 'fifo')
    assert ioc_common.read_jail_etc_file(str(jail_root), 'hosts') is None
    assert ioc_common.read_jail_etc_file(str(jail_root), 'fifo') is None
    assert ioc_common.read_jail_etc_file(str(jail_root), 'absent') is None


# --- the start steps that use them -----------------------------------------

def starter(tmp_path, **props):
    start = object.__new__(ioc_start.IOCStart)
    start.path = str(tmp_path)
    start.uuid = 'nofollow'
    start.callback = None
    start.silent = True
    start.get = lambda key: props[key]
    return start


def test_a_custom_resolver_is_written_0644_and_not_through_a_link(
        tmp_path, jail_root):
    victim = tmp_path / 'victim'
    victim.write_text('keep\n')
    (jail_root / 'etc' / 'resolv.conf').symlink_to(victim)
    starter(tmp_path, resolver='nameserver 10.0.0.1;search lab').\
        start_generate_resolv()
    target = jail_root / 'etc' / 'resolv.conf'
    assert target.read_text() == 'nameserver 10.0.0.1\nsearch lab\n'
    assert mode_of(target) == 0o644 and victim.read_text() == 'keep\n'


def test_the_default_resolver_copies_the_hosts_file_with_its_mode(
        tmp_path, jail_root):
    # resolver=/etc/resolv.conf (the default) copies the host's file, as
    # shutil.copy did: same bytes, same mode. Any other value is written
    # literally as the file's lines (inherited), tested above.
    if not os.path.isfile('/etc/resolv.conf'):
        pytest.skip('no /etc/resolv.conf on this host')
    starter(tmp_path, resolver='/etc/resolv.conf').start_generate_resolv()
    target = jail_root / 'etc' / 'resolv.conf'
    with open('/etc/resolv.conf', 'rb') as f:
        assert target.read_bytes() == f.read()
    # The host's file may itself be a link (systemd-resolved): its mode is
    # its target's, which is what is copied.
    source_mode = stat.S_IMODE(os.stat('/etc/resolv.conf').st_mode)
    assert mode_of(target) == source_mode


def test_resolver_dev_null_leaves_the_file_alone(tmp_path, jail_root):
    (jail_root / 'etc' / 'resolv.conf').write_text('mine\n')
    starter(tmp_path, resolver='/dev/null').start_generate_resolv()
    assert (jail_root / 'etc' / 'resolv.conf').read_text() == 'mine\n'


def test_an_etc_link_skips_the_step_and_the_start_goes_on(tmp_path):
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    (tmp_path / 'root').mkdir()
    (tmp_path / 'root' / 'etc').symlink_to(host_etc)
    starter(tmp_path, resolver='nameserver 10.0.0.1').start_generate_resolv()
    assert os.listdir(host_etc) == []


def test_host_time_replaces_the_jails_localtime_without_following_it(
        tmp_path, jail_root):
    victim = tmp_path / 'victim'
    victim.write_text('keep\n')
    (jail_root / 'etc' / 'localtime').symlink_to(victim)
    starter(tmp_path, host_time='yes').start_copy_localtime()
    target = jail_root / 'etc' / 'localtime'
    if os.path.islink('/etc/localtime'):
        assert os.readlink(target) == os.readlink('/etc/localtime')
    elif os.path.exists('/etc/localtime'):
        with open('/etc/localtime', 'rb') as f:
            assert target.read_bytes() == f.read()
        assert not target.is_symlink()
    assert victim.read_text() == 'keep\n'


def test_host_time_off_leaves_localtime_alone(tmp_path, jail_root):
    (jail_root / 'etc' / 'localtime').write_text('jail tz\n')
    starter(tmp_path, host_time='no').start_copy_localtime()
    assert (jail_root / 'etc' / 'localtime').read_text() == 'jail tz\n'
