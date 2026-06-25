import pandas as pd
import pytest

from smoke_sense.data import Metric


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
            "metric": [Metric.PM2_5.value, Metric.PM2_5.value],
            "value": [12.3, 15.1],
            "aqi": [52, 58],
            "agg_window": [60, 60],
            "source": ["aqs", "aqs"],
        }
    )
