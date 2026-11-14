import subprocess
import tomllib

from pathlib import Path
from unittest import mock

from iocage_lib.ioc_create import IOCCreate


def test_readme_python_requirement_matches_package_metadata():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / 'pyproject.toml').read_text())
    minimum = project['project']['requires-python'].removeprefix('>=')
    requirements = (root / 'README.md').read_text().split(
        '### REQUIREMENTS\n', 1
    )[1].split('\n### ', 1)[0]

    assert f'- Python {minimum}+' in requirements


def test_package_repo_lookup_uses_dns_search_domains():
    """Keep resolving short names such as packages via the DNS search list."""
    # Bypass jail creation; only the package-install method is under test.
    create = IOCCreate.__new__(IOCCreate)
    create.callback = None
    create.silent = True
    create.plugin = False
    create.pkglist = []
    create.log = mock.Mock()
    # Keep the remaining install steps successful and isolated from the host.
    with (
        mock.patch('iocage_lib.ioc_list.IOCList') as jail_list,
        mock.patch('dns.resolver.resolve') as resolve,
        mock.patch('iocage_lib.ioc_exec.SilentExec'),
        mock.patch('iocage_lib.ioc_exec.IOCExec'),
        mock.patch(
            'iocage_lib.ioc_create.su.run',
            return_value=subprocess.CompletedProcess([], 0, b'')
        ),
        mock.patch('iocage_lib.ioc_common.consume_and_log'),
        mock.patch('iocage_lib.ioc_common.logit'),
        mock.patch('iocage_lib.ioc_stop.IOCStop'),
    ):
        jail_list.return_value.list_get_jid.return_value = (True, '42')
        create.create_install_packages(
            'example', '/jail', repo='https://packages/repo'
        )

    # Preserve query()'s search-domain behavior when switching to resolve().
    resolve.assert_called_once_with('packages', search=True)
