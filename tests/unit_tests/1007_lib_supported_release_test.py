import hashlib

from pathlib import Path
from unittest import mock

import pytest

from iocage_lib.ioc_fetch import IOCFetch
from iocage_lib.iocage import IOCage


def test_readme_freebsd_requirements_track_supported_releases():
    root = Path(__file__).resolve().parents[2]
    requirements = (root / 'README.md').read_text().split(
        '### REQUIREMENTS\n', 1
    )[1].split('\n### ', 1)[0]

    assert 'https://www.freebsd.org/security/#sup' in requirements


@pytest.mark.parametrize('listed', [False, True], ids=['unlisted', 'listed'])
def test_explicit_doc_archive_preserves_optional_manifest_fallback(
    tmp_path, listed
):
    download_dir = tmp_path / 'download' / '15.1-RELEASE'
    download_dir.mkdir(parents=True)
    payload = b'custom-server documentation archive'
    (download_dir / 'doc.txz').write_bytes(payload)
    manifest = 'base.txz\t' + '0' * 64 + '\t1\n'
    if listed:
        checksum = hashlib.sha256(payload).hexdigest()
        manifest += f'doc.txz\t{checksum}\t1\n'
    (download_dir / 'MANIFEST').write_text(manifest)
    fetch = IOCFetch.__new__(IOCFetch)
    fetch.iocroot = str(tmp_path)
    fetch.release = '15.1-RELEASE'
    fetch.files = ['MANIFEST', 'doc.txz']
    fetch.files_left = list(fetch.files)
    fetch.hardened = False
    fetch.root_dir = 'releases'
    fetch.server = 'https://custom.example.invalid'
    fetch.auth = None
    fetch.verify = True
    fetch.update = False
    fetch.callback = None
    fetch.silent = True
    fetch.__fetch_exists__ = mock.Mock()
    fetch.fetch_extract = mock.Mock()

    def download(files, missing=False):
        # Fail immediately if the optional archive enters the retry loop.
        assert not missing, 'Unexpected retry for optional doc.txz'

    fetch.fetch_download = mock.Mock(side_effect=download)
    with mock.patch('iocage_lib.ioc_fetch.requests.get') as http:
        http.return_value.status_code = 200
        fetch.fetch_http_release([])

    fetch.fetch_download.assert_called_once_with(['MANIFEST', 'doc.txz'])
    assert fetch.files == ['MANIFEST', 'doc.txz']
    assert fetch.files_left == []
    if listed:
        fetch.fetch_extract.assert_called_once_with('doc.txz')
    else:
        fetch.fetch_extract.assert_not_called()


@pytest.mark.parametrize(
    'release', ['14.4-RELEASE', '14.5-RELEASE', '15.1-RELEASE']
)
@pytest.mark.parametrize('arch', ['amd64', 'arm64'])
def test_fetch_uses_current_distribution_archives(release, arch):
    iocage = IOCage.__new__(IOCage)
    iocage.silent = True
    iocage.callback = None
    expected = ['MANIFEST', 'base.txz', 'src.txz']
    if arch == 'amd64':
        expected.insert(2, 'lib32.txz')

    with (
        mock.patch('iocage_lib.iocage.os.uname',
                   return_value=('FreeBSD', 'test', '15.1-RELEASE', '', arch)),
        mock.patch('iocage_lib.iocage.ioc_common.checkoutput',
                   return_value='15.1-RELEASE'),
        mock.patch('iocage_lib.iocage.ioc_fetch.IOCFetch') as fetch,
    ):
        iocage.fetch(release=release)

    assert fetch.call_args.args == (release,)
    assert fetch.call_args.kwargs['files'] == expected
    fetch.return_value.fetch_release.assert_called_once_with()


def test_fetch_preserves_explicit_archive_selection():
    iocage = IOCage.__new__(IOCage)
    iocage.silent = True
    iocage.callback = None
    files = ['MANIFEST', 'base.txz']
    with (
        mock.patch('iocage_lib.iocage.ioc_common.checkoutput',
                   return_value='15.1-RELEASE'),
        mock.patch('iocage_lib.iocage.ioc_fetch.IOCFetch') as fetch,
    ):
        iocage.fetch(release='15.1-RELEASE', files=files)

    assert fetch.call_args.kwargs['files'] == files


def test_fstab_preserves_long_destination_and_jail_root():
    iocage = IOCage.__new__(IOCage)
    iocage.iocroot = '/iocage'
    iocage.__check_jail_existence__ = mock.Mock(
        return_value=('example', '/jail')
    )
    destination = '/mnt/' + 'directory/' * 12
    with (
        mock.patch('iocage_lib.iocage.ioc_fstab.IOCFstab') as fstab,
        mock.patch('iocage_lib.iocage.ioc_common.logit') as log,
    ):
        iocage.fstab('add', '/source', destination, 'nullfs', 'ro', '0', '0',
                     add_path=True)

    expected = f'/iocage/jails/example/root{destination}'
    assert fstab.call_args.args[3] == expected
    log.assert_not_called()
