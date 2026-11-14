"""Package setup cleans up only the temporary state it owns."""

import json

from types import SimpleNamespace
from unittest import mock

import pytest

from iocage_lib import ioc_common, ioc_create, ioc_exceptions
from iocage_lib.ioc_start import IOCStart


@pytest.mark.parametrize('running', [False, True])
@pytest.mark.parametrize('failure', [None, 'bootstrap', 'upgrade', 'json'])
def test_install_cleanup(package_install, tmp_path, running, failure):
    install = package_install
    install.state.running = running
    if failure == 'bootstrap':
        install.bootstrap.return_value = SimpleNamespace(
            returncode=1, stdout=b'bootstrap failed')
    elif failure == 'upgrade':
        install.outcomes['upgrade'] = [[b'upgrade failed']]
    elif failure == 'json':
        path = tmp_path / 'packages.json'
        path.write_text('not JSON')
        install.create.pkglist = str(path)
    if failure:
        with pytest.raises((RuntimeError, json.JSONDecodeError)):
            install.create.create_install_packages('test', '/mock/jails/test',
                                                   repo='')
    else:
        install.create.create_install_packages('test', '/mock/jails/test',
                                               repo='')
    assert install.state.running is running
    assert install.stop.call_count == int(not running)


@pytest.mark.parametrize('readonly', ['on', 'off'])
@pytest.mark.parametrize('failure', [False, True])
def test_template_readonly_is_restored(package_install, readonly, failure):
    install = package_install
    dataset = mock.Mock(properties={'readonly': readonly})
    dataset.set_property.side_effect = dataset.properties.__setitem__
    if failure:
        install.bootstrap.return_value = SimpleNamespace(
            returncode=1, stdout=b'bootstrap failed')
    with mock.patch.object(ioc_create, 'Dataset', return_value=dataset):
        if failure:
            with pytest.raises(RuntimeError):
                install.create.create_install_packages(
                    'test', '/mock/templates/test', repo='')
        else:
            install.create.create_install_packages(
                'test', '/mock/templates/test', repo='')
    assert dataset.properties['readonly'] == readonly
    assert install.state.running is False


@pytest.mark.parametrize('interactive', [False, True])
def test_partial_start_is_cleaned_up(package_install, monkeypatch, interactive):
    install = package_install
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', interactive)

    def partial_start(*args, **kwargs):
        install.state.running = True
        ioc_common.logit({
            'level': 'EXCEPTION',
            'message': 'start failed after creating jail'
        })

    install.start.side_effect = partial_start
    exception = SystemExit if interactive else RuntimeError
    with pytest.raises(exception):
        install.create.create_install_packages('test', '/mock/jails/test',
                                               repo='')
    install.stop.assert_called_once()
    assert install.state.running is False


@pytest.mark.parametrize('interactive', [False, True])
@pytest.mark.parametrize('template', [False, True])
def test_concurrent_start_is_not_cleaned_up(
    package_install, monkeypatch, caplog, interactive, template
):
    install = package_install
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', interactive)
    monkeypatch.setattr(ioc_create.os, 'uname',
                        lambda: ('FreeBSD', 'armdev', '15.1-RELEASE'))
    dataset = mock.Mock(properties={'readonly': 'off'})
    monkeypatch.setattr(ioc_create, 'Dataset', mock.Mock(return_value=dataset))
    location = '/mock/templates/test' if template else '/mock/jails/test'

    def concurrent_start(*args, **kwargs):
        # Another operation starts the jail before IOCStart checks it.
        install.state.running = True
        dataset.properties['readonly'] = 'on'
        start = IOCStart(*args, unit_test=True, **kwargs)
        start.__start_jail__()

    install.start.side_effect = concurrent_start
    exception = SystemExit if interactive else ioc_exceptions.JailRunning
    with pytest.raises(exception) as error:
        install.create.create_install_packages('test', location, repo='')
    if interactive:
        assert error.value.code == 1
        assert isinstance(error.value.__cause__, ioc_exceptions.JailRunning)
        assert caplog.text.count('test is already running!') == 1
    else:
        assert str(error.value) == 'test is already running!'
    install.stop.assert_not_called()
    install.bootstrap.assert_not_called()
    dataset.set_property.assert_not_called()
    assert install.state.running is True
    assert dataset.properties['readonly'] == 'on'


def test_cleanup_failure_keeps_install_diagnostic(package_install, caplog):
    install = package_install
    install.bootstrap.return_value = SimpleNamespace(
        returncode=1, stdout=b'bootstrap failed')
    install.stop.side_effect = RuntimeError('cleanup failed')
    with pytest.raises(RuntimeError, match='cleanup failed'):
        install.create.create_install_packages('test', '/mock/jails/test',
                                               repo='')
    assert 'bootstrap failed' in caplog.text
