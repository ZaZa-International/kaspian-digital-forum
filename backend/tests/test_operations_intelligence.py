"""ML must use the current remaining plan and must not change dispatcher state."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main, ml, operations, store, weather

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
PATH = "/api/operations/SHP-1042"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(operations, "_now", lambda: NOW)
    monkeypatch.setattr(weather, "cached_hourly_forecast", lambda: {"status": "unavailable", "segments": {}})
    monkeypatch.setattr(main, "WRITE_TOKEN", "")
    calls = []

    def predict(route, weight_t, storm=False, scenario=None, *, scoring_delay_h=None, trained=None):
        calls.append({"route": route, "scenario": scenario, "scoring": scoring_delay_h, "trained": trained})
        return {
            "status": "ok", "ml_delay_h": 10, "scoring_delay_h": scoring_delay_h,
            "interval": {"lower_h": 5, "upper_h": 15, "level": .9, "usable": False},
            "dataset_kind": "synthetic",
        }

    monkeypatch.setattr(ml, "predict_delay", predict)
    monkeypatch.setattr(ml, "get_trained", lambda: {"model_hash": "test-revision"})
    monkeypatch.setattr(ml, "info", lambda trained=None: {"status": "ok", "dataset": {"source_kind": "synthetic"}, **(trained or {})})
    main._rate_buckets.clear()
    with TestClient(main.app) as c:
        c.ml_calls = calls
        yield c
    main._rate_buckets.clear()


def test_current_remaining_plan_and_read_only_forecast(client):
    before = client.get(PATH).json()
    result = client.get(PATH + "/intelligence").json()
    assert result["status"] == "ok"
    assert result["plan_version"] == before["plan"]["version"]
    assert result["scope"]["remaining_leg_ids"] == before["shipment"]["active_leg_ids"]
    assert "R1-L1" not in [leg["id"] for leg in client.ml_calls[0]["route"]["legs"]]
    assert result["baseline"]["eta"] == before["baseline"]["eta"]
    assert result["baseline"]["scoring_delay_h"] == pytest.approx(
        result["baseline"]["remaining_h"] - result["baseline"]["base_remaining_h"], abs=.1
    )
    assert not result["arrival"]["usable"]
    assert datetime.fromisoformat(result["arrival"]["estimate"]) == NOW + timedelta(
        hours=result["baseline"]["base_remaining_h"] + 10
    )
    after = client.get(PATH).json()
    assert after == before


def test_ml_follows_accepted_route_and_real_port_conditions(client):
    detail = client.get(PATH + "?storm=true").json()
    p = detail["proposals"][0]
    accepted = client.post(PATH + "/accept?storm=true", json={
        "proposal_id": p["id"], "version": p["version"],
    }, headers={"Idempotency-Key": "ml-route-test"})
    assert accepted.status_code == 200
    result = client.get(PATH + "/intelligence?extra_customs_h=10").json()
    assert result["plan_version"] == 2
    assert result["scope"]["remaining_leg_ids"] == p["leg_ids"]
    assert result["what_if"] is True
    legs = client.ml_calls[-(len(result["alternatives"]) + 1)]["route"]["legs"]
    assert all("effective_customs_h" in leg and "congestion_pct" in leg for leg in legs)
    # Additional customs is already included in projected inputs, never add it twice.
    at_customs = [leg for leg in legs if leg["effective_customs_h"]]
    assert at_customs and all(leg["effective_customs_h"] >= 10 for leg in at_customs)


def test_active_leg_base_excludes_elapsed_travel_and_departure_wait(client):
    response = client.post("/api/shipments/SHP-1042/events", json={
        "event_type": "departure", "leg_id": "R1-L2", "source": "test operator",
        "actual_ts": (NOW - timedelta(hours=5)).isoformat(),
    })
    assert response.status_code == 201
    result = client.get(PATH + "/intelligence").json()
    leg = client.ml_calls[0]["route"]["legs"][0]
    assert result["scope"]["in_transit"] is True
    assert leg["in_transit"] is True
    assert leg["base_remaining_h"] == max(0, leg["base_h"] - 5)
    assert result["alternatives"] == []


def test_blocked_route_has_no_misleading_partial_prediction(client):
    result = client.get(PATH + "/intelligence?close=makhachkala").json()
    assert result["status"] == "blocked"
    assert result["prediction"] is None
    assert result["baseline"]["eta"] is None
    assert result["arrival"]["estimate"] is None


def test_bad_model_leaves_operational_calculation_available(client, monkeypatch):
    monkeypatch.setattr(ml, "predict_delay", lambda *a, **k: {
        "status": "unavailable", "ml_delay_h": None, "interval": None,
    })
    result = client.get(PATH + "/intelligence").json()
    assert result["status"] == "unavailable"
    assert result["baseline"]["eta"]
    assert result["arrival"]["estimate"] is None
    assert client.get("/api/operations").status_code == 200


def test_unknown_shipment_and_invalid_scenario(client):
    assert client.get("/api/operations/unknown/intelligence").status_code == 404
    for query in ("wave_m=-1", "wave_m=16", "extra_customs_h=169", "close=unknown"):
        assert client.get(PATH + "/intelligence?" + query).status_code == 422


def test_model_revision_is_pinned_across_one_comparison(client, monkeypatch):
    versions = iter([{"model_hash": "first"}, {"model_hash": "second"}])
    monkeypatch.setattr(ml, "get_trained", lambda: next(versions))
    result = client.get(PATH + "/intelligence").json()
    assert result["alternatives"]
    assert {call["trained"]["model_hash"] for call in client.ml_calls} == {"first"}
    assert result["model"]["model_hash"] == "first"
    assert next(versions)["model_hash"] == "second"
