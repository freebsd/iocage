"""Defer optional modules while keeping their operations callable."""

import subprocess
import sys

from unittest import mock

import pytest

from iocage_lib import ioc_common, ioc_fetch, iocage, release


def test_library_import_has_no_optional_imports():
    subprocess.run([sys.executable, '-c', """
import iocage_lib.iocage
import sys
assert not any(name in sys.modules for name in
               ('requests', 'jsonschema', 'git', 'dns', 'urllib.request',
                'tarfile', 'iocage_lib.ioc_plugin', 'iocage_lib.ioc_fetch',
                'iocage_lib.ioc_upgrade', 'iocage_lib.ioc_image'))
"""], check=True)


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
    fetch.release = '15.1-RELEASE'
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
        get.return_value.content = b'<td>releng/15.1</td>'
        assert ioc_common.parse_latest_release() == '15.1-RELEASE'


def test_remote_releases_import_requests():
    releases = release.ListableReleases.__new__(release.ListableReleases)
    releases.remote = True
    releases.eol_check = False
    releases.resource = lambda value: value
    with mock.patch('requests.get') as get, \
            mock.patch.object(release, 'check_release_newer',
                              return_value=False):
        get.return_value.status_code = 200
        get.return_value.content = b'<a href="15.1-RELEASE/">'
        assert list(releases) == ['15.1-RELEASE']


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
        jail.upgrade('15.1-RELEASE')
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
