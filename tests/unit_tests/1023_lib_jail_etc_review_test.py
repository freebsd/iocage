# Follow-ups to the link-safe helpers for a jail's etc: a root or etc that
# is not a plain directory raises JailEtcUnreachable (an OSError) naming the
# refused path, a file over JAIL_ETC_FILE_MAX is not read, an interrupted
# write leaves no temporary file, and a jail created from a template or a
# release gets its etc/hosts without following a link on either side.
import errno
import os
import stat

import pytest

import iocage_lib.ioc_common as ioc_common
import iocage_lib.ioc_create as ioc_create
import iocage_lib.ioc_start as ioc_start

SECRET = 'host-only line\n'


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


def recorder(messages):
    # Used with silent=False: logit() calls a callback twice when silent.
    return lambda content, exception: messages.append(content)


# --- JailEtcUnreachable -----------------------------------------------------

def test_an_etc_link_raises_unreachable_naming_the_path(tmp_path):
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    root = tmp_path / 'jail' / 'root'
    root.mkdir(parents=True)
    (root / 'etc').symlink_to(host_etc)
    with pytest.raises(ioc_common.JailEtcUnreachable) as error:
        ioc_common.read_jail_etc_file(str(root), 'hosts')
    assert isinstance(error.value, OSError)
    # O_DIRECTORY | O_NOFOLLOW on a link: EMLINK on FreeBSD, ENOTDIR or
    # ELOOP elsewhere.
    assert error.value.errno in (errno.EMLINK, errno.ENOTDIR, errno.ELOOP)
    assert error.value.filename == os.path.join(str(root), 'etc')
    assert error.value.strerror.startswith('not a plain directory (')
    assert os.listdir(host_etc) == []


def test_a_missing_root_or_an_etc_file_raises_unreachable(tmp_path):
    missing = tmp_path / 'no-such-jail' / 'root'
    with pytest.raises(ioc_common.JailEtcUnreachable) as error:
        ioc_common.write_jail_etc_file(str(missing), 'hosts', data=b'x\n')
    assert error.value.errno == errno.ENOENT
    assert error.value.filename == str(missing)
    assert error.value.strerror.startswith('missing (')

    root = tmp_path / 'jail' / 'root'
    root.mkdir(parents=True)
    (root / 'etc').write_text('a file, not a directory\n')
    with pytest.raises(ioc_common.JailEtcUnreachable) as error:
        ioc_common.jail_etc_kind(str(root), 'rc.conf')
    assert error.value.errno == errno.ENOTDIR


def test_other_errors_are_not_reported_as_unreachable(jail_root):
    if os.geteuid() == 0:
        pytest.skip('root opens a directory of mode 000')
    os.chmod(jail_root, 0)
    try:
        with pytest.raises(PermissionError) as error:
            ioc_common.read_jail_etc_file(str(jail_root), 'hosts')
        assert not isinstance(error.value, ioc_common.JailEtcUnreachable)
    finally:
        os.chmod(jail_root, 0o755)


# --- JAIL_ETC_FILE_MAX ------------------------------------------------------

def test_a_file_over_the_limit_is_not_read(jail_root):
    hosts = jail_root / 'etc' / 'hosts'
    with open(hosts, 'wb') as f:
        # Sparse: no data blocks, only its size.
        f.truncate(ioc_common.JAIL_ETC_FILE_MAX + 1)
    assert ioc_common.read_jail_etc_file(str(jail_root), 'hosts') is None
    assert ioc_common.read_jail_etc_file(
        str(jail_root), 'hosts', binary=True) is None


def test_the_limit_is_inclusive_and_edits_leave_a_larger_file(
        jail_root, monkeypatch):
    monkeypatch.setattr(ioc_common, 'JAIL_ETC_FILE_MAX', 16)
    rc_conf = jail_root / 'etc' / 'rc.conf'
    rc_conf.write_bytes(b'a="1"\nbbbb="22"\n')
    assert ioc_common.read_jail_etc_file(
        str(jail_root), 'rc.conf', binary=True) == b'a="1"\nbbbb="22"\n'

    rc_conf.write_bytes(b'a="1"\nbbbbb="22"\n')
    assert ioc_common.read_jail_etc_file(str(jail_root), 'rc.conf') is None
    assert ioc_common.edit_jail_rc_conf(
        str(jail_root), set_values={'a': '2'}) == 'not-a-file'
    assert ioc_common.replace_in_jail_etc_file(
        str(jail_root), 'rc.conf', 'a="1"', 'a="2"') == 'not-a-file'
    assert rc_conf.read_bytes() == b'a="1"\nbbbbb="22"\n'


