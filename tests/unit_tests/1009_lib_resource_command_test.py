from types import SimpleNamespace
from unittest import mock

import pytest

from iocage_lib.ioc_exceptions import CommandFailed
from iocage_lib.ioc_json import IOCCpuset, IOCRCTL


OPERATIONS = [
    lambda: IOCCpuset('test').set_cpuset('1'),
    IOCCpuset.retrieve_cpu_sets,
    lambda: IOCRCTL('test').rctl_rules_exist(),
]
OPERATION_NAMES = ['set_cpuset', 'retrieve_cpu_sets', 'rctl_rules_exist']


@pytest.mark.parametrize('operation', OPERATIONS, ids=OPERATION_NAMES)
@pytest.mark.parametrize('error', [
    OSError('execution failed'),
    RuntimeError('unexpected failure'),
    KeyboardInterrupt(),
], ids=['os_error', 'runtime_error', 'interrupt'])
def test_resource_commands_propagate_unexpected_errors(operation, error):
    with mock.patch('iocage_lib.ioc_exec.SilentExec', side_effect=error):
        with pytest.raises(type(error)) as raised:
            operation()

    assert raised.value is error


@pytest.mark.parametrize(
    'operation, failure_result', list(zip(OPERATIONS, [True, -2, False])),
    ids=OPERATION_NAMES,
)
def test_resource_commands_preserve_expected_failures(
    operation, failure_result
):
    with mock.patch(
        'iocage_lib.ioc_exec.SilentExec',
        side_effect=CommandFailed('command failed'),
    ):
        assert operation() == failure_result


@pytest.mark.parametrize('value, expected', [(None, 'all'), ('1', '1')])
def test_set_cpuset_preserves_success_and_command(value, expected):
    with mock.patch('iocage_lib.ioc_exec.SilentExec') as execute:
        assert IOCCpuset('test').set_cpuset(value) is False

    execute.assert_called_once_with(
        ['cpuset', '-l', expected, '-j', 'ioc-test'],
        None, unjailed=True, decode=True,
    )


@pytest.mark.parametrize('output, expected', [
    ('cpuset 0 mask: 0, 1, 2, 3\ncpuset 0 domain policy: first-touch\n', 3),
    ('unexpected output\n', -2),
])
def test_retrieve_cpu_sets_preserves_output_handling(output, expected):
    with mock.patch(
        'iocage_lib.ioc_exec.SilentExec',
        return_value=SimpleNamespace(stdout=output),
    ) as execute:
        assert IOCCpuset.retrieve_cpu_sets() == expected

    execute.assert_called_once_with(
        ['cpuset', '-g', '-s', '0'], None, unjailed=True, decode=True,
    )


@pytest.mark.parametrize('prop, output, expected', [
    (None, 'jail:ioc-test:pcpu:deny=20/jail\n', True),
    ('pcpu', 'jail:ioc-test:pcpu:deny=20/jail\n', True),
    ('memoryuse', 'jail:ioc-test:pcpu:deny=20/jail\n', False),
    (None, 'jail:ioc-other:pcpu:deny=20/jail\n', False),
])
def test_rctl_rules_exist_preserves_rule_filter(prop, output, expected):
    with mock.patch(
        'iocage_lib.ioc_exec.SilentExec',
        return_value=SimpleNamespace(stdout=output),
    ) as execute:
        assert IOCRCTL('test').rctl_rules_exist(prop) is expected

    execute.assert_called_once_with(
        ['rctl'], None, unjailed=True, decode=True,
    )
