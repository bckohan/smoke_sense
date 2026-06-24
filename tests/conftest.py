import pandas as pd
import pytest

from smoke_sense.data import Pollutant


@pytest.fixture
def sample_rows() -> pd.DataFrame:
    """A minimal valid-shaped frame (pre-validation) for round-trip tests."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2023-07-01T00:00:00Z", "2023-07-01T01:00:00Z"], utc=True
            ),
            "county_fips": ["06037", "06037"],
            "station_id": ["060371103", "060371103"],
            "latitude": [34.0, 34.0],
            "longitude": [-118.2, -118.2],
            "pollutant": [Pollutant.PM2_5.value, Pollutant.PM2_5.value],
            "value": [12.3, 15.1],
            "unit": ["µg/m³", "µg/m³"],
            "aqi": [52, 58],
            "source": ["aqs", "aqs"],
        }
    )
