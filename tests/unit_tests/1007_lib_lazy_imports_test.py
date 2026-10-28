"""Console imports must not load HTTP/schema code or discover libc."""

import concurrent.futures
import subprocess
import sys
import textwrap

from unittest import mock

import pytest

from iocage_lib import ioc_common, ioc_fetch, ioc_fstab, iocage, release


def run_cli_script(arguments, script):
    """Isolate CLI imports, which replace streams and signal handlers."""
    setup = '''
import locale
import subprocess
import sys

from contextlib import ExitStack
from unittest.mock import patch

import click

from click.testing import CliRunner
arguments = sys.argv[1:]
sys.argv = ['iocage', *arguments]
with ExitStack() as stack:
    stack.enter_context(patch.object(locale, 'setlocale'))
    stack.enter_context(patch.object(subprocess, 'check_call'))
    import iocage_cli
with ExitStack() as stack:
    stack.enter_context(patch.object(iocage_cli, 'IOCLogger'))
    stack.enter_context(patch.object(iocage_cli.os, 'geteuid', return_value=0))
    check = stack.enter_context(patch('iocage_cli.ioc_check.IOCCheck'))
    jail = stack.enter_context(patch('iocage_lib.iocage.IOCage'))
'''
    subprocess.run(
        [sys.executable, '-c',
         setup + textwrap.indent(textwrap.dedent(script), '    '), *arguments],
        check=True
    )


def test_library_import_has_no_optional_imports_or_subprocesses():
    subprocess.run([sys.executable, '-c', '''
import subprocess
from unittest.mock import patch
with patch.object(subprocess, 'Popen', side_effect=AssertionError):
    import iocage_lib.iocage
import sys
assert not any(name in sys.modules for name in
               ('requests', 'jsonschema', 'git', 'dns', 'urllib.request',
                'tarfile', 'iocage_lib.ioc_plugin', 'iocage_lib.ioc_fetch',
                'iocage_lib.ioc_upgrade', 'iocage_lib.ioc_image'))
assert iocage_lib.ioc_fstab.LIBC is None
'''], check=True)


@pytest.mark.parametrize('name', ['dev', 'ALL'])
def test_console_cli_uses_cached_checks_and_lazy_jails(name):
    run_cli_script(['console', name], '''
result = CliRunner().invoke(iocage_cli.cli, arguments)
assert result.exit_code == 0, result.output
check.assert_called_once_with(use_cache=True)
assert jail.call_args.kwargs['skip_jails'] == (arguments[1] != 'ALL')
assert 'coloredlogs' not in sys.modules
''')


@pytest.mark.parametrize('styles', [{}, {'error': {'color': 'red'}}])
def test_console_logging_initializes_formatter_once(styles):
    run_cli_script([], '''
import io
import logging
import coloredlogs
styles = ''' + repr(styles) + '''
handler = iocage_cli.InfoHandler(level_styles=styles)
assert handler.formatter is None
records = [logging.LogRecord('iocage', level, '', 0, 'message', (), None)
           for level in (logging.INFO, logging.ERROR)]
formatter = coloredlogs.ColoredFormatter(fmt='%(message)s',
                                       level_styles=styles)
stdout, stderr = io.StringIO(), io.StringIO()
with patch.object(sys, 'stdout', stdout), patch.object(sys, 'stderr', stderr):
    with patch.object(coloredlogs, 'ColoredFormatter',
                      wraps=coloredlogs.ColoredFormatter) as create:
        for record in records:
            handler.handle(record)
        create.assert_called_once_with(fmt='%(message)s', level_styles=styles)
assert stdout.getvalue() == formatter.format(records[0]) + '\\n'
assert stderr.getvalue() == formatter.format(records[1]) + '\\n'
''')


def test_console_logging_first_use_is_thread_safe():
    run_cli_script([], '''
import io
import logging
import coloredlogs
from concurrent.futures import ThreadPoolExecutor
handler = iocage_cli.InfoHandler(level_styles={})
record = logging.LogRecord('iocage', logging.INFO, '', 0, 'message', (), None)
output = io.StringIO()
with patch.object(sys, 'stdout', output):
    with patch.object(coloredlogs, 'ColoredFormatter',
                      wraps=coloredlogs.ColoredFormatter) as create:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(handler.handle, [record] * 32))
        create.assert_called_once_with(fmt='%(message)s', level_styles={})
assert output.getvalue() == 'message\\n' * 32
''')


def test_console_logging_respects_custom_formatter():
    run_cli_script([], '''
import io
import logging
handler = iocage_cli.InfoHandler()
handler.setFormatter(logging.Formatter('custom %(message)s'))
record = logging.LogRecord('iocage', logging.ERROR, '', 0, 'message', (), None)
output = io.StringIO()
with patch.object(sys, 'stderr', output):
    handler.handle(record)
assert output.getvalue() == 'custom message\\n'
assert 'coloredlogs' not in sys.modules
''')


