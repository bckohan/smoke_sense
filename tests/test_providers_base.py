from datetime import date

import pandas as pd
import pytest

from smoke_sense import data
from smoke_sense.data import Pollutant
from smoke_sense.providers import base


def test_register_and_get_provider():
    @base.register
    class FakeProvider(base.AQIProvider):
        name = "fake"
        supported = {Pollutant.PM2_5}

        def fetch(self, county_fips, start, end, pollutants):
            return data.empty_frame()

    assert "fake" in base.all_providers()
    provider = base.get_provider("fake")
    assert isinstance(provider, FakeProvider)
    result = provider.fetch("06037", date(2023, 1, 1), date(2023, 1, 2), [Pollutant.PM2_5])
    assert list(result.columns) == list(data.COLUMNS)


def test_get_provider_unknown_raises():
    with pytest.raises(KeyError, match="nope"):
        base.get_provider("nope")
