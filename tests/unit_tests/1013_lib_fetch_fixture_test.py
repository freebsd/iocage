"""Both functional-test fetch paths must honor the selected download source."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from iocage_cli.fetch import cli as fetch_cli


@pytest.fixture
def fetch_tests():
    modules = []
    directory = Path(__file__).resolve().parents[1] / 'functional_tests'
    for filename in ('0002_fetch_test.py', '0097_upgrade_test.py'):
        spec = importlib.util.spec_from_file_location(
            'fetch_fixture_' + filename[:-3], directory / filename,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


@pytest.mark.parametrize('release', ['latest', 'LATEST', 'Latest'])
def test_upgrade_accepts_latest_aliases(fetch_tests, release):
    _, upgrade = fetch_tests
    with mock.patch.object(
        upgrade.iocage_lib.ioc_common, 'parse_latest_release',
        return_value='15.1-RELEASE',
    ) as latest:
        try:
            releases = upgrade.upgrade_releases.__wrapped__(release)
        except pytest.skip.Exception as error:
            pytest.fail(f'Valid release alias {release!r} was skipped: {error}')

    assert releases == ('14.5-RELEASE', '15.1-RELEASE')
    latest.assert_called_once_with()


@pytest.mark.parametrize(
    'server,user,password,auth,root_dir,http,_file,noupdate',
    [
        ('download.freebsd.org', 'anonymous', 'anonymous@', None, None,
         False, False, False),
        ('mirror.example.test', 'anonymous', 'anonymous@', None, '/releases',
         False, False, False),
        ('mirror.example.test', 'test-user', 'test-password', 'basic',
         '/private/releases', False, False, True),
        ('mirror.example.test', None, None, None, '/http/releases',
         True, False, False),
        (None, None, None, None, '/srv/releases', False, True, True),
        ('download.freebsd.org', 'anonymous', 'anonymous@', None, None,
         False, False, True),
    ],
    ids=['defaults', 'mirror', 'authenticated', 'http', 'local', 'no-update'],
)
def test_both_fetches_preserve_options(
    fetch_options, fetch_tests, server, user, password, auth, root_dir,
    http, _file, noupdate
):
    initial, upgrade = fetch_tests
    source, target = '14.5-RELEASE', '15.1-RELEASE'
    options_before = list(fetch_options)
    commands = []

    def invoke(command):
        commands.append(list(command))
        # Match invoke_cli's mutation to detect accidental fixture reuse.
        command.insert(0, 'iocage')

    jail = mock.Mock(return_value=SimpleNamespace(exists=True))
    initial.test_fetch(target, fetch_options, invoke)
    upgrade.test_01_create_jail_with_older_release(
        invoke, jail, (source, target), fetch_options,
    )

    expected = {
        'server': server or 'download.freebsd.org',
        'user': user or 'anonymous', 'password': password or 'anonymous@',
        'auth': auth or None, 'root_dir': root_dir or None,
        '_file': _file, 'update': not noupdate, 'props': (),
    }
    parsed = []
    for command, release in zip(commands[:2], (target, source)):
        assert command[0] == 'fetch'
        assert ('--http' in command or '-h' in command) is http
        with fetch_cli.make_context('fetch', command[1:]) as context:
            params = context.params
            assert params['release'] == release
            assert {key: params[key] for key in expected} == expected
            parsed.append(params['http'])

    assert parsed[0] == parsed[1]
    assert fetch_options == options_before
    assert commands[2:] == [[
        'create', '-r', source, '-n', upgrade.JAIL_NAME, 'nat=1',
        'allow_raw_sockets=1',
    ]]
    jail.assert_called_once_with(upgrade.JAIL_NAME)
