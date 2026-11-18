# the remaining places where the host read or wrote a file
# inside a jail's root by its path (clone/create, the upgrade's edit of
# freebsd-update.conf, the legacy migrations, debug) go through the link-safe
# helpers. Runs on the host's own Python (iocage_lib imports libzfs).
import os
import stat

import pytest

import iocage_lib.ioc_common as ioc_common
import iocage_lib.ioc_create as ioc_create
import iocage_lib.ioc_debug as ioc_debug
import iocage_lib.ioc_upgrade as ioc_upgrade

SECRET = (
    'host-only line one\nComponents src world kernel\nhost-only line three\n'
)


@pytest.fixture
def jail_root(tmp_path):
    root = tmp_path / 'jail' / 'root'
    (root / 'etc').mkdir(parents=True)
    return root


@pytest.fixture
def host_file(tmp_path):
    path = tmp_path / 'host-file'
    path.write_text(SECRET)
    return path


# --- jail_etc_kind, replace_in_jail_etc_file --------------------------------

def test_kind_tells_a_plain_file_from_nothing_and_from_anything_else(
        jail_root, host_file, tmp_path):
    etc = jail_root / 'etc'
    assert ioc_common.jail_etc_kind(str(jail_root), 'rc.conf') == 'absent'
    (etc / 'rc.conf').write_text('a="1"\n')
    assert ioc_common.jail_etc_kind(str(jail_root), 'rc.conf') == 'file'
    (etc / 'to-host').symlink_to(host_file)
    (etc / 'dangling').symlink_to(tmp_path / 'not-there')
    os.mkfifo(etc / 'fifo')
    (etc / 'dir').mkdir()
    for name in ('to-host', 'dangling', 'fifo', 'dir'):
        assert ioc_common.jail_etc_kind(str(jail_root), name) == 'other'


def test_replace_edits_a_plain_file_and_keeps_its_bytes_and_mode(jail_root):
    path = jail_root / 'etc' / 'hosts'
    path.write_bytes(b'127.0.0.1 old old.local\n# caf\xe9 old\n')
    os.chmod(path, 0o600)
    assert ioc_common.replace_in_jail_etc_file(
        str(jail_root), 'hosts', 'old', 'new') == 'written'
    assert path.read_bytes() == b'127.0.0.1 new new.local\n# caf\xe9 new\n'
    assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
    inode = path.stat().st_ino
    assert ioc_common.replace_in_jail_etc_file(
        str(jail_root), 'hosts', 'absent-text', 'x') == 'unchanged'
    assert path.stat().st_ino == inode


def test_replace_leaves_a_link_and_the_file_it_points_at(jail_root, host_file):
    link = jail_root / 'etc' / 'hosts'
    link.symlink_to(host_file)
    assert ioc_common.replace_in_jail_etc_file(
        str(jail_root), 'hosts', 'host-only', 'copied') == 'not-a-file'
    assert link.is_symlink() and host_file.read_text() == SECRET


def test_replace_says_absent_and_refuses_an_etc_that_is_a_link(
        jail_root, tmp_path):
    assert ioc_common.replace_in_jail_etc_file(
        str(jail_root), 'hosts', 'a', 'b') == 'absent'
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    (host_etc / 'hosts').write_text('a\n')
    other = tmp_path / 'other' / 'root'
    other.mkdir(parents=True)
    (other / 'etc').symlink_to(host_etc)
    with pytest.raises(OSError):
        ioc_common.replace_in_jail_etc_file(str(other), 'hosts', 'a', 'b')
    assert (host_etc / 'hosts').read_text() == 'a\n'


# --- create_rc (create, clone) ----------------------------------------------

def creator(tmp_path):
    create = object.__new__(ioc_create.IOCCreate)
    create.iocroot = str(tmp_path / 'iocage')
    os.makedirs(create.iocroot, exist_ok=True)
    create.callback = None
    create.silent = True
    return create


def test_create_rc_writes_the_default_where_there_is_none(tmp_path, jail_root):
    create = creator(tmp_path)
    create.create_rc(str(jail_root.parent), 'jail')
    default = (tmp_path / 'iocage' / 'default_rc.conf').read_bytes()
    rc_conf = jail_root / 'etc' / 'rc.conf'
    assert default and rc_conf.read_bytes() == default
    assert stat.S_IMODE(os.lstat(rc_conf).st_mode) == 0o644


