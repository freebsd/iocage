"""Import the real CLI safely and initialize it only on invocation."""
import io
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


def test_import_preserves_process_state():
    program = '''
import sys
from unittest import mock
import click.core
verify = getattr(click.core, '_verify_python3_env', None)
stdout, stderr = sys.stdout, sys.stderr
with (
    mock.patch('locale.setlocale') as locale,
    mock.patch('signal.signal') as signal,
    mock.patch('subprocess.check_call') as check,
    mock.patch('iocage_lib.ioc_common.set_interactive') as interactive,
):
    import iocage_cli
    assert sys.stdout is stdout and sys.stderr is stderr
    locale.assert_not_called()
    signal.assert_not_called()
    check.assert_not_called()
    interactive.assert_not_called()
    assert getattr(click.core, '_verify_python3_env', None) is verify
    assert callable(iocage_cli.IOCageCLI.main)
    assert callable(iocage_cli.print_version)
'''
    result = subprocess.run(
        [sys.executable, '-c', program], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('option', ['--help', '--version'])
def test_runtime_initializes_before_eager_options(option):
    import iocage_cli
    from click.testing import CliRunner

    with (
        mock.patch.object(iocage_cli, '_configure_runtime') as configure,
        mock.patch.object(sys, 'argv', ['iocage', option]),
    ):
        result = CliRunner().invoke(iocage_cli.cli, [option])

    assert result.exit_code == 0, result.output
    configure.assert_called_once_with()
    assert ('start' if option == '--help' else 'Version') in result.output


def test_version_exits_with_explicit_success(capsys):
    import iocage_cli

    context = mock.Mock(resilient_parsing=False)
    context.exit.side_effect = SystemExit(0)
    with pytest.raises(SystemExit) as exit_info:
        iocage_cli.print_version(context, None, True)

    context.exit.assert_called_once_with(0)
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f'Version\t{iocage_cli.__version__}\n'


@pytest.mark.parametrize('reconfigurable', [False, True])
def test_runtime_accepts_captured_streams(reconfigurable):
    import iocage_cli

    stdout, stderr = io.StringIO(), io.StringIO()
    if reconfigurable:
        stdout.reconfigure = mock.Mock()
        stderr.reconfigure = mock.Mock()
    with (
        mock.patch.object(sys, 'stdout', stdout),
        mock.patch.object(sys, 'stderr', stderr),
        mock.patch.object(iocage_cli.locale, 'setlocale') as locale,
        mock.patch.object(iocage_cli.signal, 'signal') as signal,
        mock.patch.object(iocage_cli, 'set_interactive') as interactive,
        mock.patch.object(iocage_cli.su, 'check_call') as check,
    ):
        iocage_cli._configure_runtime()
        assert sys.stdout is stdout and sys.stderr is stderr

    locale.assert_called_once_with(iocage_cli.locale.LC_ALL, 'en_US.UTF-8')
    interactive.assert_called_once_with(True)
    assert signal.call_args_list == [
        mock.call(iocage_cli.signal.SIGINT,
                  iocage_cli.signal.default_int_handler),
        mock.call(iocage_cli.signal.SIGPIPE, iocage_cli.signal.SIG_DFL),
    ]
    check.assert_called_once_with(
        ['sysctl', 'vfs.zfs.version.spa'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if reconfigurable:
        for stream in (stdout, stderr):
            stream.reconfigure.assert_called_once_with(
                encoding='utf8', line_buffering=True,
            )


@pytest.mark.parametrize('stream_name', ['stdout', 'stderr'])
def test_runtime_preserves_partially_read_streams(tmp_path, stream_name):
    import iocage_cli

    with (tmp_path / stream_name).open('w+', encoding='utf8') as stream:
        stream.write('existing output\n')
        stream.seek(0)
        assert stream.read(1) == 'e'
        streams = {'stdout': io.StringIO(), 'stderr': io.StringIO()}
        streams[stream_name] = stream

        with (
            mock.patch.object(sys, 'stdout', streams['stdout']),
            mock.patch.object(sys, 'stderr', streams['stderr']),
            mock.patch.object(iocage_cli.locale, 'setlocale'),
            mock.patch.object(iocage_cli.signal, 'signal'),
            mock.patch.object(iocage_cli, 'set_interactive') as interactive,
            mock.patch.object(iocage_cli.su, 'check_call') as check,
        ):
            assert iocage_cli.cli.main(
                ['--version'], standalone_mode=False,
            ) == 0
            assert getattr(sys, stream_name) is stream
            assert not stream.closed
            stream.write('still writable\n')
            stream.seek(0)
            contents = stream.read()

        assert contents.startswith('existing output\n')
        assert 'still writable\n' in contents
        output = (contents if stream_name == 'stdout'
                  else streams['stdout'].getvalue())
        assert f'Version\t{iocage_cli.__version__}\n' in output
        interactive.assert_called_once_with(True)
        check.assert_called_once_with(
            ['sysctl', 'vfs.zfs.version.spa'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )


def test_runtime_preserves_missing_zfs_error():
    import iocage_cli

    with (
        mock.patch.object(sys, 'stdout', io.StringIO()),
        mock.patch.object(sys, 'stderr', io.StringIO()),
        mock.patch.object(iocage_cli.locale, 'setlocale'),
        mock.patch.object(iocage_cli.signal, 'signal'),
        mock.patch.object(iocage_cli, 'set_interactive'),
        mock.patch.object(iocage_cli.su, 'check_call',
                          side_effect=subprocess.CalledProcessError(
                              1, ['sysctl', 'vfs.zfs.version.spa'])),
    ):
        with pytest.raises(SystemExit, match='ZFS is required to use iocage'):
            iocage_cli._configure_runtime()
