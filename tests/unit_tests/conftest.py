"""Isolated package installation fixtures shared by creation regressions."""

import contextlib
import logging

from types import SimpleNamespace
from unittest import mock

import pytest

from iocage_lib import ioc_common, ioc_create, ioc_exceptions


@pytest.fixture
def package_install(monkeypatch):
    create = ioc_create.IOCCreate.__new__(ioc_create.IOCCreate)
    create.pkglist = ['missing-package', 'nano']
    create.plugin = create.silent = False
    create.callback = None
    create.log = logging.getLogger('iocage')
    state = SimpleNamespace(running=False)
    outcomes, attempts, logs = {}, {}, []
    start = mock.Mock(
        side_effect=lambda *a, **k: setattr(state, 'running', True))
    stop = mock.Mock(
        side_effect=lambda *a, **k: setattr(state, 'running', False))
    listing = mock.Mock()
    listing.list_get_jid.side_effect = lambda _: (state.running, '42')
    original_log = ioc_common.logit

    def log(content, **kwargs):
        logs.append(content)
        return original_log(content, **kwargs)

    @contextlib.contextmanager
    def execute(command, *args, **kwargs):
        key = command[-1] if command[1] == 'install' else 'upgrade'
        attempts[key] = attempts.get(key, 0) + 1
        values = outcomes.get(key, [])
        error = values.pop(0) if values else None

        def output():
            if error is not None:
                raise ioc_exceptions.CommandFailed(error)
            yield b'', b''

        yield output()

    bootstrap = mock.Mock(
        return_value=SimpleNamespace(returncode=0, stdout=b''))
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', False)
    monkeypatch.setattr(ioc_common, 'logit', log)
    monkeypatch.setattr(ioc_create.iocage_lib.ioc_list, 'IOCList',
                        mock.Mock(return_value=listing))
    monkeypatch.setattr(ioc_create.iocage_lib.ioc_start, 'IOCStart', start)
    monkeypatch.setattr(ioc_create.iocage_lib.ioc_stop, 'IOCStop', stop)
    monkeypatch.setattr(ioc_create.iocage_lib.ioc_exec, 'IOCExec', execute)
    monkeypatch.setattr(ioc_create.su, 'run', bootstrap)
    return SimpleNamespace(create=create, state=state, outcomes=outcomes,
                           attempts=attempts, logs=logs, start=start, stop=stop,
                           bootstrap=bootstrap)
