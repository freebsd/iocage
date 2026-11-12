import subprocess
import sys
import textwrap

from pathlib import Path

import pytest


def _run_cli_check(check, args=()):
    """Keep CLI stream and signal changes inside a child process."""
    # Stub host setup while exercising the real Click commands.
    program = '''
import sys
from unittest import mock
from click.testing import CliRunner

with mock.patch('subprocess.check_call'), mock.patch('locale.setlocale'):
    import iocage_cli
'''
    program += textwrap.indent(textwrap.dedent(check), '    ')
    result = subprocess.run(
        [sys.executable, '-c', program, *args], text=True, capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_group_help_discovers_commands():
    """A Click Group must still discover lazily imported subcommands."""
    _run_cli_check('''
        sys.argv = ['iocage', '--help']
        result = CliRunner().invoke(iocage_cli.cli, ['--help'])
        assert result.exit_code == 0, result.output
        assert 'start' in result.output
        assert 'stop' in result.output
    ''')


@pytest.mark.parametrize('args, expected_header', [
    ([], True), (['--header'], False),
], ids=['default', 'no-headers'])
def test_list_header_flag_disables_headers(args, expected_header):
    """Preserve the existing scripting flag when upgrading Click."""
    _run_cli_check(f'''
        from iocage_cli.list import cli as list_cli

        # Only the FreeBSD/HardenedBSD distinction matters, not a release.
        with (
            mock.patch('iocage_lib.ioc_common.checkoutput',
                       return_value='FreeBSD'),
            mock.patch('iocage_lib.iocage.IOCage') as iocage,
        ):
            iocage.return_value.list.return_value = []
            result = CliRunner().invoke(list_cli, sys.argv[1:])
            assert result.exit_code == 0, result.output
            assert (
                iocage.return_value.list.call_args.args[1]
                is {expected_header}
            )
    ''', args)


@pytest.mark.parametrize('args, expected_http', [
    (['--remote'], True), (['--remote', '--http'], False),
], ids=['default', 'no-http'])
def test_remote_http_flag_disables_http(args, expected_http):
    _run_cli_check(f'''
        from iocage_cli.list import cli as list_cli

        # Only the FreeBSD/HardenedBSD distinction matters, not a release.
        with (
            mock.patch('iocage_lib.ioc_common.checkoutput',
                       return_value='FreeBSD'),
            mock.patch('iocage_lib.iocage.IOCage') as iocage,
        ):
            iocage.return_value.fetch.return_value = []
            result = CliRunner().invoke(list_cli, sys.argv[1:])
            assert result.exit_code == 0, result.output
            assert (
                iocage.return_value.fetch.call_args.kwargs['http']
                is {expected_http}
            )
    ''', args)
