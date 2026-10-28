"""Quick prefix enumeration may reuse a complete filesystem snapshot."""

from unittest import mock

import pytest

from iocage_lib import cache as cache_module
from iocage_lib import dataset, ioc_list
from iocage_lib.zfs import ZFSException

ROOT = 'tank/iocage'
JAILS = f'{ROOT}/jails'


@pytest.fixture
def metadata(monkeypatch):
    cached = cache_module.Cache()
    cached.pool_data = {'tank': {}}
    cached.dataset_data = {
        'tank': {'type': 'filesystem', 'org.freebsd.ioc:active': 'yes'},
    }
    rows = {
        name: {'type': 'filesystem', 'mountpoint': f'/mnt/{name}'}
        for name in (ROOT, JAILS, f'{JAILS}/dev.two', f'{JAILS}/dev',
                     f'{JAILS}/dev/root', f'{ROOT}/templates',
                     f'{ROOT}/templates/template', f'{ROOT}/jails2/other')
    }
    query = mock.Mock(return_value=rows)
    probe = mock.Mock(return_value=True)
    listing = mock.Mock(return_value=sorted(rows))
    monkeypatch.setattr(cache_module, 'all_properties', query)
    monkeypatch.setattr(cache_module, 'dataset_exists', probe)
    monkeypatch.setattr(cache_module, 'get_all_dependents', listing)
    return cached, rows, query, probe, listing


@pytest.mark.parametrize('depth,expected', [
    (None, [JAILS, f'{JAILS}/dev', f'{JAILS}/dev.two', f'{JAILS}/dev/root']),
    (0, [JAILS, f'{JAILS}/dev', f'{JAILS}/dev.two', f'{JAILS}/dev/root']),
    (1, [f'{JAILS}/dev', f'{JAILS}/dev.two']),
    (2, [f'{JAILS}/dev', f'{JAILS}/dev.two', f'{JAILS}/dev/root']),
])
def test_complete_snapshot_preserves_depth_and_order(
    metadata, depth, expected,
):
    cached, _, query, probe, listing = metadata
    cached.datasets
    # Single-resource updates can add types outside the filesystem snapshot.
    cached.update_dataset_data(f'{JAILS}/disk', {'type': 'volume'})
    cached.update_dataset_data(f'{JAILS}/dev@snap', {'type': 'snapshot'})
    assert cached.dependents(
        JAILS, depth, use_cached_datasets=True) == expected
    query.assert_called_once_with(
        [ROOT], recursive=True, types=['filesystem'])
    probe.assert_called_once_with(ROOT)
    listing.assert_not_called()


def test_default_enumeration_keeps_existing_names_query(metadata):
    cached, _, _, _, listing = metadata
    cached.datasets
    listing.return_value.append(f'{JAILS}/new')
    assert f'{JAILS}/new' in cached.dependents(JAILS, 1)
    listing.assert_called_once_with()


@pytest.mark.parametrize('requested', ['tank', 'tank/other', 'tank/iocage2'])
def test_snapshot_outside_scope_falls_back(metadata, requested):
    cached, _, _, _, listing = metadata
    cached.datasets
    listing.return_value = [requested, f'{requested}/fresh']
    assert cached.dependents(
        requested, 1, use_cached_datasets=True) == [f'{requested}/fresh']
    listing.assert_called_once_with()


def test_partial_metadata_does_not_count_as_complete_snapshot(metadata):
    cached, _, query, _, listing = metadata
    cached.update_dataset_data(f'{JAILS}/dev', {'type': 'filesystem'})
    assert cached.dataset_tree_root is None
    assert cached.dependents(JAILS, 1, use_cached_datasets=True) == [
        f'{JAILS}/dev', f'{JAILS}/dev.two',
    ]
    listing.assert_called_once_with()
    query.assert_not_called()


def test_failed_query_does_not_mark_snapshot_complete(metadata):
    cached, _, query, _, listing = metadata
    query.side_effect = ZFSException(1, 'query failed')
    with pytest.raises(ZFSException):
        cached.datasets
    assert cached.dataset_tree_root is None
    cached.dependents(JAILS, 1, use_cached_datasets=True)
    listing.assert_called_once_with()


def test_global_snapshot_covers_other_pools(metadata):
    cached, rows, query, probe, listing = metadata
    probe.return_value = False
    rows['other/jails/new'] = {'type': 'filesystem'}
    cached.datasets
    assert cached.dataset_tree_root == ''
    assert cached.dependents(
        'other/jails', 1, use_cached_datasets=True) == ['other/jails/new']
    query.assert_called_once_with([], recursive=True, types=['filesystem'])
    listing.assert_not_called()


def test_reset_invalidates_snapshot_scope(metadata):
    cached, _, _, _, listing = metadata
    cached.datasets
    cached.reset()
    assert cached.dataset_tree_root is None
    cached.dependents(JAILS, 1, use_cached_datasets=True)
    listing.assert_called_once_with()


def test_cached_dependents_still_skip_locked_datasets(
    metadata, monkeypatch,
):
    cached, rows, _, _, listing = metadata
    rows[f'{JAILS}/dev.two'].update(
        encryption='aes-256-gcm', keystatus='unavailable')
    monkeypatch.setattr(dataset, 'cache', cached)
    parent = dataset.Dataset(JAILS)
    assert [child.name for child in parent.get_dependents(
        use_cached_datasets=True)] == [f'{JAILS}/dev']
    listing.assert_not_called()


def test_uncached_dependents_keep_native_query(metadata, monkeypatch):
    cached, _, _, _, listing = metadata
    monkeypatch.setattr(dataset, 'cache', cached)
    parent = dataset.Dataset(JAILS)
    with mock.patch.object(dataset, 'get_dependents', return_value=[]) as query:
        assert list(parent.get_dependents(
            ds_cache=False, use_cached_datasets=True)) == []
    query.assert_called_once_with(JAILS, 1)
    listing.assert_not_called()


@pytest.mark.parametrize('quick', [False, True])
def test_uuid_listing_reuses_snapshot_only_when_requested(
    metadata, monkeypatch, quick,
):
    cached, _, _, _, listing = metadata
    monkeypatch.setattr(dataset, 'cache', cached)
    with mock.patch.object(ioc_list.iocage_lib.ioc_json, 'IOCJson') as config:
        config.return_value.pool = 'tank'
        config.return_value.iocroot = '/mnt/tank/iocage'
        result = ioc_list.IOCList('uuid', quick=quick).list_datasets()
    assert result == {
        'dev': '/mnt/tank/iocage/jails/dev',
        'dev.two': '/mnt/tank/iocage/jails/dev.two',
        'template': '/mnt/tank/iocage/templates/template',
    }
    assert listing.call_count == (0 if quick else 1)