def test_libc_first_use_is_thread_safe(monkeypatch):
    monkeypatch.setattr(ioc_fstab, 'LIBC', None)
    library = mock.Mock()
    with mock.patch.object(ioc_fstab, 'load_ctypes_library',
                           return_value=library) as load:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            handles = list(pool.map(
                lambda _: ioc_fstab._get_libc(), range(32)))
        assert all(handle is library for handle in handles)
        load.assert_called_once()
        # Reading fstab holds this lock while calling its encode helper.
        with ioc_fstab.FSTAB_LOCK:
            assert ioc_fstab._get_libc() is library


def test_other_cli_commands_keep_uncached_checks():
    run_cli_script(['list'], '''
command = click.Command('list', callback=lambda: None)
stack.enter_context(patch.object(iocage_cli.IOCageCLI, 'get_command',
                                return_value=command))
result = CliRunner().invoke(iocage_cli.cli, arguments)
assert result.exit_code == 0, result.output
check.assert_called_once_with(use_cache=False)
''')


def test_libc_failure_is_deferred_and_can_be_retried(monkeypatch):
    monkeypatch.setattr(ioc_fstab, 'LIBC', None)
    with mock.patch.object(ioc_fstab, 'load_ctypes_library',
                           side_effect=ImportError('libc missing')):
        with pytest.raises(ImportError, match='libc missing'):
            ioc_fstab._get_libc()
    assert ioc_fstab.LIBC is None
    with mock.patch.object(ioc_fstab, 'load_ctypes_library') as load:
        assert ioc_fstab._get_libc() is load.return_value


def test_fstab_encode_decode_load_libc_on_demand(monkeypatch):
    monkeypatch.setattr(ioc_fstab, 'LIBC', None)
    library = mock.Mock()

    def encode(result, value, flags):
        result.value = b'/some\\040path'

    def decode(result, value, flags):
        result.value = b'/some path'

    library.strvis.side_effect = encode
    library.strunvis.side_effect = decode
    fstab = ioc_fstab.IOCFstab.__new__(ioc_fstab.IOCFstab)
    with mock.patch.object(ioc_fstab, 'load_ctypes_library',
                           return_value=library) as load:
        assert fstab.__fstab_encode__(None) is None
        load.assert_not_called()
        assert fstab.__fstab_encode__('/some path') == '/some\\040path'
        assert fstab.__fstab_decode__('/some\\040path') == '/some path'
        load.assert_called_once()


def test_eol_http_import_still_works():
    with mock.patch('requests.get') as get:
        get.return_value.status_code = 200
        get.return_value.content = b'<td>13.2-RELEASE</td>'
        assert ioc_fetch.IOCFetch.__fetch_eol_check__() == ['13.2-RELEASE']


@pytest.mark.parametrize('auth', [None, 'basic', 'digest'])
def test_release_probe_http_auth(auth):
    fetch = ioc_fetch.IOCFetch.__new__(ioc_fetch.IOCFetch)
    fetch.server = 'https://example.invalid'
    fetch.root_dir = 'releases'
    fetch.release = '15.0-RELEASE'
    fetch.auth = auth
    fetch.user = 'user'
    fetch.password = 'password'
    fetch.verify = True
    with mock.patch('requests.get') as get:
        get.return_value.status_code = 200
        fetch.__fetch_exists__()
        get.assert_called_once()
        if auth == 'basic':
            assert get.call_args.kwargs['auth'] == ('user', 'password')
        elif auth == 'digest':
            assert get.call_args.kwargs['auth'].username == 'user'


def test_latest_release_http_import_still_works():
    with mock.patch('requests.get') as get:
        get.return_value.status_code = 200
        get.return_value.content = b'<td>releng/15.0</td>'
        assert ioc_common.parse_latest_release() == '15.0-RELEASE'


def test_remote_releases_import_requests():
    releases = release.ListableReleases.__new__(release.ListableReleases)
    releases.remote = True
    releases.eol_check = False
    releases.resource = lambda value: value
    with mock.patch('requests.get') as get, \
            mock.patch.object(release, 'check_release_newer',
                              return_value=False):
        get.return_value.status_code = 200
        get.return_value.content = b'<a href="15.0-RELEASE/">'
        assert list(releases) == ['15.0-RELEASE']


@pytest.mark.parametrize('plugin', [False, True])
def test_deferred_fetch_operations(plugin):
    jail = iocage.IOCage.__new__(iocage.IOCage)
    jail.silent = True
    jail.callback = None
    implementation = ('iocage_lib.ioc_plugin.IOCPlugin' if plugin else
                      'iocage_lib.ioc_fetch.IOCFetch')
    with mock.patch(implementation) as factory, \
            mock.patch.object(ioc_common, 'checkoutput',
                              return_value='15.1-RELEASE'):
        expected = factory.return_value
        result = jail.fetch(list=True, remote=True, plugins=plugin)
        factory.assert_called_once()
        if plugin:
            assert result is expected.fetch_plugin_index.return_value
        else:
            assert result is expected.fetch_release.return_value


