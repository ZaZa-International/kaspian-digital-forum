"""Explicit captures and observed labels against isolated dispatcher state."""

import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import data, main, ml, operations, store, weather

NOW = datetime(2026, 11, 13, 8, tzinfo=timezone.utc)
FLEET_CSV = "/api/operations/intelligence/snapshots.csv"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "journal.db")
    monkeypatch.setattr(operations, "_now", lambda: clock[0])
    monkeypatch.setattr(store, "datetime", type("TestClock", (), {"now": staticmethod(lambda tz: clock[0])}))
    monkeypatch.setattr(weather, "cached_hourly_forecast", lambda: {"status": "unavailable", "segments": {}})
    monkeypatch.setattr(main, "WRITE_TOKEN", "")
    clock = [NOW]
    calls = []

    def predict(route, weight_t, storm=False, scenario=None, *, scoring_delay_h=None, trained=None):
        calls.append((route["id"], scoring_delay_h))
        return {
            "status": "ok", "features": ml.route_features(route, weight_t, storm, scenario),
            "ml_delay_h": 10, "interval": {"lower_h": 5, "upper_h": 15, "level": .9, "usable": False},
            "model_version": "test-model", "model_hash": "a" * 64, "dataset_hash": "b" * 64,
            "dataset_kind": "synthetic", "reliability": {"preferred_source": "scoring"},
        }

    monkeypatch.setattr(ml, "predict_delay", predict)
    monkeypatch.setattr(ml, "get_trained", lambda: {"model_hash": "a" * 64})
    monkeypatch.setattr(ml, "info", lambda trained=None: {"status": "ok"})
    main._rate_buckets.clear()
    with TestClient(main.app) as c:
        c.clock, c.predict_calls = clock, calls
        yield c
    main._rate_buckets.clear()


def _path(shipment_id):
    return f"/api/operations/{shipment_id}/intelligence/snapshots"


