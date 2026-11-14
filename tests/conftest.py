import json
import os
import pathlib
import tempfile

FIX = pathlib.Path(__file__).parent / "fixtures"
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""

import pytest  # noqa: E402


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


@pytest.fixture
def garden_city():
    return load("garden_city_ks")


@pytest.fixture
def hastings():
    return load("hastings_ne")


@pytest.fixture
def offline(monkeypatch):
    """Stub every network call with the saved real-data fixtures."""
    from app import onboarding, service
    from app.data import forecast

    def fake_build(lat, lon, with_ndvi=True):
        return load("garden_city_ks" if lat < 39 else "hastings_ne")

    monkeypatch.setattr(service, "build_features", fake_build)
    monkeypatch.setattr(service, "add_ndvi", lambda f: f)
    monkeypatch.setattr(onboarding, "county_name", lambda lat, lon: "Finney County, KS" if lat < 39 else "Adams County, NE")
    fc = [{"date": f"2026-07-{10 + i}", "tmax": 36.0, "tmin": 18.0, "precip": 0.0, "et0": 7.0} for i in range(7)]
    monkeypatch.setattr(forecast, "forecast", lambda lat, lon, days=7: fc)
    monkeypatch.setattr(service.fc, "forecast", lambda lat, lon, days=7: fc)
    monkeypatch.setattr(onboarding, "zip_to_point", lambda t: (38.05, -100.75, "Garden City, KS") if "67846" in t else None)
    monkeypatch.setattr(onboarding, "_spawn", lambda fn, *args: fn(*args))