@pytest.mark.parametrize('operation', ['export', 'import_'])
def test_deferred_image_operations(operation):
    jail = iocage.IOCage.__new__(iocage.IOCage)
    jail.jail = 'dev'
    with mock.patch('iocage_lib.ioc_image.IOCImage') as factory, \
            mock.patch.object(jail, '__check_jail_existence__',
                              return_value=('dev', '/iocage/jails/dev')), \
            mock.patch.object(jail, 'list', return_value=(False, '-')):
        getattr(jail, operation)()
        factory.assert_called_once()
        if operation == 'export':
            factory.return_value.export_jail.assert_called_once_with(
                'dev', '/iocage/jails/dev', compression_algo='zip')
        else:
            factory.return_value.import_jail.assert_called_once_with(
                'dev', compression_algo='zip', path=None)


@pytest.mark.parametrize('jail_type,basejail', [
    ('jail', 0), ('jail', 1), ('pluginv2', 0)
])
def test_deferred_upgrade_operations(jail_type, basejail):
    jail = iocage.IOCage.__new__(iocage.IOCage)
    jail._all = False
    jail.callback = None
    conf = {'type': jail_type, 'release': '14.3-RELEASE', 'basejail': basejail,
            'plugin_name': 'test', 'plugin_repository': 'example.invalid'}
    implementation = ('iocage_lib.ioc_upgrade.IOCUpgrade'
                      if jail_type == 'jail' else
                      'iocage_lib.ioc_plugin.IOCPlugin')
    with mock.patch(implementation) as factory, \
            mock.patch.object(jail, '__check_jail_existence__',
                              return_value=('dev', '/iocage/jails/dev')), \
            mock.patch.object(jail, 'list', return_value=(True, '6')), \
            mock.patch.object(iocage.ioc_json, 'IOCJson') as config, \
            mock.patch.object(ioc_common, 'check_release_newer'), \
            mock.patch.object(ioc_common, 'logit'):
        config.return_value.json_get_value.return_value = conf
        jail.upgrade('15.0-RELEASE')
        factory.assert_called_once()
        if basejail:
            factory.return_value.upgrade_basejail.assert_called_once_with()
        elif jail_type == 'jail':
            factory.return_value.upgrade_jail.assert_called_once_with()
        else:
            factory.return_value.upgrade.assert_called_once_with('6')


def test_remote_release_eol_check_loads_fetch():
    with mock.patch.object(
            release.IocageListableResource, '__init__',
            lambda self: setattr(self, 'dataset_path', 'tank')), \
            mock.patch.object(ioc_fetch.IOCFetch, '__fetch_eol_check__',
                              return_value=['14.2-RELEASE']) as eol:
        releases = release.ListableReleases(remote=True)
        assert releases.eol_list == ['14.2-RELEASE']
        eol.assert_called_once_with()


def test_plugin_listing_loads_plugin_repository(tmp_path):
    listing = iocage.ioc_list.IOCList.__new__(iocage.ioc_list.IOCList)
    listing.full = listing.plugin = listing.plugin_data = True
    listing.basejail_only = listing.header = False
    listing.silent = True
    listing.callback = None
    listing.sort = 'name'
    conf = {'host_hostuuid': 'dev', 'ip4_addr': 'none', 'ip6_addr': 'none',
            'type': 'pluginv2', 'release': '15.1-RELEASE', 'basejail': 0,
            'plugin_name': 'test', 'plugin_repository': 'example.invalid'}
    jail = mock.Mock(name='jail')
    jail.name = 'tank/iocage/jails/dev'
    jail.properties = {'mountpoint': str(tmp_path)}
    (tmp_path / 'INDEX').write_text('{"test": {"primary_pkg": "testpkg"}}')
    with mock.patch('iocage_lib.ioc_plugin.IOCPlugin') as plugin, \
            mock.patch.object(iocage.ioc_json, 'IOCJson') as config, \
            mock.patch.object(iocage.ioc_list, 'Dataset') as dataset, \
            mock.patch.object(ioc_common, 'get_active_jails',
                              return_value={}), \
            mock.patch.object(ioc_common, 'get_host_gateways',
                              return_value={}), \
            mock.patch.object(ioc_common, 'retrieve_ip4_for_jail',
                              return_value={'full_ip4': '', 'short_ip4': ''}):
        plugin.return_value.git_destination = str(tmp_path)
        config.return_value.json_get_value.return_value = conf
        dataset.return_value.exists = False
        rows = listing.list_all([jail])
        plugin.assert_called_once_with(git_repository='example.invalid')
        plugin.return_value.pull_clone_git_repo.assert_not_called()
        assert 'testpkg' in rows[0]