def starter(tmp_path, messages, **props):
    start = object.__new__(ioc_start.IOCStart)
    start.path = str(tmp_path)
    start.uuid = 'review'
    start.callback = recorder(messages)
    start.silent = False
    start.get = lambda key: props[key]
    return start


def test_start_leaves_an_rc_conf_over_the_limit_and_says_so(tmp_path):
    etc = tmp_path / 'root' / 'etc'
    etc.mkdir(parents=True)
    with open(etc / 'rc.conf', 'wb') as f:
        f.truncate(ioc_common.JAIL_ETC_FILE_MAX + 1)
    messages = []
    starter(tmp_path, messages).__edit_rc_conf__({'rtsold_enable': 'YES'})
    assert os.path.getsize(etc / 'rc.conf') == \
        ioc_common.JAIL_ETC_FILE_MAX + 1
    assert [m['level'] for m in messages] == ['WARNING']
    assert messages[0]['message'] == (
        'review: etc/rc.conf not updated: it is not a plain file, or is'
        ' over 16 MiB'
    )


def test_start_names_the_path_it_did_not_go_through(tmp_path):
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    (tmp_path / 'root').mkdir()
    (tmp_path / 'root' / 'etc').symlink_to(host_etc)
    messages = []
    starter(tmp_path, messages, resolver='nameserver 10.0.0.1').\
        start_generate_resolv()
    assert os.listdir(host_etc) == []
    assert [m['level'] for m in messages] == ['WARNING']
    assert messages[0]['message'].startswith(
        f'review: etc/resolv.conf not updated: {tmp_path}/root/etc is not a'
        ' plain directory ('
    )


# --- an interrupted write ---------------------------------------------------

def test_an_interrupted_write_leaves_no_temporary_file(
        jail_root, monkeypatch):
    hosts = jail_root / 'etc' / 'hosts'
    hosts.write_text('old\n')

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(ioc_common.os, 'rename', interrupted)
    with pytest.raises(KeyboardInterrupt):
        ioc_common.write_jail_etc_file(str(jail_root), 'hosts', data=b'new\n')
    monkeypatch.undo()
    assert os.listdir(jail_root / 'etc') == ['hosts']
    assert hosts.read_text() == 'old\n'


# --- create from a template or a release: etc/hosts -------------------------

def creator(tmp_path, messages, template=True):
    create = object.__new__(ioc_create.IOCCreate)
    create.iocroot = str(tmp_path / 'iocage')
    create.template = template
    create.release = 'tpl' if template else '15.1-RELEASE'
    create.callback = recorder(messages)
    create.silent = False
    return create


def source_root(tmp_path, template=True):
    kind, name = ('templates', 'tpl') if template else \
        ('releases', '15.1-RELEASE')
    root = tmp_path / 'iocage' / kind / name / 'root'
    root.mkdir(parents=True)
    return root


def new_jail(tmp_path):
    location = tmp_path / 'iocage' / 'jails' / 'new'
    (location / 'root').mkdir(parents=True)
    return location


@pytest.mark.parametrize('template', [True, False])
def test_create_writes_hosts_as_before(tmp_path, template):
    etc = source_root(tmp_path, template) / 'etc'
    etc.mkdir()
    (etc / 'hosts').write_text('::1\t\tlocalhost\n127.0.0.1\t\tlocalhost\n')
    location = new_jail(tmp_path)
    (location / 'root' / 'etc').mkdir()
    messages = []
    creator(tmp_path, messages, template).create_hosts(
        str(location), {'ip4_addr': 'vnet0|10.0.0.5/24'}, None, 'new',
        'new.x\tnew'
    )
    hosts = location / 'root' / 'etc' / 'hosts'
    assert hosts.read_text() == (
        '::1\t\tlocalhost\n127.0.0.1\t\tlocalhost new\n10.0.0.5\tnew.x\tnew\n'
    )
    assert stat.S_IMODE(os.lstat(hosts).st_mode) == 0o644
    assert messages == []


