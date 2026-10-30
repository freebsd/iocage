import subprocess
import sys
from pathlib import Path


def test_lazy_group_help_and_boolean_options():
    # CLI initialization owns stdout/stderr and checks the host's ZFS sysctl.
    # Keep those process effects outside pytest, and mock only that host check.
    program = '''
import sys
from unittest import mock
with mock.patch('subprocess.check_call'), mock.patch('locale.setlocale'):
    import iocage_cli
import click
from click.testing import CliRunner
assert isinstance(iocage_cli.cli, click.Group)
sys.argv = ['iocage', '--help']
runner = CliRunner()
result = runner.invoke(iocage_cli.cli, ['--help'])
assert result.exit_code == 0, result.output
assert 'start' in result.output and 'stop' in result.output
from iocage_cli.list import cli as list_cli
with (
    mock.patch('iocage_lib.ioc_common.checkoutput',
               return_value='15.1-RELEASE'),
    mock.patch('iocage_lib.iocage.IOCage') as iocage,
):
    instance = iocage.return_value
    instance.list.return_value = []
    instance.fetch.return_value = []
    for args, expected_header in (([], True), (['--header'], False)):
        result = runner.invoke(list_cli, args)
        assert result.exit_code == 0, result.output
        assert instance.list.call_args.args[1] is expected_header
    for args, expected_http in (
        (['--remote'], True), (['--remote', '--http'], False)
    ):
        result = runner.invoke(list_cli, args)
        assert result.exit_code == 0, result.output
        assert instance.fetch.call_args.kwargs['http'] is expected_http
'''
    result = subprocess.run(
        [sys.executable, '-c', program], text=True, capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, result.stdout + result.stderr
