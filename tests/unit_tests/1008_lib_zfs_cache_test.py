from unittest import mock

import pytest

from iocage_lib import dataset, pools, resource
from iocage_lib.cache import Cache
from iocage_lib.ioc_json import IOCJson


@pytest.fixture
def resource_cache(monkeypatch):
    cache = Cache()
    cache.pool_data = {'testpool': {'health': 'ONLINE', 'comment': '-'}}
    cache.dataset_data = {
        'testpool': {
            'encryption': 'off',
            'org.freebsd.ioc:active': 'yes',
            'mountpoint': '/testpool',
        },
        'testpool/iocage': {
            'encryption': 'off',
            'mounted': 'yes',
            'mountpoint': '/testpool/iocage',
        },
    }
    cache.ioc_pool = 'testpool'
    monkeypatch.setattr(dataset, 'cache', cache)
    monkeypatch.setattr(pools, 'cache', cache)
    monkeypatch.setattr(resource, 'iocage_cache', cache)
    return cache


@pytest.mark.parametrize('encryption, keystatus, locked', [
    ('off', None, False),
    ('aes-256-gcm', 'available', False),
    ('aes-256-gcm', 'unavailable', True),
])
def test_uncached_pool_health_preserves_dataset_lock_state(
    resource_cache, encryption, keystatus, locked
):
    root_properties = resource_cache.dataset_data['testpool']
    root_properties['encryption'] = encryption
    if keystatus is not None:
        root_properties['keystatus'] = keystatus

    with mock.patch(
        'iocage_lib.resource.properties',
        return_value={'health': 'ONLINE', 'comment': '-'},
    ) as read_properties:
        assert pools.Pool('testpool', cache=False).health == 'ONLINE'

    read_properties.assert_called_once_with('testpool', 'zpool')
    assert dataset.Dataset('testpool').locked is locked
    assert resource_cache.dataset_data['testpool'] == root_properties


def test_configuration_reads_after_uncached_pool_health(resource_cache):
    with mock.patch(
        'iocage_lib.resource.properties',
        return_value={'health': 'ONLINE', 'comment': '-'},
    ):
        assert pools.Pool('testpool', cache=False).health == 'ONLINE'

    with mock.patch.object(IOCJson, 'get_mac_prefix', return_value='02ff60'):
        configuration = IOCJson(checking_datasets=True)

    assert configuration.json_get_value('pool') == 'testpool'
    assert configuration.json_get_value('iocroot') == '/testpool/iocage'


def test_lazy_pool_properties_use_pool_cache(resource_cache):
    # The pool enters the cached inventory after its handle was created.
    pool_properties = resource_cache.pool_data.pop('testpool')
    resource_cache.pool_data['otherpool'] = {'health': 'ONLINE'}
    pool = pools.Pool('testpool')
    resource_cache.pool_data['testpool'] = pool_properties

    assert pool.health == 'ONLINE'
    assert dataset.Dataset('testpool').locked is False
