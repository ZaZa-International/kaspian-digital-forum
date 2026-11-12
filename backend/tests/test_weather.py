"""Weather provenance tests: live (mocked) vs offline fallback."""
from fastapi.testclient import TestClient

from app import weather
from app.main import app

client = TestClient(app)


def test_live_source_labelled():
    body = client.get("/api/weather/live").json()
    assert body["live"] is True
    for seg in body["segments"].values():
        assert seg["source"] == "live: Open-Meteo"


def test_offline_fallback_labelled(monkeypatch):
    monkeypatch.setattr(weather, "_fetch_one", lambda lat, lon: (None, None))
    weather._cache.update({"at": 0.0, "payload": None})
    body = client.get("/api/weather/live").json()
    assert body["live"] is False
    for seg in body["segments"].values():
        assert seg["source"].startswith("demo")
    # forecast still works and labels the fallback provenance
    fc = client.get("/api/routes/R1/forecast").json()
    sea = next(l for l in fc["legs"] if l["mode"] == "sea")
    assert sea["weather"]["source"].startswith("demo")


def test_storm_source_labelled():
    fc = client.get("/api/routes/R1/forecast?storm=true").json()
    sea = next(l for l in fc["legs"] if l["mode"] == "sea")
    assert sea["weather"]["source"] == "storm scenario"
    assert sea["weather"]["wave_m"] == 3.4