def _create(client):
    response = client.post("/api/shipments", json={
        "name": "test cargo", "cargo": "test", "weight_t": 48, "route_id": "R1", "docs": {},
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _capture(client, shipment_id, key="snapshot"):
    response = client.post(_path(shipment_id), headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


def _arrival(client, shipment_id, leg_id, at=None, **fields):
    response = client.post(f"/api/shipments/{shipment_id}/events", json={
        "event_type": "arrival", "leg_id": leg_id, "source": "test-operator",
        "occurred_ts": at.isoformat() if at else None, **fields,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _finish(client, snapshot, final_at, **fields):
    client.clock[0] = final_at + timedelta(hours=2)
    shipment_id = snapshot["shipment_id"]
    for leg_id in snapshot["remaining_leg_ids"][:-1]:
        _arrival(client, shipment_id, leg_id, final_at - timedelta(hours=1))
    return _arrival(client, shipment_id, snapshot["remaining_leg_ids"][-1], final_at, **fields)


def _csv(client, path=FLEET_CSV):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    return list(csv.DictReader(io.StringIO(response.text)))


def test_explicit_idempotent_capture_rejects_scenario_and_obeys_write_guard(client, monkeypatch):
    shipment_id = _create(client)
    url = _path(shipment_id)
    initial = client.get(url).json()
    assert initial["counts"] == {"matched": 0, "pending": 0, "excluded": 0, "total": 0}
    assert initial["metrics"]["mae_h"] is None
    assert _csv(client) == []
    assert client.predict_calls == []

    monkeypatch.setattr(main, "WRITE_TOKEN", "test-token")
    assert client.post(url).status_code == 401
    headers = {"X-Write-Token": "test-token", "Idempotency-Key": "duplicate-request"}
    assert client.post(url + "?storm=true", headers=headers).status_code == 422
    assert client.post(url + "?wave_m=0", headers=headers).status_code == 422
    assert client.post(url, headers=headers, json={"storm": True}).status_code == 422
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, retry = list(pool.map(lambda _: client.post(url, headers=headers), range(2)))
    assert first.status_code == retry.status_code == 201
    assert first.json() == retry.json()
    saved = first.json()
    assert saved["status"] == "pending"
    assert saved["prediction"]["model_hash"] == "a" * 64
    assert saved["prediction"]["dataset_hash"] == "b" * 64
    assert saved["remaining_leg_ids"] == saved["plan_leg_ids"]
    assert saved["prediction"]["features"]["weight_t"] == 48
    journal = client.get(url).json()
    assert journal["snapshots"] == [saved]
    assert journal["counts"]["total"] == 1
    assert client.get(url + "?limit=201").status_code == 422
    assert client.get(_path("unknown")).status_code == 404
    assert client.get(_path("unknown") + ".csv").status_code == 404
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ml_snapshots").fetchone()[0] == 1
        assert store.list_events(shipment_id, conn) == []


def test_match_final_arrival_metrics_and_csv_use_frozen_inputs_and_correction(client):
    shipment_id = _create(client)
    first = _capture(client, shipment_id)
    client.clock[0] = NOW + timedelta(hours=1)
    second = _capture(client, shipment_id, "snapshot-2")
    final_at = NOW + timedelta(hours=first["baseline"]["base_remaining_h"] + 8)
    arrival = _finish(client, first, final_at, source="=operator,supplied")
    rows = client.get(_path(shipment_id) + "?limit=1").json()
    assert rows["counts"] == {"matched": 2, "pending": 0, "excluded": 0, "total": 2}
    assert rows["metrics"]["n"] == 2
    assert rows["metrics"]["unit"] == "snapshot"
    assert rows["metrics"]["mae_h"] == 2.5
    assert rows["metrics"]["interval_coverage"] == 1
    assert rows["metrics"]["interval_n"] == 2
    assert len(rows["snapshots"]) == 1
    assert rows["snapshots"][0]["id"] == second["id"]
    assert rows["snapshots"][0]["outcome"]["delay_h"] == 7

    (row,) = _csv(client)
    assert _csv(client, _path(shipment_id) + ".csv") == [row]
    assert row["snapshot_id"] == first["id"] and row["event_id"] == arrival["id"]
    assert row["forecast_at"] == first["forecast_at"]
    assert row["outcome_at"] == final_at.isoformat()
    assert row["observed_at"] == arrival["received_ts"]
    assert row["event_source"] == "operator-reported:=operator,supplied"
    assert float(row["delay_h"]) == 8
    assert {name: float(row[name]) for name in ml.FEATURES} == first["prediction"]["features"]

    client.clock[0] += timedelta(hours=20)
    correction = _arrival(client, shipment_id, arrival["leg_id"], final_at + timedelta(hours=10),
                          correction_of=arrival["id"])
    (corrected,) = _csv(client)
    assert corrected["event_id"] == correction["id"]
    assert corrected["observed_at"] == correction["received_ts"]
    assert float(corrected["delay_h"]) == 18
    assert client.get(_path(shipment_id)).json()["metrics"]["interval_coverage"] == 0
    assert client.post(_path(shipment_id)).status_code == 409

    _arrival(client, shipment_id, arrival["leg_id"], final_at, correction_of=correction["id"], confidence="demo")
    assert _csv(client) == []
    assert client.get(_path(shipment_id)).json()["counts"]["pending"] == 2


def test_demo_and_plan_change_exclude_snapshots(client):
    demo = _capture(client, "SHP-1042")
    assert demo["reason_code"] == "demo_shipment"
    _finish(client, demo, NOW + timedelta(days=4))
    assert client.get(_path("SHP-1042")).json()["counts"]["excluded"] == 1
    assert _csv(client, _path("SHP-1042") + ".csv") == []

    shipment_id = _create(client)
    original = _capture(client, shipment_id)
    operation = f"/api/operations/{shipment_id}"
    storm = client.get(operation + "?storm=true").json()
    proposal = storm["proposals"][0]
    accepted = client.post(operation + "/accept?storm=true", json={
        "proposal_id": proposal["id"], "version": proposal["version"],
    }, headers={"Idempotency-Key": "accept-reroute"})
    assert accepted.status_code == 200, accepted.text
    revised = _capture(client, shipment_id, "after-reroute")
    assert revised["plan_version"] == original["plan_version"] + 1
    _finish(client, revised, client.clock[0] + timedelta(days=5))
    journal = client.get(_path(shipment_id)).json()
    assert journal["counts"]["matched"] == 1
    assert journal["counts"]["excluded"] == 1
    assert journal["snapshots"][-1]["reason_code"] == "plan_changed"
    assert _csv(client)[0]["snapshot_id"] == revised["id"]
    assert client.post("/api/demo/reset").status_code == 200
    assert _csv(client) == []
    assert client.get(_path("SHP-1042")).json()["counts"]["total"] == 0


def test_requires_arrival_after_forecast_with_explicit_occurrence_and_receipt(client):
    shipment_id = _create(client)
    snapshot = _capture(client, shipment_id)
    legs = data.get_route("R1")["legs"]
    client.clock[0] = NOW + timedelta(hours=8)
    for leg in legs[:-1]:
        _arrival(client, shipment_id, leg["id"], NOW - timedelta(hours=3))
    final_leg = legs[-1]["id"]
    final = _arrival(client, shipment_id, final_leg)
    assert client.get(_path(shipment_id)).json()["snapshots"][0]["reason_code"] == "missing_actual_time"

    correction = _arrival(client, shipment_id, final_leg, NOW - timedelta(hours=1), correction_of=final["id"])
    assert client.get(_path(shipment_id)).json()["snapshots"][0]["reason_code"] == "arrival_before_forecast"
    assert _csv(client) == []

    client.clock[0] = NOW + timedelta(hours=12)
    _arrival(client, shipment_id, final_leg, NOW + timedelta(hours=2), correction_of=correction["id"])
    client.clock[0] = NOW + timedelta(hours=10)
    pending = client.get(_path(shipment_id)).json()["snapshots"][0]
    assert pending["id"] == snapshot["id"] and pending["reason_code"] == "waiting_for_record"
    assert _csv(client) == []
    client.clock[0] = NOW + timedelta(hours=13)
    assert float(_csv(client)[0]["delay_h"]) == 0

    _arrival(client, shipment_id, final_leg, NOW + timedelta(hours=3))
    assert client.get(_path(shipment_id)).json()["snapshots"][0]["reason_code"] == "ambiguous_arrival"
    assert _csv(client) == []
