import datetime
import time

from unittest import mock

import pytest

from iocage_lib import ioc_image, iocage


# Local dates differ from UTC around these instants, exposing local-time use.
@pytest.mark.parametrize('zone', [
    'Pacific/Kiritimati', 'America/Los_Angeles'
])
@pytest.mark.parametrize('stamp', [
    (2026, 12, 31, 23, 59, 58), (2027, 1, 1, 0, 0, 1)
])
def test_snapshot_names_and_image_dates_stay_in_utc(monkeypatch, zone, stamp):
    original = datetime.datetime
    instant = original(*stamp, tzinfo=datetime.timezone.utc)

    class FixedDatetime(original):
        @classmethod
        def now(cls, tz=None):
            value = instant.astimezone(tz)
            return value.replace(tzinfo=None) if tz is None else value

    jail = iocage.IOCage.__new__(iocage.IOCage)
    jail.jails = {'first': '/unused/first', 'second': '/unused/second'}
    jail._all = True
    jail.snapshot = mock.Mock()
    try:
        with monkeypatch.context() as patch:
            patch.setenv('TZ', zone)
            time.tzset()
            with (
                mock.patch.object(datetime, 'datetime', FixedDatetime),
                mock.patch('iocage_lib.ioc_json.IOCJson') as configuration,
            ):
                configuration.return_value.json_get_value.side_effect = {
                    'pool': 'tank', 'iocroot': '/unused/iocage'
                }.__getitem__
                jail.snapshot_all(None)
                image = ioc_image.IOCImage()
    finally:
        # monkeypatch restored TZ; refresh libc's cached timezone as well.
        time.tzset()

    expected = instant.strftime('%F_%T')
    assert jail.snapshot.call_args_list == [mock.call(expected)] * 2
    assert jail._all is False
    assert image.date == instant.strftime('%F')
