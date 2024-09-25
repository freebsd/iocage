# Copyright (c) 2014-2019, iocage
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted providing that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
# IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import pytest


require_root = pytest.mark.require_root
require_zpool = pytest.mark.require_zpool

SNAP_NAME = 'snaptest'
SNAPALL_NAME = 'snapalltest'


@require_root
@require_zpool
def test_01_remove_snapshot(invoke_cli, resource_selector, skip_test):
    jails = resource_selector.all_jails_having_snapshots
    skip_test(not jails)

    snap_jail = None
    for jail in jails:
        if not jail.is_template and not jail.is_cloned and any(
            SNAP_NAME in snap.id for snap in jail.recursive_snapshots
        ):
            snap_jail = jail
            break

    skip_test(not snap_jail)

    remove_snap = None
    for snap in snap_jail.recursive_snapshots:
        if SNAP_NAME in snap.id:
            remove_snap = snap
            break

    assert remove_snap.exists is True

    invoke_cli(
        ['snapremove', '-n', remove_snap.id.split('@')[1], snap_jail.name]
    )

    assert remove_snap.exists is False


@require_root
@require_zpool
def test_02_remove_snapshot_of_all_jails(
    invoke_cli, resource_selector, skip_test):
    jails = resource_selector.all_jails
    skip_test(not jails)

    snap_jails = []
    for jail in jails:
        if any(
            SNAPALL_NAME in snap.id for snap in jail.recursive_snapshots
        ):
            snap_jails.append(jail)

    skip_test(not snap_jails)

    remove_snaps = []
    for snap_jail in snap_jails:
        for snap in snap_jail.recursive_snapshots:
            if SNAPALL_NAME in snap.id:
                remove_snaps.append(snap)

    assert all(snap.exists is True for snap in remove_snaps)

    invoke_cli(
        ['snapremove', '-n', SNAPALL_NAME, 'ALL']
    )

    assert all(snap.exists is False for snap in remove_snaps)

@require_root
@require_zpool
def test_03_remove_all_snapshots_fail(invoke_cli, resource_selector, skip_test):
    jails = resource_selector.all_jails_having_snapshots
    skip_test(not jails)

    snap_jail = None
    for jail in jails:
        if (not jail.is_template and not jail.is_cloned and
            len(jail.recursive_snapshots)>0):
            snap_jail = jail
            break

    skip_test(not snap_jail)

    failremove_snap = snap_jail.recursive_snapshots[0]

    assert failremove_snap.exists is True

    # Voluntarily forgetting the -f force flag
    invoke_cli(
        ['snapremove', '-n', 'ALL', snap_jail.name],
        assert_returncode=False
    )

    assert failremove_snap.exists is True


@require_root
@require_zpool
def test_04_remove_all_snapshots_success(invoke_cli, resource_selector,
                                        snapshot, skip_test):
    jails = resource_selector.all_jails_having_snapshots
    skip_test(not jails)

    snap_jail = None
    for jail in jails:
        if (not jail.is_template and not jail.is_cloned and
            len(jail.recursive_snapshots)>1):
            snap_jail = jail
            break

    skip_test(not snap_jail)

    remove_snaps = set(snap_jail.recursive_snapshots)
    assert all(snap.exists is True for snap in remove_snaps)

    cloned_snaps = resource_selector.cloned_snapshots_set
    assert all(snap.exists is True for snap in cloned_snaps)

    filtered_remove_snaps = remove_snaps - {
            snapshot(s, s.rsplit('@', 1)[0])
            for snap in cloned_snaps
            for s in (snap.name.replace('/root@', '@'), snap.name)
            }

    result = invoke_cli(
        ['snapremove', '-n', 'ALL', snap_jail.name, '--force']
    )

    assert all(snap.exists is False for snap in filtered_remove_snaps)
    assert all(snap.exists is True for snap in cloned_snaps)


@require_root
@require_zpool
def test_05_remove_all_snapshots_all_jails(invoke_cli, resource_selector,
                                        snapshot, skip_test):
    jails = resource_selector.all_jails_having_snapshots
    skip_test(not jails)

    cloned_snaps = resource_selector.cloned_snapshots_set
    assert all(snap.exists is True for snap in cloned_snaps)

    remove_snaps = {
        snap for jail in jails for snap in jail.recursive_snapshots
    }
    assert all(snap.exists is True for snap in remove_snaps)

    # We want to keep cloned jail datasets, cloned root datasets,
    # and non-cloned jail datasets that contain cloned root datasets.
    # This last case happens when creating jails from templates.
    filtered_remove_snaps = remove_snaps - {
        snapshot(s, s.rsplit('@', 1)[0])
        for snap in cloned_snaps
        for s in (snap.name.replace('/root@', '@'), snap.name)
        }

    result = invoke_cli(
        ['snapremove', '-n', 'ALL', 'ALL', '--force']
    )

    assert all(snap.exists is False for snap in filtered_remove_snaps)
    assert all(snap.exists is True for snap in cloned_snaps)
