"""Keep functional-test helpers compatible with the public libzfs API."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


class NativeResource:
    """Expose the serializer contract without requiring native bindings."""

    def __init__(self, state):
        self.asdict = mock.Mock(return_value=state)

    def __getstate__(self, *args, **kwargs):
        raise AssertionError('Use the public asdict serializer')


@pytest.fixture
def helpers():
    binding = SimpleNamespace(ZFS=mock.MagicMock(), ZFSException=LookupError)
    path = Path(__file__).resolve().parents[1] / 'data_classes.py'
    spec = importlib.util.spec_from_file_location('test_zfs_helpers', path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict('sys.modules', libzfs=binding):
        spec.loader.exec_module(module)
    return module, binding.ZFS.return_value.__enter__.return_value


@pytest.mark.parametrize('active_count', [0, 1, 2])
def test_active_pool_selection(helpers, active_count):
    module, native = helpers
    pools = []
    for index in range(active_count + 1):
        root = NativeResource({'properties': {
            'org.freebsd.ioc:active': {
                'value': 'yes' if index < active_count else 'no',
            },
        }})
        root.mountpoint = f'/pool{index}'
        pools.append(SimpleNamespace(name=f'pool{index}', root_dataset=root))
    native.pools = pools
    zfs = module.ZFS()

    if active_count > 1:
        with pytest.raises(AssertionError, match='2 Active pools found'):
            zfs.set_pool()
    else:
        zfs.set_pool()
        assert zfs.pool == ('pool0' if active_count else None)
        assert zfs.pool_mountpoint == ('/pool0' if active_count else None)
        if active_count:
            # An established pool should not be looked up a second time.
            zfs.set_pool()

    for pool in pools:
        pool.root_dataset.asdict.assert_called_once_with(recursive=False)


@pytest.mark.parametrize('identifier', ['tank', 'tank/iocage'])
def test_pool_and_dataset_properties(helpers, identifier):
    module, native = helpers
    properties = {'mountpoint': {'value': '/tank/iocage'}}
    dataset = {'properties': properties, 'children': []}
    pool = {'root_dataset': dataset}
    native.get.return_value = NativeResource(pool)
    native.get_dataset.return_value = NativeResource(dataset)
    zfs = module.ZFS()

    assert zfs.get('tank') == pool
    assert zfs.get_dataset('tank/iocage') == dataset
    assert zfs.zfs_get_property(identifier, 'mountpoint') == '/tank/iocage'
    assert zfs.zfs_get_property(identifier, 'missing') == '-'
    native.get.return_value.asdict.assert_called_with()
    native.get_dataset.return_value.asdict.assert_called_with()


@pytest.mark.parametrize('recursive', [False, True])
@pytest.mark.parametrize('empty', [False, True])
def test_snapshot_selection(helpers, recursive, empty):
    module, native = helpers
    snapshots = [
        NativeResource({'id': f'{path}@snapshot', 'properties': {}})
        for path in ('tank/iocage', 'tank/iocage/child',
                     'tank/iocage/child/grandchild')
    ]
    datasets = [NativeResource({}) for _ in snapshots]
    for dataset, snapshot in zip(datasets, snapshots):
        dataset.snapshots = iter([] if empty else [snapshot])
    datasets[0].children_recursive = iter(datasets[1:])
    native.get_dataset.return_value = datasets[0]
    zfs = module.ZFS()
    lookup = (zfs.get_snapshots_recursively if recursive
              else zfs.get_snapshots)

    selected = [] if empty else snapshots if recursive else snapshots[:1]
    assert lookup('tank/iocage') == [s.asdict.return_value for s in selected]
    native.get_dataset.assert_called_once_with('tank/iocage')
    for snapshot in snapshots:
        if snapshot in selected:
            snapshot.asdict.assert_called_once_with()
        else:
            snapshot.asdict.assert_not_called()
    for dataset in datasets:
        dataset.asdict.assert_not_called()


def test_snapshot_dictionary(helpers):
    module, native = helpers
    state = {
        'id': 'tank/iocage@snapshot', 'properties': {'used': {'value': '0'}},
    }
    native.get_snapshot.return_value = NativeResource(state)

    assert module.ZFS.get_snapshot_safely('tank/iocage@snapshot') == state
    native.get_snapshot.return_value.asdict.assert_called_once_with()


@pytest.mark.parametrize('snapshot', [False, True])
def test_missing_resource_returns_none(helpers, snapshot):
    module, native = helpers
    zfs = module.ZFS()
    lookup = zfs.get_snapshot_safely if snapshot else zfs.get_dataset
    native_lookup = native.get_snapshot if snapshot else native.get_dataset
    native_lookup.side_effect = module.libzfs.ZFSException('not found')

    assert lookup('missing') is None
    native_lookup.assert_called_once_with('missing')


@pytest.mark.parametrize('snapshot', [False, True])
def test_unexpected_resource_error_propagates(helpers, snapshot):
    module, native = helpers
    zfs = module.ZFS()
    lookup = zfs.get_snapshot_safely if snapshot else zfs.get_dataset
    native_lookup = native.get_snapshot if snapshot else native.get_dataset
    native_lookup.side_effect = RuntimeError('binding failed')

    with pytest.raises(RuntimeError, match='binding failed'):
        lookup('tank/iocage')
