# Copyright (c) 2014-2026, iocage
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
import iocage_lib.ioc_common

require_root = pytest.mark.require_root
require_zpool = pytest.mark.require_zpool
require_upgrade = pytest.mark.require_upgrade
require_nat = pytest.mark.require_nat

JAIL_NAME = 'upgrade_jail'
UPGRADE_SOURCES = {
    '14.5-RELEASE': '14.4-RELEASE',
    '15.1-RELEASE': '14.5-RELEASE',
}


@pytest.fixture
def upgrade_releases(release):
    target = (
        iocage_lib.ioc_common.parse_latest_release()
        if release == 'latest' else release
    )
    source = UPGRADE_SOURCES.get(target)
    if source is None:
        pytest.skip(f'No supported upgrade source configured for {target}')
    return source, target


@require_root
@require_zpool
@require_nat
@require_upgrade
def test_01_create_jail_with_older_release(invoke_cli, jail, upgrade_releases):
    source, _ = upgrade_releases
    invoke_cli(['fetch', '-r', source])
    invoke_cli(
        ['create', '-r', source, '-n', JAIL_NAME, 'nat=1',
         'allow_raw_sockets=1']
    )

    assert jail(JAIL_NAME).exists is True


@require_upgrade
@require_nat
@require_root
@require_zpool
def test_02_upgrade_jail(
        invoke_cli, skip_test, upgrade_releases, jail
):
    _, target = upgrade_releases
    jail = jail(JAIL_NAME)

    skip_test(not jail)

    invoke_cli(
        ['upgrade', jail.name, '-r', target]
    )
