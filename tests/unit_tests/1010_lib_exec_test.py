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


@pytest.mark.parametrize('stdout', [
    'Command output\n',
    # Simulate the notice; this does not describe the release's current status.
    'WARNING: FreeBSD 15.1-RELEASE HAS PASSED ITS END-OF-LIFE DATE\n',
])
def test_failed_commands_propagate_even_with_eol_notice(stdout):
    with pytest.raises(CommandFailed) as raised:
        run_command(stdout, 'Command failed\n', 1)

    assert b'Command failed\n' in b''.join(raised.value.message)


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
