import pytest

from smoke_sense.cadence import Cadence, resolve_cadence


def test_minutes_mapping():
    assert Cadence.RAW.minutes == 0
    assert Cadence.TEN_MIN.minutes == 10
    assert Cadence.THIRTY_MIN.minutes == 30
    assert Cadence.HOURLY.minutes == 60
    assert Cadence.SIX_HOURLY.minutes == 360
    assert Cadence.DAILY.minutes == 1440


def test_enum_value_is_name():
    assert Cadence.TEN_MIN.value == "TEN_MIN"


def test_resolve_exact_match():
    assert resolve_cadence([0, 10, 30, 60, 360, 1440], 10) == 10


def test_resolve_rounds_down_to_finest_not_coarser():
    assert resolve_cadence([0, 10, 30, 60], 20) == 10


def test_resolve_fallback_when_provider_cannot_go_finer():
    assert resolve_cadence([60], 10) == 60


def test_resolve_raw_request():
    assert resolve_cadence([0, 10, 60], 0) == 0
