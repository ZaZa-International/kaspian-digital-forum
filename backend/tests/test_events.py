"""Events & provenance tests (cycle 6)."""
import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    store.clear_events()
    yield
    store.clear_events()


def test_timeline_has_model_and_demo_actual_events():
    tl = client.get("/api/shipments/SHP-1042/timeline").json()
    assert all(e["kind"] == "model" for e in tl["events"])
    assert tl["actual_events"], "seed shipment must have demo actual events"
    for e in tl["actual_events"]:
        assert e["kind"] == "actual"
        assert e["source"] == "demo-simulation"
        assert e["confidence"] == "demo"
    # created shipments start without actual events
    store.clear_events()


def test_event_ingestion_and_validation():
    r = client.post("/api/shipments/SHP-1042/events", json={
        "event_type": "arrival", "source": "terminal API (demo adapter)",
        "location": "Махачкала", "mode": "rail",
        "occurred_ts": "2026-11-12T10:00:00+00:00", "note": "факт из адаптера"})
    assert r.status_code == 201
    body = r.json()
    assert body["received_ts"] and body["id"]
    tl = client.get("/api/shipments/SHP-1042/timeline").json()
    assert any(e["source"] == "terminal API (demo adapter)" for e in tl["actual_events"])
    assert store.list_events("SHP-1042")
    # validation
    assert client.post("/api/shipments/SHP-1042/events", json={
        "event_type": "hack", "source": "x"}).status_code == 422
    assert client.post("/api/shipments/SHP-1042/events", json={
        "event_type": "arrival", "source": "x", "occurred_ts": "not-a-date"}).status_code == 422
    assert client.post("/api/shipments/SHP-1042/events", json={
        "event_type": "arrival"}).status_code == 422  # source required
    assert client.post("/api/shipments/NOPE/events", json={
        "event_type": "arrival", "source": "x"}).status_code == 404


def test_provenance_endpoint():
    sources = client.get("/api/provenance").json()["sources"]
    by_id = {s["id"]: s for s in sources}
    assert by_id["weather_live"]["kind"] in ("live", "fallback")
    assert "CC BY 4.0" in by_id["weather_live"]["license"]
    assert by_id["ml_labels"]["kind"] == "synthetic"
    assert by_id["ports"]["kind"] == "demo"
    assert by_id["ferry_wait"]["kind"] == "assumption"
    assert by_id["conditional_legs"]["kind"] == "assumption"
    assert "27_315_3151_14_1" in by_id["co2_factors"]["rows"]["rail"]
    assert all(s["kind"] in ("live", "fallback", "historical", "demo", "synthetic",
                             "static", "assumption") for s in sources)