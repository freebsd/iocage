"""Cached checks retain validation and use fresh state when provisioning."""

from unittest import mock

import pytest

from iocage_lib import ioc_check, ioc_common


@pytest.fixture
def datasets(monkeypatch):
    monkeypatch.setattr(ioc_common, 'INTERACTIVE', False)
    with mock.patch.object(ioc_check, 'Dataset') as factory, \
            mock.patch.object(ioc_check.iocage_lib.ioc_json, 'IOCJson') as js, \
            mock.patch.object(ioc_check.shutil, 'rmtree') as cleanup, \
            mock.patch.object(ioc_check.os, 'geteuid', return_value=0), \
            mock.patch.object(ioc_check.cache, 'reset') as reset:
        js.return_value.json_get_value.return_value = 'tank'
        resources = {}

        def dataset(name, cache=True):
            if name not in resources:
                ds = mock.Mock()
                ds.name = name
                ds.exists = True
                ds.path = f'/{name}'
                ds.properties = {'exec': 'on'}
                resources[name] = ds
            return resources[name]

        factory.side_effect = dataset
        yield factory, resources, cleanup, reset


@pytest.mark.parametrize('use_cache,reset_cache,expected', [
    (False, False, False), (True, False, True), (False, True, True)
])
def test_check_cache_is_independent_of_reset(
    datasets, use_cache, reset_cache, expected
):
    factory, _, cleanup, reset = datasets
    ioc_check.IOCCheck(use_cache=use_cache, reset_cache=reset_cache)
    assert all(call.kwargs['cache'] is expected
               for call in factory.call_args_list)
    assert reset.call_count == int(reset_cache)
    cleanup.assert_called_once_with('/tank/iocage/.plugin_index',
                                    ignore_errors=True)


@pytest.mark.parametrize('invalid', ['mountpoint', 'exec'])
def test_cached_check_rejects_invalid_dataset(datasets, invalid):
    factory, _, _, _ = datasets
    ds = factory('tank/iocage', cache=True)
    if invalid == 'mountpoint':
        ds.path = ''
    else:
        ds.properties = {'exec': 'off'}
    with pytest.raises(RuntimeError, match='mountpoint|exec=off'):
        ioc_check.IOCCheck(use_cache=True)


@pytest.mark.parametrize('created_by_other_process', [False, True])
def test_missing_dataset_gets_fresh_check_under_lock(
    datasets, created_by_other_process
):
    factory, _, _, _ = datasets
    original = factory.side_effect
    missing = original('tank/iocage/images')
    missing.exists = False
    fresh = mock.Mock()
    fresh.exists = created_by_other_process
    fresh.properties = {'exec': 'on'}

    def dataset(name, cache=True):
        if name == 'tank/iocage/images' and not cache:
            assert ioc_check.DATASET_CREATION_LOCK.locked()
            return fresh
        return original(name, cache)

    factory.side_effect = dataset
    ioc_check.IOCCheck(use_cache=True)
    assert mock.call('tank/iocage/images', cache=False) in \
        factory.call_args_list
    assert fresh.create.call_count == int(not created_by_other_process)


def test_nonroot_cannot_create_missing_dataset(datasets):
    factory, _, _, _ = datasets
    factory('tank/iocage/images').exists = False
    with mock.patch.object(ioc_check.os, 'geteuid', return_value=1001):
        with pytest.raises(RuntimeError, match='Run as root'):
            ioc_check.IOCCheck(use_cache=True)
