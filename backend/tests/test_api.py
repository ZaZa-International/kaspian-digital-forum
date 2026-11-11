"""Smoke tests for the Caspian Twin API. Run: pytest -q (from backend/)."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_level_history_shape():
    r = client.get("/api/level/history")
    assert r.status_code == 200
    body = r.json()
    assert body["series"][0]["year"] == 2006
    assert body["current"]["level_m"] == -28.70


def test_forecast_monotonic_fall():
    r = client.get("/api/level/forecast?scenario=pessimistic&year=2050")
    assert r.status_code == 200
    assert r.json()["level_m"] == -34.0


def test_forecast_validation():
    assert client.get("/api/level/forecast?scenario=nope").status_code == 422
    assert client.get("/api/level/forecast?year=2100").status_code == 422


def test_ports_and_stations():
    assert len(client.get("/api/ports").json()["ports"]) == 8
    assert len(client.get("/api/stations").json()["stations"]) == 6


def test_simulate_marks_shallow_ports():
    r = client.post("/api/scenarios/simulate", json={"year": 2050, "scenario": "pessimistic"})
    assert r.status_code == 200
    body = r.json()
    assert body["level_m"] == -34.0
    assert body["ports_at_risk"] >= 4
    astrakhan = next(p for p in body["ports"] if p["id"] == "astrakhan")
    assert astrakhan["status"] == "critical"


def test_frontend_served():
    r = client.get("/")
    assert r.status_code == 200 and "Двойник Каспия" in r.text
