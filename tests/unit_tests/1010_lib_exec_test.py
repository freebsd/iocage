import os
import resource
import select
import sys

import pytest

from iocage_lib.ioc_exceptions import CommandFailed
from iocage_lib.ioc_exec import SilentExec


def run_command(stdout, stderr, status, delay=0, close_stderr=False, **kwargs):
    program = (
        'import os\nimport sys\nimport time\n'
        f'sys.stdout.write({stdout!r})\n'
        'sys.stdout.flush()\n'
        f'time.sleep({delay})\n'
        f'sys.stderr.write({stderr!r})\n'
        'sys.stderr.flush()\n'
    )
    if close_stderr:
        program += 'os.close(2)\n'
    program += f'time.sleep({delay})\nsys.exit({status})\n'
    return SilentExec(
        [sys.executable, '-c', program], None, unjailed=True, decode=True,
        **kwargs,
    )


def test_failed_commands_preserve_exit_handling():
    with pytest.raises(CommandFailed) as raised:
        run_command('Command output\n', 'Command failed\n', 1)

    assert b'Command failed\n' in b''.join(raised.value.message)


def test_warning_only_eol_status_preserves_existing_handling():
    # freebsd-update returns 1 for this warning after a completed fetch.
    # Simulate the notice; this is not the release's current EOL status.
    notice = (
        'WARNING: FreeBSD 15.1-RELEASE HAS PASSED ITS END-OF-LIFE DATE\n'
    )
    result = run_command(notice, '', 1)

    assert result.stdout == notice
    assert result.stderr == ''


@pytest.mark.parametrize('delay', [0, 0.02])
def test_successful_commands_preserve_output(delay):
    result = run_command(
        'Command output\n', 'Diagnostic output\n', 0, delay=delay,
    )

    assert result.stdout == 'Command output\n'
    assert result.stderr == 'Diagnostic output\n'


def test_eof_reads_preserve_failure_diagnostics():
    with pytest.raises(CommandFailed) as raised:
        run_command(
            'Command output\n', 'Command failed\n', 1,
            delay=0.02, close_stderr=True,
        )

    assert b'Command failed\n' in b''.join(raised.value.message)


def test_release_update_preserves_no_pending_updates_handling():
    result = run_command(
        'No updates are available to install.\n', '', 2, uuid=None,
    )

    assert result.stdout == 'No updates are available to install.\n'


def test_commands_work_with_high_numbered_pipe_descriptors():
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    required = 1100
    if hard != resource.RLIM_INFINITY and hard < required:
        pytest.skip('Process descriptor limit is below the test requirement')
    descriptors = []
    try:
        if soft < required:
            resource.setrlimit(resource.RLIMIT_NOFILE, (required, hard))
        while not descriptors or descriptors[-1] < 1050:
            descriptors.append(os.open(os.devnull, os.O_RDONLY))
        result = run_command('Command output\n', 'Diagnostic output\n', 0)
        assert result.stdout == 'Command output\n'
        assert result.stderr == 'Diagnostic output\n'
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


@pytest.mark.parametrize('closed', [(1,), (2,), (1, 2)])
@pytest.mark.parametrize('decode', [False, True])
def test_early_pipe_eof_does_not_spin_or_retain_empty_output(
    mocker, closed, decode,
):
    poller = mocker.Mock(wraps=select.poll())
    mocker.patch('iocage_lib.ioc_exec.select.poll', return_value=poller)
    program = (
        'import os, time\n'
        'os.write(1, b"stdout before\\n")\n'
        'os.write(2, b"stderr before\\n")\n'
        f'for fd in {closed!r}: os.close(fd)\n'
        'time.sleep(0.05)\n'
    )
    if 1 not in closed:
        program += 'os.write(1, b"stdout after\\n")\n'
    if 2 not in closed:
        program += 'os.write(2, b"stderr after\\n")\n'

    result = SilentExec(
        [sys.executable, '-c', program], None, unjailed=True, decode=decode,
    )

    stdout = b'stdout before\n'
    stderr = b'stderr before\n'
    if 1 not in closed:
        stdout += b'stdout after\n'
    if 2 not in closed:
        stderr += b'stderr after\n'
    assert result.stdout == (stdout.decode() if decode else stdout)
    assert result.stderr == (stderr.decode() if decode else stderr)
    assert all(any(chunk) for chunk in result.output)
    # A hangup must not produce thousands of immediate wakeups.
    assert poller.poll.call_count < 10


@pytest.mark.parametrize('decode', [False, True])
def test_hangups_drain_buffered_output(decode):
    program = (
        'import os, time\n'
        'os.write(1, b"o" * 131072)\n'
        'os.write(2, b"e" * 131072)\n'
        'os.close(1)\nos.close(2)\n'
        'time.sleep(0.02)\n'
    )

    result = SilentExec(
        [sys.executable, '-c', program], None, unjailed=True, decode=decode,
    )

    assert result.stdout == ('o' if decode else b'o') * 131072
    assert result.stderr == ('e' if decode else b'e') * 131072


def test_both_pipes_closed_before_failure_preserve_diagnostics():
    program = (
        'import os, sys, time\n'
        'os.write(2, b"Command failed\\n")\n'
        'os.close(1)\nos.close(2)\n'
        'time.sleep(0.02)\nsys.exit(1)\n'
    )

    with pytest.raises(CommandFailed) as raised:
        SilentExec([sys.executable, '-c', program], None, unjailed=True)

    assert b'Command failed\n' in b''.join(raised.value.message)


def test_command_exit_does_not_wait_for_inherited_open_pipes(mocker):
    proc = mocker.Mock(returncode=0)
    proc.stdout.fileno.return_value = 3
    proc.stderr.fileno.return_value = 4
    proc.poll.return_value = 0
    mocker.patch('iocage_lib.ioc_exec.su.Popen', return_value=proc)
    mocker.patch('iocage_lib.ioc_exec.fcntl.fcntl', return_value=0)
    poller = mocker.Mock()
    # The child exited, but descendants still hold both output pipes open.
    poller.poll.side_effect = [
        [], [], AssertionError('Do not wait for inherited pipes to close'),
    ]
    mocker.patch('iocage_lib.ioc_exec.select.poll', return_value=poller)

    result = SilentExec(['command'], None, unjailed=True)

    assert result.stdout == result.stderr == b''


@pytest.mark.parametrize('decode', [False, True])
def test_readiness_without_data_does_not_close_pipe(mocker, decode):
    proc = mocker.Mock(returncode=0)
    proc.stdout.fileno.return_value = 3
    proc.stderr.fileno.return_value = 4
    proc.stdout.read.side_effect = [None, b'stdout', b'']
    proc.stderr.read.side_effect = [b'stderr', None, b'']
    proc.poll.return_value = None
    mocker.patch('iocage_lib.ioc_exec.su.Popen', return_value=proc)
    mocker.patch('iocage_lib.ioc_exec.fcntl.fcntl', return_value=0)
    poller = mocker.Mock()
    open_pipes = set()
    poller.register.side_effect = lambda fd, events: open_pipes.add(fd)
    poller.unregister.side_effect = open_pipes.remove
    poller.poll.side_effect = lambda timeout: [
        (fd, select.POLLIN) for fd in open_pipes
    ]
    mocker.patch('iocage_lib.ioc_exec.select.poll', return_value=poller)

    result = SilentExec(['command'], None, unjailed=True, decode=decode)

    assert result.stdout == ('stdout' if decode else b'stdout')
    assert result.stderr == ('stderr' if decode else b'stderr')
    assert all(any(chunk) for chunk in result.output)