def test_create_assigns_localhost_as_before(tmp_path, monkeypatch):
    etc = source_root(tmp_path) / 'etc'
    etc.mkdir()
    (etc / 'hosts').write_text('127.0.0.1\t\tlocalhost\n')
    location = new_jail(tmp_path)
    (location / 'root' / 'etc').mkdir()
    ran, written = [], []
    monkeypatch.setattr(ioc_create.su, 'run', lambda cmd: ran.append(cmd))

    class Json:
        def json_write(self, config):
            written.append(dict(config))

    config = {
        'assign_localhost': 1, 'vnet': 0, 'localhost_ip': '127.0.0.7',
        'ip4_addr': 'none'
    }
    creator(tmp_path, []).create_hosts(
        str(location), config, Json(), 'new', 'new'
    )
    assert (location / 'root' / 'etc' / 'hosts').read_text() == \
        '127.0.0.7\t\tlocalhost localhost.my.domain new\n'
    assert ran == [['ifconfig', 'lo0', 'alias', '127.0.0.7/32']]
    assert written == [config]


def test_a_template_hosts_link_is_not_read_into_the_new_jail(
        tmp_path, host_file):
    etc = source_root(tmp_path) / 'etc'
    etc.mkdir()
    (etc / 'hosts').symlink_to(host_file)
    location = new_jail(tmp_path)
    (location / 'root' / 'etc').mkdir()
    # The new jail's root is a clone of the template's: the same link.
    (location / 'root' / 'etc' / 'hosts').symlink_to(host_file)
    messages = []
    creator(tmp_path, messages).create_hosts(
        str(location), {'ip4_addr': 'none'}, None, 'new', 'new'
    )
    assert (location / 'root' / 'etc' / 'hosts').is_symlink()
    assert host_file.read_text() == SECRET
    assert [m['level'] for m in messages] == ['WARNING']
    assert 'etc/hosts is not a plain file' in messages[0]['message']


def test_the_new_jails_hosts_link_is_replaced_not_followed(
        tmp_path, host_file):
    etc = source_root(tmp_path) / 'etc'
    etc.mkdir()
    (etc / 'hosts').write_text('127.0.0.1\tlocalhost\n')
    location = new_jail(tmp_path)
    (location / 'root' / 'etc').mkdir()
    (location / 'root' / 'etc' / 'hosts').symlink_to(host_file)
    creator(tmp_path, []).create_hosts(
        str(location), {'ip4_addr': 'none'}, None, 'new', 'new'
    )
    hosts = location / 'root' / 'etc' / 'hosts'
    assert not hosts.is_symlink()
    assert hosts.read_text() == '127.0.0.1\tlocalhost new\n'
    assert host_file.read_text() == SECRET


def test_an_etc_link_on_either_side_skips_hosts(tmp_path):
    host_etc = tmp_path / 'host-etc'
    host_etc.mkdir()
    (host_etc / 'hosts').write_text(SECRET)
    template_root = source_root(tmp_path)
    (template_root / 'etc').symlink_to(host_etc)
    location = new_jail(tmp_path)
    (location / 'root' / 'etc').symlink_to(host_etc)
    messages = []
    creator(tmp_path, messages).create_hosts(
        str(location), {'ip4_addr': 'none'}, None, 'new', 'new'
    )
    assert os.listdir(host_etc) == ['hosts']
    assert (host_etc / 'hosts').read_text() == SECRET
    assert [m['level'] for m in messages] == ['WARNING']
    assert f'{template_root}/etc is not a plain directory' in \
        messages[0]['message']

    # A plain template; the new jail's etc is still a link.
    os.unlink(template_root / 'etc')
    (template_root / 'etc').mkdir()
    (template_root / 'etc' / 'hosts').write_text('127.0.0.1\tlocalhost\n')
    messages.clear()
    creator(tmp_path, messages).create_hosts(
        str(location), {'ip4_addr': 'none'}, None, 'new', 'new'
    )
    assert os.listdir(host_etc) == ['hosts']
    assert (host_etc / 'hosts').read_text() == SECRET
    assert [m['level'] for m in messages] == ['WARNING']
    assert f'{location}/root/etc is not a plain directory' in \
        messages[0]['message']
