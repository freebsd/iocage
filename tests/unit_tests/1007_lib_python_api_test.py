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


def test_package_install_dns_keeps_search_domain_behavior():
    create = IOCCreate.__new__(IOCCreate)
    create.callback = None
    create.silent = True
    create.plugin = False
    create.pkglist = []
    create.log = mock.Mock()
    with (
        mock.patch('iocage_lib.ioc_list.IOCList') as jail_list,
        mock.patch('dns.resolver.resolve') as resolve,
        mock.patch('iocage_lib.ioc_exec.SilentExec'),
        mock.patch('iocage_lib.ioc_exec.IOCExec'),
        mock.patch(
            'iocage_lib.ioc_create.su.run',
            return_value=subprocess.CompletedProcess([], 0, b'')
        ) as run,
        mock.patch('iocage_lib.ioc_common.consume_and_log'),
        mock.patch('iocage_lib.ioc_common.logit'),
        mock.patch('iocage_lib.ioc_stop.IOCStop') as stop,
    ):
        jail_list.return_value.list_get_jid.return_value = (True, '42')
        create.create_install_packages(
            'example', '/jail', repo='https://packages/repo'
        )

    resolve.assert_called_once_with('packages', search=True)
    assert run.call_args.args[0] == [
        'pkg-static', '-j', '42', 'install', '-q', '-y', 'pkg'
    ]
    stop.assert_not_called()