def test_create_rc_leaves_the_jails_own_file(tmp_path, jail_root):
    rc_conf = jail_root / 'etc' / 'rc.conf'
    rc_conf.write_text('mine="1"\n')
    creator(tmp_path).create_rc(str(jail_root.parent), 'jail')
    assert rc_conf.read_text() == 'mine="1"\n'


def test_create_rc_creates_nothing_through_a_link(
        tmp_path, jail_root, host_file):
    rc_conf = jail_root / 'etc' / 'rc.conf'
    target = tmp_path / 'not-there'
    rc_conf.symlink_to(target)
    creator(tmp_path).create_rc(str(jail_root.parent), 'jail')
    assert not target.exists() and rc_conf.is_symlink()

    os.unlink(rc_conf)
    rc_conf.symlink_to(host_file)
    creator(tmp_path).create_rc(str(jail_root.parent), 'jail')
    assert host_file.read_text() == SECRET and rc_conf.is_symlink()


def test_create_rc_goes_on_when_etc_is_a_link(tmp_path):
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    root = tmp_path / 'jail' / 'root'
    root.mkdir(parents=True)
    (root / 'etc').symlink_to(host_etc)
    creator(tmp_path).create_rc(str(root.parent), 'jail')
    assert os.listdir(host_etc) == []


# --- the upgrade's edit of freebsd-update.conf ------------------------------

def upgrader(jail_root):
    upgrade = object.__new__(ioc_upgrade.IOCUpgrade)
    upgrade.path = str(jail_root)
    upgrade.callback = None
    upgrade.silent = True
    return upgrade


def test_upgrade_takes_the_kernel_out_of_a_plain_conf(jail_root):
    conf = jail_root / 'etc' / 'freebsd-update.conf'
    conf.write_text('KeyPrint x\nComponents src world kernel\n')
    upgrader(jail_root).__upgrade_check_conf__()
    assert conf.read_text() == 'KeyPrint x\nComponents src world\n'
    # Already without it: nothing to do, no refusal.
    upgrader(jail_root).__upgrade_check_conf__()
    assert conf.read_text() == 'KeyPrint x\nComponents src world\n'


def test_upgrade_refuses_a_conf_that_is_a_link_and_copies_nothing(
        jail_root, host_file):
    conf = jail_root / 'etc' / 'freebsd-update.conf'
    conf.symlink_to(host_file)
    with pytest.raises(Exception) as error:
        upgrader(jail_root).__upgrade_check_conf__()
    assert 'not a plain file' in str(error.value)
    assert conf.is_symlink() and host_file.read_text() == SECRET
    assert sorted(os.listdir(jail_root / 'etc')) == ['freebsd-update.conf']


def test_upgrade_refuses_when_the_conf_is_gone(jail_root):
    with pytest.raises(Exception):
        upgrader(jail_root).__upgrade_check_conf__()


# --- debug ------------------------------------------------------------------

def test_debug_reads_a_plain_file_and_never_through_a_link(
        jail_root, host_file):
    lines = ioc_debug.IOCDebug.__jail_etc_lines__
    (jail_root / 'etc' / 'hosts').write_bytes(
        b'127.0.0.1 localhost\n\xff odd\n'
    )
    assert lines(str(jail_root), 'hosts') == [
        '127.0.0.1 localhost', '� odd'
    ]
    (jail_root / 'etc' / 'rc.conf').symlink_to(host_file)
    answer = lines(str(jail_root), 'rc.conf')
    assert answer == ['etc/rc.conf not read: absent or not a plain file']
    assert lines(str(jail_root), 'nsswitch.conf') == [
        'etc/nsswitch.conf not read: absent or not a plain file'
    ]


def test_debug_says_so_when_etc_is_a_link(tmp_path):
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    (host_etc / 'hosts').write_text(SECRET)
    root = tmp_path / 'jail' / 'root'
    root.mkdir(parents=True)
    (root / 'etc').symlink_to(host_etc)
    answer = ioc_debug.IOCDebug.__jail_etc_lines__(str(root), 'hosts')
    assert len(answer) == 1 and answer[0].startswith('etc/hosts not read: ')
    assert 'host-only' not in answer[0]
